import json
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient

from app import database
import app.main as main
from app.schemas import LLMConfig


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "api.db")
    with TestClient(main.app) as test_client:
        yield test_client


def test_config_api(client: TestClient):
    payload = {
        "name": "生成版本 A",
        "config": {
            "base_url": "https://gen.test/v1",
            "api_key": "gen-key",
            "model": "gen-model",
            "timeout": 30,
        }
    }

    response = client.post("/api/config/generate", json=payload)

    assert response.status_code == 200
    assert response.json()["name"] == "生成版本 A"
    assert response.json()["is_active"] is True
    assert response.json()["config"]["api_key"] == "********"

    judge_payload = {
        "name": "裁判版本 A",
        "config": {
            "base_url": "https://judge.test/v1",
            "api_key": "judge-key",
            "model": "judge-model",
            "timeout": 45,
        }
    }
    assert client.post("/api/config/judge", json=judge_payload).status_code == 200

    state = client.get("/api/config").json()

    assert state["runtime_settings"]["judge_concurrency"] == 50
    assert state["generate_versions"][0]["config"]["model"] == "gen-model"
    assert state["generate_versions"][0]["config"]["api_key"] == "********"
    assert state["generate_versions"][0]["config"]["temperature"] is None
    assert state["judge_versions"][0]["config"]["model"] == "judge-model"
    assert state["judge_versions"][0]["config"]["api_key"] == "jud****-key"
    assert state["judge_versions"][0]["config"]["temperature"] is None
    assert state["prompt_combine_templates"]["system_template"] == "{{system}}\\n"
    assert state["judge_prompt_versions"][0]["fields"][0]["placeholder"] == "prompt"

    runtime_response = client.put("/api/config/runtime", json={"judge_concurrency": 8})

    assert runtime_response.status_code == 200
    assert runtime_response.json()["judge_concurrency"] == 8
    assert client.get("/api/config").json()["runtime_settings"]["judge_concurrency"] == 8

    prompt_combine_payload = {
        "system_template": "S={{system}}\\n",
        "user_template": "U={{user}}\\n",
        "assistant_template": "A={{assistant}}\\n",
        "narrative_output_template": "{{text}}",
        "choice_option_template": "{{index}}. {{text}}（{{reason}}）",
    }
    prompt_combine_response = client.put(
        "/api/config/prompt-combine",
        json=prompt_combine_payload,
    )

    assert prompt_combine_response.status_code == 200
    assert prompt_combine_response.json()["user_template"] == "U={{user}}\\n"
    assert client.get("/api/config").json()["prompt_combine_templates"] == prompt_combine_payload

    update_response = client.put(
        f"/api/config/generate/{response.json()['id']}",
        json={
            "name": "生成版本 A",
            "config": {
                "base_url": "https://gen2.test/v1",
                "api_key": "",
                "model": "gen-model-2",
                "timeout": 31,
            },
        },
    )
    assert update_response.status_code == 200

    async def load_real_version():
        return await database.get_llm_config_version(
            main.DATABASE_PATH,
            database.GENERATE_KIND,
            response.json()["id"],
        )

    real_version = anyio.run(load_real_version)
    assert real_version.config.api_key == "gen-key"

    clone_response = client.post(
        "/api/config/generate",
        json={
            "name": "生成版本 A clone",
            "inherit_api_key_from_id": response.json()["id"],
            "config": {
                "base_url": "https://gen-clone.test/v1",
                "api_key": "",
                "model": "gen-model-clone",
                "timeout": 32,
            },
        },
    )
    assert clone_response.status_code == 200

    async def load_clone_version():
        return await database.get_llm_config_version(
            main.DATABASE_PATH,
            database.GENERATE_KIND,
            clone_response.json()["id"],
        )

    clone_version = anyio.run(load_clone_version)
    assert clone_version.config.api_key == "gen-key"

    judge_prompt_response = client.post(
        "/api/config/judge-prompts",
        json={
            "name": "Judge Prompt A",
            "template": "A={{a}} B={{b}}",
            "fields": [
                {"placeholder": "a", "source_field": "prompt"},
                {"placeholder": "b", "source_field": "output"},
            ],
        },
    )
    assert judge_prompt_response.status_code == 200
    assert judge_prompt_response.json()["is_active"] is True
    assert client.post(
        f"/api/config/judge-prompts/{judge_prompt_response.json()['id']}/activate"
    ).status_code == 200


