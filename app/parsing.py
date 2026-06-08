from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.schemas import JudgePromptVersion, PromptCombineTemplates, ScoreConfig
from app.settings import (
    DEFAULT_NARRATIVE_OUTPUT_TEMPLATE,
    DEFAULT_PROMPT_COMBINE_ASSISTANT_TEMPLATE,
    DEFAULT_PROMPT_COMBINE_SYSTEM_TEMPLATE,
    DEFAULT_PROMPT_COMBINE_USER_TEMPLATE,
)


@dataclass(frozen=True)
class LlmJudgeJsonlItem:
    line_number: int
    playthrough_id: str
    turn: int
    prompt: str
    raw_payload: dict[str, Any]
    judge_target: str = ""
    score_config: ScoreConfig | None = None


class JsonlParseError(ValueError):
    """Raised when an uploaded jsonl file contains invalid records."""


def parse_llm_judge_jsonl(
    content: bytes,
    judge_prompt: JudgePromptVersion,
    choice_judge_prompt: JudgePromptVersion | None = None,
    prompt_combine_templates: PromptCombineTemplates | None = None,
) -> list[LlmJudgeJsonlItem]:
    return _parse_llm_judge_record_pairs(
        _load_jsonl_records(content),
        judge_prompt,
        choice_judge_prompt,
        prompt_combine_templates,
    )


def parse_llm_judge_records(
    records: list[Any],
    judge_prompt: JudgePromptVersion,
    choice_judge_prompt: JudgePromptVersion | None = None,
    prompt_combine_templates: PromptCombineTemplates | None = None,
) -> list[LlmJudgeJsonlItem]:
    return _parse_llm_judge_record_pairs(
        list(enumerate(records, start=1)),
        judge_prompt,
        choice_judge_prompt,
        prompt_combine_templates,
    )


def _parse_llm_judge_record_pairs(
    records: list[tuple[int, Any]],
    judge_prompt: JudgePromptVersion,
    choice_judge_prompt: JudgePromptVersion | None = None,
    prompt_combine_templates: PromptCombineTemplates | None = None,
) -> list[LlmJudgeJsonlItem]:
    templates = prompt_combine_templates or _default_prompt_combine_templates()
    judge_prompts = {
        "": judge_prompt,
        "narrative": judge_prompt,
        "choice": choice_judge_prompt or judge_prompt,
    }
    items = [
        item
        for line_number, payload in records
        for item in _validate_llm_judge_payloads(payload, line_number, judge_prompts, templates)
    ]
    if not items:
        raise JsonlParseError("文件中没有可处理的数据")
    return items


