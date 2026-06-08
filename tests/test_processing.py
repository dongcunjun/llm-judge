from pathlib import Path

import pytest

from app import database
from app.parsing import LlmJudgeJsonlItem, parse_llm_judge_jsonl
from app.processing import (
    parse_llm_judge_output,
    process_llm_judge_job,
)
from app.schemas import (
    AppConfig,
    JudgePromptField,
    JudgePromptVersion,
    LLMConfig,
    ScoreComponent,
    ScoreConfig,
)


def test_parse_llm_judge_output_keeps_json_shape_and_ignores_result_semantics():
    parsed = parse_llm_judge_output('{"overall_score": 0.8, "overall_evidence": "理由", "result": 1}')

    assert parsed["parse_status"] == "valid"
    assert parsed["overall_score"] == 0.8
    assert parsed["overall_evidence"] == "理由"
    assert parsed["raw_json"]["result"] == 1

    invalid = parse_llm_judge_output("存在")
    assert invalid["parse_status"] == "other"
    assert invalid["valid_json"] is False
    assert invalid["raw_output"] == "存在"


@pytest.mark.asyncio
async def test_process_llm_judge_job_saves_json_score_and_other_outputs(tmp_path: Path):
    db_path = tmp_path / "test.db"
    await database.init_db(db_path)
    job_id = "llm-judge-job"
    await database.create_job(db_path, job_id, 3, kind="llm_judge_upload")
    items = [
        LlmJudgeJsonlItem(1, "p1", 1, "valid", {"playthrough_id": "p1", "turn": 1}, "quality"),
        LlmJudgeJsonlItem(2, "p2", 2, "missing", {"playthrough_id": "p2", "turn": 2}),
        LlmJudgeJsonlItem(3, "p3", 3, "invalid", {"playthrough_id": "p3", "turn": 3}),
    ]

    async def fake_llm(config: LLMConfig, prompt: str) -> str:
        if prompt == "valid":
            return '{"overall_score": 0.7, "overall_evidence": "不错"}'
        if prompt == "missing":
            return '{"foo": "bar"}'
        return "not json"

    await process_llm_judge_job(
        db_path,
        job_id,
        items,
        LLMConfig(base_url="https://judge.test/v1", model="judge-model"),
        llm_caller=fake_llm,
    )

    job = await database.get_job(db_path, job_id)
    results = await database.list_results(db_path, result_type="llm_judged", page_size=10)

    assert job["status"] == "completed"
    assert job["judge_bug_count"] == 3
    assert job["judge_exists_count"] == 1
    assert job["judge_other_count"] == 2
    assert results["total"] == 3
    valid = next(item for item in results["items"] if item["playthrough_id"] == "p1")
    assert valid["input"] == ""
    assert valid["output"] == ""
    assert valid["judge_result"]["target"] == "quality"
    assert valid["judge_result"]["judge"]["overall_score"] == 0.7
    assert "judge_prompt" not in valid["judge_result"]
    invalid = next(item for item in results["items"] if item["playthrough_id"] == "p3")
    assert invalid["judge_result"]["judge"]["raw_output"] == "not json"


@pytest.mark.asyncio
async def test_llm_judged_results_are_sorted_by_playthrough_and_turn(tmp_path: Path):
    db_path = tmp_path / "test.db"
    await database.init_db(db_path)
    job_id = "llm-judge-sorted"
    await database.create_job(db_path, job_id, 3, kind="llm_judge_upload")

    for playthrough_id, turn in [("p1", 2), ("p2", 3), ("p1", 5)]:
        await database.save_result(
            db_path,
            job_id=job_id,
            playthrough_id=playthrough_id,
            turn=turn,
            prompt="prompt",
            input="input",
            output="output",
            judge_result={"target": "quality", "judge": {"overall_score": 0.8}},
        )

    results = await database.list_results(
        db_path,
        job_id=job_id,
        result_type="llm_judged",
        page_size=10,
    )

    assert [(item["playthrough_id"], item["turn"]) for item in results["items"]] == [
        ("p1", 2),
        ("p1", 5),
        ("p2", 3),
    ]


