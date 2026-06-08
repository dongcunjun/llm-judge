from __future__ import annotations

import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
ENV_PATH = PROJECT_ROOT / ".env"


def _load_local_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


def _optional_int_env(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc


_load_local_env(ENV_PATH)

DATABASE_PATH = Path(os.getenv("JUDGE_SYSTEM_DB", PROJECT_ROOT / "judge_system.db"))
UPLOADS_DIR = PROJECT_ROOT / "uploads"

DEFAULT_UPLOAD_JUDGE_VERSION_ID = _optional_int_env("LLM_JUDGE_UPLOAD_JUDGE_VERSION_ID")
DEFAULT_UPLOAD_NARRATIVE_JUDGE_PROMPT_VERSION_ID = _optional_int_env(
    "LLM_JUDGE_UPLOAD_NARRATIVE_JUDGE_PROMPT_VERSION_ID"
)
DEFAULT_UPLOAD_CHOICE_JUDGE_PROMPT_VERSION_ID = _optional_int_env(
    "LLM_JUDGE_UPLOAD_CHOICE_JUDGE_PROMPT_VERSION_ID"
)
DEFAULT_UPLOAD_PROMPT_COMBINE_VERSION_ID = _optional_int_env(
    "LLM_JUDGE_UPLOAD_PROMPT_COMBINE_VERSION_ID"
)

DEFAULT_BATCH_JUDGE_VERSION_ID = _optional_int_env("LLM_JUDGE_BATCH_JUDGE_VERSION_ID")
DEFAULT_BATCH_NARRATIVE_JUDGE_PROMPT_VERSION_ID = _optional_int_env(
    "LLM_JUDGE_BATCH_NARRATIVE_JUDGE_PROMPT_VERSION_ID"
)
DEFAULT_BATCH_CHOICE_JUDGE_PROMPT_VERSION_ID = _optional_int_env(
    "LLM_JUDGE_BATCH_CHOICE_JUDGE_PROMPT_VERSION_ID"
)
DEFAULT_BATCH_PROMPT_COMBINE_VERSION_ID = _optional_int_env(
    "LLM_JUDGE_BATCH_PROMPT_COMBINE_VERSION_ID"
)

_raw_batch_retry = _optional_int_env("LLM_JUDGE_BATCH_RETRY_COUNT")
DEFAULT_BATCH_JUDGE_RETRY_COUNT = _raw_batch_retry if _raw_batch_retry is not None else 2

DEFAULT_LLM_JUDGE_PROMPT = """你是一名严谨的 LLM-as-Judge 裁判。
请根据【输入】和【输出】评估输出质量，并只返回 JSON。

# 输入
{{prompt}}

# 输出
{{output}}

【强制输出要求】
请严格返回合法 JSON，不要包含 Markdown 代码块或额外解释。

{
    "overall_score": 0,
    "overall_evidence": "请简要说明评分理由"
}
"""

DEFAULT_JUDGE_CONCURRENCY = _optional_int_env("JUDGE_CONCURRENCY") or 50

DEFAULT_PROMPT_COMBINE_SYSTEM_TEMPLATE = "{{system}}\\n"
DEFAULT_PROMPT_COMBINE_USER_TEMPLATE = "玩家：{{user}}\\n"
DEFAULT_PROMPT_COMBINE_ASSISTANT_TEMPLATE = "剧情：{{assistant}}\\n"
DEFAULT_NARRATIVE_OUTPUT_TEMPLATE = "{{text}}"
DEFAULT_CHOICE_OPTION_TEMPLATE = "{{index}}. {{text}}（{{reason}}）"
