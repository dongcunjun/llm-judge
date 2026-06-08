from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

from app import database
from app.llm import call_chat_completion
from app.parsing import LlmJudgeJsonlItem
from app.schemas import AppConfig, LLMConfig, ScoreConfig
from app.settings import DEFAULT_JUDGE_CONCURRENCY

LLMInput = str | list[dict[str, Any]]
LLMCaller = Callable[[LLMConfig, LLMInput], Awaitable[str]]
LLM_JUDGE_RETRY_COUNT = 3
ItemT = TypeVar("ItemT")


def _resolve_path(data: Any, path: str) -> Any:
    current: Any = data
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _compute_score(
    parsed: dict[str, Any],
    score_config: ScoreConfig | None,
) -> tuple[float | None, Any, dict[str, Any] | None]:
    if score_config is None:
        return parsed.get("overall_score"), parsed.get("overall_evidence"), None

    evidence = _resolve_path(parsed, score_config.evidence_path)
    if score_config.mode == "weighted":
        components = score_config.components or []
        total_weight = sum(c.weight for c in components)
        breakdown_components: list[dict[str, Any]] = []
        if total_weight <= 0:
            return None, evidence, {"mode": "weighted", "total_weight": total_weight, "components": []}
        accumulator = 0.0
        for component in components:
            value = _coerce_number(_resolve_path(parsed, component.path))
            normalized_weight = component.weight / total_weight
            breakdown_components.append(
                {
                    "path": component.path,
                    "weight": component.weight,
                    "normalized_weight": normalized_weight,
                    "score": value,
                    "contribution": value * normalized_weight if value is not None else None,
                }
            )
            if value is None:
                return None, evidence, {
                    "mode": "weighted",
                    "total_weight": total_weight,
                    "components": breakdown_components,
                    "total_score": None,
                }
            accumulator += value * component.weight
        score = accumulator / total_weight
        return score, evidence, {
            "mode": "weighted",
            "total_weight": total_weight,
            "components": breakdown_components,
            "total_score": score,
        }

    score = _coerce_number(_resolve_path(parsed, score_config.score_path))
    return score, evidence, {
        "mode": "direct",
        "score_path": score_config.score_path,
        "components": [
            {
                "path": score_config.score_path,
                "weight": 1,
                "normalized_weight": 1,
                "score": score,
                "contribution": score,
            }
        ],
        "total_score": score,
    }


def parse_llm_judge_output(
    output: str,
    score_config: ScoreConfig | None = None,
) -> dict[str, Any]:
    text = output.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {
            "parse_status": "other",
            "valid_json": False,
            "raw_output": output,
            "overall_score": None,
            "overall_evidence": "",
        }

    if isinstance(parsed, dict):
        score, evidence, score_breakdown = _compute_score(parsed, score_config)
    else:
        score, evidence, score_breakdown = None, None, None
    has_required = score is not None and evidence is not None
    result = {
        "parse_status": "valid" if has_required else "other",
        "valid_json": True,
        "raw_json": parsed,
        "overall_score": score,
        "overall_evidence": str(evidence) if evidence is not None else "",
    }
    if score_breakdown is not None:
        result["score_breakdown"] = score_breakdown
    return result


def validate_llm_config(config: LLMConfig, label: str) -> None:
    if not config.base_url.strip():
        raise ValueError(f"{label} base_url 不能为空")
    if not config.model.strip():
        raise ValueError(f"{label} model 不能为空")
    if config.timeout <= 0:
        raise ValueError(f"{label} timeout 必须大于 0")