@pytest.mark.asyncio
async def test_llm_judge_file_summary_uses_overall_score_distribution_by_target(tmp_path: Path):
    db_path = tmp_path / "test.db"
    await database.init_db(db_path)
    job_id = "llm-judge-score-distribution"
    await database.create_job(
        db_path,
        job_id,
        5,
        kind="llm_judge_upload",
        source_filename="batch",
    )

    for target, score in [
        ("narrative", 1),
        ("narrative", 1),
        ("narrative", 2),
        ("choice", 2),
        ("choice", 3),
    ]:
        await database.save_result(
            db_path,
            job_id=job_id,
            playthrough_id=f"{target}-{score}",
            turn=1,
            prompt="prompt",
            input="input",
            output="output",
            judge_result={"target": target, "judge": {"overall_score": score}},
        )

    files = await database.list_llm_judge_files(db_path, source_filename="batch")

    summary = files[0]["score_breakdown_summary"]
    assert summary["title"] == "最终评分分布"
    assert summary["score_distribution"] == [
        {
            "target": "narrative",
            "total": 3,
            "buckets": [{"score": "1", "count": 2}, {"score": "2", "count": 1}],
        },
        {
            "target": "choice",
            "total": 2,
            "buckets": [{"score": "2", "count": 1}, {"score": "3", "count": 1}],
        },
    ]


@pytest.mark.asyncio
async def test_process_llm_judge_job_saves_target_input_and_output(tmp_path: Path):
    db_path = tmp_path / "test.db"
    await database.init_db(db_path)
    await database.create_job(db_path, "target-io-job", 2, kind="llm_judge_upload")
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="P={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc","turn":1,'
        '"narrative":{"input":"正文输入","output":"正文输出"},'
        '"choice":{"input":"选项输入","output":"选项输出"}}\n'
    ).encode()
    items = parse_llm_judge_jsonl(content, prompt)

    async def fake_llm(config: LLMConfig, prompt: str) -> str:
        return '{"overall_score": 0.8, "overall_evidence": "ok"}'

    await process_llm_judge_job(
        db_path,
        "target-io-job",
        items,
        LLMConfig(base_url="https://judge.test/v1", model="judge-model"),
        llm_caller=fake_llm,
    )

    results = await database.list_results(
        db_path,
        job_id="target-io-job",
        result_type="llm_judged",
        page_size=10,
    )
    by_target = {item["judge_result"]["target"]: item for item in results["items"]}

    assert by_target["narrative"]["input"] == "正文输入"
    assert by_target["narrative"]["output"] == "正文输出"
    assert by_target["choice"]["input"] == "选项输入"
    assert by_target["choice"]["output"] == "选项输出"


@pytest.mark.asyncio
async def test_recover_interrupted_jobs_marks_orphans_and_restores_retry_totals(tmp_path: Path):
    db_path = tmp_path / "test.db"
    await database.init_db(db_path)

    await database.create_job(db_path, "normal-running", 10, kind="generate")
    await database.mark_job_running(db_path, "normal-running")
    await database.record_job_progress(
        db_path,
        "normal-running",
        processed_rows=4,
        success_count=4,
        failure_count=0,
        errors=[],
    )

    await database.create_job(db_path, "retry-running", 100, kind="generate")
    await database.record_job_progress(
        db_path,
        "retry-running",
        processed_rows=100,
        success_count=95,
        failure_count=5,
        errors=[],
    )
    await database.finish_job(db_path, "retry-running", "completed_with_errors", [])
    await database.prepare_job_retry(db_path, "retry-running", 5)
    await database.mark_job_running(db_path, "retry-running")
    await database.record_job_progress(
        db_path,
        "retry-running",
        processed_rows=2,
        success_count=97,
        failure_count=3,
        errors=[],
    )

    recovered = await database.recover_interrupted_jobs(db_path)
    normal_job = await database.get_job(db_path, "normal-running")
    retry_job = await database.get_job(db_path, "retry-running")

    assert recovered == 2
    assert normal_job["status"] == "failed"
    assert normal_job["total_rows"] == 10
    assert normal_job["processed_rows"] == 4
    assert "中断" in normal_job["errors"][0]["message"]
    assert retry_job["status"] == "completed_with_errors"
    assert retry_job["total_rows"] == 100
    assert retry_job["processed_rows"] == 100
    assert retry_job["success_count"] == 97
    assert retry_job["failure_count"] == 3


