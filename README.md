# Judge llm response

一个基于 FastAPI 的 LLM 回复裁判系统。通过 Batch API 提交 JSON 数组进行异步裁判。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 启动

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8322
```

### Web 页面

| 路径 | 说明 |
| --- | --- |
| `/` | 自动跳转到 `/config` |
| `/llm-judge` | 自动跳转到 `/config` |
| `/llm-judge/batch-test` | Batch API 测试页 — 在线编辑 JSON、提交批量和查看结果 |
| `/config` | 配置管理页 — 管理 Judge LLM API 版本、Judge Prompt 版本、历史轮次拼接模版、并发设置 |

数据库首次启动会自动写入默认配置版本。

### 健康检查

```bash
curl 'http://127.0.0.1:8322/api/health'
```

返回：

```json
{"status": "ok"}
```

用于存活探针/负载均衡健康检查，不依赖数据库或 LLM 配置。

---

## 环境变量

在项目根目录创建 `.env` 文件（可参考 `.env.example`），服务启动时自动加载。系统环境变量优先级高于 `.env` 文件。

### 数据库路径

```bash
JUDGE_SYSTEM_DB=/path/to/judge_system.db
```

默认值：项目根目录下的 `judge_system.db`。

### 并发控制

```bash
# 裁判任务的并行数量（默认 50，范围 1-500）
JUDGE_CONCURRENCY=50
```

也可通过 API 运行时修改（见"并发控制"章节）。

### Batch / Upload API 默认参数

不传请求参数时，两个提交端点分别从以下环境变量取默认版本 ID。

```bash
# Batch 端点（/api/llm-judge/batch）默认值
LLM_JUDGE_BATCH_JUDGE_VERSION_ID=1
LLM_JUDGE_BATCH_NARRATIVE_JUDGE_PROMPT_VERSION_ID=1
LLM_JUDGE_BATCH_CHOICE_JUDGE_PROMPT_VERSION_ID=2
LLM_JUDGE_BATCH_PROMPT_COMBINE_VERSION_ID=1

# 裁判结果非 JSON 时的重试次数（默认 2，范围 0-10）
LLM_JUDGE_BATCH_RETRY_COUNT=2

