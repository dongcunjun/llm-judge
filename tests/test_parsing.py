import pytest

from app.parsing import JsonlParseError, parse_llm_judge_jsonl
from app.schemas import JudgePromptField, JudgePromptVersion, PromptCombineTemplates


def test_parse_llm_judge_jsonl_renders_top_level_field_mapping():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="P={{input}}\nO={{answer}}\nM={{meta}}",
        fields=[
            JudgePromptField(placeholder="input", source_field="prompt"),
            JudgePromptField(placeholder="answer", source_field="output"),
            JudgePromptField(placeholder="meta", source_field="meta"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,"prompt":"hello","output":"world","meta":{"a":1}}\n'
    ).encode()

    items = parse_llm_judge_jsonl(content, prompt)

    assert len(items) == 1
    assert items[0].playthrough_id == "abc123"
    assert items[0].turn == 5
    assert items[0].prompt == 'P=hello\nO=world\nM={"a": 1}'
    assert items[0].raw_payload["meta"] == {"a": 1}


def test_parse_llm_judge_jsonl_accepts_input_as_prompt_alias():
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
    content = b'{"playthrough_id":"abc123","turn":5,"input":"hello","output":"world"}\n'

    items = parse_llm_judge_jsonl(content, prompt)

    assert items[0].prompt == "P=hello\nO=world"
    assert items[0].raw_payload["input"] == "hello"


def test_parse_llm_judge_jsonl_flat_target_routes_to_choice_prompt():
    narrative_prompt = JudgePromptVersion(
        id=1,
        name="narrative",
        template="N={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    choice_prompt = JudgePromptVersion(
        id=2,
        name="choice",
        template="C={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        target="choice",
        created_at="",
        updated_at="",
    )
    content = b'{"input":"\xe9\x80\x89\xe9\xa1\xb9 prompt","output":"\xe5\xbd\x93\xe5\x89\x8d\xe9\x80\x89\xe9\xa1\xb9","target":"choice"}\n'

    items = parse_llm_judge_jsonl(content, narrative_prompt, choice_prompt)

    assert len(items) == 1
    assert items[0].judge_target == "choice"
    # Routed to the choice prompt template ("C=..."), not the narrative one.
    assert items[0].prompt == "C=选项 prompt\nO=当前选项"


def test_parse_llm_judge_jsonl_flat_without_target_uses_narrative_prompt():
    narrative_prompt = JudgePromptVersion(
        id=1,
        name="narrative",
        template="N={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    choice_prompt = JudgePromptVersion(
        id=2,
        name="choice",
        template="C={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        target="choice",
        created_at="",
        updated_at="",
    )
    content = b'{"input":"hello","output":"world"}\n'

    items = parse_llm_judge_jsonl(content, narrative_prompt, choice_prompt)

    assert items[0].judge_target == ""
    assert items[0].prompt == "N=hello\nO=world"


def test_parse_llm_judge_jsonl_expands_narrative_and_choice_sections():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="P={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="input"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"input":"正文 prompt","output":"当前剧情"},'
        '"choice":{"input":"选项 prompt","output":"当前选项"}}\n'
    ).encode()

    items = parse_llm_judge_jsonl(content, prompt)

    assert [item.judge_target for item in items] == ["narrative", "choice"]
    assert items[0].prompt == "P=正文 prompt\nO=当前剧情"
    assert items[1].prompt == "P=选项 prompt\nO=当前选项"
    assert "target" not in items[0].raw_payload
    assert "target" not in items[1].raw_payload