def test_parse_llm_judge_output_direct_mode_uses_custom_paths():
    cfg = ScoreConfig(
        mode="direct",
        score_path="result.score",
        evidence_path="result.reason",
    )
    parsed = parse_llm_judge_output(
        '{"result": {"score": 0.42, "reason": "ok"}}',
        cfg,
    )
    assert parsed["parse_status"] == "valid"
    assert parsed["overall_score"] == 0.42
    assert parsed["overall_evidence"] == "ok"


def test_parse_llm_judge_output_weighted_mode_normalises_weights():
    cfg = ScoreConfig(
        mode="weighted",
        evidence_path="overall_evidence",
        components=[
            ScoreComponent(path="scores.coherence", weight=2),
            ScoreComponent(path="scores.accuracy", weight=2),
        ],
    )
    parsed = parse_llm_judge_output(
        '{"scores": {"coherence": 0.6, "accuracy": 0.8},'
        ' "overall_evidence": "理由"}',
        cfg,
    )
    assert parsed["parse_status"] == "valid"
    assert parsed["overall_score"] == pytest.approx(0.7)
    assert parsed["overall_evidence"] == "理由"
    assert parsed["score_breakdown"]["mode"] == "weighted"
    assert parsed["score_breakdown"]["components"] == [
        {
            "path": "scores.coherence",
            "weight": 2.0,
            "normalized_weight": 0.5,
            "score": 0.6,
            "contribution": 0.3,
        },
        {
            "path": "scores.accuracy",
            "weight": 2.0,
            "normalized_weight": 0.5,
            "score": 0.8,
            "contribution": 0.4,
        },
    ]


def test_parse_llm_judge_output_weighted_missing_component_marks_other():
    cfg = ScoreConfig(
        mode="weighted",
        evidence_path="overall_evidence",
        components=[
            ScoreComponent(path="scores.coherence", weight=1),
            ScoreComponent(path="scores.accuracy", weight=1),
        ],
    )
    parsed = parse_llm_judge_output(
        '{"scores": {"coherence": 0.6}, "overall_evidence": "理由"}',
        cfg,
    )
    assert parsed["parse_status"] == "other"
    assert parsed["overall_score"] is None
    assert parsed["overall_evidence"] == "理由"


def test_parse_llm_judge_output_weighted_uneven_weights():
    cfg = ScoreConfig(
        mode="weighted",
        evidence_path="overall_evidence",
        components=[
            ScoreComponent(path="a", weight=3),
            ScoreComponent(path="b", weight=1),
        ],
    )
    parsed = parse_llm_judge_output(
        '{"a": 1.0, "b": 0.0, "overall_evidence": "x"}',
        cfg,
    )
    assert parsed["overall_score"] == pytest.approx(0.75)


def test_parse_llm_judge_output_no_config_keeps_legacy_behavior():
    parsed = parse_llm_judge_output(
        '{"overall_score": 0.5, "overall_evidence": "原因"}'
    )
    assert parsed["parse_status"] == "valid"
    assert parsed["overall_score"] == 0.5
    assert parsed["overall_evidence"] == "原因"
    assert "score_breakdown" not in parsed


def test_parse_llm_judge_output_direct_mode_rejects_non_numeric():
    cfg = ScoreConfig(
        mode="direct",
        score_path="overall_score",
        evidence_path="overall_evidence",
    )
    parsed = parse_llm_judge_output(
        '{"overall_score": "high", "overall_evidence": "原因"}',
        cfg,
    )
    assert parsed["parse_status"] == "other"
    assert parsed["overall_score"] is None