# Upload 端点（/api/llm-judge/upload）默认值
LLM_JUDGE_UPLOAD_JUDGE_VERSION_ID=1
LLM_JUDGE_UPLOAD_NARRATIVE_JUDGE_PROMPT_VERSION_ID=1
LLM_JUDGE_UPLOAD_CHOICE_JUDGE_PROMPT_VERSION_ID=2
LLM_JUDGE_UPLOAD_PROMPT_COMBINE_VERSION_ID=1
```

### 优先级（所有参数通用）

```text
请求参数（query） > .env 配置 > Web 页面"设为默认"的 active 版本
```

---

## 输入格式

每条记录包含以下可选字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `playthrough_id` | string | （可选）剧情/回放 ID，缺省为 `""` |
| `turn` | int | （可选）轮次编号，缺省为 `0` |

裁判内容支持两种结构：**分段式**（section）和**扁平式**（flat）。

### 分段式（section）

每条记录包含 `narrative`（正文）和/或 `choice`（选项）分段，每个分段是一个对象。系统会按对应 target 的 Judge Prompt 分别裁判。

```json
{
  "narrative": {"input": "...", "output": "..."},
  "choice": {"input": "...", "output": "..."}
}
```

**分段内部的键名跟随该 target 的 Judge Prompt 占位符映射配置**（`fields` 中的 `source_field`）。也就是说，分段里需要哪些键，由 Judge Prompt 的字段映射决定，而非写死。其中：

- `source_field == "input"` 的字段：值为 dict（含 `system` + `messages`）时按拼接模版渲染为对话 prompt，字符串则原样使用。
- `source_field == "output"` 的字段：按 target 走输出拼接模版（narrative 用 `narrative_output_template`，choice 用 `choice_option_template`）。
- 其余 `source_field`：原样取值填入占位符。

常用约定即 `input` / `output` 两个键（见下方 input/output 格式说明）。

### 扁平式（flat）

记录顶层直接放 `input` / `output`，通过 `target`（或 `judge_target`）字段指定裁判类型：

```json
{"input": "...", "output": "...", "target": "choice"}
```

- `target` 为 `choice` → 路由到选项 Judge Prompt，`output` 走选项拼接模版。
- `target` 为 `narrative` 或缺省 → 路由到正文 Judge Prompt，`output` 走剧情拼接模版。
- `input` 为 dict 时同样走对话拼接模版渲染。

> 分段式与扁平式行为一致：按 target 选择 Judge Prompt、`input` 走对话模版、`output` 走 narrative/choice 模版。

### input / output 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `input` | string 或 dict | 模型输入（prompt），见下方格式说明 |
| `output` | string 或 dict | 模型输出，正文为 HTML 字符串，选项为 `{"options": [...]}` 对象 |

### input 格式

**字典格式**（含 `system` + `messages`，系统会按拼接模版渲染为 prompt）：

```json
{
  "system": "你是一个视觉交互式小说游戏的剧情师...",
  "messages": [
    {"role": "user", "content": "开始模拟..."},
    {"role": "assistant", "content": "<div>上一轮的输出...</div>"},
    {"role": "user", "content": "玩家选项..."}
  ]
}
```

**扁平字符串格式**（已经是渲染好的 prompt 文本）：

```json
"《prompt 文本》"
```

### output 格式

- **正文** (`narrative.output`)：字符串，通常是 HTML 格式的剧情内容
- **选项** (`choice.output`)：字典，包含 `options` 数组。每个 option 有 `text`、可选的 `reason` 和 `actionId`

### 记录拆分规则

- 一个 record 同时包含 `narrative` 和 `choice` 分段 → 拆成 2 个 judge item（各用对应 target 的 Judge Prompt）
- 只包含其中一个分段 → 只生成 1 个 judge item
- 扁平式记录（无分段）→ 生成 1 个 judge item，target 由顶层 `target` / `judge_target` 决定
- 某分段对应的 Judge Prompt 未配置任何字段映射（`fields` 为空）→ 跳过该分段

### 输入校验规则

提交时会对每条记录做严格校验，不符合会返回 400 错误：

| 校验规则 | 错误信息 |
| --- | --- |
| 记录必须是 JSON 对象 | `第 N 行必须是 JSON 对象` |
| `playthrough_id` 存在时必须为字符串 | `第 N 行 playthrough_id 必须是字符串` |
| `turn` 存在时必须为整数 | `第 N 行 turn 必须是整数` |
| `narrative` / `choice` 分段必须是对象 | `第 N 行 {target} 必须是对象` |
| 分段中映射的字段必须存在 | `第 N 行 {target}.{source_field} 缺失` |
| 分段中映射的字段不能为 null 或空白 | `第 N 行 {target}.{source_field} 不能为空` |
| 分段的 `input` 必须是字符串或对象 | `第 N 行 {field_name} 必须是字符串或对象` |
| 字典格式 input 的 `system` 必须是字符串 | `第 N 行 {field_name}.system 必须是字符串` |
| 字典格式 input 的 `messages` 必须是数组 | `第 N 行 {field_name}.messages 必须是数组` |
| messages 每项的 role 必须是 `user` 或 `assistant` | `第 N 行 {field_name}.messages 第 i 项 role 必须是 user 或 assistant` |
| 同时有 `narrative` 和 `choice` 分段时至少一个不能为空 | `第 N 行 narrative 或 choice 至少一个不能为空` |
| 记录中无任何可处理数据 | `文件中没有可处理的数据` |

> 分段中"映射的字段"指该 target 的 Judge Prompt `fields` 里配置的每个 `source_field`。校验要求它们在分段里存在、非 null、且字符串化并 `strip()` 后非空。

---

## 提交裁判

`POST /api/llm-judge/batch` 接收 JSON 数组，立即返回任务信息，后台异步执行裁判。

### 分段式请求（input 为对话字典）

```bash
curl -X POST 'http://127.0.0.1:8322/api/llm-judge/batch' \
  -H 'Content-Type: application/json' \
  -d '[
    {
      "narrative": {
        "input": {
          "system": "你是一个视觉交互式小说游戏的剧情师...",
          "messages": [
            {"role": "user", "content": "开始模拟..."},
            {"role": "assistant", "content": "<div data-kind=\"scratch\">...</div>"},
            {"role": "user", "content": "玩家选项..."}
          ]
        },
        "output": "<div data-kind=\"scratch\">...</div>\n\n<p>正文输出的 HTML 内容...</p>"
      },
      "choice": {
        "input": {
          "system": "你是一个视觉交互式小说游戏的剧情师...",
          "messages": []
        },
        "output": {
          "options": [
            {"text": "选项1", "reason": "推进剧情"},
            {"text": "选项2", "reason": "推进剧情"}
          ]
        }
      }
    }
  ]'