async def process_llm_judge_job(
    db_path: Path,
    job_id: str,
    items: list[LlmJudgeJsonlItem],
    judge_config: LLMConfig,
    job_concurrency: int = DEFAULT_JUDGE_CONCURRENCY,
    llm_caller: LLMCaller = call_chat_completion,
    *,
    judge_retry_count: int = 2,
    initial_processed_rows: int = 0,
    initial_success_count: int = 0,
    initial_failure_count: int = 0,
    initial_errors: list[dict[str, Any]] | None = None,
    initial_judge_bug_count: int = 0,
    initial_valid_json_count: int = 0,
    initial_other_count: int = 0,
) -> None:
    errors = list(initial_errors or [])
    processed_rows = initial_processed_rows
    success_count = initial_success_count
    failure_count = initial_failure_count
    judge_bug_count = initial_judge_bug_count
    valid_json_count = initial_valid_json_count
    other_count = initial_other_count

    await database.mark_job_running(db_path, job_id)
    print(f"[judge-retry] job={job_id} 开始处理，judge_retry_count={judge_retry_count}，共{len(items)}条记录")
    try:
        validate_llm_config(judge_config, "裁判模型")
    except Exception as exc:
        await database.finish_job(db_path, job_id, "failed", [{"message": str(exc)}])
        return

    cancelled = False
    async for outcome in _iter_bounded_outcomes(
        items,
        job_concurrency,
        lambda item: _process_llm_judge_item(item, judge_config, llm_caller, db_path, job_id, judge_retry_count),
    ):
        if await database.is_job_cancelled(db_path, job_id):
            cancelled = True
            break
        if outcome.get("cancelled"):
            cancelled = True
            break
        try:
            if outcome["ok"]:
                await database.save_result(
                    db_path,
                    job_id=job_id,
                    playthrough_id=outcome["playthrough_id"],
                    turn=outcome["turn"],
                    prompt=outcome["prompt"],
                    input=outcome["input"],
                    output=outcome["output"],
                    judge_result=outcome["judge_result"],
                )
                judge_bug_count += 1
                if outcome.get("parse_status") == "valid":
                    valid_json_count += 1
                else:
                    other_count += 1
                success_count += 1
            else:
                failure_count += 1
                errors.append(outcome["error"])
        except Exception as exc:
            failure_count += 1
            errors.append(_error_payload(outcome, str(exc)))
        finally:
            processed_rows += 1
            await database.record_job_progress(
                db_path,
                job_id,
                processed_rows=processed_rows,
                success_count=success_count,
                failure_count=failure_count,
                errors=errors,
            )

    await database.finish_job(
        db_path,
        job_id,
        "cancelled" if cancelled else ("completed" if failure_count == 0 else "completed_with_errors"),
        errors,
        judge_bug_count=judge_bug_count,
        judge_exists_count=valid_json_count,
        judge_not_exists_count=0,
        judge_other_count=other_count,
    )


async def process_llm_judge_retry(
    db_path: Path,
    job_id: str,
    items: list[LlmJudgeJsonlItem],
    judge_config: LLMConfig,
    job_concurrency: int = DEFAULT_JUDGE_CONCURRENCY,
    llm_caller: LLMCaller = call_chat_completion,
    *,
    judge_retry_count: int = 2,
    original_total_rows: int | None = None,
    original_processed_rows: int | None = None,
    initial_success_count: int | None = None,
    initial_failure_count: int | None = None,
    initial_judge_bug_count: int | None = None,
    initial_valid_json_count: int | None = None,
    initial_other_count: int | None = None,
) -> None:
    current_job = await database.get_job(db_path, job_id)
    if current_job is None:
        return

    errors: list[dict[str, Any]] = []
    total_rows = original_total_rows if original_total_rows is not None else current_job.get("total_rows", 0)
    restored_processed_rows = (
        original_processed_rows
        if original_processed_rows is not None
        else current_job.get("processed_rows", total_rows)
    )
    success_count = (
        initial_success_count
        if initial_success_count is not None
        else current_job.get("success_count", 0)
    )
    failure_count = (
        initial_failure_count
        if initial_failure_count is not None
        else current_job.get("failure_count", 0)
    )
    judge_bug_count = (
        initial_judge_bug_count
        if initial_judge_bug_count is not None
        else current_job.get("judge_bug_count", 0)
    )
    valid_json_count = (
        initial_valid_json_count
        if initial_valid_json_count is not None
        else current_job.get("judge_exists_count", 0)
    )
    other_count = (
        initial_other_count
        if initial_other_count is not None
        else current_job.get("judge_other_count", 0)
    )
    retry_processed_rows = 0

    await database.mark_job_running(db_path, job_id)
    try:
        validate_llm_config(judge_config, "裁判模型")
    except Exception as exc:
        await database.finish_job(
            db_path,
            job_id,
            "failed",
            [{"message": str(exc)}],
            total_rows=total_rows,
            processed_rows=restored_processed_rows,
        )
        return

    cancelled = False
    async for outcome in _iter_bounded_outcomes(
        items,
        job_concurrency,
        lambda item: _process_llm_judge_item(item, judge_config, llm_caller, db_path, job_id, judge_retry_count),
    ):
        if await database.is_job_cancelled(db_path, job_id) or outcome.get("cancelled"):
            cancelled = True
            break
        try:
            if outcome["ok"]:
                await database.save_result(
                    db_path,
                    job_id=job_id,
                    playthrough_id=outcome["playthrough_id"],
                    turn=outcome["turn"],
                    prompt=outcome["prompt"],
                    input=outcome["input"],
                    output=outcome["output"],
                    judge_result=outcome["judge_result"],
                )
                judge_bug_count += 1
                if outcome.get("parse_status") == "valid":
                    valid_json_count += 1
                else:
                    other_count += 1
                success_count += 1
                failure_count -= 1
            else:
                errors.append(outcome["error"])
        except Exception as exc:
            errors.append(_error_payload(outcome, str(exc)))
        finally:
            retry_processed_rows += 1
            await database.record_job_progress(
                db_path,
                job_id,
                processed_rows=retry_processed_rows,
                success_count=success_count,
                failure_count=max(0, failure_count),
                errors=errors,
            )

    remaining_failures = max(0, failure_count)
    await database.finish_job(
        db_path,
        job_id,
        "cancelled" if cancelled else ("completed" if remaining_failures == 0 else "completed_with_errors"),
        errors,
        total_rows=total_rows,
        processed_rows=restored_processed_rows,
        failure_count=remaining_failures,
        judge_bug_count=judge_bug_count,
        judge_exists_count=valid_json_count,
        judge_not_exists_count=0,
        judge_other_count=other_count,
    )