def _load_jsonl_records(content: bytes) -> list[tuple[int, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise JsonlParseError("文件必须使用 UTF-8 编码") from exc

    records: list[tuple[int, Any]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JsonlParseError(f"第 {line_number} 行不是合法 JSON: {exc.msg}") from exc
        records.append((line_number, payload))
    return records


def _validate_llm_judge_payloads(
    payload: Any,
    line_number: int,
    judge_prompts: dict[str, JudgePromptVersion],
    prompt_combine_templates: PromptCombineTemplates,
) -> list[LlmJudgeJsonlItem]:
    if not isinstance(payload, dict):
        raise JsonlParseError(f"第 {line_number} 行必须是 JSON 对象")

    playthrough_id, turn = _validate_identity_payload(payload, line_number)
    section_items = _llm_judge_section_items(
        payload, line_number, prompt_combine_templates, judge_prompts
    )
    if section_items:
        return [
            _build_llm_judge_item(
                payload={**payload, **section_payload},
                line_number=line_number,
                playthrough_id=playthrough_id,
                turn=turn,
                judge_prompt=judge_prompts.get(target) or judge_prompts[""],
                judge_target=target,
            )
            for target, section_payload in section_items
        ]

    flat_target = str(payload.get("judge_target") or payload.get("target") or "")
    judge_prompt = judge_prompts.get(flat_target) or judge_prompts[""]
    payload = _format_flat_input(payload, prompt_combine_templates, line_number)
    payload = _format_flat_output(payload, prompt_combine_templates, flat_target)
    return [
        _build_llm_judge_item(
            payload=payload,
            line_number=line_number,
            playthrough_id=playthrough_id,
            turn=turn,
            judge_prompt=judge_prompt,
            judge_target=flat_target,
        )
    ]


def _format_flat_input(
    payload: dict[str, Any],
    templates: PromptCombineTemplates,
    line_number: int,
) -> dict[str, Any]:
    """Render a flat-style item's dict input via the prompt-combine templates.

    Section-style items run ``narrative.input`` / ``choice.input`` through
    ``_llm_judge_input_to_text`` (which expands a ``{system, messages}`` dict into
    combined dialog text) before reaching ``_build_llm_judge_item``.  Flat items are
    formatted here so a dict input becomes the same dialog text instead of being
    JSON-serialised.  Only the explicit top-level ``input`` key is formatted, and
    only when its value is a dict — other mapped fields are passed through.
    """
    input_key = "input"
    if input_key not in payload or not isinstance(payload[input_key], dict):
        return payload
    formatted = _llm_judge_input_to_text(
        payload[input_key],
        line_number=line_number,
        field_name=input_key,
        templates=templates,
    )
    payload = dict(payload)
    payload[input_key] = formatted
    return payload


def _format_flat_output(
    payload: dict[str, Any],
    templates: PromptCombineTemplates,
    flat_target: str,
) -> dict[str, Any]:
    """Render a flat-style item's output via the prompt-combine templates.

    Section-style items run their output through ``_llm_judge_output_to_text`` in
    ``_llm_judge_section_items`` before reaching ``_build_llm_judge_item`` — choice
    output via ``choice_option_template`` and narrative output via
    ``narrative_output_template``.  Flat items are formatted here so both paths
    agree.  A ``choice`` target uses the choice template; any other target
    (including the empty default that maps to the narrative prompt) uses the
    narrative template.  Only the explicit top-level ``output`` key is formatted —
    other mapped fields (e.g. ``meta``) are passed through untouched.
    """
    output_target = "choice" if flat_target == "choice" else "narrative"
    output_key = "output"
    if output_key not in payload or payload[output_key] is None:
        return payload
    formatted = _llm_judge_output_to_text(
        payload[output_key],
        target=output_target,
        templates=templates,
    )
    payload = dict(payload)
    payload[output_key] = formatted
    return payload


def _llm_judge_section_items(
    payload: dict[str, Any],
    line_number: int,
    prompt_combine_templates: PromptCombineTemplates,
    judge_prompts: dict[str, JudgePromptVersion],
) -> list[tuple[str, dict[str, Any]]]:
    sections: list[tuple[str, dict[str, Any]]] = []
    has_section_key = any(key in payload for key in ("narrative", "choice"))
    for target in ("narrative", "choice"):
        section = payload.get(target)
        if section is None or section == {}:
            continue
        if not isinstance(section, dict):
            raise JsonlParseError(f"第 {line_number} 行 {target} 必须是对象")
        judge_prompt = judge_prompts.get(target) or judge_prompts[""]
        if not judge_prompt.fields:
            # No placeholder mapping configured for this target — nothing to take
            # from the section, so skip it.
            continue

        # Section keys now follow the placeholder mapping: each mapped
        # ``source_field`` is read from the section dict, validated as non-empty,
        # formatted, and flattened to the top level so _build_llm_judge_item can
        # take it by source_field.  ``input`` keeps its dialog-template semantics
        # and ``output`` its narrative/choice-template semantics; any other field
        # (including ``prompt``) is taken verbatim.
        flat: dict[str, Any] = {}
        for field in judge_prompt.fields:
            source_field = field.source_field
            if source_field not in section:
                raise JsonlParseError(
                    f"第 {line_number} 行 {target}.{source_field} 缺失"
                )
            value = section[source_field]
            if value is None:
                raise JsonlParseError(
                    f"第 {line_number} 行 {target}.{source_field} 不能为空"
                )
            if source_field == "input":
                formatted = _llm_judge_input_to_text(
                    value,
                    line_number=line_number,
                    field_name=f"{target}.{source_field}",
                    templates=prompt_combine_templates,
                )
            elif source_field == "output":
                formatted = _llm_judge_output_to_text(
                    value,
                    target=target,
                    templates=prompt_combine_templates,
                )
            else:
                formatted = _value_to_text(value)
            if not formatted.strip():
                raise JsonlParseError(
                    f"第 {line_number} 行 {target}.{source_field} 不能为空"
                )
            flat[source_field] = formatted

        sections.append((target, {target: {**section, **flat}, **flat}))
    if has_section_key and not sections:
        raise JsonlParseError(f"第 {line_number} 行 narrative 或 choice 至少一个不能为空")
    return sections


def _llm_judge_input_to_text(
    value: Any,
    *,
    line_number: int,
    field_name: str,
    templates: PromptCombineTemplates,
) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _dialog_input_to_prompt(
            value,
            line_number=line_number,
            field_name=field_name,
            templates=templates,
        )
    raise JsonlParseError(f"第 {line_number} 行 {field_name} 必须是字符串或对象")


def _dialog_input_to_prompt(
    value: dict[str, Any],
    *,
    line_number: int,
    field_name: str,
    templates: PromptCombineTemplates,
) -> str:
    system = value.get("system")
    if not isinstance(system, str):
        raise JsonlParseError(f"第 {line_number} 行 {field_name}.system 必须是字符串")
    messages = value.get("messages")
    if not isinstance(messages, list):
        raise JsonlParseError(f"第 {line_number} 行 {field_name}.messages 必须是数组")

    prompt_parts = [
        render_prompt_combine_template(templates.system_template, {"system": system})
    ]
    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            raise JsonlParseError(
                f"第 {line_number} 行 {field_name}.messages 第 {index} 项必须是对象"
            )
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, str):
            raise JsonlParseError(
                f"第 {line_number} 行 {field_name}.messages 第 {index} 项 content 必须是字符串"
            )
        if role == "user":
            prompt_parts.append(
                render_prompt_combine_template(templates.user_template, {"user": content})
            )
        elif role == "assistant":
            prompt_parts.append(
                render_prompt_combine_template(templates.assistant_template, {"assistant": content})
            )
        else:
            raise JsonlParseError(
                f"第 {line_number} 行 {field_name}.messages 第 {index} 项 role 必须是 user 或 assistant"
            )
    return "".join(prompt_parts)


def _llm_judge_output_to_text(
    value: Any,
    *,
    target: str,
    templates: PromptCombineTemplates,
) -> str:
    if target == "narrative":
        return render_prompt_combine_template(
            templates.narrative_output_template,
            {"text": _value_to_text(value)},
        )
    if target == "choice":
        formatted = _format_choice_options(value, templates.choice_option_template)
        if formatted is not None:
            return formatted
    return _value_to_text(value)


def _format_choice_options(value: Any, option_template: str) -> str | None:
    parsed = value
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return None

    options = parsed
    if isinstance(parsed, dict):
        options = parsed.get("options")
    if not isinstance(options, list):
        return None

    parts: list[str] = []
    for index, option in enumerate(options, start=1):
        if isinstance(option, dict):
            text = option.get("text", "")
            reason = option.get("reason", "")
        elif isinstance(option, str):
            text = option
            reason = ""
        else:
            text = _value_to_text(option)
            reason = ""
        parts.append(
            render_prompt_combine_template(
                option_template,
                {
                    "index": str(index),
                    "text": _value_to_text(text) if not isinstance(text, str) else text,
                    "reason": _value_to_text(reason) if not isinstance(reason, str) else reason,
                },
            )
        )
    return "".join(parts)


def _build_llm_judge_item(
    *,
    payload: dict[str, Any],
    line_number: int,
    playthrough_id: str,
    turn: int,
    judge_prompt: JudgePromptVersion,
    judge_target: str,
) -> LlmJudgeJsonlItem:
    variables: dict[str, str] = {}
    for field in judge_prompt.fields:
        if field.source_field in payload:
            value = payload[field.source_field]
        elif field.source_field == "prompt" and "input" in payload:
            value = payload["input"]
        else:
            raise JsonlParseError(
                f"第 {line_number} 行缺少 Judge Prompt 字段 {field.source_field}"
            )
        if value is None:
            raise JsonlParseError(
                f"第 {line_number} 行 Judge Prompt 字段 {field.source_field} 不能为 null"
            )
        variables[field.placeholder] = _value_to_text(value)

    # Normalise raw_payload so "input" / "output" keys always exist for the
    # frontend detail panel (Input / Output tabs).  Section-style items already
    # carry these keys; flat items get them from the first and last template
    # source fields respectively.
    payload = dict(payload)
    payload.pop("judge_target", None)
    payload.pop("target", None)
    source_fields = [f.source_field for f in judge_prompt.fields]
    if "input" not in payload and source_fields:
        first_key = source_fields[0]
        if first_key in payload:
            payload["input"] = _value_to_text(payload[first_key])
    if "output" not in payload and len(source_fields) >= 2:
        last_key = source_fields[-1]
        if last_key in payload:
            payload["output"] = _value_to_text(payload[last_key])

    prompt = render_prompt_template(judge_prompt.template, variables)
    return LlmJudgeJsonlItem(
        line_number=line_number,
        playthrough_id=playthrough_id,
        turn=turn,
        prompt=prompt,
        raw_payload=payload,
        judge_target=judge_target,
        score_config=judge_prompt.score_config,
    )


def _validate_identity_payload(payload: dict[str, Any], line_number: int) -> tuple[str, int]:
    playthrough_id = payload.get("playthrough_id")
    turn = payload.get("turn")

    if playthrough_id is not None and not isinstance(playthrough_id, str):
        raise JsonlParseError(f"第 {line_number} 行 playthrough_id 必须是字符串")
    if turn is not None and (not isinstance(turn, int) or isinstance(turn, bool)):
        raise JsonlParseError(f"第 {line_number} 行 turn 必须是整数")

    pid = playthrough_id.strip() if isinstance(playthrough_id, str) and playthrough_id.strip() else ""
    t = turn if isinstance(turn, int) and not isinstance(turn, bool) else 0
    return pid, t


def render_prompt_combine_template(template: str, variables: dict[str, str]) -> str:
    return render_prompt_template(template, variables)


def render_prompt_template(template: str, variables: dict[str, str]) -> str:
    rendered = template.replace("\\n", "\n")
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
        rendered = rendered.replace(f"{{{{ {key} }}}}", value)
    return rendered


def _value_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _format_choices_text(choices: Any, option_template: str | None = None) -> str:
    if option_template:
        formatted = _format_choice_options(choices, option_template)
        if formatted is not None:
            return formatted

    parsed = choices
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return choices

    if not isinstance(parsed, dict):
        return _value_to_text(choices)

    options = parsed.get("options")
    if not isinstance(options, list):
        return ""

    parts = []
    for opt in options:
        if isinstance(opt, dict):
            text = opt.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif isinstance(opt, str) and opt:
            parts.append(opt)

    return "；".join(parts) if parts else _value_to_text(choices)


def _default_prompt_combine_templates() -> PromptCombineTemplates:
    return PromptCombineTemplates(
        system_template=DEFAULT_PROMPT_COMBINE_SYSTEM_TEMPLATE,
        user_template=DEFAULT_PROMPT_COMBINE_USER_TEMPLATE,
        assistant_template=DEFAULT_PROMPT_COMBINE_ASSISTANT_TEMPLATE,
        narrative_output_template=DEFAULT_NARRATIVE_OUTPUT_TEMPLATE,
    )