```

### 分段式请求（input 为字符串）

```bash
curl -X POST 'http://127.0.0.1:8322/api/llm-judge/batch' \
  -H 'Content-Type: application/json' \
  -d '[
    {
      "narrative": {
        "input": "《生成正文的prompt》",
        "output": "《当前的剧情》"
      },
      "choice": {
        "input": "《生成选项的prompt》",
        "output": "《当前的选项》"
      }
    }
  ]'
```

### 扁平式请求（无分段，顶层字段 + target）

记录顶层直接放 `input` / `output`，用 `target` 指定裁判类型（缺省为正文）。详见"输入格式 → 扁平式（flat）"。

```bash
curl -X POST 'http://127.0.0.1:8322/api/llm-judge/batch' \
  -H 'Content-Type: application/json' \
  -d '[
    {"input": "《生成正文的prompt》", "output": "《当前的剧情》", "target": "narrative"},
    {"input": "《生成选项的prompt》", "output": {"options": [{"text": "选项1", "reason": "推进剧情"}]}, "target": "choice"}
  ]'
```

### Query 参数

所有参数均为可选。不传时按 `请求参数 > .env 配置 > Web 页面 active 版本` 的优先级回退。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `judge_version_id` | int | 裁判 LLM API 版本 ID |
| `narrative_judge_prompt_version_id` | int | 正文 Judge Prompt 版本 ID |
| `choice_judge_prompt_version_id` | int | 选项 Judge Prompt 版本 ID |
| `prompt_combine_version_id` | int | 对话/输出拼接模板版本 ID |
| `concurrency` | int | 本任务并发数，范围 1-500。不传则使用运行时默认值（见"并发控制"）。任务创建时快照，运行中不变 |
| `judge_retry_count` | int | 裁判结果非 JSON 时的重试次数，范围 0-10。不传则使用 `LLM_JUDGE_BATCH_RETRY_COUNT`（默认 **2**） |

示例：

```bash
curl -X POST 'http://127.0.0.1:8322/api/llm-judge/batch?narrative_judge_prompt_version_id=1&choice_judge_prompt_version_id=2&concurrency=20&judge_retry_count=3' \
  -H 'Content-Type: application/json' \
  -d '[{"narrative":{"input":{"system":"...","messages":[{"role":"user","content":"..."},{"role":"assistant","content":"..."},{"role":"user","content":"..."}]},"output":"<div>...</div>"},"choice":{"input":{"system":"...","messages":[]},"output":{"options":[{"text":"选项1","reason":"推进剧情"},{"text":"选项2","reason":"推进剧情"}]}}}]'
```

### 返回

```json
{
  "job_id": "f3a1...",
  "total_rows": 2,
  "status": "pending"
}
```

| 字段 | 说明 |
| --- | --- |
| `job_id` | 异步任务 ID，用于后续查询和轮询 |
| `total_rows` | 实际 judge item 数量 |
| `status` | 创建时为 `pending` |

### 错误返回

校验失败时返回 400：

```json
{
  "detail": "第 1 行 narrative.input.system 必须是字符串"
}
```

空数组提交：

```json
{
  "detail": "batch 数据不能为空"
}
```

### 上传 JSONL 文件提交

除 JSON 数组外，也可通过 `multipart/form-data` 上传 `.jsonl` 文件（每行一条记录，格式同"输入格式"章节）。

```bash
curl -X POST 'http://127.0.0.1:8322/api/llm-judge/upload' \
  -F 'file=@playthrough.jsonl' \
  -F 'judge_version_id=1' \
  -F 'narrative_judge_prompt_version_id=1' \
  -F 'choice_judge_prompt_version_id=2' \
  -F 'prompt_combine_version_id=1'