def test_index_redirects_to_llm_judge(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert "Judge llm response" in response.text
    assert "/api/llm-judge/upload" in response.text


def test_generate_page(client: TestClient):
    response = client.get("/generate")

    assert response.status_code == 200
    assert "Generate" in response.text
    assert "/api/generate/upload" in response.text


def test_llm_judge_page(client: TestClient):
    response = client.get("/llm-judge")

    assert response.status_code == 200
    assert "Judge llm response" in response.text
    assert "/api/llm-judge/upload" in response.text
    assert "选择 Generate 结果" not in response.text


def test_llm_judge_batch_test_page(client: TestClient):
    response = client.get("/llm-judge/batch-test")

    assert response.status_code == 200
    assert "Batch API 测试" in response.text
    assert "judge_version_id" in response.text
    assert "prompt_combine_version_id" in response.text
    assert "/static/llm_judge_batch_test.js" in response.text


def test_judge_prompt_config_is_split_by_target(client: TestClient):
    narrative_response = client.post(
        "/api/config/judge-prompts/narrative",
        json={
            "name": "正文专用 Prompt",
            "template": "N={{prompt}} {{output}}",
            "fields": [
                {"placeholder": "prompt", "source_field": "prompt"},
                {"placeholder": "output", "source_field": "output"},
            ],
        },
    )
    choice_response = client.post(
        "/api/config/judge-prompts/choice",
        json={
            "name": "选项专用 Prompt",
            "template": "C={{prompt}} {{output}}",
            "fields": [
                {"placeholder": "prompt", "source_field": "prompt"},
                {"placeholder": "output", "source_field": "output"},
            ],
        },
    )

    assert narrative_response.status_code == 200
    assert choice_response.status_code == 200

    state = client.get("/api/config").json()
    narrative_names = {item["name"] for item in state["narrative_judge_prompt_versions"]}
    choice_names = {item["name"] for item in state["choice_judge_prompt_versions"]}
    assert "正文专用 Prompt" in narrative_names
    assert "正文专用 Prompt" not in choice_names
    assert "选项专用 Prompt" in choice_names
    assert "选项专用 Prompt" not in narrative_names

    wrong_target = client.post(
        f"/api/config/judge-prompts/{narrative_response.json()['id']}/activate/choice"
    )
    assert wrong_target.status_code == 404


def test_config_page(client: TestClient):
    response = client.get("/config")

    assert response.status_code == 200
    assert "版本管理" in response.text
    assert "裁判 LLM API" in response.text
    assert "强制 JSON-only 解码模式" in response.text
    assert "正文 Judge Prompt" in response.text
    assert "选项 Judge Prompt" in response.text
    assert "Generate API" not in response.text
    assert "历史轮次拼接模版" not in response.text
    assert "/static/config.js" in response.text
    assert "static_version" not in response.text


def test_judge_api_config_saves_force_json_only(client: TestClient):
    response = client.post(
        "/api/config/judge",
        json={
            "name": "JSON-only 裁判",
            "config": {
                "base_url": "https://judge.test/v1",
                "api_key": "judge-key",
                "model": "judge-model",
                "timeout": 45,
                "force_json_only": True,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["config"]["force_json_only"] is True

    state = client.get("/api/config").json()
    version = next(item for item in state["judge_versions"] if item["name"] == "JSON-only 裁判")
    assert version["config"]["force_json_only"] is True


def test_llm_judge_prompt_select_config_is_split(client: TestClient):
    narrative_response = client.post(
        "/api/config/judge-prompts/narrative",
        json={
            "name": "任务页正文 Prompt",
            "template": "N={{prompt}} {{output}}",
            "fields": [
                {"placeholder": "prompt", "source_field": "prompt"},
                {"placeholder": "output", "source_field": "output"},
            ],
        },
    )
    choice_response = client.post(
        "/api/config/judge-prompts/choice",
        json={
            "name": "任务页选项 Prompt",
            "template": "C={{prompt}} {{output}}",
            "fields": [
                {"placeholder": "prompt", "source_field": "prompt"},
                {"placeholder": "output", "source_field": "output"},
            ],
        },
    )

    assert narrative_response.status_code == 200
    assert choice_response.status_code == 200

    state = client.get("/api/config").json()
    assert all(item["target"] == "narrative" for item in state["narrative_judge_prompt_versions"])
    assert all(item["target"] == "choice" for item in state["choice_judge_prompt_versions"])
    assert state["active_narrative_judge_prompt_id"] == narrative_response.json()["id"]
    assert state["active_choice_judge_prompt_id"] == choice_response.json()["id"]


def test_llm_judge_default_prompts_are_resolved_by_target(client: TestClient):
    async def resolve_prompts():
        return await main._resolve_llm_judge_prompt_versions(None, None)

    narrative_prompt, choice_prompt = anyio.run(resolve_prompts)

    assert narrative_prompt.target == "narrative"
    assert choice_prompt.target == "choice"
    assert narrative_prompt.name != choice_prompt.name


def test_llm_judge_rejects_unknown_target_prompt_id(client: TestClient):
    response = client.post(
        "/api/llm-judge/batch",
        params={
            "narrative_judge_prompt_version_id": 99999,
            "choice_judge_prompt_version_id": 99999,
        },
        json=[
            {
                "playthrough_id": "unknown-prompt",
                "turn": 1,
                "choice": {"input": "prompt", "output": "choice"},
            }
        ],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Judge Prompt 版本不存在"


def test_safe_source_filename_strips_path_and_header_control_characters():
    assert main._safe_source_filename("../outside.jsonl") == "outside.jsonl"
    assert main._safe_source_filename(r"..\outside.jsonl") == "outside.jsonl"
    assert main._excel_export_disposition('..\r\nX-Test: injected.jsonl', "job") == (
        "attachment; filename=\"X-Test__injected_judge.xlsx\"; "
        "filename*=UTF-8''X-Test%3A%20injected_judge.xlsx"
    )


def test_running_job_cannot_resume_delete_or_retry(client: TestClient):
    async def seed_running_job():
        await database.create_job(
            main.DATABASE_PATH,
            "running-job",
            1,
            kind="generate",
            source_filename="input.jsonl",
        )
        await database.mark_job_running(main.DATABASE_PATH, "running-job")
        await database.record_job_progress(
            main.DATABASE_PATH,
            "running-job",
            processed_rows=0,
            success_count=0,
            failure_count=1,
            errors=[],
        )

    anyio.run(seed_running_job)

    cancel_response = client.post("/api/jobs/running-job/cancel")
    assert cancel_response.status_code == 200
    assert client.get("/api/jobs/running-job").json()["status"] == "running"
    assert client.post("/api/jobs/running-job/resume").status_code == 400
    assert client.delete("/api/jobs/running-job").status_code == 400


def test_results_api_strips_filters_and_preserves_json_string_fields(client: TestClient):
    prompt = 'P={"items":[{"x":"y"}]}\n<p data-speaker="A">"hi"</p>'
    input_text = 'line1\nline2 "quoted" \\ slash { } [ ]'
    output = '<assistant>\n{"options":[{"text":"继续","reason":"推进"}]}\n</assistant>'

    async def seed_result():
        await database.create_job(
            main.DATABASE_PATH,
            "json-string-job",
            1,
            kind="llm_judge_upload",
            source_filename="batch",
        )
        await database.save_result(
            main.DATABASE_PATH,
            job_id="json-string-job",
            playthrough_id="pt",
            turn=1,
            prompt=prompt,
            input=input_text,
            output=output,
            judge_result={"target": "narrative", "judge": {"overall_score": 0.8}},
        )

    anyio.run(seed_result)

    response = client.get(
        "/api/results",
        params={
            "job_id": " json-string-job ",
            "result_type": "llm_judged",
            "page_size": 0,
        },
    )

    assert response.status_code == 200
    parsed = json.loads(response.text)
    assert parsed["total"] == 1
    item = parsed["items"][0]
    assert item["prompt"] == prompt
    assert item["input"] == input_text
    assert item["output"] == output


def test_results_api_filters_by_target_score_and_sorts_score(client: TestClient):
    async def seed_results():
        await database.create_job(
            main.DATABASE_PATH,
            "score-filter-job",
            4,
            kind="llm_judge_upload",
            source_filename="batch",
        )
        for index, (target, score) in enumerate(
            [("narrative", 2), ("choice", 1), ("narrative", 1), ("choice", 3)],
            start=1,
        ):
            await database.save_result(
                main.DATABASE_PATH,
                job_id="score-filter-job",
                playthrough_id=f"pt-{index}",
                turn=index,
                prompt="prompt",
                input="input",
                output="output",
                judge_result={"target": target, "judge": {"overall_score": score}},
            )

    anyio.run(seed_results)

    target_response = client.get(
        "/api/results",
        params={
            "job_id": "score-filter-job",
            "result_type": "llm_judged",
            "judge_target": "narrative",
            "score_sort": "asc",
            "page_size": 0,
        },
    )
    assert target_response.status_code == 200
    target_items = target_response.json()["items"]
    assert [item["judge_result"]["target"] for item in target_items] == ["narrative", "narrative"]
    assert [item["judge_result"]["judge"]["overall_score"] for item in target_items] == [1, 2]

    score_response = client.get(
        "/api/results",
        params={
            "job_id": "score-filter-job",
            "result_type": "llm_judged",
            "score": 1,
            "score_sort": "desc",
            "page_size": 0,
        },
    )
    assert score_response.status_code == 200
    score_items = score_response.json()["items"]
    assert score_response.json()["total"] == 2
    assert [item["judge_result"]["target"] for item in score_items] == ["choice", "narrative"]


def test_upload_llm_judge_job_and_query_results(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    prompt_response = client.post(
        "/api/config/judge-prompts",
        json={
            "name": "上传 Prompt",
            "template": "P={{p}}\nO={{o}}",
            "fields": [
                {"placeholder": "p", "source_field": "prompt"},
                {"placeholder": "o", "source_field": "output"},
            ],
        },
    )
    assert prompt_response.status_code == 200
    prompt_version_id = prompt_response.json()["id"]
    config_response = client.post(
        "/api/config/judge",
        json={
            "name": "LLM Judge API",
            "config": {
                "base_url": "https://judge.test/v1",
                "api_key": "judge-key",
                "model": "judge-model",
                "timeout": 45,
            },
        },
    )
    judge_version_id = config_response.json()["id"]

    async def fake_process_llm_judge_job(
        db_path: Path,
        job_id: str,
        items,
        judge_config,
        job_concurrency=50,
        **kwargs,
    ):
        assert judge_config.model == "judge-model"
        assert items[0].prompt == "P=hello\nO=world"
        await database.mark_job_running(db_path, job_id)
        await database.save_result(
            db_path,
            job_id=job_id,
            playthrough_id=items[0].playthrough_id,
            turn=items[0].turn,
            prompt=items[0].prompt,
            input=items[0].raw_payload["input"],
            output=items[0].raw_payload["output"],
            judge_result={
                "target": "LLM-as-Judge",
                "judge": {
                    "parse_status": "valid",
                    "valid_json": True,
                    "overall_score": 0.9,
                    "overall_evidence": "好",
                },
            },
        )
        await database.record_job_progress(
            db_path,
            job_id,
            processed_rows=1,
            success_count=1,
            failure_count=0,
            errors=[],
        )
        await database.finish_job(
            db_path,
            job_id,
            "completed",
            [],
            judge_bug_count=1,
            judge_exists_count=1,
            judge_other_count=0,
        )

    monkeypatch.setattr(main, "process_llm_judge_job", fake_process_llm_judge_job)

    content = (
        '{"playthrough_id":"abc123","turn":5,"prompt":"hello","output":"world"}\n'
    ).encode()
    response = client.post(
        "/api/llm-judge/upload",
        data={
            "judge_version_id": str(judge_version_id),
            "judge_prompt_version_id": str(prompt_version_id),
        },
        files={"file": ("llm-judge.jsonl", content, "application/jsonl")},
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"
    llm_files = client.get("/api/llm-judge/files").json()
    assert len(llm_files) == 1
    assert llm_files[0]["valid_json_count"] == 1

    results = client.get(
        "/api/results",
        params={"playthrough_id": "abc123", "turn": 5, "result_type": "llm_judged"},
    ).json()

    assert results["total"] == 1
    assert results["items"][0]["judge_result"]["judge"]["overall_score"] == 0.9
    assert "judge_prompt" not in results["items"][0]["judge_result"]


def test_upload_llm_judge_job_expands_narrative_and_choice(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    prompt_response = client.post(
        "/api/config/judge-prompts",
        json={
            "name": "上传 Nested Prompt",
            "template": "P={{prompt}}\nO={{output}}",
            "fields": [
                {"placeholder": "prompt", "source_field": "prompt"},
                {"placeholder": "output", "source_field": "output"},
            ],
        },
    )
    assert prompt_response.status_code == 200
    prompt_version_id = prompt_response.json()["id"]

    async def fake_process_llm_judge_job(
        db_path: Path,
        job_id: str,
        items,
        judge_config,
        job_concurrency=50,
        **kwargs,
    ):
        assert len(items) == 2
        assert [item.judge_target for item in items] == ["narrative", "choice"]
        assert items[0].prompt == "P=正文 prompt\nO=当前剧情"
        assert items[1].prompt == "P=选项 prompt\nO=当前选项"
        await database.mark_job_running(db_path, job_id)
        for item in items:
            await database.save_result(
                db_path,
                job_id=job_id,
                playthrough_id=item.playthrough_id,
                turn=item.turn,
                prompt=item.prompt,
                input=item.raw_payload["input"],
                output=item.raw_payload["output"],
                judge_result={
                    "target": item.judge_target,
                    "judge": {
                        "parse_status": "valid",
                        "valid_json": True,
                        "overall_score": 0.7,
                        "overall_evidence": "可用",
                    },
                },
            )
        await database.record_job_progress(
            db_path,
            job_id,
            processed_rows=2,
            success_count=2,
            failure_count=0,
            errors=[],
        )
        await database.finish_job(
            db_path,
            job_id,
            "completed",
            [],
            judge_bug_count=2,
            judge_exists_count=2,
            judge_other_count=0,
        )

    monkeypatch.setattr(main, "process_llm_judge_job", fake_process_llm_judge_job)

    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"input":"正文 prompt","output":"当前剧情"},'
        '"choice":{"input":"选项 prompt","output":"当前选项"}}\n'
    ).encode()
    response = client.post(
        "/api/llm-judge/upload",
        data={"judge_prompt_version_id": str(prompt_version_id)},
        files={"file": ("llm-judge-nested.jsonl", content, "application/jsonl")},
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert response.json()["total_rows"] == 2
    results = client.get("/api/results", params={"job_id": job_id, "result_type": "llm_judged"}).json()
    assert results["total"] == 2
    assert {item["judge_result"]["target"] for item in results["items"]} == {"narrative", "choice"}


def test_batch_llm_judge_job_accepts_json_array(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    prompt_response = client.post(
        "/api/config/judge-prompts",
        json={
            "name": "Batch Prompt",
            "template": "P={{prompt}}\nO={{output}}",
            "fields": [
                {"placeholder": "prompt", "source_field": "prompt"},
                {"placeholder": "output", "source_field": "output"},
            ],
        },
    )
    assert prompt_response.status_code == 200
    prompt_version_id = prompt_response.json()["id"]

    captured: dict[str, object] = {}

    async def fake_process_llm_judge_job(db_path: Path, job_id: str, items, judge_config, job_concurrency=50):
        captured["items"] = items
        await database.mark_job_running(db_path, job_id)
        await database.record_job_progress(
            db_path,
            job_id,
            processed_rows=len(items),
            success_count=len(items),
            failure_count=0,
            errors=[],
        )
        await database.finish_job(
            db_path,
            job_id,
            "completed",
            [],
            judge_bug_count=len(items),
            judge_exists_count=len(items),
            judge_other_count=0,
        )

    monkeypatch.setattr(main, "process_llm_judge_job", fake_process_llm_judge_job)

    payload = [
        {
            "playthrough_id": "abc123",
            "turn": 5,
            "narrative": {"input": "正文输入", "output": "正文输出"},
            "choice": {
                "input": "选项输入",
                "output": {"options": [{"text": "继续", "reason": "推进剧情"}]},
            },
        },
        {
            "playthrough_id": "abc123",
            "turn": 5,
            "narrative": {"input": "正文输入", "output": "正文输出"},
            "choice": {
                "input": "选项输入",
                "output": {"options": [{"text": "继续", "reason": "推进剧情"}]},
            },
        },
    ]
    response = client.post(
        "/api/llm-judge/batch",
        params={"judge_prompt_version_id": str(prompt_version_id)},
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["total_rows"] == 4
    job_id = response.json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"
    assert [item.judge_target for item in captured["items"]] == [
        "narrative",
        "choice",
        "narrative",
        "choice",
    ]
    assert not (main.UPLOADS_DIR / job_id / "batch.jsonl").exists()
    llm_files = client.get("/api/llm-judge/files").json()
    assert llm_files[0]["filename"] == "batch"


def test_batch_llm_judge_job_uses_configured_default_prompt_ids(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    narrative_response = client.post(
        "/api/config/judge-prompts/narrative",
        json={
            "name": "Batch Env Narrative Prompt",
            "template": "N={{prompt}}\nO={{output}}",
            "fields": [
                {"placeholder": "prompt", "source_field": "prompt"},
                {"placeholder": "output", "source_field": "output"},
            ],
        },
    )
    choice_response = client.post(
        "/api/config/judge-prompts/choice",
        json={
            "name": "Batch Env Choice Prompt",
            "template": "C={{prompt}}\nO={{output}}",
            "fields": [
                {"placeholder": "prompt", "source_field": "prompt"},
                {"placeholder": "output", "source_field": "output"},
            ],
        },
    )
    assert narrative_response.status_code == 200
    assert choice_response.status_code == 200

    async def fake_process_llm_judge_job(db_path: Path, job_id: str, items, judge_config, job_concurrency=50):
        assert items[0].prompt == "N=正文输入\nO=正文输出"
        assert items[1].prompt == "C=选项输入\nO=1. 继续（推进剧情）"
        await database.mark_job_running(db_path, job_id)
        await database.finish_job(db_path, job_id, "completed", [])

    monkeypatch.setattr(main, "process_llm_judge_job", fake_process_llm_judge_job)
    monkeypatch.setattr(
        main,
        "DEFAULT_BATCH_NARRATIVE_JUDGE_PROMPT_VERSION_ID",
        narrative_response.json()["id"],
    )
    monkeypatch.setattr(
        main,
        "DEFAULT_BATCH_CHOICE_JUDGE_PROMPT_VERSION_ID",
        choice_response.json()["id"],
    )

    response = client.post(
        "/api/llm-judge/batch",
        json=[
            {
                "playthrough_id": "abc123",
                "turn": 5,
                "narrative": {"input": "正文输入", "output": "正文输出"},
                "choice": {
                    "input": "选项输入",
                    "output": {"options": [{"text": "继续", "reason": "推进剧情"}]},
                },
            }
        ],
    )

    assert response.status_code == 200


def test_upload_llm_judge_job_uses_dialog_prompt_template(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    prompt_response = client.post(
        "/api/config/judge-prompts/narrative",
        json={
            "name": "上传对话 Prompt",
            "template": "P={{prompt}}\nO={{output}}",
            "fields": [
                {"placeholder": "prompt", "source_field": "prompt"},
                {"placeholder": "output", "source_field": "output"},
            ],
        },
    )
    template_response = client.post(
        "/api/config/prompt-combine",
        json={
            "name": "上传对话模版",
            "system_template": "S={{system}}\n",
            "user_template": "U={{user}}\n",
            "assistant_template": "A={{assistant}}\n",
            "narrative_output_template": "<story>{{text}}</story>",
            "choice_option_template": "{{index}}. {{text}} - {{reason}}\n",
        },
    )
    assert prompt_response.status_code == 200
    assert template_response.status_code == 200

    async def fake_process_llm_judge_job(
        db_path: Path,
        job_id: str,
        items,
        judge_config,
        job_concurrency=50,
        **kwargs,
    ):
        assert len(items) == 1
        assert items[0].prompt == "P=S=系统\nU=你好\nA=剧情\n\nO=<story>当前剧情</story>"
        await database.mark_job_running(db_path, job_id)
        await database.finish_job(db_path, job_id, "completed", [])

    monkeypatch.setattr(main, "process_llm_judge_job", fake_process_llm_judge_job)

    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"input":{"system":"系统","messages":['
        '{"role":"user","content":"你好"},'
        '{"role":"assistant","content":"剧情"}'
        ']},"output":"当前剧情"}}\n'
    ).encode()
    response = client.post(
        "/api/llm-judge/upload",
        data={
            "narrative_judge_prompt_version_id": str(prompt_response.json()["id"]),
            "prompt_combine_version_id": str(template_response.json()["id"]),
        },
        files={"file": ("llm-judge-dialog.jsonl", content, "application/jsonl")},
    )

    assert response.status_code == 200


def test_llm_judge_from_generate_job_converts_and_persists_input_output(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    client.put(
        "/api/config/prompt-combine",
        json={
            "system_template": "S:{{system}}\\n",
            "user_template": "U:{{user}}\\n",
            "assistant_template": "A:{{assistant}}\\n",
            "narrative_output_template": "{{text}}",
            "choice_option_template": "{{index}}:{{text}}/{{reason}}",
        },
    )
    prompt_response = client.post(
        "/api/config/judge-prompts",
        json={
            "name": "Generate 转 LLM Judge Prompt",
            "template": "P={{prompt}}\nO={{output}}",
            "fields": [
                {"placeholder": "prompt", "source_field": "prompt"},
                {"placeholder": "output", "source_field": "output"},
            ],
        },
    )
    assert prompt_response.status_code == 200
    prompt_version_id = prompt_response.json()["id"]

    async def fake_process_llm_judge_job(db_path: Path, job_id: str, items, judge_config, job_concurrency=50):
        assert len(items) == 2
        assert [item.judge_target for item in items] == ["narrative", "choice"]
        assert items[0].raw_payload["narrative"] == {
            "input": "S:系统\nU:玩家输入\n",
            "output": "新生成回复",
        }
        assert items[1].raw_payload["choice"] == {
            "input": "S:选项系统\nU:生成选项\n",
            "output": "1:继续/",
        }
        assert items[0].prompt == "P=S:系统\nU:玩家输入\n\nO=新生成回复"
        assert items[1].prompt == "P=S:选项系统\nU:生成选项\n\nO=1:继续/"
        await database.mark_job_running(db_path, job_id)
        for item in items:
            await database.save_result(
                db_path,
                job_id=job_id,
                playthrough_id=item.playthrough_id,
                turn=item.turn,
                prompt=item.prompt,
                input=item.raw_payload["input"],
                output=item.raw_payload["output"],
                judge_result={
                    "target": item.judge_target,
                    "judge": {
                        "parse_status": "valid",
                        "valid_json": True,
                        "overall_score": 0.8,
                        "overall_evidence": "可用",
                    },
                },
            )
        await database.record_job_progress(
            db_path,
            job_id,
            processed_rows=2,
            success_count=2,
            failure_count=0,
            errors=[],
        )
        await database.finish_job(
            db_path,
            job_id,
            "completed",
            [],
            judge_bug_count=2,
            judge_exists_count=2,
            judge_other_count=0,
        )

    monkeypatch.setattr(main, "process_llm_judge_job", fake_process_llm_judge_job)

    async def seed_generate_io_record():
        await database.create_job(
            main.DATABASE_PATH,
            "generate-io-for-llm-judge",
            1,
            kind="generate",
            source_filename="generate_3.jsonl",
        )
        await database.mark_job_running(main.DATABASE_PATH, "generate-io-for-llm-judge")
        await database.save_generate_llm_io_record(
            main.DATABASE_PATH,
            job_id="generate-io-for-llm-judge",
            source_filename="generate_3.jsonl",
            playthrough_id="abc123",
            turn=5,
            llm_input={
                "narrative": {
                    "system": "系统",
                    "messages": [{"role": "user", "content": "玩家输入"}],
                },
                "choice": {
                    "system": "选项系统",
                    "messages": [{"role": "user", "content": "生成选项"}],
                },
            },
            llm_output={
                "narrative": "新生成回复",
                "choices": {"options": ["继续"]},
            },
            player_feedback="选项有问题",
        )
        await database.record_job_progress(
            main.DATABASE_PATH,
            "generate-io-for-llm-judge",
            processed_rows=1,
            success_count=1,
            failure_count=0,
            errors=[],
        )
        await database.finish_job(main.DATABASE_PATH, "generate-io-for-llm-judge", "completed", [])

    anyio.run(seed_generate_io_record)

    response = client.post(
        "/api/llm-judge/from-generate/generate-io-for-llm-judge",
        params={"judge_prompt_version_id": str(prompt_version_id)},
    )

    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"
    source_files = list((main.UPLOADS_DIR / job_id).iterdir())
    assert len(source_files) == 1
    stored_payload = json.loads(source_files[0].read_text(encoding="utf-8"))
    assert stored_payload == {
        "playthrough_id": "abc123",
        "turn": 5,
        "narrative": {
            "input": "S:系统\nU:玩家输入\n",
            "output": "新生成回复",
        },
        "choice": {
            "input": "S:选项系统\nU:生成选项\n",
            "output": '{"options":["继续"]}',
        },
    }
    llm_files = client.get("/api/llm-judge/files").json()
    assert llm_files[0]["source_job_id"] == "generate-io-for-llm-judge"
    assert llm_files[0]["prompt_combine_version_name"]


def test_resume_cancelled_llm_judge_job_keeps_prompt_combine_template(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    template_response = client.post(
        "/api/config/prompt-combine",
        json={
            "name": "html标签版",
            "system_template": "{{system}}\\n",
            "user_template": "<user>\n{{user}}\n</user>\\n",
            "assistant_template": "<assistant>\n{{assistant}}\n</assistant>\\n",
            "narrative_output_template": "{{text}}",
            "choice_option_template": "<option{{index}}>{{text}}</option>",
        },
    )
    assert template_response.status_code == 200

    captured = {}

    async def fake_process_llm_judge_job(
        db_path: Path,
        job_id: str,
        items,
        judge_config,
        job_concurrency=50,
        **kwargs,
    ):
        captured["items"] = items
        await database.finish_job(
            db_path,
            job_id,
            "completed",
            [],
            total_rows=1,
            processed_rows=1,
            success_count=1,
            failure_count=0,
            judge_bug_count=1,
            judge_exists_count=1,
        )

    monkeypatch.setattr(main, "process_llm_judge_job", fake_process_llm_judge_job)

    async def seed_cancelled_llm_judge_job():
        await database.create_job(
            main.DATABASE_PATH,
            "llm-judge-resume-template",
            1,
            kind="llm_judge_upload",
            source_filename="llm-judge.jsonl",
            narrative_judge_prompt_version_name="default",
            choice_judge_prompt_version_name="default",
            prompt_combine_version_name="html标签版",
        )
        await database.finish_job(
            main.DATABASE_PATH,
            "llm-judge-resume-template",
            "cancelled",
            [],
            processed_rows=0,
            success_count=0,
            failure_count=0,
        )

    anyio.run(seed_cancelled_llm_judge_job)
    upload_dir = main.UPLOADS_DIR / "llm-judge-resume-template"
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "llm-judge.jsonl").write_text(
        '{"playthrough_id":"same","turn":1,'
        '"choice":{"input":"选项 prompt","output":{"options":['
        '{"text":"继续","reason":"推进剧情"},'
        '{"text":"询问","reason":"获得线索"}'
        ']}}}\n',
        encoding="utf-8",
    )

    response = client.post("/api/jobs/llm-judge-resume-template/resume")

    assert response.status_code == 200
    assert len(captured["items"]) == 1
    assert "<option1>继续</option>" in captured["items"][0].prompt
    assert "<option2>询问</option>" in captured["items"][0].prompt