def test_parse_llm_judge_jsonl_combines_dialog_input_objects():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="P={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="input"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    templates = PromptCombineTemplates(
        system_template="S:{{system}}\n",
        user_template="U:{{user}}\n",
        assistant_template="A:{{assistant}}\n",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"input":{"system":"系统","messages":['
        '{"role":"user","content":"你好"},'
        '{"role":"assistant","content":"剧情"}'
        ']},"output":"当前剧情"}}\n'
    ).encode()

    items = parse_llm_judge_jsonl(content, prompt, prompt_combine_templates=templates)

    assert len(items) == 1
    assert items[0].prompt == "P=S:系统\nU:你好\nA:剧情\n\nO=当前剧情"
    assert items[0].raw_payload["input"] == "S:系统\nU:你好\nA:剧情\n"


def test_parse_llm_judge_jsonl_formats_flat_dict_input():
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
    templates = PromptCombineTemplates(
        system_template="S:{{system}}\n",
        user_template="U:{{user}}\n",
        assistant_template="A:{{assistant}}\n",
    )
    content = (
        '{"input":{"system":"系统","messages":['
        '{"role":"user","content":"你好"},'
        '{"role":"assistant","content":"剧情"}'
        ']},"output":"当前剧情"}\n'
    ).encode()

    items = parse_llm_judge_jsonl(content, prompt, prompt_combine_templates=templates)

    assert len(items) == 1
    # Flat dict input expanded via combine templates, same as section-style.
    assert items[0].prompt == "P=S:系统\nU:你好\nA:剧情\n\nO=当前剧情"
    assert items[0].raw_payload["input"] == "S:系统\nU:你好\nA:剧情\n"


def test_parse_llm_judge_jsonl_formats_flat_narrative_output_text():
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
    templates = PromptCombineTemplates(
        system_template="S:{{system}}\n",
        user_template="U:{{user}}\n",
        assistant_template="A:{{assistant}}\n",
        narrative_output_template="<story>{{text}}</story>",
    )
    content = b'{"input":"\xe6\xad\xa3\xe6\x96\x87 prompt","output":"\xe5\xbd\x93\xe5\x89\x8d\xe5\x89\xa7\xe6\x83\x85"}\n'

    items = parse_llm_judge_jsonl(content, prompt, prompt_combine_templates=templates)

    assert len(items) == 1
    # Flat narrative output wrapped by narrative_output_template, same as section.
    assert items[0].prompt == "P=正文 prompt\nO=<story>当前剧情</story>"
    assert items[0].raw_payload["output"] == "<story>当前剧情</story>"


def test_parse_llm_judge_jsonl_formats_narrative_output_text():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="P={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="input"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    templates = PromptCombineTemplates(
        system_template="S:{{system}}\n",
        user_template="U:{{user}}\n",
        assistant_template="A:{{assistant}}\n",
        narrative_output_template="<story>{{text}}</story>",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"input":"正文 prompt","output":"当前剧情"}}\n'
    ).encode()

    items = parse_llm_judge_jsonl(content, prompt, prompt_combine_templates=templates)

    assert len(items) == 1
    assert items[0].prompt == "P=正文 prompt\nO=<story>当前剧情</story>"
    assert items[0].raw_payload["output"] == "<story>当前剧情</story>"


