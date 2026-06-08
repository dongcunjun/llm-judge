from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = 60.0
    thinking: bool = False
    force_json_only: bool = False
    temperature: float | None = None
    top_k: int | None = None
    top_p: float | None = None


class AppConfig(BaseModel):
    generate: LLMConfig = Field(default_factory=LLMConfig)
    judge: LLMConfig = Field(default_factory=LLMConfig)


class LLMConfigVersionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    config: LLMConfig = Field(default_factory=LLMConfig)
    inherit_api_key_from_id: int | None = None


class LLMConfigVersion(LLMConfigVersionCreate):
    id: int
    is_active: bool
    created_at: str
    updated_at: str


class RuntimeSettings(BaseModel):
    judge_concurrency: int = Field(default=50, ge=1, le=500)


class PromptCombineTemplates(BaseModel):
    system_template: str = Field(..., min_length=1)
    user_template: str = Field(..., min_length=1)
    assistant_template: str = Field(..., min_length=1)
    narrative_output_template: str = Field(default="{{text}}", min_length=1)
    choice_option_template: str = Field(default="{{index}}. {{text}}（{{reason}}）", min_length=1)


class PromptCombineTemplateVersionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    system_template: str = Field(..., min_length=1)
    user_template: str = Field(..., min_length=1)
    assistant_template: str = Field(..., min_length=1)
    narrative_output_template: str = Field(default="{{text}}", min_length=1)
    choice_option_template: str = Field(default="{{index}}. {{text}}（{{reason}}）", min_length=1)


class PromptCombineTemplateVersion(PromptCombineTemplateVersionCreate):
    id: int
    is_active: bool
    created_at: str
    updated_at: str


class JudgePromptField(BaseModel):
    placeholder: str = Field(..., min_length=1, max_length=120)
    source_field: str = Field(..., min_length=1, max_length=120)


class ScoreComponent(BaseModel):
    path: str = Field(..., min_length=1, max_length=200)
    weight: float = Field(..., ge=0)


class ScoreConfig(BaseModel):
    mode: str = Field(default="direct")
    score_path: str = Field(default="overall_score", min_length=1, max_length=200)
    evidence_path: str = Field(default="overall_evidence", min_length=1, max_length=200)
    components: list[ScoreComponent] = Field(default_factory=list)


class JudgePromptVersionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    template: str = Field(..., min_length=1)
    fields: list[JudgePromptField] = Field(default_factory=list)
    score_config: ScoreConfig | None = None


class JudgePromptVersion(JudgePromptVersionCreate):
    id: int
    is_active: bool
    target: str = "narrative"
    created_at: str
    updated_at: str


class ConfigVersionsState(BaseModel):
    judge_versions: list[LLMConfigVersion]
    prompt_combine_versions: list[PromptCombineTemplateVersion] = Field(default_factory=list)
    judge_prompt_versions: list[JudgePromptVersion] = Field(default_factory=list)
    narrative_judge_prompt_versions: list[JudgePromptVersion] = Field(default_factory=list)
    choice_judge_prompt_versions: list[JudgePromptVersion] = Field(default_factory=list)
    runtime_settings: RuntimeSettings = Field(default_factory=RuntimeSettings)
    prompt_combine_templates: PromptCombineTemplates
    active_judge_id: int | None = None
    active_prompt_combine_id: int | None = None
    active_judge_prompt_id: int | None = None
    active_narrative_judge_prompt_id: int | None = None
    active_choice_judge_prompt_id: int | None = None


class JobResponse(BaseModel):
    job_id: str
    total_rows: int
    status: str



class JudgeFileSummary(BaseModel):
    job_id: str
    filename: str
    kind: str = ""
    source_job_id: str = ""
    generate_llm_version_name: str = ""
    judge_llm_version_name: str = ""
    prompt_combine_version_name: str = ""
    judge_prompt_version_name: str = ""
    narrative_judge_prompt_version_name: str = ""
    choice_judge_prompt_version_name: str = ""
    status: str
    total_rows: int
    processed_rows: int = 0
    success_count: int
    failure_count: int
    result_count: int
    bug_count: int
    exists_count: int
    not_exists_count: int
    other_count: int
    valid_json_count: int = 0
    score_breakdown_summary: dict[str, Any] = Field(default_factory=dict)
    concurrency: int = 0
    judge_retry_count: int = 2
    created_at: str
    updated_at: str


class JobStatus(BaseModel):
    job_id: str
    status: str
    total_rows: int
    processed_rows: int
    success_count: int
    failure_count: int
    errors: list[dict[str, Any]]
    judge_llm_version_name: str = ""
    narrative_judge_prompt_version_name: str = ""
    choice_judge_prompt_version_name: str = ""
    prompt_combine_version_name: str = ""
    judge_prompt_version_name: str = ""
    created_at: str
    updated_at: str


class ResultSummary(BaseModel):
    id: int
    job_id: str
    playthrough_id: str = ""
    turn: int = 0
    prompt: str
    input: str
    output: str = ""
    judge_result: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ResultsPage(BaseModel):
    items: list[ResultSummary]
    page: int
    page_size: int
    total: int
