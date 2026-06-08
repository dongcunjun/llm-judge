from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from urllib.parse import quote
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import database
from app.parsing import (
    JsonlParseError,
    parse_llm_judge_jsonl,
    parse_llm_judge_records,
    render_prompt_combine_template,
)
from app.parsing import LlmJudgeJsonlItem
from app.processing import (
    process_llm_judge_job,
    process_llm_judge_retry,
)
from app.schemas import (
    AppConfig,
    ConfigVersionsState,
    JudgeFileSummary,
    JudgePromptVersion,
    JudgePromptVersionCreate,
    JobResponse,
    JobStatus,
    LLMConfig,
    LLMConfigVersion,
    LLMConfigVersionCreate,
    PromptCombineTemplates,
    PromptCombineTemplateVersion,
    PromptCombineTemplateVersionCreate,
    ResultSummary,
    ResultsPage,
    RuntimeSettings,
)
from app.settings import APP_DIR, DATABASE_PATH, UPLOADS_DIR
from app.settings import (
    DEFAULT_BATCH_CHOICE_JUDGE_PROMPT_VERSION_ID,
    DEFAULT_BATCH_JUDGE_RETRY_COUNT,
    DEFAULT_BATCH_JUDGE_VERSION_ID,
    DEFAULT_BATCH_NARRATIVE_JUDGE_PROMPT_VERSION_ID,
    DEFAULT_BATCH_PROMPT_COMBINE_VERSION_ID,
    DEFAULT_UPLOAD_CHOICE_JUDGE_PROMPT_VERSION_ID,
    DEFAULT_UPLOAD_JUDGE_VERSION_ID,
    DEFAULT_UPLOAD_NARRATIVE_JUDGE_PROMPT_VERSION_ID,
    DEFAULT_UPLOAD_PROMPT_COMBINE_VERSION_ID,
)


LLM_JUDGE_BATCH_SOURCE_NAME = "batch"
MAX_SOURCE_FILENAME_LENGTH = 180


@asynccontextmanager
async def lifespan(_: FastAPI):
    await database.init_db(DATABASE_PATH)
    await database.recover_interrupted_jobs(DATABASE_PATH)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="裁判系统", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


def static_version(filename: str) -> str:
    path = APP_DIR / "static" / filename
    try:
        return str(int(path.stat().st_mtime))
    except OSError:
        return "dev"


templates.env.globals["static_version"] = static_version


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index():
    return RedirectResponse(url="/config")


@app.get("/llm-judge", response_class=HTMLResponse)
async def llm_judge_page(request: Request):
    return RedirectResponse(url="/config")


@app.get("/llm-judge/batch-test", response_class=HTMLResponse)
async def llm_judge_batch_test_page(request: Request):
    return templates.TemplateResponse(request, "llm_judge_batch_test.html")


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    return templates.TemplateResponse(request, "config.html")


@app.get("/api/config", response_model=ConfigVersionsState)
async def get_config():
    return _masked_config_state(await database.get_config_state(DATABASE_PATH))


@app.put("/api/config", response_model=ConfigVersionsState)
async def update_config(config: AppConfig):
    await database.save_app_config(DATABASE_PATH, config)
    return _masked_config_state(await database.get_config_state(DATABASE_PATH))


@app.put("/api/config/runtime", response_model=RuntimeSettings)
async def update_runtime_config(settings: RuntimeSettings):
    return await database.save_runtime_settings(DATABASE_PATH, settings)


@app.put("/api/config/prompt-combine", response_model=PromptCombineTemplates)
async def update_prompt_combine_config(templates_payload: PromptCombineTemplates):
    return await database.save_prompt_combine_templates(DATABASE_PATH, templates_payload)


def _masked_config_state(state: ConfigVersionsState) -> ConfigVersionsState:
    return _copy_model(
        state,
        {
            "judge_versions": [_masked_llm_version(version) for version in state.judge_versions],
        },
    )


def _masked_llm_version(version: LLMConfigVersion) -> LLMConfigVersion:
    config = _copy_model(version.config, {"api_key": _mask_api_key(version.config.api_key)})
    return _copy_model(version, {"config": config})


def _copy_model(model, update: dict):
    if hasattr(model, "model_copy"):
        return model.model_copy(update=update)
    return model.copy(update=update)


def _mask_api_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "********"
    return f"{value[:3]}****{value[-4:]}"