async def _iter_bounded_outcomes(
    items: list[ItemT],
    concurrency: int,
    run_item: Callable[[ItemT], Awaitable[dict[str, Any]]],
) -> AsyncIterator[dict[str, Any]]:
    pending: set[asyncio.Task[dict[str, Any]]] = set()
    iterator = iter(items)
    limit = max(1, concurrency)

    def schedule_next() -> None:
        try:
            item = next(iterator)
        except StopIteration:
            return
        pending.add(asyncio.create_task(run_item(item)))

    for _ in range(limit):
        schedule_next()

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    yield await task
                except asyncio.CancelledError:
                    continue
                schedule_next()
    finally:
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _process_llm_judge_item(
    item: LlmJudgeJsonlItem,
    judge_config: LLMConfig,
    llm_caller: LLMCaller,
    db_path: Path,
    job_id: str,
    judge_retry_count: int = 2,
) -> dict[str, Any]:
    if await database.is_job_cancelled(db_path, job_id):
        return {"ok": False, "cancelled": True}
    try:
        total_attempts = judge_retry_count + 1
        parsed = None
        raw_output = ""
        for attempt in range(1, total_attempts + 1):
            try:
                raw_output = await call_llm_judge_with_retries(
                    judge_config,
                    item.prompt,
                    llm_caller,
                )
                parsed = parse_llm_judge_output(raw_output, item.score_config)
                if parsed["parse_status"] == "valid":
                    if attempt > 1:
                        print(f"[judge-retry] job={job_id} item={item.line_number} turn={item.turn} 第{attempt}次尝试成功解析为JSON")
                    break
                else:
                    print(f"[judge-retry] job={job_id} item={item.line_number} turn={item.turn} 第{attempt}次尝试，裁判结果不是有效JSON，{'将重试' if attempt < total_attempts else '已达最大重试次数'}")
            except Exception as exc:
                print(f"[judge-retry] job={job_id} item={item.line_number} turn={item.turn} 第{attempt}次尝试，LLM调用异常: {exc}")
                if attempt >= total_attempts:
                    raise
        target = item.judge_target
        if not isinstance(target, str) or not target.strip():
            target = "LLM-as-Judge"
        judge_result = {
            "target": target.strip(),
            "judge": parsed,
            "raw_output": raw_output,
        }
        return {
            "ok": True,
            "line_number": item.line_number,
            "playthrough_id": item.playthrough_id,
            "turn": item.turn,
            "prompt": item.prompt,
            "input": str(item.raw_payload.get("input") or ""),
            "output": str(item.raw_payload.get("output") or ""),
            "judge_result": judge_result,
            "parse_status": parsed["parse_status"],
        }
    except Exception as exc:
        return {
            "ok": False,
            "line_number": item.line_number,
            "playthrough_id": item.playthrough_id,
            "turn": item.turn,
            "error": _error_payload(
                {
                    "line_number": item.line_number,
                    "playthrough_id": item.playthrough_id,
                    "turn": item.turn,
                    "prompt": item.prompt,
                    "raw_payload": item.raw_payload,
                    "judge_target": item.judge_target,
                },
                str(exc),
            ),
        }


def _error_payload(outcome: dict[str, Any], message: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "line_number": outcome["line_number"],
        "playthrough_id": outcome["playthrough_id"],
        "turn": outcome["turn"],
        "message": message,
    }
    for key in ("prompt", "output", "messages", "llm_input", "player_feedback", "source_filename", "raw_payload"):
        if key in outcome and outcome[key] is not None:
            payload[key] = outcome[key]
    return payload


async def call_llm_judge_with_retries(
    config: LLMConfig,
    judge_prompt: str,
    llm_caller: LLMCaller,
    retry_count: int = LLM_JUDGE_RETRY_COUNT,
) -> str:
    attempts = retry_count + 1
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            return await llm_caller(config, judge_prompt)
        except Exception as exc:
            last_error = str(exc)
        if attempt < attempts:
            continue
    raise RuntimeError(f"LLM-as-Judge 调用失败，已重试 {retry_count} 次: {last_error}")
