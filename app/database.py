from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.schemas import (
    AppConfig,
    ConfigVersionsState,
    LLMConfig,
    LLMConfigVersion,
    LLMConfigVersionCreate,
    JudgePromptField,
    JudgePromptVersion,
    JudgePromptVersionCreate,
    ScoreComponent,
    ScoreConfig,
    PromptCombineTemplates,
    PromptCombineTemplateVersion,
    PromptCombineTemplateVersionCreate,
    RuntimeSettings,
)
from app.settings import (
    DEFAULT_CHOICE_OPTION_TEMPLATE,
    DEFAULT_JUDGE_CONCURRENCY,
    DEFAULT_LLM_JUDGE_PROMPT,
    DEFAULT_NARRATIVE_OUTPUT_TEMPLATE,
    DEFAULT_PROMPT_COMBINE_ASSISTANT_TEMPLATE,
    DEFAULT_PROMPT_COMBINE_SYSTEM_TEMPLATE,
    DEFAULT_PROMPT_COMBINE_USER_TEMPLATE,
)


JUDGE_KIND = "judge"
JUDGE_CONCURRENCY_KEY = "judge_concurrency"
PROMPT_COMBINE_TEMPLATES_KEY = "prompt_combine_templates"
NARRATIVE_JUDGE_PROMPT_ID_KEY = "narrative_judge_prompt_version_id"
CHOICE_JUDGE_PROMPT_ID_KEY = "choice_judge_prompt_version_id"
SQLITE_TIMEOUT_SECONDS = 30.0


def _connect(db_path: Path) -> aiosqlite.Connection:
    return aiosqlite.connect(db_path, timeout=SQLITE_TIMEOUT_SECONDS)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _model_json(model: Any) -> str:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json()
    return model.json(ensure_ascii=False)


async def init_db(db_path: Path) -> None:
    async with _connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 30000")
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_config_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                config TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(kind, name)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                total_rows INTEGER NOT NULL,
                processed_rows INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                errors TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                playthrough_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                input TEXT NOT NULL,
                output TEXT NOT NULL DEFAULT '',
                judge_result TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(job_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS generate_llm_io_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                playthrough_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                llm_input TEXT NOT NULL,
                llm_output TEXT NOT NULL,
                player_feedback TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(job_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_combine_template_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                system_template TEXT NOT NULL,
                user_template TEXT NOT NULL,
                assistant_template TEXT NOT NULL,
                narrative_output_template TEXT NOT NULL DEFAULT '{{text}}',
                choice_option_template TEXT NOT NULL DEFAULT '{{index}}. {{text}}（{{reason}}）',
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS judge_prompt_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target TEXT NOT NULL DEFAULT 'narrative',
                version_no INTEGER NOT NULL DEFAULT 0,
                template TEXT NOT NULL,
                fields TEXT NOT NULL,
                score_config TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(target, name),
                UNIQUE(target, version_no)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_results_playthrough_turn ON results(playthrough_id, turn)"
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_results_job_id ON results(job_id)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_generate_llm_io_job_id ON generate_llm_io_records(job_id)"
        )
        await db.commit()
    await _ensure_runtime_columns(db_path)
    await ensure_default_versions(db_path)


async def recover_interrupted_jobs(db_path: Path) -> int:
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM jobs WHERE status IN ('pending', 'running')"
        )
        if not rows:
            return 0

        now = utc_now()
        recovered = 0
        for row in rows:
            job = _job_from_row(row)
            total_rows = int(job.get("total_rows") or 0)
            processed_rows = int(job.get("processed_rows") or 0)
            success_count = int(job.get("success_count") or 0)
            failure_count = int(job.get("failure_count") or 0)
            completed_count = success_count + failure_count

            retry_like_progress = completed_count > total_rows or success_count > total_rows
            recovered_total_rows = max(total_rows, completed_count)
            recovered_processed_rows = (
                recovered_total_rows
                if retry_like_progress
                else min(processed_rows, recovered_total_rows)
            )

            if recovered_processed_rows >= recovered_total_rows:
                status = "completed_with_errors" if failure_count else "completed"
            else:
                status = "failed"

            errors = job.get("errors") or []
            errors.append(
                {
                    "message": "任务在上次服务停止时中断，已自动收束状态",
                    "previous_status": job.get("status"),
                }
            )
            await db.execute(
                """
                UPDATE jobs
                SET status = ?, total_rows = ?, processed_rows = ?,
                    errors = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    recovered_total_rows,
                    recovered_processed_rows,
                    _json_dumps(errors),
                    now,
                    job["job_id"],
                ),
            )
            recovered += 1
        await db.commit()
        return recovered


async def _ensure_runtime_columns(db_path: Path) -> None:
    async with _connect(db_path) as db:
        await _add_column_if_missing(db, "jobs", "kind", "TEXT NOT NULL DEFAULT 'legacy'")
        await _add_column_if_missing(db, "jobs", "source_job_id", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing(db, "jobs", "source_filename", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing(
            db,
            "jobs",
            "generate_llm_version_name",
            "TEXT NOT NULL DEFAULT ''",
        )
        await _add_column_if_missing(db, "jobs", "generate_llm_version_id", "INTEGER")
        await _add_column_if_missing(db, "jobs", "generate_llm_config", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing(
            db,
            "jobs",
            "judge_llm_version_name",
            "TEXT NOT NULL DEFAULT ''",
        )
        await _add_column_if_missing(db, "jobs", "judge_llm_config", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing(db, "results", "judge_result", "TEXT NOT NULL DEFAULT '{}'")
        await _normalize_existing_judge_results(db)
        await _rename_column_if_needed(db, "results", "output1", "input")
        await _add_column_if_missing(db, "results", "output", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing(db, "jobs", "prompt_combine_version_name", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing(db, "jobs", "judge_prompt_version_name", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing(db, "jobs", "judge_prompt", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing(
            db,
            "jobs",
            "narrative_judge_prompt_version_name",
            "TEXT NOT NULL DEFAULT ''",
        )
        await _add_column_if_missing(db, "jobs", "narrative_judge_prompt", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing(
            db,
            "jobs",
            "choice_judge_prompt_version_name",
            "TEXT NOT NULL DEFAULT ''",
        )
        await _add_column_if_missing(db, "jobs", "choice_judge_prompt", "TEXT NOT NULL DEFAULT ''")
        await _add_column_if_missing(
            db,
            "judge_prompt_versions",
            "target",
            "TEXT NOT NULL DEFAULT 'narrative'",
        )
        await _add_column_if_missing(
            db,
            "judge_prompt_versions",
            "score_config",
            "TEXT NOT NULL DEFAULT ''",
        )
        await _add_column_if_missing(
            db,
            "judge_prompt_versions",
            "version_no",
            "INTEGER NOT NULL DEFAULT 0",
        )
        await _backfill_judge_prompt_version_no(db)
        await _migrate_judge_prompt_unique_constraint(db)
        await _add_column_if_missing(
            db,
            "prompt_combine_template_versions",
            "narrative_output_template",
            f"TEXT NOT NULL DEFAULT '{DEFAULT_NARRATIVE_OUTPUT_TEMPLATE}'",
        )
        await _add_column_if_missing(
            db,
            "prompt_combine_template_versions",
            "choice_option_template",
            f"TEXT NOT NULL DEFAULT '{DEFAULT_CHOICE_OPTION_TEMPLATE}'",
        )
        await _drop_prompt_combine_llm_output_template_column(db)
        await _add_column_if_missing(db, "jobs", "judge_bug_count", "INTEGER NOT NULL DEFAULT 0")
        await _add_column_if_missing(db, "jobs", "judge_exists_count", "INTEGER NOT NULL DEFAULT 0")
        await _add_column_if_missing(db, "jobs", "judge_not_exists_count", "INTEGER NOT NULL DEFAULT 0")
        await _add_column_if_missing(db, "jobs", "judge_other_count", "INTEGER NOT NULL DEFAULT 0")
        await _add_column_if_missing(db, "jobs", "cancelled", "INTEGER NOT NULL DEFAULT 0")
        await _add_column_if_missing(db, "jobs", "auto_judge", "INTEGER NOT NULL DEFAULT 0")
        await _add_column_if_missing(db, "jobs", "concurrency", "INTEGER NOT NULL DEFAULT 0")
        await _add_column_if_missing(db, "jobs", "judge_retry_count", "INTEGER NOT NULL DEFAULT 2")
        await db.commit()


async def _add_column_if_missing(
    db: aiosqlite.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    rows = await db.execute_fetchall(f"PRAGMA table_info({table})")
    if column not in {row[1] for row in rows}:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def _drop_prompt_combine_llm_output_template_column(db: aiosqlite.Connection) -> None:
    rows = await db.execute_fetchall("PRAGMA table_info(prompt_combine_template_versions)")
    columns = [row[1] for row in rows]
    if "llm_output_template" not in columns:
        return

    await db.execute("ALTER TABLE prompt_combine_template_versions RENAME TO prompt_combine_template_versions_old")
    await db.execute(
        """
        CREATE TABLE prompt_combine_template_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            system_template TEXT NOT NULL,
            user_template TEXT NOT NULL,
            assistant_template TEXT NOT NULL,
            narrative_output_template TEXT NOT NULL DEFAULT '{{text}}',
            choice_option_template TEXT NOT NULL DEFAULT '{{index}}. {{text}}（{{reason}}）',
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        """
        INSERT INTO prompt_combine_template_versions
            (id, name, system_template, user_template, assistant_template,
             narrative_output_template, choice_option_template,
             is_active, created_at, updated_at)
        SELECT id, name, system_template, user_template, assistant_template,
               narrative_output_template, choice_option_template,
               is_active, created_at, updated_at
        FROM prompt_combine_template_versions_old
        """
    )
    await db.execute("DROP TABLE prompt_combine_template_versions_old")