def _clean_query_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _model_json(model) -> str:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json()
    return model.json(ensure_ascii=False)


def _llm_config_from_json(value: str) -> LLMConfig:
    if hasattr(LLMConfig, "model_validate_json"):
        return LLMConfig.model_validate_json(value)
    return LLMConfig.parse_raw(value)



async def _resolve_prompt_combine_templates(
    version_id: int | None,
) -> tuple[PromptCombineTemplates, str]:
    if version_id is None:
        version_name = await database.get_active_prompt_combine_version_name(DATABASE_PATH)
        return await database.get_prompt_combine_templates(DATABASE_PATH), version_name
    version = await database.get_prompt_combine_version(DATABASE_PATH, version_id)
    if version is None:
        raise HTTPException(status_code=400, detail="历史轮次拼接模版版本不存在")
    return (
        PromptCombineTemplates(
            system_template=version.system_template,
            user_template=version.user_template,
            assistant_template=version.assistant_template,
            narrative_output_template=version.narrative_output_template,
            choice_option_template=version.choice_option_template,
        ),
        version.name,
    )


async def _resolve_target_judge_prompt_version(
    target: str,
    version_id: int | None,
) -> JudgePromptVersion:
    state = await database.get_config_state(DATABASE_PATH)
    versions = (
        state.narrative_judge_prompt_versions
        if target == "narrative"
        else state.choice_judge_prompt_versions
    )
    active_id = (
        state.active_narrative_judge_prompt_id
        if target == "narrative"
        else state.active_choice_judge_prompt_id
    )
    if version_id is None:
        active = next((v for v in versions if v.id == active_id), None)
        if active:
            return active
        if versions:
            return versions[0]
        raise HTTPException(status_code=400, detail="Judge Prompt 版本不存在")
    version = await database.get_judge_prompt_version(DATABASE_PATH, target, version_id)
    if version is None:
        raise HTTPException(status_code=400, detail="Judge Prompt 版本不存在")
    return version


async def _resolve_llm_judge_prompt_versions(
    narrative_version_id: int | None,
    choice_version_id: int | None,
) -> tuple[JudgePromptVersion, JudgePromptVersion]:
    narrative_prompt = await _resolve_target_judge_prompt_version(
        "narrative",
        narrative_version_id,
    )
    choice_prompt = await _resolve_target_judge_prompt_version(
        "choice",
        choice_version_id,
    )
    return narrative_prompt, choice_prompt


async def _judge_config_for_llm_judge(
    judge_version_id: int | None,
) -> tuple[LLMConfig, str]:
    app_config = await database.get_app_config(DATABASE_PATH)
    if judge_version_id is None:
        judge_version_name = await database.get_active_llm_config_version_name(
            DATABASE_PATH,
            database.JUDGE_KIND,
        )
        return app_config.judge, judge_version_name

    judge_version = await database.get_llm_config_version(
        DATABASE_PATH,
        database.JUDGE_KIND,
        judge_version_id,
    )
    if judge_version is None:
        raise HTTPException(status_code=400, detail="裁判 LLM API 版本不存在")
    return judge_version.config, judge_version.name


async def _judge_config_for_llm_judge_resume(job: dict) -> LLMConfig:
    snapshot = job.get("judge_llm_config") or ""
    if snapshot:
        try:
            return _llm_config_from_json(snapshot)
        except Exception:
            pass
    state = await database.get_config_state(DATABASE_PATH)
    version_name = job.get("judge_llm_version_name", "")
    for version in state.judge_versions:
        if version.name == version_name:
            return version.config
    return (await database.get_app_config(DATABASE_PATH)).judge


def _llm_judge_prompt_pair_name(
    narrative_prompt: JudgePromptVersion,
    choice_prompt: JudgePromptVersion,
) -> str:
    if narrative_prompt.name == choice_prompt.name:
        return narrative_prompt.name
    return f"正文:{narrative_prompt.name} / 选项:{choice_prompt.name}"



def _safe_source_filename(filename: str | None, default: str = "uploaded.jsonl") -> str:
    value = re.split(r"[/\\]+", (filename or "").strip())[-1]
    value = "".join(character for character in value if character >= " " and character != "\x7f")
    value = value.lstrip(".")
    if not value:
        value = default
    if len(value) > MAX_SOURCE_FILENAME_LENGTH:
        suffix = ".jsonl" if value.lower().endswith(".jsonl") else ""
        value = f"{value[:MAX_SOURCE_FILENAME_LENGTH - len(suffix)]}{suffix}"
    return value