def test_parse_llm_judge_jsonl_formats_choice_output_options():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="P={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="input"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    templates = PromptCombineTemplates(
        system_template="S:{{system}}\n",
        user_template="U:{{user}}\n",
        assistant_template="A:{{assistant}}\n",
        choice_option_template="[{{index}}]{{text}}/{{reason}}\n",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"choice":{"input":"选项 prompt","output":{"options":['
        '{"text":"继续","reason":"推进剧情"},'
        '{"text":"询问","reason":"获得线索"}'
        ']}}}\n'
    ).encode()

    items = parse_llm_judge_jsonl(content, prompt, prompt_combine_templates=templates)

    assert len(items) == 1
    assert items[0].prompt == "P=选项 prompt\nO=[1]继续/推进剧情\n[2]询问/获得线索\n"
    assert items[0].raw_payload["output"] == "[1]继续/推进剧情\n[2]询问/获得线索\n"


def test_parse_llm_judge_jsonl_formats_flat_choice_output_options():
    narrative_prompt = JudgePromptVersion(
        id=1,
        name="narrative",
        template="N={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    choice_prompt = JudgePromptVersion(
        id=2,
        name="choice",
        template="C={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        target="choice",
        created_at="",
        updated_at="",
    )
    templates = PromptCombineTemplates(
        system_template="S:{{system}}\n",
        user_template="U:{{user}}\n",
        assistant_template="A:{{assistant}}\n",
        choice_option_template="[{{index}}]{{text}}/{{reason}}\n",
    )
    content = (
        '{"input":"选项 prompt","target":"choice","output":{"options":['
        '{"text":"继续","reason":"推进剧情"},'
        '{"text":"询问","reason":"获得线索"}'
        ']}}\n'
    ).encode()

    items = parse_llm_judge_jsonl(
        content, narrative_prompt, choice_prompt, prompt_combine_templates=templates
    )

    assert len(items) == 1
    assert items[0].judge_target == "choice"
    # Routed to the choice prompt AND output rendered via the option template.
    assert items[0].prompt == "C=选项 prompt\nO=[1]继续/推进剧情\n[2]询问/获得线索\n"
    assert items[0].raw_payload["output"] == "[1]继续/推进剧情\n[2]询问/获得线索\n"


def test_parse_llm_judge_jsonl_accepts_single_non_empty_section():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="{{prompt}}|{{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="input"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{},'
        '"choice":{"input":"选项 prompt","output":"当前选项"}}\n'
    ).encode()

    items = parse_llm_judge_jsonl(content, prompt)

    assert len(items) == 1
    assert items[0].judge_target == "choice"
    assert items[0].prompt == "选项 prompt|当前选项"


def test_parse_llm_judge_jsonl_rejects_missing_mapped_field():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="{{input}}",
        fields=[JudgePromptField(placeholder="input", source_field="prompt")],
        is_active=True,
        created_at="",
        updated_at="",
    )

    with pytest.raises(JsonlParseError, match="prompt"):
        parse_llm_judge_jsonl(b'{"playthrough_id":"abc123","turn":5}\n', prompt)


def test_parse_llm_judge_jsonl_defaults_missing_playthrough_id_and_turn():
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
    content = b'{"prompt":"hello","output":"world"}\n'

    items = parse_llm_judge_jsonl(content, prompt)

    assert len(items) == 1
    assert items[0].playthrough_id == ""
    assert items[0].turn == 0
    assert items[0].prompt == "P=hello\nO=world"


def test_parse_llm_judge_jsonl_accepts_null_playthrough_id_and_turn():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="{{prompt}}",
        fields=[JudgePromptField(placeholder="prompt", source_field="prompt")],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = b'{"playthrough_id":null,"turn":null,"prompt":"hello"}\n'

    items = parse_llm_judge_jsonl(content, prompt)

    assert len(items) == 1
    assert items[0].playthrough_id == ""
    assert items[0].turn == 0


def test_parse_llm_judge_jsonl_accepts_empty_playthrough_id():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="{{prompt}}",
        fields=[JudgePromptField(placeholder="prompt", source_field="prompt")],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = b'{"playthrough_id":"  ","turn":3,"prompt":"hello"}\n'

    items = parse_llm_judge_jsonl(content, prompt)

    assert len(items) == 1
    assert items[0].playthrough_id == ""
    assert items[0].turn == 3


def test_parse_llm_judge_jsonl_rejects_invalid_playthrough_id_type():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="{{prompt}}",
        fields=[JudgePromptField(placeholder="prompt", source_field="prompt")],
        is_active=True,
        created_at="",
        updated_at="",
    )

    with pytest.raises(JsonlParseError, match="playthrough_id 必须是字符串"):
        parse_llm_judge_jsonl(b'{"playthrough_id":123,"turn":5,"prompt":"hello"}\n', prompt)


def test_parse_llm_judge_jsonl_rejects_invalid_turn_type():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="{{prompt}}",
        fields=[JudgePromptField(placeholder="prompt", source_field="prompt")],
        is_active=True,
        created_at="",
        updated_at="",
    )

    with pytest.raises(JsonlParseError, match="turn 必须是整数"):
        parse_llm_judge_jsonl(b'{"playthrough_id":"abc","turn":"5","prompt":"hello"}\n', prompt)