```

与 batch 端点的区别：

| 项 | `/batch` | `/upload` |
| --- | --- | --- |
| 请求体 | JSON 数组（`application/json`） | 文件（`multipart/form-data`，字段名 `file`） |
| 参数传递 | query 参数 | form 字段 |
| 默认版本环境变量前缀 | `LLM_JUDGE_BATCH_*` | `LLM_JUDGE_UPLOAD_*` |
| 源文件保留 | 否（源名为 `batch`） | 是（保留原始 `.jsonl`，支持继续/导出） |

非 `.jsonl` 文件返回 400 `请上传 .jsonl 文件`。返回结构同 batch（`JobResponse`）。

> 上传任务保留了原始文件，因此支持后续的"继续任务"（resume）和"Excel 导出"；batch 任务不保留源文件。

---

## LLM 调用与重试

每个 judge item 调用 LLM 失败时会自动重试。重试次数由 `judge_retry_count` 决定（query 参数 > `LLM_JUDGE_BATCH_RETRY_COUNT` 环境变量 > 默认 **2**），范围 0-10。重试发生在以下场景：

- 网络超时
- LLM API 返回错误
- 连接异常
- 裁判结果不是合法 JSON

重试次数会在任务创建时快照到任务参数中，运行期间不变。重试耗尽后仍失败的 item 标记为失败，错误信息记录在任务的 `errors` 列表中。

任务终结后，可通过 `POST /api/llm-judge/{job_id}/retry-failures` 对失败项重新发起裁判（见"任务管理 → 重试失败项"）。

---

## 裁判模型配置

### 配置来源

任务使用的裁判 LLM 配置（`base_url`、`api_key`、`model`、`timeout` 等）来自以下来源（按优先级）：

1. **Query 参数 `judge_version_id`** — 指定一个已配置的 LLM 裁判版本
2. **环境变量** — `.env` 中配置的默认版本
3. **当前 active 版本** — Web 管理页面中设为"当前使用"的裁判 LLM 配置

### 配置快照

任务创建时，裁判 LLM 配置会以 JSON 快照形式保存到数据库中。任务执行期间始终使用快照时的配置，即使后续通过 Web 页面修改了 LLM 配置，已创建的任务也不受影响。

### 配置校验

任务开始执行时会校验配置完整性：

- `base_url` 不能为空
- `model` 不能为空
- `timeout` 必须 > 0

校验失败则任务直接标记为 `failed`。

---

## Prompt 拼接模板

拼接模板分两组，对应配置页"对话 prompt 模版"中的两个分区：

- **对话模板**（`system_template` / `user_template` / `assistant_template`）：用于 `input` 字段内容拼接。当 input 为**字典格式**（含 `system` + `messages`）时，把对话渲染为 prompt 文本。
- **narrative / choice 模板**（`narrative_output_template` / `choice_option_template`）：用于 `output` 字段内容拼接。narrative 用剧情拼接格式，choice 用选项拼接格式。

### 默认模板

系统默认使用数据库中的 **html标签版** 拼接模板（首次启动自动写入并设为 active）。各项的值如下（`\n` 表示模板末尾追加的换行符）：

```text
system_template:
{{system}}\n

user_template:
<user>
{{user}}
</user>\n

assistant_template:
<assistant>
{{assistant}}
</assistant>\n

narrative_output_template:
<assistant>
{{text}}
</assistant>\n

choice_option_template:
<user{{index}}>
{{text}}
</user>\n
```

| 模板 | 说明 |
| --- | --- |
| system_template | system prompt 的渲染格式 |
| user_template | user 消息的渲染格式 |
| assistant_template | assistant 消息的渲染格式 |
| narrative_output_template | 正文 output 的渲染格式 |
| choice_option_template | 选项列表中每个选项的渲染格式 |

> 另外还内置了一个 **简单版** 模板（`玩家：{{user}}\n` / `剧情：{{assistant}}\n` 等纯文本风格），可在配置管理页切换或通过 `prompt_combine_version_id` 指定。

### 自定义模板

通过 `prompt_combine_version_id` 参数可以指定一个自定义的拼接模板版本。模板版本在 Web 管理页面中创建和管理。

渲染示例：使用默认的 **html标签版** 模板，字典格式的 input 会被渲染为：

```text
你是一个视觉交互式小说游戏的剧情师...
<user>
开始模拟...
</user>
<assistant>
<div>上一轮的输出...</div>
</assistant>
<user>
玩家选项...
</user>
```

---

## 任务管理

### 任务状态流转

```text
pending → running → completed
                  → completed_with_errors
                  → failed
                  → cancelled