async def _rename_column_if_needed(
    db: aiosqlite.Connection,
    table: str,
    old_name: str,
    new_name: str,
) -> None:
    """Rename a column if it still has the old name (idempotent)."""
    rows = await db.execute_fetchall(f"PRAGMA table_info({table})")
    col_names = {row[1] for row in rows}
    if old_name in col_names and new_name not in col_names:
        await db.execute(f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}")


async def _drop_column_if_exists(
    db: aiosqlite.Connection,
    table: str,
    column: str,
) -> None:
    rows = await db.execute_fetchall(f"PRAGMA table_info({table})")
    if column in {row[1] for row in rows}:
        await db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


async def _backfill_judge_prompt_version_no(db: aiosqlite.Connection) -> None:
    rows = await db.execute_fetchall(
        "SELECT id, target, version_no FROM judge_prompt_versions ORDER BY target, id"
    )
    counters: dict[str, int] = {}
    for row in rows:
        row_id, target, version_no = row[0], row[1], row[2]
        next_no = counters.get(target, 0) + 1
        counters[target] = next_no
        if not version_no or version_no <= 0:
            await db.execute(
                "UPDATE judge_prompt_versions SET version_no = ? WHERE id = ?",
                (next_no, row_id),
            )


async def _migrate_judge_prompt_unique_constraint(db: aiosqlite.Connection) -> None:
    """Migrate from legacy UNIQUE(name) to UNIQUE(target, name) + UNIQUE(target, version_no).

    The legacy constraint is part of the column definition (UNIQUE on name),
    which SQLite materializes as an sqlite_autoindex_*. Such constraints can
    only be removed by recreating the table. This function is idempotent.
    """
    schema_row = await db.execute_fetchall(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='judge_prompt_versions'"
    )
    if not schema_row:
        return
    table_sql = schema_row[0][0] or ""
    needs_rebuild = "name TEXT NOT NULL UNIQUE" in table_sql

    if needs_rebuild:
        await db.execute(
            """
            CREATE TABLE judge_prompt_versions__new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target TEXT NOT NULL DEFAULT 'narrative',
                version_no INTEGER NOT NULL DEFAULT 0,
                template TEXT NOT NULL,
                fields TEXT NOT NULL,
                score_config TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(target, name),
                UNIQUE(target, version_no)
            )
            """
        )
        await db.execute(
            """
            INSERT INTO judge_prompt_versions__new
                (id, name, target, version_no, template, fields, score_config,
                 is_active, created_at, updated_at)
            SELECT id, name, target, version_no, template, fields, score_config,
                   is_active, created_at, updated_at
            FROM judge_prompt_versions
            """
        )
        await db.execute("DROP TABLE judge_prompt_versions")
        await db.execute(
            "ALTER TABLE judge_prompt_versions__new RENAME TO judge_prompt_versions"
        )
        return

    rows = await db.execute_fetchall("PRAGMA index_list(judge_prompt_versions)")
    has_target_name = False
    has_target_version_no = False
    for row in rows:
        index_name = row[1]
        is_unique = bool(row[2])
        if not is_unique:
            continue
        cols = await db.execute_fetchall(f"PRAGMA index_info({index_name})")
        col_names = [c[2] for c in cols]
        if col_names == ["target", "name"] or col_names == ["name", "target"]:
            has_target_name = True
        elif col_names == ["target", "version_no"] or col_names == ["version_no", "target"]:
            has_target_version_no = True

    if not has_target_name:
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_judge_prompt_versions_target_name "
            "ON judge_prompt_versions(target, name)"
        )
    if not has_target_version_no:
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_judge_prompt_versions_target_version_no "
            "ON judge_prompt_versions(target, version_no)"
        )


async def _normalize_existing_judge_results(db: aiosqlite.Connection) -> None:
    rows = await db.execute_fetchall("SELECT id, judge_result FROM results")
    for row_id, raw_value in rows:
        value = _json_loads(raw_value, {})
        normalized = _normalize_judge_result(value)
        if value != normalized:
            await db.execute(
                "UPDATE results SET judge_result = ? WHERE id = ?",
                (_json_dumps(normalized), row_id),
            )


