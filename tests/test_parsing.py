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


def test_parse_llm_judge_jsonl_expands_narrative_and_choice_sections():
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


def test_parse_llm_judge_jsonl_formats_narrative_output_text():
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


def test_parse_llm_judge_jsonl_accepts_single_non_empty_section():
    prompt = JudgePromptVersion(
        id=1,
        name="p",
        template="{{prompt}}|{{output}}",
        fields=[
            JudgePromptField(placeholder="prompt", source_field="prompt"),
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