```

| 状态 | 说明 |
| --- | --- |
| `pending` | 已创建，等待执行 |
| `running` | 正在执行中 |
| `completed` | 全部 item 处理成功 |
| `completed_with_errors` | 部分 item 失败，部分成功 |
| `failed` | 配置校验失败等致命错误 |
| `cancelled` | 被用户取消。已处理的 item 结果保留，未处理的不再执行 |

### 查询任务状态

```bash
curl 'http://127.0.0.1:8322/api/jobs/{job_id}'
```

返回示例：

```json
{
  "job_id": "f3a1...",
  "status": "completed",
  "total_rows": 2,
  "processed_rows": 2,
  "success_count": 2,
  "failure_count": 0,
  "errors": [],
  "judge_llm_version_name": "生产环境",
  "narrative_judge_prompt_version_name": "正文裁判v1",
  "choice_judge_prompt_version_name": "选项裁判v1",
  "prompt_combine_version_name": "默认拼接",
  "created_at": "2026-05-31T13:00:00Z",
  "updated_at": "2026-05-31T13:00:10Z"
}
```

`status` 进入以下状态之一时，可以认为任务结束：

```text
completed
completed_with_errors
failed
cancelled
```

### 取消任务

```bash
curl -X POST 'http://127.0.0.1:8322/api/jobs/{job_id}/cancel'
```

取消后已处理的 item 结果保留，未处理的不再执行。

> **限制**：只能取消 `pending` 或 `running` 状态的任务。已终结的任务（`completed` / `completed_with_errors` / `failed` / `cancelled`）不能再次取消。

取消是异步的——调用取消接口后，后台会在当前在途的 item 完成后停止调度新的 item。取消操作的生效时间取决于当前在途 item 的 LLM 调用耗时。

### 删除任务

```bash
curl -X DELETE 'http://127.0.0.1:8322/api/jobs/{job_id}'
```

删除任务及其关联的所有结果数据。进行中的任务（`pending` / `running`）不能删除。

### 重试失败项

```bash
curl -X POST 'http://127.0.0.1:8322/api/llm-judge/{job_id}/retry-failures'
```

对任务中失败的 item 重新发起裁判，复用任务创建时快照的裁判配置，立即返回 `JobResponse`（`total_rows` 为本次重试的失败项数量）。

> **限制**：
> - 仅 LLM-as-Judge 任务支持；进行中（`pending` / `running`）的任务不能重试。
> - 失败项需保留原始数据才能重试，否则返回 `没有可重试的失败项（缺少原始数据）`。

### 继续已停止的任务

```bash
curl -X POST 'http://127.0.0.1:8322/api/jobs/{job_id}/resume'
```

对被取消（`cancelled`）的任务，从未处理的 item 继续执行。已处理的结果保留，仅调度剩余 item，立即返回 `JobResponse`（`total_rows` 为原任务总数）。

> **限制**：
> - 仅 `cancelled` 状态的任务可继续；其他状态返回 `只能继续已停止的任务`。
> - 已无未完成项时返回 `该任务已经没有未完成项`。
> - 仅上传（保留了源文件）的任务支持继续；其他任务类型返回 `该任务类型不支持继续`。因此 **batch 任务不支持继续**，需用"重试失败项"或重新提交。

---

## 查询裁判结果

### 结果列表

```bash
curl 'http://127.0.0.1:8322/api/results?job_id={job_id}&result_type=llm_judged'
```

可选参数：

| 参数 | 说明 |
| --- | --- |
| `job_id` | 任务 ID |
| `playthrough_id` | 剧情/回放 ID |
| `turn` | 轮次 |
| `result_type` | 固定传 `llm_judged` |
| `judge_state` | 裁判状态过滤，见下方说明 |
| `page` | 页码，默认 `1` |
| `page_size` | 每页数量，默认 `0`（不分页），最大 `100000` |

#### `judge_state` 参数

| 值 | 说明 |
| --- | --- |
| `exists` | LLM 返回了合法 JSON 且包含 `overall_score` 和 `overall_evidence`（`parse_status = "valid"`） |
| `not_exists` | 保留值，当前版本不会产生此状态的记录 |
| `other` | LLM 返回了合法 JSON 但缺少必填字段 `overall_score` 或 `overall_evidence`（`parse_status = "other"`） |

未传 `judge_state` 时返回所有记录，包括 JSON 解析失败的（此时 `parse_status` 为 `"other"` 且 `valid_json` 为 `false`）。

### 单条结果

`result_id` 即结果列表中每条记录的 `id` 字段（数据库自增主键，整数）：

```bash
curl 'http://127.0.0.1:8322/api/results/{result_id}'
```

### 返回结构

```json
{
  "items": [
    {
      "id": 1,
      "job_id": "f3a1...",
      "playthrough_id": "abc123",
      "turn": 5,
      "prompt": "实际发送给裁判模型的 prompt",
      "input": "该 judge target 对应的输入",
      "output": "该 judge target 对应的输出",
      "judge_result": {
        "target": "narrative",
        "judge": {
          "parse_status": "valid",
          "valid_json": true,
          "overall_score": 0.8,
          "overall_evidence": "评分理由"
        },
        "raw_output": "裁判模型原始返回文本"
      },
      "created_at": "2026-05-31T13:00:10Z"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

`judge_result` 字段：

| 字段 | 说明 |
| --- | --- |
| `target` | 裁判目标标签。对应分段名：`narrative` 或 `choice` |
| `judge.parse_status` | `valid` = 含有所需字段的合法 JSON；`other` = JSON 合法但缺少必填字段，或 JSON 解析失败 |
| `judge.valid_json` | LLM 返回是否为合法 JSON（`true` / `false`） |
| `judge.overall_score` | 总体评分（0-1）。`valid_json` 为 `false` 时为 `null` |
| `judge.overall_evidence` | 评分理由 |
| `judge.raw_json` | （仅当 `valid_json` 为 `true` 时存在）解析后的 JSON 对象 |
| `raw_output` | LLM 的原始返回文本 |

`prompt` 字段即实际发送给裁判模型的完整 prompt。旧版曾在结果对象中重复保存 `judge_prompt`，当前版本不再重复保存。

`input` / `output` 字段：分别保存该 `judge_result.target` 对应的输入和输出文本。

### 任务/文件列表

```bash
curl 'http://127.0.0.1:8322/api/llm-judge/files?sort_by=created'
```

返回所有 LLM-as-Judge 任务的汇总列表（每个任务一项），包含任务名、状态、各类计数（成功/失败/裁判存在/其他）、并发数、重试次数、关联的版本名称等。

| 参数 | 说明 |
| --- | --- |
| `sort_by` | 排序方式：`created`（按创建时间，默认）或 `exists`（按裁判存在数量） |
| `source` | 按源文件名过滤（可选） |

### 导出 Excel

```bash
curl -OJ 'http://127.0.0.1:8322/api/llm-judge/{job_id}/export'
```

将任务的全部裁判结果导出为 `.xlsx` 文件，列包含 `prompt`、`input`、`output`、`target`、`overall_score`、`overall_evidence`、`parse_status`、`raw_output`。

> **说明**：
> - 单元格超过 Excel 上限（约 32000 字符）时，`prompt` 会自动拆分为 `prompt_1`、`prompt_2` …… 多列。
> - 仅上传类任务（`kind = llm_judge_upload`）支持导出；batch 任务返回 400 `该任务不是 LLM-as-Judge 任务`。
> - 任务无结果数据时返回 404。

---

## 并发控制

并发数由全局 `judge_concurrency` 配置控制，决定同时向 LLM 发起的最大请求数。

### 配置方式

**方式一：环境变量**（启动时生效）

在 `.env` 中设置：

```bash
JUDGE_CONCURRENCY=50
```

默认值 50，取值范围 1-500。

**方式二：Runtime API**（运行时动态生效，无需重启）

```bash
curl -X PUT 'http://127.0.0.1:8322/api/config/runtime' \
  -H 'Content-Type: application/json' \
  -d '{"judge_concurrency": 30}'
```

返回：

```json
{
  "judge_concurrency": 30
}
```

> **注意**：并发数修改后只对新创建的任务生效，已运行中的任务继续使用创建时的并发数。每个任务在创建时会将当时的 `judge_concurrency` 值快照到任务参数中。

### 并发实现

系统使用 `asyncio.Semaphore` 模式：始终保持最多 N 个 judge item 同时在途。当某个 item 完成（成功或失败）后立即调度下一个，直到所有 item 处理完毕。

并发数底层限制：`max(1, concurrency)`，即最小为 1。

---

## 配置管理 API

### 获取全部配置

```bash
curl 'http://127.0.0.1:8322/api/config'
```

返回所有版本列表、当前 active 版本、运行时设置等完整状态。API key 等敏感字段以脱敏形式返回。

### Judge LLM API 版本管理

```bash
# 创建
curl -X POST 'http://127.0.0.1:8322/api/config/judge' \
  -H 'Content-Type: application/json' \
  -d '{"name": "生产环境", "config": {"base_url": "...", "api_key": "...", "model": "..."}}'

# 更新
curl -X PUT 'http://127.0.0.1:8322/api/config/judge/{version_id}' \
  -H 'Content-Type: application/json' \
  -d '{"name": "生产环境v2", "config": {"base_url": "...", "model": "new-model"}}'

# 激活（设为默认）
curl -X POST 'http://127.0.0.1:8322/api/config/judge/{version_id}/activate'

# 删除
curl -X DELETE 'http://127.0.0.1:8322/api/config/judge/{version_id}'
```

创建和更新时，如果 `api_key` 为空，会保留现有版本或指定继承版本的 key。

### Judge Prompt 版本管理

Judge Prompt 支持按 `target`（`narrative` / `choice`）分别配置，包含模板和字段映射。

```bash
# 创建（默认 target=narrative）
curl -X POST 'http://127.0.0.1:8322/api/config/judge-prompts' \
  -H 'Content-Type: application/json' \
  -d '{"name": "正文裁判v1", "template": "请评估...\n\n# 输入\n{{prompt}}\n\n# 输出\n{{output}}", "fields": [{"placeholder": "prompt", "source_field": "input"}, {"placeholder": "output", "source_field": "output"}]}'

# 创建指定 target 的 prompt（target 取 narrative 或 choice）
curl -X POST 'http://127.0.0.1:8322/api/config/judge-prompts/choice' \
  -H 'Content-Type: application/json' \
  -d '{"name": "选项裁判v1", "template": "...", "fields": [...]}'

# 更新（需带上 target）
curl -X PUT 'http://127.0.0.1:8322/api/config/judge-prompts/{target}/{version_id}' \
  -H 'Content-Type: application/json' \
  -d '{"name": "新名称", "template": "...", "fields": [...]}'

# 激活为指定 target 的默认
curl -X POST 'http://127.0.0.1:8322/api/config/judge-prompts/{target}/{version_id}/activate'

# 删除（需带上 target）
curl -X DELETE 'http://127.0.0.1:8322/api/config/judge-prompts/{target}/{version_id}'
```

`target` 必须是 `narrative` 或 `choice`，传其他值返回 400 `Judge Prompt 类型无效`。

`fields` 中的 `placeholder` 对应模板里的 `{{变量名}}`，`source_field` 指定从输入数据（分段式则为分段内部、扁平式则为记录顶层）的哪个字段取值。

`source_field` 的取值会按字段名做不同处理：

- `input`：值为 dict 时走对话模板渲染，字符串原样。
- `output`：按 target 走 narrative/choice 输出拼接模板。
- 其他名字：原样取值填入占位符。

> 因此分段式记录里需要哪些键，完全由这里的 `source_field` 决定；分段内部不再写死 `input` / `output`。

### Prompt 拼接模版版本管理

拼接模版用于将字典格式的 `input`（`system` + `messages`）渲染为 prompt 文本，以及将 output 格式化为可读文本。包含 5 个模板，默认使用 **html标签版**（各项取值见上文"Prompt 拼接模板 → 默认模板"）：

| 模板 | 说明 |
| --- | --- |
| `system_template` | system 消息渲染模版 |
| `user_template` | user 消息渲染模版 |
| `assistant_template` | assistant 消息渲染模版 |
| `narrative_output_template` | 正文 output 渲染模版 |
| `choice_option_template` | 选项渲染模版 |

```bash
# 创建
curl -X POST 'http://127.0.0.1:8322/api/config/prompt-combine' \
  -H 'Content-Type: application/json' \
  -d '{"name": "html标签版", "system_template": "{{system}}\\n", "user_template": "<user>\n{{user}}\n</user>\\n", "assistant_template": "<assistant>\n{{assistant}}\n</assistant>\\n", "narrative_output_template": "<assistant>\n{{text}}\n</assistant>\\n", "choice_option_template": "<user{{index}}>\n{{text}}\n</user>\\n"}'

# 更新
curl -X PUT 'http://127.0.0.1:8322/api/config/prompt-combine/{version_id}' \
  -H 'Content-Type: application/json' \
  -d '{...}'

# 激活
curl -X POST 'http://127.0.0.1:8322/api/config/prompt-combine/{version_id}/activate'

# 删除
curl -X DELETE 'http://127.0.0.1:8322/api/config/prompt-combine/{version_id}'
```

### 运行时修改当前 active 拼接模版

```bash
curl -X PUT 'http://127.0.0.1:8322/api/config/prompt-combine' \
  -H 'Content-Type: application/json' \
  -d '{"system_template": "S: {{system}}\n", ...}'
```

---

## 完整的环境变量参考

```bash
# 并发控制（所有 judge 任务共享）
JUDGE_CONCURRENCY=50                                  # 默认 50，范围 1-500

# Batch API 默认版本 ID（不传 query 参数时生效）
LLM_JUDGE_BATCH_JUDGE_VERSION_ID=1
LLM_JUDGE_BATCH_NARRATIVE_JUDGE_PROMPT_VERSION_ID=1
LLM_JUDGE_BATCH_CHOICE_JUDGE_PROMPT_VERSION_ID=2
LLM_JUDGE_BATCH_PROMPT_COMBINE_VERSION_ID=1
LLM_JUDGE_BATCH_RETRY_COUNT=2                         # 裁判结果非 JSON 时重试次数，默认 2，范围 0-10

# Upload API 默认版本 ID（不传 form 字段时生效）
LLM_JUDGE_UPLOAD_JUDGE_VERSION_ID=1
LLM_JUDGE_UPLOAD_NARRATIVE_JUDGE_PROMPT_VERSION_ID=1
LLM_JUDGE_UPLOAD_CHOICE_JUDGE_PROMPT_VERSION_ID=2
LLM_JUDGE_UPLOAD_PROMPT_COMBINE_VERSION_ID=1

# 数据库路径
JUDGE_SYSTEM_DB=/path/to/judge_system.db
```

---

## 项目结构

```text
.
├── app/
│   ├── main.py            # FastAPI 应用 — 路由、接口、任务调度
│   ├── database.py        # SQLite 数据库操作（aiosqlite，含自动迁移）
│   ├── llm.py             # LLM HTTP 客户端（httpx，支持 thinking 等参数）
│   ├── parsing.py         # JSONL 解析 & 输入格式校验 & 模板渲染
│   ├── processing.py      # 后台异步任务处理（LLM 调用、并发控制、重试）
│   ├── schemas.py         # Pydantic 数据模型
│   ├── settings.py        # 配置加载 & 默认值 & 环境变量
│   ├── static/
│   │   ├── config.js      # 配置管理页面前端逻辑
│   │   ├── llm_judge_batch_test.js  # Batch 测试页面前端逻辑
│   │   └── styles.css     # 样式
│   └── templates/
│       ├── config.html              # 配置管理页面
│       └── llm_judge_batch_test.html # Batch API 测试页面
├── tests/
│   ├── test_api.py        # API 集成测试
│   ├── test_llm.py        # LLM 客户端单元测试
│   ├── test_parsing.py    # 解析逻辑单元测试
│   └── test_processing.py # 任务处理逻辑单元测试
├── uploads/               # 上传文件存储目录
├── .env.example           # 环境变量参考
├── requirements.txt       # Python 依赖
└── pyproject.toml         # Pytest 配置
```