@pytest.mark.asyncio
async def test_process_llm_judge_job_uses_item_score_config(tmp_path: Path):
    db_path = tmp_path / "weighted.db"
    await database.init_db(db_path)
    job_id = "weighted-job"
    await database.create_job(db_path, job_id, 1, kind="llm_judge_upload")
    score_config = ScoreConfig(
        mode="weighted",
        evidence_path="overall_evidence",
        components=[
            ScoreComponent(path="scores.coherence", weight=1),
            ScoreComponent(path="scores.accuracy", weight=1),
        ],
    )
    items = [
        LlmJudgeJsonlItem(
            line_number=1,
            playthrough_id="p1",
            turn=1,
            prompt="weighted",
            raw_payload={"playthrough_id": "p1", "turn": 1},
            judge_target="quality",
            score_config=score_config,
        ),
    ]

    async def fake_llm(config: LLMConfig, prompt: str) -> str:
        return (
            '{"scores": {"coherence": 0.4, "accuracy": 0.8},'
            ' "overall_evidence": "组合"}'
        )

    await process_llm_judge_job(
        db_path,
        job_id,
        items,
        LLMConfig(base_url="https://judge.test/v1", model="judge-model"),
        llm_caller=fake_llm,
    )

    results = await database.list_results(
        db_path, result_type="llm_judged", page_size=10
    )
    assert results["total"] == 1
    judge = results["items"][0]["judge_result"]["judge"]
    assert judge["parse_status"] == "valid"
    assert judge["overall_score"] == pytest.approx(0.6)
    assert judge["overall_evidence"] == "组合"


@pytest.mark.asyncio
async def test_judge_prompt_score_config_round_trip(tmp_path: Path):
    db_path = tmp_path / "score-config.db"
    await database.init_db(db_path)
    from app.schemas import JudgePromptVersionCreate

    payload = JudgePromptVersionCreate(
        name="加权裁判",
        template="评估\n输入：{{prompt}}\n输出：{{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        score_config=ScoreConfig(
            mode="weighted",
            evidence_path="overall_evidence",
            components=[
                ScoreComponent(path="scores.coherence", weight=0.4),
                ScoreComponent(path="scores.accuracy", weight=0.6),
            ],
        ),
    )
    created = await database.create_judge_prompt_version(db_path, payload)
    fetched = await database.get_judge_prompt_version(db_path, created.target, created.id)
    assert fetched is not None
    assert fetched.score_config is not None
    assert fetched.score_config.mode == "weighted"
    assert [c.path for c in fetched.score_config.components] == [
        "scores.coherence",
        "scores.accuracy",
    ]


@pytest.mark.asyncio
async def test_judge_prompt_score_config_rejects_invalid_path(tmp_path: Path):
    db_path = tmp_path / "score-config-invalid.db"
    await database.init_db(db_path)
    from app.schemas import JudgePromptVersionCreate

    payload = JudgePromptVersionCreate(
        name="非法路径",
        template="评估{{prompt}}{{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        score_config=ScoreConfig(
            mode="direct",
            score_path="scores[0].value",
            evidence_path="overall_evidence",
        ),
    )
    with pytest.raises(ValueError):
        await database.create_judge_prompt_version(db_path, payload)


@pytest.mark.asyncio
async def test_judge_prompt_score_config_rejects_weighted_without_components(
    tmp_path: Path,
):
    db_path = tmp_path / "score-config-empty.db"
    await database.init_db(db_path)
    from app.schemas import JudgePromptVersionCreate

    payload = JudgePromptVersionCreate(
        name="空 weighted",
        template="评估{{prompt}}{{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        score_config=ScoreConfig(
            mode="weighted",
            evidence_path="overall_evidence",
            components=[],
        ),
    )
    with pytest.raises(ValueError):
        await database.create_judge_prompt_version(db_path, payload)