def test_parse_llm_judge_jsonl_section_unmapped_output_not_required():
    # Judge Prompt maps only the input field, not output.  A section that omits
    # output entirely is accepted because only mapped fields are validated.
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="P={{prompt}}",
        fields=[JudgePromptField(placeholder="prompt", source_field="input")],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"input":"正文 prompt"}}\n'
    ).encode()

    items = parse_llm_judge_jsonl(content, prompt)

    assert len(items) == 1
    assert items[0].prompt == "P=正文 prompt"


def test_parse_llm_judge_jsonl_section_null_mapped_output_rejected():
    # When the Judge Prompt maps the output field, a null section output is
    # rejected before flattening.
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="P={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="input"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"input":"正文 prompt","output":null}}\n'
    ).encode()

    with pytest.raises(JsonlParseError, match="narrative.output 不能为空"):
        parse_llm_judge_jsonl(content, prompt)


def test_parse_llm_judge_jsonl_section_uses_custom_mapped_keys():
    # Section keys follow the placeholder mapping: non input/output names are
    # taken verbatim from the section dict.
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="C={{ctx}}\nR={{reply}}",
        fields=[
            JudgePromptField(placeholder="ctx", source_field="ctx"),
            JudgePromptField(placeholder="reply", source_field="reply"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"ctx":"场景","reply":"回应"}}\n'
    ).encode()

    items = parse_llm_judge_jsonl(content, prompt)

    assert len(items) == 1
    assert items[0].judge_target == "narrative"
    assert items[0].prompt == "C=场景\nR=回应"


def test_parse_llm_judge_jsonl_section_input_dict_still_uses_dialog_template():
    # source_field == "input" keeps dialog-template formatting even with custom
    # output key names alongside.
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="P={{prompt}}\nO={{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="input"),
            JudgePromptField(placeholder="output", source_field="output"),
        ],
        is_active=True,
        created_at="",
        updated_at="",
    )
    templates = PromptCombineTemplates(
        system_template="S:{{system}}\n",
        user_template="U:{{user}}\n",
        assistant_template="A:{{assistant}}\n",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"input":{"system":"系统","messages":['
        '{"role":"user","content":"你好"}'
        ']},"output":"当前剧情"}}\n'
    ).encode()

    items = parse_llm_judge_jsonl(content, prompt, prompt_combine_templates=templates)

    assert len(items) == 1
    assert items[0].prompt == "P=S:系统\nU:你好\n\nO=当前剧情"


def test_parse_llm_judge_jsonl_section_rejects_missing_mapped_field():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="C={{ctx}}",
        fields=[JudgePromptField(placeholder="ctx", source_field="ctx")],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"input":"正文"}}\n'
    ).encode()

    with pytest.raises(JsonlParseError, match="narrative.ctx 缺失"):
        parse_llm_judge_jsonl(content, prompt)


def test_parse_llm_judge_jsonl_section_rejects_null_mapped_field():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="C={{ctx}}",
        fields=[JudgePromptField(placeholder="ctx", source_field="ctx")],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"ctx":null}}\n'
    ).encode()

    with pytest.raises(JsonlParseError, match="narrative.ctx 不能为空"):
        parse_llm_judge_jsonl(content, prompt)


def test_parse_llm_judge_jsonl_section_rejects_blank_mapped_field():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="C={{ctx}}",
        fields=[JudgePromptField(placeholder="ctx", source_field="ctx")],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"ctx":"   "}}\n'
    ).encode()

    with pytest.raises(JsonlParseError, match="narrative.ctx 不能为空"):
        parse_llm_judge_jsonl(content, prompt)


def test_parse_llm_judge_jsonl_section_without_fields_is_skipped():
    # A Judge Prompt with no fields produces no item for the section.  With a
    # single section present, this triggers the "at least one non-empty" error.
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="static",
        fields=[],
        is_active=True,
        created_at="",
        updated_at="",
    )
    content = (
        '{"playthrough_id":"abc123","turn":5,'
        '"narrative":{"input":"正文","output":"剧情"}}\n'
    ).encode()

    with pytest.raises(JsonlParseError, match="narrative 或 choice 至少一个不能为空"):
        parse_llm_judge_jsonl(content, prompt)