def _excel_export_disposition(source_filename: str, job_id: str) -> str:
    source_name = _safe_source_filename(source_filename, job_id)
    stem = source_name[:-6] if source_name.lower().endswith(".jsonl") else source_name
    filename = f"{stem}_judge.xlsx"
    ascii_filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "judge.xlsx"
    return f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{quote(filename)}"


async def _run_config_write(awaitable):
    try:
        return await awaitable
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/config/judge", response_model=LLMConfigVersion)
async def create_judge_config(payload: LLMConfigVersionCreate):
    version = await _run_config_write(
        database.create_llm_config_version(
            DATABASE_PATH,
            database.JUDGE_KIND,
            payload,
            inherit_from_version_id=payload.inherit_api_key_from_id,
        )
    )
    return _masked_llm_version(version)


@app.put("/api/config/judge/{version_id}", response_model=LLMConfigVersion)
async def update_judge_config(version_id: int, payload: LLMConfigVersionCreate):
    version = await _run_config_write(
        database.update_llm_config_version(DATABASE_PATH, database.JUDGE_KIND, version_id, payload)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="裁判 API 版本不存在")
    return _masked_llm_version(version)


@app.post("/api/config/judge/{version_id}/activate", response_model=LLMConfigVersion)
async def activate_judge_config(version_id: int):
    version = await _run_config_write(
        database.activate_llm_config_version(DATABASE_PATH, database.JUDGE_KIND, version_id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="裁判 API 版本不存在")
    return _masked_llm_version(version)


@app.post("/api/config/prompt-combine", response_model=PromptCombineTemplateVersion)
async def create_prompt_combine_config(payload: PromptCombineTemplateVersionCreate):
    return await _run_config_write(database.create_prompt_combine_version(DATABASE_PATH, payload))


@app.put("/api/config/prompt-combine/{version_id}", response_model=PromptCombineTemplateVersion)
async def update_prompt_combine_config(version_id: int, payload: PromptCombineTemplateVersionCreate):
    version = await _run_config_write(
        database.update_prompt_combine_version(DATABASE_PATH, version_id, payload)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="历史轮次拼接模版版本不存在")
    return version


@app.post("/api/config/prompt-combine/{version_id}/activate", response_model=PromptCombineTemplateVersion)
async def activate_prompt_combine_config(version_id: int):
    version = await _run_config_write(
        database.activate_prompt_combine_version(DATABASE_PATH, version_id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="历史轮次拼接模版版本不存在")
    return version


@app.delete("/api/config/prompt-combine/{version_id}")
async def delete_prompt_combine_config(version_id: int):
    try:
        deleted = await database.delete_prompt_combine_version(DATABASE_PATH, version_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="历史轮次拼接模版版本不存在")
    return {"ok": True}


@app.delete("/api/config/judge/{version_id}")
async def delete_judge_config(version_id: int):
    try:
        deleted = await database.delete_llm_config_version(DATABASE_PATH, database.JUDGE_KIND, version_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="裁判 API 版本不存在")
    return {"ok": True}


@app.post("/api/config/judge-prompts", response_model=JudgePromptVersion)
async def create_judge_prompt_config(payload: JudgePromptVersionCreate):
    return await _run_config_write(database.create_judge_prompt_version(DATABASE_PATH, payload))


@app.post("/api/config/judge-prompts/{target}", response_model=JudgePromptVersion)
async def create_target_judge_prompt_config(target: str, payload: JudgePromptVersionCreate):
    if target not in {"narrative", "choice"}:
        raise HTTPException(status_code=400, detail="Judge Prompt 类型无效")
    return await _run_config_write(
        database.create_judge_prompt_version(DATABASE_PATH, payload, target=target)
    )


@app.put("/api/config/judge-prompts/{target}/{version_id}", response_model=JudgePromptVersion)
async def update_judge_prompt_config(target: str, version_id: int, payload: JudgePromptVersionCreate):
    if target not in {"narrative", "choice"}:
        raise HTTPException(status_code=400, detail="Judge Prompt 类型无效")
    version = await _run_config_write(
        database.update_judge_prompt_version(DATABASE_PATH, target, version_id, payload)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Judge Prompt 版本不存在")
    return version


@app.post("/api/config/judge-prompts/{target}/{version_id}/activate", response_model=JudgePromptVersion)
async def activate_judge_prompt_config(target: str, version_id: int):
    if target not in {"narrative", "choice"}:
        raise HTTPException(status_code=400, detail="Judge Prompt 类型无效")
    version = await _run_config_write(
        database.activate_llm_judge_prompt_default(DATABASE_PATH, target, version_id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Judge Prompt 版本不存在")
    return version


@app.delete("/api/config/judge-prompts/{target}/{version_id}")
async def delete_judge_prompt_config(target: str, version_id: int):
    if target not in {"narrative", "choice"}:
        raise HTTPException(status_code=400, detail="Judge Prompt 类型无效")
    try:
        deleted = await database.delete_judge_prompt_version(DATABASE_PATH, target, version_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Judge Prompt 版本不存在")
    return {"ok": True}



@app.post("/api/llm-judge/{job_id}/retry-failures", response_model=JobResponse)
async def retry_llm_judge_failures(
    background_tasks: BackgroundTasks,
    job_id: str,
):
    job = await database.get_job(DATABASE_PATH, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("kind") != "llm_judge_upload":
        raise HTTPException(status_code=400, detail="该任务不是 LLM-as-Judge 任务")
    if job["status"] in ("pending", "running"):
        raise HTTPException(status_code=400, detail="进行中的任务不能重试")

    retry_items = await _failed_llm_judge_items(job)
    if not retry_items:
        raise HTTPException(status_code=400, detail="没有可重试的失败项（缺少原始数据）")

    judge_config = await _judge_config_for_llm_judge_resume(job)
    runtime_settings = await database.get_runtime_settings(DATABASE_PATH)
    original_total_rows = job["total_rows"]
    original_processed_rows = job.get("processed_rows", original_total_rows)
    await database.prepare_job_retry(DATABASE_PATH, job_id, len(retry_items))
    background_tasks.add_task(
        process_llm_judge_retry,
        DATABASE_PATH,
        job_id,
        retry_items,
        judge_config,
        runtime_settings.judge_concurrency,
        original_total_rows=original_total_rows,
        original_processed_rows=original_processed_rows,
        initial_success_count=job["success_count"],
        initial_failure_count=job["failure_count"],
        initial_judge_bug_count=job.get("judge_bug_count", 0),
        initial_valid_json_count=job.get("judge_exists_count", 0),
        initial_other_count=job.get("judge_other_count", 0),
    )
    return JobResponse(job_id=job_id, total_rows=len(retry_items), status="pending")


async def _source_file_content(job_id: str) -> bytes | None:
    job_dir = UPLOADS_DIR / job_id
    if not job_dir.is_dir():
        return None
    source_files = [path for path in job_dir.iterdir() if path.is_file()]
    if not source_files:
        return None
    return source_files[0].read_bytes()




async def _all_llm_judge_items_for_job(job: dict) -> list[LlmJudgeJsonlItem]:
    content = await _source_file_content(job["job_id"])
    if content is None:
        raise HTTPException(status_code=400, detail="缺少原始上传文件，无法继续该 LLM-as-Judge 任务")
    narrative_version_id = await _judge_prompt_version_id_for_name(
        "narrative",
        job.get("narrative_judge_prompt_version_name", "")
        or job.get("judge_prompt_version_name", ""),
    )
    choice_version_id = await _judge_prompt_version_id_for_name(
        "choice",
        job.get("choice_judge_prompt_version_name", ""),
    )
    narrative_prompt, choice_prompt = await _resolve_llm_judge_prompt_versions(
        narrative_version_id,
        choice_version_id,
    )
    prompt_combine_version_id = await _prompt_combine_version_id_for_name(
        job.get("prompt_combine_version_name", "")
    )
    prompt_combine_templates, _ = await _resolve_prompt_combine_templates(prompt_combine_version_id)
    try:
        return parse_llm_judge_jsonl(
            content,
            narrative_prompt,
            choice_prompt,
            prompt_combine_templates,
        )
    except JsonlParseError as exc:
        raise HTTPException(status_code=400, detail=f"原始上传文件无法解析: {exc}") from exc


async def _remaining_llm_judge_items(job: dict) -> list[LlmJudgeJsonlItem]:
    all_items = await _all_llm_judge_items_for_job(job)
    existing_results = await database.get_all_results_for_job(DATABASE_PATH, job["job_id"])
    finished_rows = {
        _llm_judge_result_key(result)
        for result in existing_results
    }
    failed_rows = {
        _llm_judge_error_key(error)
        for error in job.get("errors", [])
        if error.get("playthrough_id") is not None and error.get("turn") is not None
    }
    excluded = finished_rows | failed_rows
    return [
        item for item in all_items
        if _llm_judge_item_key(item) not in excluded
    ]


async def _failed_llm_judge_items(job: dict) -> list[LlmJudgeJsonlItem]:
    errors = job.get("errors", [])
    if not errors:
        return []
    all_items = await _all_llm_judge_items_for_job(job)
    failed_rows = {
        _llm_judge_error_key(error)
        for error in errors
        if error.get("playthrough_id") is not None and error.get("turn") is not None
    }
    existing_results = await database.get_all_results_for_job(DATABASE_PATH, job["job_id"])
    resolved_rows = {
        _llm_judge_result_key(result)
        for result in existing_results
    }
    failed_rows -= resolved_rows
    return [
        item for item in all_items
        if _llm_judge_item_key(item) in failed_rows
    ]


def _llm_judge_item_key(item: LlmJudgeJsonlItem) -> tuple[str, int, str, int]:
    return (str(item.playthrough_id), int(item.turn), item.judge_target or "", int(item.line_number))


def _llm_judge_error_key(error: dict) -> tuple[str, int, str, int]:
    return (
        str(error.get("playthrough_id") or ""),
        int(error.get("turn") or 0),
        str(error.get("judge_target") or _raw_payload_target(error.get("raw_payload"))),
        int(error.get("line_number") or 0),
    )


def _llm_judge_result_key(result: dict) -> tuple[str, int, str, int]:
    judge_result = result.get("judge_result") or {}
    target = judge_result.get("target") if isinstance(judge_result, dict) else ""
    return (str(result.get("playthrough_id") or ""), int(result.get("turn") or 0), target, 0)


def _raw_payload_target(raw_payload: object) -> str:
    if isinstance(raw_payload, dict):
        return str(raw_payload.get("target") or "")
    return ""


async def _prompt_combine_version_id_for_name(version_name: str) -> int | None:
    if not version_name:
        return None
    state = await database.get_config_state(DATABASE_PATH)
    for version in state.prompt_combine_versions:
        if version.name == version_name:
            return version.id
    return None


async def _judge_prompt_version_id_for_name(target: str, version_name: str) -> int | None:
    if not version_name:
        return None
    state = await database.get_config_state(DATABASE_PATH)
    versions = (
        state.narrative_judge_prompt_versions
        if target == "narrative"
        else state.choice_judge_prompt_versions
    )
    for version in versions:
        if version.name == version_name:
            return version.id
    return None


async def _create_and_schedule_llm_judge_job(
    background_tasks: BackgroundTasks,
    items: list[LlmJudgeJsonlItem],
    source_filename: str,
    *,
    judge_version_id: int | None = None,
    narrative_prompt: JudgePromptVersion,
    choice_prompt: JudgePromptVersion,
    prompt_combine_version_name: str,
    content: bytes | None = None,
    concurrency: int | None = None,
    judge_retry_count: int = 2,
) -> JobResponse:
    job_id = uuid4().hex
    source_filename = _safe_source_filename(source_filename)
    judge_config, judge_version_name = await _judge_config_for_llm_judge(judge_version_id)
    if concurrency is None:
        runtime_settings = await database.get_runtime_settings(DATABASE_PATH)
        job_concurrency = runtime_settings.judge_concurrency
    else:
        job_concurrency = concurrency
    if content is not None:
        job_dir = UPLOADS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        source_path = job_dir / source_filename
        source_path.write_bytes(content)

    await database.create_job(
        DATABASE_PATH,
        job_id,
        len(items),
        kind="llm_judge_upload",
        source_filename=source_filename,
        judge_llm_version_name=judge_version_name,
        judge_llm_config=_model_json(judge_config),
        judge_prompt_version_name=_llm_judge_prompt_pair_name(narrative_prompt, choice_prompt),
        judge_prompt=narrative_prompt.template,
        narrative_judge_prompt_version_name=narrative_prompt.name,
        narrative_judge_prompt=narrative_prompt.template,
        choice_judge_prompt_version_name=choice_prompt.name,
        choice_judge_prompt=choice_prompt.template,
        prompt_combine_version_name=prompt_combine_version_name,
        concurrency=job_concurrency,
        judge_retry_count=judge_retry_count,
    )
    background_tasks.add_task(
        process_llm_judge_job,
        DATABASE_PATH,
        job_id,
        items,
        judge_config,
        job_concurrency,
        judge_retry_count=judge_retry_count,
    )
    return JobResponse(job_id=job_id, total_rows=len(items), status="pending")


async def _parse_llm_judge_content(
    content: bytes,
    *,
    narrative_judge_prompt_version_id: int | None = None,
    choice_judge_prompt_version_id: int | None = None,
    prompt_combine_version_id: int | None = None,
) -> tuple[list[LlmJudgeJsonlItem], JudgePromptVersion, JudgePromptVersion, str]:
    narrative_prompt, choice_prompt = await _resolve_llm_judge_prompt_versions(
        narrative_judge_prompt_version_id,
        choice_judge_prompt_version_id,
    )
    prompt_combine_templates, prompt_combine_version_name = await _resolve_prompt_combine_templates(
        prompt_combine_version_id
    )
    try:
        items = parse_llm_judge_jsonl(
            content,
            narrative_prompt,
            choice_prompt,
            prompt_combine_templates,
        )
    except JsonlParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return items, narrative_prompt, choice_prompt, prompt_combine_version_name


async def _parse_llm_judge_batch_records(
    records: list[dict],
    *,
    narrative_judge_prompt_version_id: int | None = None,
    choice_judge_prompt_version_id: int | None = None,
    prompt_combine_version_id: int | None = None,
) -> tuple[list[LlmJudgeJsonlItem], JudgePromptVersion, JudgePromptVersion, str]:
    narrative_prompt, choice_prompt = await _resolve_llm_judge_prompt_versions(
        narrative_judge_prompt_version_id,
        choice_judge_prompt_version_id,
    )
    prompt_combine_templates, prompt_combine_version_name = await _resolve_prompt_combine_templates(
        prompt_combine_version_id
    )
    try:
        items = parse_llm_judge_records(
            records,
            narrative_prompt,
            choice_prompt,
            prompt_combine_templates,
        )
    except JsonlParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return items, narrative_prompt, choice_prompt, prompt_combine_version_name


@app.post("/api/llm-judge/upload", response_model=JobResponse)
async def upload_llm_judge_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    judge_version_id: int | None = Form(default=None),
    narrative_judge_prompt_version_id: int | None = Form(default=None),
    choice_judge_prompt_version_id: int | None = Form(default=None),
    prompt_combine_version_id: int | None = Form(default=None),
):
    source_filename = _safe_source_filename(file.filename)
    if not source_filename.lower().endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="请上传 .jsonl 文件")

    judge_version_id = judge_version_id or DEFAULT_UPLOAD_JUDGE_VERSION_ID
    narrative_judge_prompt_version_id = (
        narrative_judge_prompt_version_id or DEFAULT_UPLOAD_NARRATIVE_JUDGE_PROMPT_VERSION_ID
    )
    choice_judge_prompt_version_id = (
        choice_judge_prompt_version_id or DEFAULT_UPLOAD_CHOICE_JUDGE_PROMPT_VERSION_ID
    )
    prompt_combine_version_id = prompt_combine_version_id or DEFAULT_UPLOAD_PROMPT_COMBINE_VERSION_ID

    content = await file.read()
    items, narrative_prompt, choice_prompt, prompt_combine_version_name = await _parse_llm_judge_content(
        content,
        narrative_judge_prompt_version_id=narrative_judge_prompt_version_id,
        choice_judge_prompt_version_id=choice_judge_prompt_version_id,
        prompt_combine_version_id=prompt_combine_version_id,
    )
    return await _create_and_schedule_llm_judge_job(
        background_tasks,
        items,
        source_filename,
        judge_version_id=judge_version_id,
        narrative_prompt=narrative_prompt,
        choice_prompt=choice_prompt,
        prompt_combine_version_name=prompt_combine_version_name,
        content=content,
    )


@app.post("/api/llm-judge/batch", response_model=JobResponse)
async def batch_llm_judge_job(
    background_tasks: BackgroundTasks,
    records: list[dict],
    judge_version_id: int | None = Query(default=None),
    narrative_judge_prompt_version_id: int | None = Query(default=None),
    choice_judge_prompt_version_id: int | None = Query(default=None),
    prompt_combine_version_id: int | None = Query(default=None),
    concurrency: int | None = Query(default=None, ge=1, le=500, description="并发数，不传则使用运行时默认值"),
    judge_retry_count: int | None = Query(default=None, ge=0, le=10, description="裁判结果非JSON时重试次数，不传则使用默认值"),
):
    if not records:
        raise HTTPException(status_code=400, detail="batch 数据不能为空")

    judge_version_id = judge_version_id or DEFAULT_BATCH_JUDGE_VERSION_ID
    narrative_judge_prompt_version_id = (
        narrative_judge_prompt_version_id or DEFAULT_BATCH_NARRATIVE_JUDGE_PROMPT_VERSION_ID
    )
    choice_judge_prompt_version_id = (
        choice_judge_prompt_version_id or DEFAULT_BATCH_CHOICE_JUDGE_PROMPT_VERSION_ID
    )
    prompt_combine_version_id = prompt_combine_version_id or DEFAULT_BATCH_PROMPT_COMBINE_VERSION_ID
    judge_retry_count = judge_retry_count if judge_retry_count is not None else DEFAULT_BATCH_JUDGE_RETRY_COUNT

    items, narrative_prompt, choice_prompt, prompt_combine_version_name = await _parse_llm_judge_batch_records(
        records,
        narrative_judge_prompt_version_id=narrative_judge_prompt_version_id,
        choice_judge_prompt_version_id=choice_judge_prompt_version_id,
        prompt_combine_version_id=prompt_combine_version_id,
    )
    return await _create_and_schedule_llm_judge_job(
        background_tasks,
        items,
        LLM_JUDGE_BATCH_SOURCE_NAME,
        judge_version_id=judge_version_id,
        narrative_prompt=narrative_prompt,
        choice_prompt=choice_prompt,
        prompt_combine_version_name=prompt_combine_version_name,
        concurrency=concurrency,
        judge_retry_count=judge_retry_count,
    )



@app.get("/api/llm-judge/files", response_model=list[JudgeFileSummary])
async def list_llm_judge_files(
    sort_by: str = Query(default="created", pattern="^(created|exists)$"),
    source: str | None = Query(default=None),
):
    return await database.list_llm_judge_files(DATABASE_PATH, sort_by=sort_by, source_filename=source)



@app.get("/api/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    job = await database.get_job(DATABASE_PATH, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = await database.get_job(DATABASE_PATH, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job["status"] not in ("pending", "running"):
        raise HTTPException(status_code=400, detail="只能取消进行中的任务")
    await database.mark_job_cancelled(DATABASE_PATH, job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/resume", response_model=JobResponse)
async def resume_job(background_tasks: BackgroundTasks, job_id: str):
    job = await database.get_job(DATABASE_PATH, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job["status"] != "cancelled":
        raise HTTPException(status_code=400, detail="只能继续已停止的任务")
    if job.get("processed_rows", 0) >= job.get("total_rows", 0):
        raise HTTPException(status_code=400, detail="该任务已经没有未完成项")

    runtime_settings = await database.get_runtime_settings(DATABASE_PATH)
    if job.get("kind") == "llm_judge_upload":
        items = await _remaining_llm_judge_items(job)
        if not items:
            raise HTTPException(status_code=400, detail="该任务已经没有未完成项")
        judge_config = await _judge_config_for_llm_judge_resume(job)
        await database.prepare_job_resume(DATABASE_PATH, job_id)
        background_tasks.add_task(
            process_llm_judge_job,
            DATABASE_PATH,
            job_id,
            items,
            judge_config,
            runtime_settings.judge_concurrency,
            initial_processed_rows=job.get("processed_rows", 0),
            initial_success_count=job.get("success_count", 0),
            initial_failure_count=job.get("failure_count", 0),
            initial_errors=job.get("errors", []),
            initial_judge_bug_count=job.get("judge_bug_count", 0),
            initial_valid_json_count=job.get("judge_exists_count", 0),
            initial_other_count=job.get("judge_other_count", 0),
        )
    else:
        raise HTTPException(status_code=400, detail="该任务类型不支持继续")

    return JobResponse(job_id=job_id, total_rows=job["total_rows"], status="pending")


@app.get("/api/results", response_model=ResultsPage)
async def list_results(
    job_id: str | None = Query(default=None),
    playthrough_id: str | None = Query(default=None),
    turn: int | None = Query(default=None),
    result_type: str | None = Query(default=None, pattern="^(llm_judged)$"),
    judge_state: str | None = Query(default=None, pattern="^(exists|not_exists|other)$"),
    judge_target: str | None = Query(default=None, pattern="^(narrative|choice)$"),
    score: float | None = Query(default=None),
    score_sort: str | None = Query(default=None, pattern="^(asc|desc)$"),
    source_filename: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=0, ge=0, le=100000),
):
    """page_size=0 表示不分页，返回全部结果"""
    return await database.list_results(
        DATABASE_PATH,
        job_id=_clean_query_text(job_id),
        playthrough_id=_clean_query_text(playthrough_id),
        turn=turn,
        result_type=result_type,
        judge_state=judge_state,
        judge_target=judge_target,
        score=score,
        score_sort=score_sort,
        source_filename=_clean_query_text(source_filename),
        page=page,
        page_size=page_size,
    )


@app.get("/api/results/{result_id}", response_model=ResultSummary)
async def get_result(result_id: int):
    result = await database.get_result(DATABASE_PATH, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="结果不存在")
    return result


@app.get("/api/llm-judge/{job_id}/export")
async def export_llm_judge_excel(job_id: str):
    import io

    from openpyxl import Workbook

    results = await database.get_all_raw_results_for_job(DATABASE_PATH, job_id)
    if not results:
        raise HTTPException(status_code=404, detail="该任务没有结果数据")
    job = await database.get_job(DATABASE_PATH, job_id)
    if not job or job.get("kind") != "llm_judge_upload":
        raise HTTPException(status_code=400, detail="该任务不是 LLM-as-Judge 任务")

    rows_data = []
    max_prompt_len = 0

    for result in results:
        judge_result = result.get("judge_result") or {}
        if not isinstance(judge_result, dict) or not judge_result:
            continue
        target = judge_result.get("target", "")
        judge = judge_result.get("judge")
        prompt_text = str(result.get("prompt") or "")
        max_prompt_len = max(max_prompt_len, len(prompt_text))
        row = {
            "prompt": prompt_text,
            "input": result.get("input", ""),
            "output": result.get("output", ""),
            "target": target,
        }
        judge_payload = judge if isinstance(judge, dict) else {}
        row["overall_score"] = judge_payload.get("overall_score", "")
        row["overall_evidence"] = judge_payload.get("overall_evidence", "")
        row["parse_status"] = judge_payload.get("parse_status", "")
        row["raw_output"] = judge_result.get("raw_output", "")
        rows_data.append(row)

    if not rows_data:
        raise HTTPException(status_code=404, detail="没有可导出的裁判数据")

    wb = Workbook()
    ws = wb.active
    ws.title = "裁判结果"

    chunk_size = 32000
    prompt_parts = max((max_prompt_len + chunk_size - 1) // chunk_size, 1)
    prompt_headers = ["prompt"] if prompt_parts == 1 else [f"prompt_{i}" for i in range(1, prompt_parts + 1)]

    headers = prompt_headers + ["input", "output", "target", "overall_score", "overall_evidence", "parse_status", "raw_output"]

    ws.append(headers)

    for row in rows_data:
        values = []
        prompt_text = str(row.get("prompt", ""))
        for i in range(prompt_parts):
            chunk = prompt_text[i * chunk_size : (i + 1) * chunk_size]
            values.append(chunk)
        for header in headers[len(prompt_headers):]:
            values.append(str(row.get(header, "")))
        ws.append(values)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    disposition = _excel_export_disposition(job.get("source_filename", ""), job_id)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    import shutil

    job = await database.get_job(DATABASE_PATH, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job["status"] in ("pending", "running"):
        raise HTTPException(status_code=400, detail="进行中的任务不能删除，请先停止任务")
    deleted = await database.delete_job(DATABASE_PATH, job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")

    job_dir = UPLOADS_DIR / job_id
    if job_dir.is_dir():
        shutil.rmtree(job_dir)
    return {"ok": True}