async def ensure_default_versions(db_path: Path) -> None:
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        legacy_rows = await db.execute_fetchall("SELECT key, value FROM config")
        legacy = {row["key"]: row["value"] for row in legacy_rows}

        await _ensure_llm_kind(
            db,
            JUDGE_KIND,
            "默认裁判 API",
            legacy.get("judge_config") or _model_json(LLMConfig()),
        )
        await _ensure_prompt_combine_kind(
            db,
            "默认对话 prompt 模版",
            legacy.get(PROMPT_COMBINE_TEMPLATES_KEY),
        )
        await db.execute(
            """
            UPDATE prompt_combine_template_versions
            SET name = ?
            WHERE name = ?
            """,
            ("默认对话 prompt 模版", "默认历史轮次拼接模版"),
        )
        await _ensure_judge_prompt_kind(db, "narrative", "默认正文 LLM-as-Judge Prompt")
        await _ensure_judge_prompt_kind(db, "choice", "默认选项 LLM-as-Judge Prompt")
        await db.execute(
            """
            INSERT INTO config(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (JUDGE_CONCURRENCY_KEY, str(DEFAULT_JUDGE_CONCURRENCY)),
        )
        await db.execute(
            """
            INSERT INTO config(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (PROMPT_COMBINE_TEMPLATES_KEY, _model_json(default_prompt_combine_templates())),
        )
        await db.commit()


async def _ensure_llm_kind(
    db: aiosqlite.Connection,
    kind: str,
    default_name: str,
    default_config: str,
) -> None:
    async with db.execute(
        "SELECT COUNT(*) AS count FROM llm_config_versions WHERE kind = ?", (kind,)
    ) as cursor:
        count_row = await cursor.fetchone()
    if count_row["count"] == 0:
        now = utc_now()
        await db.execute(
            """
            INSERT INTO llm_config_versions(kind, name, config, is_active, created_at, updated_at)
            VALUES(?, ?, ?, 1, ?, ?)
            """,
            (kind, default_name, default_config, now, now),
        )
        return

    async with db.execute(
        "SELECT id FROM llm_config_versions WHERE kind = ? AND is_active = 1 LIMIT 1",
        (kind,),
    ) as cursor:
        active_row = await cursor.fetchone()
    if active_row is None:
        await db.execute(
            """
            UPDATE llm_config_versions
            SET is_active = 1
            WHERE id = (
                SELECT id FROM llm_config_versions
                WHERE kind = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            )
            """,
            (kind,),
        )


async def _ensure_prompt_combine_kind(
    db: aiosqlite.Connection,
    default_name: str,
    legacy_templates_json: str | None,
) -> None:
    async with db.execute("SELECT COUNT(*) FROM prompt_combine_template_versions") as cursor:
        count_row = await cursor.fetchone()
    if count_row[0] > 0:
        return

    defaults = default_prompt_combine_templates()
    if legacy_templates_json:
        try:
            payload = json.loads(legacy_templates_json)
            if isinstance(payload, dict):
                defaults = PromptCombineTemplates(
                    system_template=payload.get("system_template", defaults.system_template),
                    user_template=payload.get("user_template", defaults.user_template),
                    assistant_template=payload.get("assistant_template", defaults.assistant_template),
                    narrative_output_template=payload.get(
                        "narrative_output_template",
                        defaults.narrative_output_template,
                    ),
                    choice_option_template=payload.get("choice_option_template", defaults.choice_option_template),
                )
        except (json.JSONDecodeError, Exception):
            pass

    now = utc_now()
    await db.execute(
        """
        INSERT INTO prompt_combine_template_versions
            (name, system_template, user_template, assistant_template,
             narrative_output_template, choice_option_template,
             is_active, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            default_name,
            defaults.system_template,
            defaults.user_template,
            defaults.assistant_template,
            defaults.narrative_output_template,
            defaults.choice_option_template,
            now,
            now,
        ),
    )


async def _ensure_judge_prompt_kind(
    db: aiosqlite.Connection,
    target: str,
    default_name: str,
) -> None:
    target = _clean_judge_prompt_target(target)
    async with db.execute(
        "SELECT COUNT(*) FROM judge_prompt_versions WHERE target = ?",
        (target,),
    ) as cursor:
        count_row = await cursor.fetchone()
    if count_row[0] == 0:
        now = utc_now()
        fields = [
            {"placeholder": "prompt", "source_field": "prompt"},
            {"placeholder": "output", "source_field": "output"},
        ]
        await db.execute(
            """
            INSERT INTO judge_prompt_versions
                (name, target, version_no, template, fields, score_config,
                 is_active, created_at, updated_at)
            VALUES(?, ?, 1, ?, ?, '', 1, ?, ?)
            """,
            (default_name, target, DEFAULT_LLM_JUDGE_PROMPT, _json_dumps(fields), now, now),
        )
        return

    async with db.execute(
        "SELECT id FROM judge_prompt_versions WHERE target = ? AND is_active = 1 LIMIT 1",
        (target,),
    ) as cursor:
        active_row = await cursor.fetchone()
    if active_row is None:
        await db.execute(
            """
            UPDATE judge_prompt_versions
            SET is_active = 1
            WHERE id = (
                SELECT id FROM judge_prompt_versions
                WHERE target = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            )
            """,
            (target,),
        )


async def get_config_state(db_path: Path) -> ConfigVersionsState:
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        llm_rows = await db.execute_fetchall(
            """
            SELECT * FROM llm_config_versions
            ORDER BY is_active DESC, updated_at DESC, id DESC
            """
        )
        prompt_combine_rows = await db.execute_fetchall(
            """
            SELECT * FROM prompt_combine_template_versions
            ORDER BY is_active DESC, updated_at DESC, id DESC
            """
        )
        judge_prompt_rows = await db.execute_fetchall(
            """
            SELECT * FROM judge_prompt_versions
            ORDER BY is_active DESC, updated_at DESC, id DESC
            """
        )
        config_rows = await db.execute_fetchall("SELECT key, value FROM config")

    judge_versions: list[LLMConfigVersion] = []
    active_judge_id: int | None = None

    for row in llm_rows:
        version = _llm_version_from_row(row)
        if row["kind"] == JUDGE_KIND:
            judge_versions.append(version)
            if version.is_active:
                active_judge_id = version.id

    prompt_combine_versions = [_prompt_combine_version_from_row(row) for row in prompt_combine_rows]
    active_prompt_combine_id = next(
        (v.id for v in prompt_combine_versions if v.is_active), None
    )
    judge_prompt_versions = [_judge_prompt_version_from_row(row) for row in judge_prompt_rows]
    narrative_judge_prompt_versions = [
        version for version in judge_prompt_versions if version.target == "narrative"
    ]
    choice_judge_prompt_versions = [
        version for version in judge_prompt_versions if version.target == "choice"
    ]
    active_judge_prompt_id = next(
        (v.id for v in narrative_judge_prompt_versions if v.is_active), None
    )

    config_values = {row["key"]: row["value"] for row in config_rows}
    active_narrative_judge_prompt_id = _configured_judge_prompt_id(
        config_values.get(NARRATIVE_JUDGE_PROMPT_ID_KEY),
        narrative_judge_prompt_versions,
        active_judge_prompt_id,
    )
    active_choice_judge_prompt_id = _configured_judge_prompt_id(
        config_values.get(CHOICE_JUDGE_PROMPT_ID_KEY),
        choice_judge_prompt_versions,
        next((v.id for v in choice_judge_prompt_versions if v.is_active), None),
    )
    active_pc = next((v for v in prompt_combine_versions if v.is_active), None)
    if active_pc:
        prompt_combine_templates = PromptCombineTemplates(
            system_template=active_pc.system_template,
            user_template=active_pc.user_template,
            assistant_template=active_pc.assistant_template,
            narrative_output_template=active_pc.narrative_output_template,
            choice_option_template=active_pc.choice_option_template,
        )
    else:
        prompt_combine_templates = _prompt_combine_templates_from_config(config_values)

    return ConfigVersionsState(
        judge_versions=judge_versions,
        prompt_combine_versions=prompt_combine_versions,
        judge_prompt_versions=judge_prompt_versions,
        narrative_judge_prompt_versions=narrative_judge_prompt_versions,
        choice_judge_prompt_versions=choice_judge_prompt_versions,
        runtime_settings=_runtime_settings_from_config(config_values),
        prompt_combine_templates=prompt_combine_templates,
        active_judge_id=active_judge_id,
        active_prompt_combine_id=active_prompt_combine_id,
        active_judge_prompt_id=active_judge_prompt_id,
        active_narrative_judge_prompt_id=active_narrative_judge_prompt_id,
        active_choice_judge_prompt_id=active_choice_judge_prompt_id,
    )


def _configured_judge_prompt_id(
    value: str | None,
    versions: list[JudgePromptVersion],
    fallback_id: int | None,
) -> int | None:
    valid_ids = {version.id for version in versions}
    try:
        configured_id = int(value) if value else None
    except (TypeError, ValueError):
        configured_id = None
    if configured_id in valid_ids:
        return configured_id
    return fallback_id


async def get_runtime_settings(db_path: Path) -> RuntimeSettings:
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = await db.execute_fetchall("SELECT key, value FROM config")
    return _runtime_settings_from_config({row["key"]: row["value"] for row in rows})


async def save_runtime_settings(db_path: Path, settings: RuntimeSettings) -> RuntimeSettings:
    async with _connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO config(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (JUDGE_CONCURRENCY_KEY, str(settings.judge_concurrency)),
        )
        await db.commit()
    return await get_runtime_settings(db_path)


async def get_prompt_combine_templates(db_path: Path) -> PromptCombineTemplates:
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        async with db.execute(
            "SELECT * FROM prompt_combine_template_versions WHERE is_active = 1 LIMIT 1"
        ) as cursor:
            active_row = await cursor.fetchone()
    if active_row:
        return PromptCombineTemplates(
            system_template=active_row["system_template"],
            user_template=active_row["user_template"],
            assistant_template=active_row["assistant_template"],
            narrative_output_template=active_row["narrative_output_template"],
            choice_option_template=active_row["choice_option_template"],
        )
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = await db.execute_fetchall("SELECT key, value FROM config")
    return _prompt_combine_templates_from_config({row["key"]: row["value"] for row in rows})


async def save_prompt_combine_templates(
    db_path: Path,
    templates: PromptCombineTemplates,
) -> PromptCombineTemplates:
    state = await get_config_state(db_path)
    active = next((v for v in state.prompt_combine_versions if v.is_active), None)
    if active:
        payload = PromptCombineTemplateVersionCreate(
            name=active.name,
            system_template=templates.system_template,
            user_template=templates.user_template,
            assistant_template=templates.assistant_template,
            narrative_output_template=templates.narrative_output_template,
            choice_option_template=templates.choice_option_template,
        )
        await update_prompt_combine_version(db_path, active.id, payload)
    else:
        async with _connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO config(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (PROMPT_COMBINE_TEMPLATES_KEY, _model_json(templates)),
            )
            await db.commit()
    return await get_prompt_combine_templates(db_path)


def _runtime_settings_from_config(values: dict[str, str]) -> RuntimeSettings:
    def _read_concurrency(key: str, default: int) -> int:
        try:
            val = int(values.get(key, default))
        except (TypeError, ValueError):
            val = default
        return max(1, min(500, val))

    return RuntimeSettings(
        judge_concurrency=_read_concurrency(JUDGE_CONCURRENCY_KEY, DEFAULT_JUDGE_CONCURRENCY),
    )


def default_prompt_combine_templates() -> PromptCombineTemplates:
    return PromptCombineTemplates(
        system_template=DEFAULT_PROMPT_COMBINE_SYSTEM_TEMPLATE,
        user_template=DEFAULT_PROMPT_COMBINE_USER_TEMPLATE,
        assistant_template=DEFAULT_PROMPT_COMBINE_ASSISTANT_TEMPLATE,
        narrative_output_template=DEFAULT_NARRATIVE_OUTPUT_TEMPLATE,
        choice_option_template=DEFAULT_CHOICE_OPTION_TEMPLATE,
    )


def _prompt_combine_templates_from_config(values: dict[str, str]) -> PromptCombineTemplates:
    payload = _json_loads(values.get(PROMPT_COMBINE_TEMPLATES_KEY), {})
    if isinstance(payload, dict):
        defaults = default_prompt_combine_templates()
        default_values = defaults.model_dump() if hasattr(defaults, "model_dump") else defaults.dict()
        merged = {**default_values, **payload}
        try:
            return PromptCombineTemplates(**merged)
        except Exception:
            pass
    return default_prompt_combine_templates()


async def get_app_config(db_path: Path) -> AppConfig:
    state = await get_config_state(db_path)
    judge = _active_or_first(state.judge_versions, LLMConfig()).config
    return AppConfig(judge=judge)


async def get_active_llm_config_version_name(db_path: Path, kind: str) -> str:
    kind = _require_llm_kind(kind)
    state = await get_config_state(db_path)
    return _active_or_first(state.judge_versions, LLMConfig()).name


async def get_active_llm_config_version(db_path: Path, kind: str) -> LLMConfigVersion:
    kind = _require_llm_kind(kind)
    state = await get_config_state(db_path)
    return _active_or_first(state.judge_versions, LLMConfig())


async def get_active_prompt_combine_version_name(db_path: Path) -> str:
    state = await get_config_state(db_path)
    versions = state.prompt_combine_versions
    if versions:
        active = next((v for v in versions if v.is_active), None)
        if active:
            return active.name
        return versions[0].name
    return ""


async def save_app_config(db_path: Path, config: AppConfig) -> None:
    await _upsert_active_llm_config(db_path, JUDGE_KIND, "默认裁判 API", config.judge)


async def create_llm_config_version(
    db_path: Path,
    kind: str,
    payload: LLMConfigVersionCreate,
    inherit_from_version_id: int | None = None,
) -> LLMConfigVersion:
    kind = _require_llm_kind(kind)
    name = _clean_name(payload.name)
    now = utc_now()
    async with _connect(db_path) as db:
        config = payload.config
        if inherit_from_version_id is not None and not config.api_key:
            db.row_factory = sqlite3.Row
            async with db.execute(
                "SELECT config FROM llm_config_versions WHERE id = ? AND kind = ?",
                (inherit_from_version_id, kind),
            ) as existing_cursor:
                existing_row = await existing_cursor.fetchone()
            if existing_row is not None:
                config = _preserve_existing_api_key(
                    config,
                    LLMConfig(**_json_loads(existing_row["config"], {})),
                )
        try:
            cursor = await db.execute(
                """
                INSERT INTO llm_config_versions(kind, name, config, is_active, created_at, updated_at)
                VALUES(?, ?, ?, 0, ?, ?)
                """,
                (kind, name, _model_json(config), now, now),
            )
            version_id = cursor.lastrowid
            await _activate_llm_version(db, kind, version_id)
            await db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("同类型配置版本名称已存在") from exc
    version = await get_llm_config_version(db_path, kind, version_id)
    if version is None:
        raise ValueError("配置版本保存失败")
    return version


async def update_llm_config_version(
    db_path: Path,
    kind: str,
    version_id: int,
    payload: LLMConfigVersionCreate,
) -> LLMConfigVersion | None:
    kind = _require_llm_kind(kind)
    name = _clean_name(payload.name)
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        async with db.execute(
            "SELECT config FROM llm_config_versions WHERE id = ? AND kind = ?",
            (version_id, kind),
        ) as existing_cursor:
            existing_row = await existing_cursor.fetchone()
        if existing_row is None:
            return None
        config = _preserve_existing_api_key(
            payload.config,
            LLMConfig(**_json_loads(existing_row["config"], {})),
        )
        try:
            cursor = await db.execute(
                """
                UPDATE llm_config_versions
                SET name = ?, config = ?, updated_at = ?
                WHERE id = ? AND kind = ?
                """,
                (name, _model_json(config), utc_now(), version_id, kind),
            )
            await db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("同类型配置版本名称已存在") from exc
    if cursor.rowcount == 0:
        return None
    return await get_llm_config_version(db_path, kind, version_id)


async def activate_llm_config_version(
    db_path: Path,
    kind: str,
    version_id: int,
) -> LLMConfigVersion | None:
    kind = _require_llm_kind(kind)
    async with _connect(db_path) as db:
        ok = await _activate_llm_version(db, kind, version_id)
        await db.commit()
    if not ok:
        return None
    return await get_llm_config_version(db_path, kind, version_id)


async def delete_llm_config_version(
    db_path: Path,
    kind: str,
    version_id: int,
) -> bool:
    kind = _require_llm_kind(kind)
    async with _connect(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM llm_config_versions WHERE kind = ?", (kind,)
        ) as cursor:
            count_row = await cursor.fetchone()
        if count_row[0] <= 1:
            raise ValueError("至少保留一个版本，无法删除")

        cursor = await db.execute(
            "DELETE FROM llm_config_versions WHERE id = ? AND kind = ?",
            (version_id, kind),
        )
        if cursor.rowcount == 0:
            return False

        await _ensure_llm_kind_has_active(db, kind)
        await db.commit()
    return True


async def _ensure_llm_kind_has_active(db: aiosqlite.Connection, kind: str) -> None:
    async with db.execute(
        "SELECT id FROM llm_config_versions WHERE kind = ? AND is_active = 1 LIMIT 1",
        (kind,),
    ) as cursor:
        active_row = await cursor.fetchone()
    if active_row is None:
        await db.execute(
            """
            UPDATE llm_config_versions
            SET is_active = 1
            WHERE id = (
                SELECT id FROM llm_config_versions
                WHERE kind = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            )
            """,
            (kind,),
        )


async def get_llm_config_version(
    db_path: Path,
    kind: str,
    version_id: int,
) -> LLMConfigVersion | None:
    kind = _require_llm_kind(kind)
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        async with db.execute(
            "SELECT * FROM llm_config_versions WHERE id = ? AND kind = ?",
            (version_id, kind),
        ) as cursor:
            row = await cursor.fetchone()
    return _llm_version_from_row(row) if row else None


async def get_llm_config_version_by_name(
    db_path: Path,
    kind: str,
    name: str,
) -> LLMConfigVersion | None:
    kind = _require_llm_kind(kind)
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        async with db.execute(
            "SELECT * FROM llm_config_versions WHERE kind = ? AND name = ?",
            (kind, name),
        ) as cursor:
            row = await cursor.fetchone()
    return _llm_version_from_row(row) if row else None


async def create_prompt_combine_version(
    db_path: Path,
    payload: PromptCombineTemplateVersionCreate,
) -> PromptCombineTemplateVersion:
    name = _clean_name(payload.name)
    now = utc_now()
    async with _connect(db_path) as db:
        try:
            cursor = await db.execute(
                """
                INSERT INTO prompt_combine_template_versions
                    (name, system_template, user_template, assistant_template,
                     narrative_output_template, choice_option_template,
                     is_active, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    name,
                    payload.system_template,
                    payload.user_template,
                    payload.assistant_template,
                    payload.narrative_output_template,
                    payload.choice_option_template,
                    now,
                    now,
                ),
            )
            version_id = cursor.lastrowid
            await _activate_prompt_combine_version(db, version_id)
            await db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("历史轮次拼接模版版本名称已存在") from exc
    version = await get_prompt_combine_version(db_path, version_id)
    if version is None:
        raise ValueError("历史轮次拼接模版版本保存失败")
    return version


async def update_prompt_combine_version(
    db_path: Path,
    version_id: int,
    payload: PromptCombineTemplateVersionCreate,
) -> PromptCombineTemplateVersion | None:
    name = _clean_name(payload.name)
    async with _connect(db_path) as db:
        try:
            cursor = await db.execute(
                """
                UPDATE prompt_combine_template_versions
                SET name = ?, system_template = ?, user_template = ?,
                    assistant_template = ?, narrative_output_template = ?,
                    choice_option_template = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    payload.system_template,
                    payload.user_template,
                    payload.assistant_template,
                    payload.narrative_output_template,
                    payload.choice_option_template,
                    utc_now(),
                    version_id,
                ),
            )
            await db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("历史轮次拼接模版版本名称已存在") from exc
    if cursor.rowcount == 0:
        return None
    return await get_prompt_combine_version(db_path, version_id)


async def activate_prompt_combine_version(db_path: Path, version_id: int) -> PromptCombineTemplateVersion | None:
    async with _connect(db_path) as db:
        ok = await _activate_prompt_combine_version(db, version_id)
        await db.commit()
    if not ok:
        return None
    return await get_prompt_combine_version(db_path, version_id)


async def get_prompt_combine_version(db_path: Path, version_id: int) -> PromptCombineTemplateVersion | None:
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        async with db.execute(
            "SELECT * FROM prompt_combine_template_versions WHERE id = ?", (version_id,)
        ) as cursor:
            row = await cursor.fetchone()
    return _prompt_combine_version_from_row(row) if row else None


async def delete_prompt_combine_version(db_path: Path, version_id: int) -> bool:
    async with _connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM prompt_combine_template_versions") as cursor:
            count_row = await cursor.fetchone()
        if count_row[0] <= 1:
            raise ValueError("至少保留一个版本，无法删除")

        cursor = await db.execute(
            "DELETE FROM prompt_combine_template_versions WHERE id = ?", (version_id,)
        )
        if cursor.rowcount == 0:
            return False

        await _ensure_prompt_combine_has_active(db)
        await db.commit()
    return True


async def get_active_judge_prompt_version_name(db_path: Path) -> str:
    state = await get_config_state(db_path)
    versions = state.judge_prompt_versions
    if versions:
        active = next((v for v in versions if v.is_active), None)
        if active:
            return active.name
        return versions[0].name
    return ""


async def get_judge_prompt_version(
    db_path: Path,
    target: str,
    version_no: int,
) -> JudgePromptVersion | None:
    target = _clean_judge_prompt_target(target)
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        async with db.execute(
            "SELECT * FROM judge_prompt_versions WHERE target = ? AND version_no = ?",
            (target, version_no),
        ) as cursor:
            row = await cursor.fetchone()
    return _judge_prompt_version_from_row(row) if row else None


async def _get_judge_prompt_row_id(
    db: aiosqlite.Connection,
    target: str,
    version_no: int,
) -> int | None:
    async with db.execute(
        "SELECT id FROM judge_prompt_versions WHERE target = ? AND version_no = ?",
        (target, version_no),
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0]) if row else None


async def create_judge_prompt_version(
    db_path: Path,
    payload: JudgePromptVersionCreate,
    target: str = "narrative",
) -> JudgePromptVersion:
    target = _clean_judge_prompt_target(target)
    name = _clean_name(payload.name)
    template = _clean_template(payload.template)
    fields = _clean_judge_prompt_fields(payload.fields, template)
    score_config = _clean_score_config(payload.score_config)
    now = utc_now()
    async with _connect(db_path) as db:
        try:
            async with db.execute(
                "SELECT COALESCE(MAX(version_no), 0) FROM judge_prompt_versions WHERE target = ?",
                (target,),
            ) as cursor:
                max_row = await cursor.fetchone()
            next_version_no = int(max_row[0] or 0) + 1
            cursor = await db.execute(
                """
                INSERT INTO judge_prompt_versions
                    (name, target, version_no, template, fields, score_config,
                     is_active, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    name,
                    target,
                    next_version_no,
                    template,
                    _json_dumps(fields),
                    _dump_score_config(score_config),
                    now,
                    now,
                ),
            )
            row_id = cursor.lastrowid
            await _activate_judge_prompt_version_by_row_id(db, row_id, target=target)
            await db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Judge Prompt 版本名称已存在") from exc
    version = await get_judge_prompt_version(db_path, target, next_version_no)
    if version is None:
        raise ValueError("Judge Prompt 版本保存失败")
    return version


async def update_judge_prompt_version(
    db_path: Path,
    target: str,
    version_no: int,
    payload: JudgePromptVersionCreate,
) -> JudgePromptVersion | None:
    target = _clean_judge_prompt_target(target)
    name = _clean_name(payload.name)
    template = _clean_template(payload.template)
    fields = _clean_judge_prompt_fields(payload.fields, template)
    score_config = _clean_score_config(payload.score_config)
    async with _connect(db_path) as db:
        try:
            cursor = await db.execute(
                """
                UPDATE judge_prompt_versions
                SET name = ?, template = ?, fields = ?, score_config = ?, updated_at = ?
                WHERE target = ? AND version_no = ?
                """,
                (
                    name,
                    template,
                    _json_dumps(fields),
                    _dump_score_config(score_config),
                    utc_now(),
                    target,
                    version_no,
                ),
            )
            await db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Judge Prompt 版本名称已存在") from exc
    if cursor.rowcount == 0:
        return None
    return await get_judge_prompt_version(db_path, target, version_no)


async def activate_judge_prompt_version(
    db_path: Path,
    target: str,
    version_no: int,
) -> JudgePromptVersion | None:
    target = _clean_judge_prompt_target(target)
    version = await get_judge_prompt_version(db_path, target, version_no)
    if version is None:
        return None
    async with _connect(db_path) as db:
        ok = await _activate_judge_prompt_version(db, target, version_no)
        await db.commit()
    if not ok:
        return None
    return await get_judge_prompt_version(db_path, target, version_no)


async def activate_llm_judge_prompt_default(
    db_path: Path,
    target: str,
    version_no: int,
) -> JudgePromptVersion | None:
    target = _clean_judge_prompt_target(target)
    key = _llm_judge_prompt_default_key(target)
    version = await get_judge_prompt_version(db_path, target, version_no)
    if version is None:
        return None
    async with _connect(db_path) as db:
        await _activate_judge_prompt_version(db, target, version_no)
        await db.execute(
            """
            INSERT INTO config(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(version_no)),
        )
        await db.commit()
    return version


def _llm_judge_prompt_default_key(target: str) -> str:
    if target == "narrative":
        return NARRATIVE_JUDGE_PROMPT_ID_KEY
    if target == "choice":
        return CHOICE_JUDGE_PROMPT_ID_KEY
    raise ValueError("Judge Prompt 默认类型无效")


async def delete_judge_prompt_version(
    db_path: Path,
    target: str,
    version_no: int,
) -> bool:
    target = _clean_judge_prompt_target(target)
    version = await get_judge_prompt_version(db_path, target, version_no)
    if version is None:
        return False
    async with _connect(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM judge_prompt_versions WHERE target = ?",
            (target,),
        ) as cursor:
            count_row = await cursor.fetchone()
        if count_row[0] <= 1:
            raise ValueError("至少保留一个版本，无法删除")

        cursor = await db.execute(
            "DELETE FROM judge_prompt_versions WHERE target = ? AND version_no = ?",
            (target, version_no),
        )
        if cursor.rowcount == 0:
            return False

        await _ensure_judge_prompt_has_active(db, target)
        await db.commit()
    return True


def _clean_judge_prompt_target(target: str) -> str:
    if target in {"narrative", "choice"}:
        return target
    raise ValueError("Judge Prompt 类型无效")


def _clean_judge_prompt_fields(
    fields: list[JudgePromptField],
    template: str,
) -> list[dict[str, str]]:
    placeholders = set(re.findall(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}", template))
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for field in fields:
        placeholder = field.placeholder.strip()
        source_field = field.source_field.strip()
        if not placeholder or not source_field:
            raise ValueError("Judge Prompt 字段映射不能为空")
        if "." in source_field:
            raise ValueError("Judge Prompt 只支持顶层输入字段")
        if placeholder in seen:
            raise ValueError(f"Judge Prompt 占位符重复: {placeholder}")
        seen.add(placeholder)
        cleaned.append({"placeholder": placeholder, "source_field": source_field})

    mapped = {field["placeholder"] for field in cleaned}
    missing = sorted(placeholders - mapped)
    if missing:
        raise ValueError(f"Judge Prompt 缺少占位符映射: {', '.join(missing)}")
    return cleaned


_SCORE_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _validate_score_path(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"Judge Prompt {label} 不能为空")
    if not _SCORE_PATH_RE.match(cleaned):
        raise ValueError(f"Judge Prompt {label} 仅支持点号路径（a.b.c）")
    return cleaned


def _clean_score_config(score_config: ScoreConfig | None) -> ScoreConfig | None:
    if score_config is None:
        return None
    mode = (score_config.mode or "direct").strip()
    if mode not in {"direct", "weighted"}:
        raise ValueError("Judge Prompt 得分计算模式无效")
    evidence_path = _validate_score_path(score_config.evidence_path, "evidence_path")
    if mode == "direct":
        score_path = _validate_score_path(score_config.score_path, "score_path")
        return ScoreConfig(
            mode="direct",
            score_path=score_path,
            evidence_path=evidence_path,
            components=[],
        )
    components_in = score_config.components or []
    if not components_in:
        raise ValueError("Judge Prompt 加权模式至少需要一个 component")
    seen: set[str] = set()
    cleaned: list[ScoreComponent] = []
    for index, component in enumerate(components_in, start=1):
        path = _validate_score_path(component.path, f"components[{index}].path")
        if component.weight < 0:
            raise ValueError(f"Judge Prompt components[{index}].weight 不能为负")
        if path in seen:
            raise ValueError(f"Judge Prompt components[{index}].path 重复: {path}")
        seen.add(path)
        cleaned.append(ScoreComponent(path=path, weight=float(component.weight)))
    if sum(c.weight for c in cleaned) <= 0:
        raise ValueError("Judge Prompt 加权模式 weight 之和必须大于 0")
    return ScoreConfig(
        mode="weighted",
        score_path=score_config.score_path or "overall_score",
        evidence_path=evidence_path,
        components=cleaned,
    )


def _dump_score_config(score_config: ScoreConfig | None) -> str:
    if score_config is None:
        return ""
    payload = {
        "mode": score_config.mode,
        "score_path": score_config.score_path,
        "evidence_path": score_config.evidence_path,
        "components": [
            {"path": c.path, "weight": c.weight} for c in score_config.components
        ],
    }
    return _json_dumps(payload)


def _load_score_config(raw: str | None) -> ScoreConfig | None:
    if not raw:
        return None
    data = _json_loads(raw, None)
    if not isinstance(data, dict):
        return None
    try:
        return ScoreConfig(**data)
    except Exception:
        return None


async def _activate_judge_prompt_version(
    db: aiosqlite.Connection,
    target: str,
    version_no: int,
) -> bool:
    target = _clean_judge_prompt_target(target)
    async with db.execute(
        "SELECT id FROM judge_prompt_versions WHERE target = ? AND version_no = ?",
        (target, version_no),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return False
    await db.execute(
        "UPDATE judge_prompt_versions SET is_active = 0 WHERE target = ?",
        (target,),
    )
    await db.execute(
        "UPDATE judge_prompt_versions SET is_active = 1 WHERE target = ? AND version_no = ?",
        (target, version_no),
    )
    return True


async def _activate_judge_prompt_version_by_row_id(
    db: aiosqlite.Connection,
    row_id: int,
    target: str,
) -> bool:
    target = _clean_judge_prompt_target(target)
    await db.execute(
        "UPDATE judge_prompt_versions SET is_active = 0 WHERE target = ?",
        (target,),
    )
    cursor = await db.execute(
        "UPDATE judge_prompt_versions SET is_active = 1 WHERE id = ? AND target = ?",
        (row_id, target),
    )
    return cursor.rowcount > 0


async def _ensure_judge_prompt_has_active(db: aiosqlite.Connection, target: str) -> None:
    target = _clean_judge_prompt_target(target)
    async with db.execute(
        "SELECT id FROM judge_prompt_versions WHERE target = ? AND is_active = 1 LIMIT 1",
        (target,),
    ) as cursor:
        active_row = await cursor.fetchone()
    if active_row is None:
        await db.execute(
            """
            UPDATE judge_prompt_versions
            SET is_active = 1
            WHERE id = (
                SELECT id FROM judge_prompt_versions
                WHERE target = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            )
            """,
            (target,),
        )


async def _activate_prompt_combine_version(
    db: aiosqlite.Connection,
    version_id: int,
) -> bool:
    async with db.execute(
        "SELECT id FROM prompt_combine_template_versions WHERE id = ?", (version_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return False
    await db.execute("UPDATE prompt_combine_template_versions SET is_active = 0")
    await db.execute(
        "UPDATE prompt_combine_template_versions SET is_active = 1 WHERE id = ?", (version_id,)
    )
    return True


async def _ensure_prompt_combine_has_active(db: aiosqlite.Connection) -> None:
    async with db.execute(
        "SELECT id FROM prompt_combine_template_versions WHERE is_active = 1 LIMIT 1"
    ) as cursor:
        active_row = await cursor.fetchone()
    if active_row is None:
        await db.execute(
            """
            UPDATE prompt_combine_template_versions
            SET is_active = 1
            WHERE id = (
                SELECT id FROM prompt_combine_template_versions
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            )
            """
        )


async def _activate_llm_version(
    db: aiosqlite.Connection,
    kind: str,
    version_id: int,
) -> bool:
    async with db.execute(
        "SELECT id FROM llm_config_versions WHERE id = ? AND kind = ?",
        (version_id, kind),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return False
    await db.execute("UPDATE llm_config_versions SET is_active = 0 WHERE kind = ?", (kind,))
    await db.execute("UPDATE llm_config_versions SET is_active = 1 WHERE id = ?", (version_id,))
    return True


async def _upsert_active_llm_config(
    db_path: Path,
    kind: str,
    default_name: str,
    config: LLMConfig,
) -> None:
    state = await get_config_state(db_path)
    versions = state.judge_versions
    active = next((version for version in versions if version.is_active), versions[0] if versions else None)
    payload = LLMConfigVersionCreate(name=active.name if active else default_name, config=config)
    if active:
        await update_llm_config_version(db_path, kind, active.id, payload)
        await activate_llm_config_version(db_path, kind, active.id)
    else:
        await create_llm_config_version(db_path, kind, payload)


def _active_or_first(versions: list[LLMConfigVersion], default: LLMConfig) -> LLMConfigVersion:
    for version in versions:
        if version.is_active:
            return version
    if versions:
        return versions[0]
    return LLMConfigVersion(
        id=0,
        name="",
        config=default,
        is_active=False,
        created_at="",
        updated_at="",
    )


def _llm_version_from_row(row: sqlite3.Row) -> LLMConfigVersion:
    return LLMConfigVersion(
        id=row["id"],
        name=row["name"],
        config=LLMConfig(**_json_loads(row["config"], {})),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _prompt_combine_version_from_row(row: sqlite3.Row) -> PromptCombineTemplateVersion:
    return PromptCombineTemplateVersion(
        id=row["id"],
        name=row["name"],
        system_template=row["system_template"],
        user_template=row["user_template"],
        assistant_template=row["assistant_template"],
        narrative_output_template=row["narrative_output_template"],
        choice_option_template=row["choice_option_template"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _judge_prompt_version_from_row(row: sqlite3.Row) -> JudgePromptVersion:
    fields = _json_loads(row["fields"], [])
    if not isinstance(fields, list):
        fields = []
    clean_fields = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        try:
            clean_fields.append(JudgePromptField(**field))
        except Exception:
            continue
    score_config_raw = row["score_config"] if "score_config" in row.keys() else ""
    score_config = _load_score_config(score_config_raw)
    keys = row.keys()
    version_no = row["version_no"] if "version_no" in keys else 0
    if not version_no:
        version_no = row["id"]
    return JudgePromptVersion(
        id=version_no,
        name=row["name"],
        template=row["template"],
        fields=clean_fields,
        score_config=score_config,
        is_active=bool(row["is_active"]),
        target=row["target"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _require_llm_kind(kind: str) -> str:
    if kind != JUDGE_KIND:
        raise ValueError("配置类型无效")
    return kind


def _clean_name(name: str) -> str:
    value = name.strip()
    if not value:
        raise ValueError("版本名称不能为空")
    return value


def _clean_template(template: str) -> str:
    value = template.strip()
    if not value:
        raise ValueError("Judge 输入模板不能为空")
    return value


def _preserve_existing_api_key(config: LLMConfig, existing: LLMConfig) -> LLMConfig:
    if config.api_key != "":
        return config
    data = config.model_dump() if hasattr(config, "model_dump") else config.dict()
    data["api_key"] = existing.api_key
    return LLMConfig(**data)


async def create_job(
    db_path: Path,
    job_id: str,
    total_rows: int,
    *,
    kind: str = "legacy",
    source_job_id: str = "",
    source_filename: str = "",
    judge_llm_version_name: str = "",
    judge_llm_config: str = "",
    prompt_combine_version_name: str = "",
    judge_prompt_version_name: str = "",
    judge_prompt: str = "",
    narrative_judge_prompt_version_name: str = "",
    narrative_judge_prompt: str = "",
    choice_judge_prompt_version_name: str = "",
    choice_judge_prompt: str = "",
    auto_judge: bool = False,
    concurrency: int = 0,
    judge_retry_count: int = 2,
) -> None:
    now = utc_now()
    async with _connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO jobs(job_id, status, total_rows, processed_rows, success_count,
                             failure_count, errors, created_at, updated_at, kind,
                             source_job_id, source_filename,
                             judge_llm_version_name, judge_llm_config,
                             prompt_combine_version_name,
                             judge_prompt_version_name, judge_prompt,
                             narrative_judge_prompt_version_name,
                             narrative_judge_prompt,
                             choice_judge_prompt_version_name,
                             choice_judge_prompt,
                             auto_judge,
                             concurrency, judge_retry_count)
            VALUES(?, 'pending', ?, 0, 0, 0, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                total_rows,
                now,
                now,
                kind,
                source_job_id,
                source_filename,
                judge_llm_version_name,
                judge_llm_config,
                prompt_combine_version_name,
                judge_prompt_version_name,
                judge_prompt,
                narrative_judge_prompt_version_name,
                narrative_judge_prompt,
                choice_judge_prompt_version_name,
                choice_judge_prompt,
                1 if auto_judge else 0,
                concurrency,
                judge_retry_count,
            ),
        )
        await db.commit()


async def mark_job_running(db_path: Path, job_id: str) -> None:
    await _update_job(db_path, job_id, status="running")


async def mark_job_cancelled(db_path: Path, job_id: str) -> None:
    await _update_job(db_path, job_id, cancelled=1)


async def is_job_cancelled(db_path: Path, job_id: str) -> bool:
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        async with db.execute(
            "SELECT cancelled FROM jobs WHERE job_id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
    return bool(row and row["cancelled"])


async def prepare_job_retry(db_path: Path, job_id: str, retry_total_rows: int) -> None:
    await _update_job(
        db_path,
        job_id,
        status="pending",
        total_rows=retry_total_rows,
        processed_rows=0,
        errors=_json_dumps([]),
        cancelled=0,
    )


async def prepare_job_resume(db_path: Path, job_id: str) -> None:
    await _update_job(db_path, job_id, status="pending", cancelled=0)


async def record_job_progress(
    db_path: Path,
    job_id: str,
    *,
    processed_rows: int,
    success_count: int,
    failure_count: int,
    errors: list[dict[str, Any]],
) -> None:
    await _update_job(
        db_path,
        job_id,
        processed_rows=processed_rows,
        success_count=success_count,
        failure_count=failure_count,
        errors=_json_dumps(errors),
    )


async def finish_job(
    db_path: Path,
    job_id: str,
    status: str,
    errors: list[dict[str, Any]],
    **extra_fields: Any,
) -> None:
    await _update_job(db_path, job_id, status=status, errors=_json_dumps(errors), **extra_fields)


async def _update_job(db_path: Path, job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = [*fields.values(), job_id]
    async with _connect(db_path) as db:
        await db.execute(f"UPDATE jobs SET {assignments} WHERE job_id = ?", values)
        await db.commit()


async def get_job(db_path: Path, job_id: str) -> dict[str, Any] | None:
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        async with db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return _job_from_row(row)


def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["errors"] = _json_loads(payload.get("errors"), [])
    return payload



async def save_result(
    db_path: Path,
    *,
    job_id: str,
    playthrough_id: str,
    turn: int,
    prompt: str,
    input: str,
    output: str = "",
    judge_result: dict[str, Any] | None = None,
) -> None:
    if judge_result is None:
        judge_result = {}
    async with _connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO results(job_id, playthrough_id, turn, prompt, input, output, judge_result,
                                created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                playthrough_id,
                turn,
                prompt,
                input,
                output,
                _json_dumps(judge_result),
                utc_now(),
            ),
        )
        await db.commit()


async def list_llm_judge_files(db_path: Path, sort_by: str = "created", source_filename: str | None = None) -> list[dict[str, Any]]:
    order_clause = "ORDER BY j.judge_exists_count DESC, j.created_at DESC" if sort_by == "exists" else "ORDER BY j.created_at DESC"
    source_clause = ""
    params: list[Any] = []
    if source_filename is not None:
        source_clause = "AND j.source_filename = ?"
        params.append(source_filename)
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = await db.execute_fetchall(
            f"""
            SELECT j.job_id, j.source_filename AS filename, j.kind, j.status, j.total_rows,
                   j.processed_rows, j.success_count, j.failure_count,
                   j.created_at, j.updated_at,
                   j.source_job_id,
                   j.generate_llm_version_name,
                   j.judge_llm_version_name,
                   j.prompt_combine_version_name, j.judge_prompt_version_name,
                   j.narrative_judge_prompt_version_name,
                   j.choice_judge_prompt_version_name,
                   j.judge_bug_count AS bug_count,
                   j.judge_exists_count AS valid_json_count,
                   0 AS exists_count,
                   0 AS not_exists_count,
                   j.judge_other_count AS other_count,
                   j.concurrency,
                   j.judge_retry_count,
                   COUNT(r.id) AS result_count
            FROM jobs j
            LEFT JOIN results r ON r.job_id = j.job_id
            WHERE j.kind = 'llm_judge_upload' {source_clause}
            GROUP BY j.job_id
            {order_clause}
            """,
            params,
        )
    files = [dict(row) for row in rows]
    await _attach_score_breakdown_summaries(db_path, files)
    return files


def _coerce_score_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _format_score_bucket(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _score_bucket_sort_key(value: str) -> tuple[int, float | str]:
    score = _coerce_score_number(value)
    if score is not None:
        return (0, score)
    return (1, value)


def _score_distribution_summary_from_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    targets: dict[str, dict[str, Any]] = {}
    for row in rows:
        judge_result = _json_loads(row["judge_result"], {})
        if not isinstance(judge_result, dict):
            continue
        judge = judge_result.get("judge")
        if not isinstance(judge, dict):
            continue
        score = _coerce_score_number(judge.get("overall_score"))
        if score is None:
            continue
        target = str(judge_result.get("target") or "unknown")
        target_bucket = targets.setdefault(target, {"target": target, "total": 0, "buckets": {}})
        score_label = _format_score_bucket(score)
        target_bucket["total"] += 1
        target_bucket["buckets"][score_label] = target_bucket["buckets"].get(score_label, 0) + 1

    distributions = []
    for target_bucket in targets.values():
        buckets = [
            {"score": score, "count": count}
            for score, count in sorted(
                target_bucket["buckets"].items(),
                key=lambda item: _score_bucket_sort_key(item[0]),
            )
        ]
        distributions.append(
            {
                "target": target_bucket["target"],
                "total": target_bucket["total"],
                "buckets": buckets,
            }
        )

    target_order = {"narrative": 0, "choice": 1}
    distributions.sort(key=lambda item: (target_order.get(item["target"], 99), item["target"]))
    return {"title": "最终评分分布", "score_distribution": distributions} if distributions else {}


def _collect_score_components(judge_result: dict[str, Any]) -> list[dict[str, Any]]:
    judge = judge_result.get("judge")
    if not isinstance(judge, dict):
        return []

    breakdown = judge.get("score_breakdown")
    if isinstance(breakdown, dict) and isinstance(breakdown.get("components"), list):
        components = []
        for component in breakdown["components"]:
            if not isinstance(component, dict):
                continue
            score = _coerce_score_number(component.get("score"))
            contribution = _coerce_score_number(component.get("contribution"))
            if score is None and contribution is None:
                continue
            components.append(
                {
                    "label": str(component.get("path") or component.get("name") or "score"),
                    "score": score,
                    "contribution": contribution,
                    "normalized_weight": _coerce_score_number(component.get("normalized_weight")),
                }
            )
        if components:
            return components

    raw_json = judge.get("raw_json")
    if not isinstance(raw_json, dict):
        return []
    dimension_scores = raw_json.get("dimension_scores")
    if not isinstance(dimension_scores, dict):
        return []

    components = []
    for name, value in dimension_scores.items():
        score = _coerce_score_number(value.get("score") if isinstance(value, dict) else value)
        if score is None:
            continue
        components.append(
            {
                "label": str(name),
                "score": score,
                "contribution": score,
                "normalized_weight": None,
            }
        )
    return components


def _score_breakdown_summary_from_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        judge_result = _json_loads(row["judge_result"], {})
        if not isinstance(judge_result, dict):
            continue
        for component in _collect_score_components(judge_result):
            label = component["label"]
            bucket = totals.setdefault(
                label,
                {
                    "label": label,
                    "score_total": 0.0,
                    "contribution_total": 0.0,
                    "weight_total": 0.0,
                    "score_count": 0,
                    "contribution_count": 0,
                    "weight_count": 0,
                },
            )
            score = component.get("score")
            contribution = component.get("contribution")
            normalized_weight = component.get("normalized_weight")
            if score is not None:
                bucket["score_total"] += score
                bucket["score_count"] += 1
            if contribution is not None:
                bucket["contribution_total"] += contribution
                bucket["contribution_count"] += 1
            if normalized_weight is not None:
                bucket["weight_total"] += normalized_weight
                bucket["weight_count"] += 1

    components = []
    for bucket in totals.values():
        if bucket["score_count"] <= 0 and bucket["contribution_count"] <= 0:
            continue
        score = bucket["score_total"] / bucket["score_count"] if bucket["score_count"] else None
        contribution = (
            bucket["contribution_total"] / bucket["contribution_count"]
            if bucket["contribution_count"]
            else score
        )
        normalized_weight = (
            bucket["weight_total"] / bucket["weight_count"]
            if bucket["weight_count"]
            else None
        )
        components.append(
            {
                "label": bucket["label"],
                "score": score,
                "contribution": contribution,
                "normalized_weight": normalized_weight,
            }
        )
    components.sort(key=lambda item: abs(item.get("contribution") or item.get("score") or 0), reverse=True)
    return {"title": "分数构成概览", "components": components} if components else {}


async def _attach_score_breakdown_summaries(db_path: Path, files: list[dict[str, Any]]) -> None:
    job_ids = [file["job_id"] for file in files if file.get("job_id")]
    if not job_ids:
        return
    placeholders = ",".join("?" for _ in job_ids)
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = await db.execute_fetchall(
            f"SELECT job_id, judge_result FROM results WHERE job_id IN ({placeholders})",
            job_ids,
        )

    rows_by_job: dict[str, list[sqlite3.Row]] = {job_id: [] for job_id in job_ids}
    for row in rows:
        rows_by_job.setdefault(row["job_id"], []).append(row)
    for file in files:
        file["score_breakdown_summary"] = _score_distribution_summary_from_rows(
            rows_by_job.get(file["job_id"], [])
        )


async def list_results(
    db_path: Path,
    *,
    playthrough_id: str | None = None,
    turn: int | None = None,
    job_id: str | None = None,
    result_type: str | None = None,
    judge_state: str | None = None,
    judge_target: str | None = None,
    score: float | None = None,
    score_sort: str | None = None,
    source_filename: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    join_clause = ""
    if job_id:
        where.append("r.job_id = ?")
        params.append(job_id)
    if playthrough_id:
        where.append("r.playthrough_id = ?")
        params.append(playthrough_id)
    if turn is not None:
        where.append("r.turn = ?")
        params.append(turn)
    if result_type == "generated":
        where.append("(r.judge_result = '{}' OR r.judge_result = '[]' OR r.judge_result = 'null')")
    elif result_type == "llm_judged":
        join_clause = "JOIN jobs j ON j.job_id = r.job_id"
        where.append("j.kind = 'llm_judge_upload'")
    if judge_target:
        where.append("json_extract(r.judge_result, '$.target') = ?")
        params.append(judge_target)
    if score is not None:
        where.append("CAST(json_extract(r.judge_result, '$.judge.overall_score') AS REAL) = ?")
        params.append(score)
    if source_filename:
        join_clause = "JOIN jobs j ON j.job_id = r.job_id"
        where.append("j.source_filename LIKE ?")
        params.append(f"%{source_filename}%")
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    if result_type == "llm_judged" and score_sort in ("asc", "desc"):
        direction = "ASC" if score_sort == "asc" else "DESC"
        order_clause = (
            "ORDER BY CAST(json_extract(r.judge_result, '$.judge.overall_score') AS REAL) "
            f"{direction}, r.playthrough_id ASC, r.turn ASC, r.id ASC"
        )
    else:
        order_clause = (
            "ORDER BY r.playthrough_id ASC, r.turn ASC, r.id ASC"
            if result_type == "llm_judged"
            else "ORDER BY r.created_at DESC, r.id DESC"
        )

    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row

        count_row = await db.execute_fetchall(
            f"SELECT COUNT(*) FROM results r {join_clause} {where_clause}", params
        )
        total = count_row[0][0]
        if page_size == 0:
            page_rows = await db.execute_fetchall(
                f"SELECT r.* FROM results r {join_clause} {where_clause} {order_clause}",
                params,
            )
        else:
            offset = (page - 1) * page_size
            page_rows = await db.execute_fetchall(
                f"SELECT r.* FROM results r {join_clause} {where_clause} {order_clause} LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            )
        page_items = [_result_from_row(row) for row in page_rows]

    return {
        "items": page_items,
        "page": page,
        "page_size": page_size,
        "total": total,
    }


async def get_all_results_for_job(db_path: Path, job_id: str) -> list[dict[str, Any]]:
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM results WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        )
    return [_result_from_row(row) for row in rows]


async def get_all_raw_results_for_job(db_path: Path, job_id: str) -> list[dict[str, Any]]:
    """Return raw results without prompt display transformation."""
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM results WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        )
    results = []
    for row in rows:
        results.append(_result_payload_from_row(row, raw=True))
    return results


async def delete_job(db_path: Path, job_id: str) -> bool:
    async with _connect(db_path) as db:
        await db.execute("DELETE FROM generate_llm_io_records WHERE job_id = ?", (job_id,))
        await db.execute("DELETE FROM results WHERE job_id = ?", (job_id,))
        cursor = await db.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        await db.commit()
        return cursor.rowcount > 0


async def get_result(db_path: Path, result_id: int) -> dict[str, Any] | None:
    async with _connect(db_path) as db:
        db.row_factory = sqlite3.Row
        async with db.execute("SELECT * FROM results WHERE id = ?", (result_id,)) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return _result_from_row(row)


def _result_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return _result_payload_from_row(row, raw=False)


def _result_payload_from_row(row: sqlite3.Row, *, raw: bool = False) -> dict[str, Any]:
    payload = dict(row)
    if "input" not in payload and "output1" in payload:
        payload["input"] = payload.pop("output1")
    raw_judge_result = payload.get("judge_result")
    judge_result = _normalize_judge_result(_json_loads(raw_judge_result, {}))
    payload["judge_result"] = judge_result
    if raw:
        return payload
    return payload


def _normalize_judge_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    result = dict(value)
    result.pop("judge_prompt", None)
    return result
