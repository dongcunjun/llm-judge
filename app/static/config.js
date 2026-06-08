const configStatus = document.querySelector("#configStatus");

const apiForms = {
  judge: {
    form: document.querySelector("#judgeForm"),
    select: document.querySelector("#judgeSelect"),
    endpoint: "/api/config/judge",
    versionsKey: "judge_versions",
    activeKey: "active_judge_id",
    label: "裁判 API",
  },
};

const promptForms = {
  narrative: {
    form: document.querySelector("#narrativePromptForm"),
    select: document.querySelector("#narrativePromptSelect"),
    activeKey: "active_narrative_judge_prompt_id",
    fallbackActiveKey: "active_judge_prompt_id",
    label: "正文 Judge Prompt",
  },
  choice: {
    form: document.querySelector("#choicePromptForm"),
    select: document.querySelector("#choicePromptSelect"),
    activeKey: "active_choice_judge_prompt_id",
    fallbackActiveKey: "active_judge_prompt_id",
    label: "选项 Judge Prompt",
  },
};
const dialogTemplateForm = document.querySelector("#dialogTemplateForm");
const dialogTemplateSelect = document.querySelector("#dialogTemplateSelect");
const dialogTemplatePreview = document.querySelector("#dialogTemplatePreview");
const promptPreviewModal = document.querySelector("#promptPreviewModal");
const promptPreviewModalTitle = document.querySelector("#promptPreviewModalTitle");
const promptPreviewModalBody = document.querySelector("#promptPreviewModalBody");
const closePromptPreviewModalButton = document.querySelector("#closePromptPreviewModal");

let configState = null;
let activePromptPreviewForm = null;

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(payload?.detail || `HTTP ${response.status}`);
  }
  return payload;
}

async function loadConfig() {
  try {
    configState = await fetchJson("/api/config");
    renderApiVersions("judge");
    renderPromptVersions("narrative");
    renderPromptVersions("choice");
    renderDialogTemplateVersions();
    configStatus.textContent = "配置已加载";
  } catch (error) {
    configStatus.textContent = error.message;
  }
}

function renderDialogTemplateVersions() {
  const versions = configState.prompt_combine_versions || [];
  dialogTemplateSelect.innerHTML = "";
  for (const version of versions) {
    const option = document.createElement("option");
    option.value = version.id;
    option.textContent = `[ID: ${version.id}] ${version.name}`;
    dialogTemplateSelect.append(option);
  }
  const selected = versions.find((version) => version.id === configState.active_prompt_combine_id) || versions[0];
  if (selected) {
    dialogTemplateSelect.value = selected.id;
  }
  fillDialogTemplateForm(selected);
}

function fillDialogTemplateForm(version) {
  dialogTemplateForm.elements.name.value = version?.name || "";
  dialogTemplateForm.elements.system_template.value = version?.system_template || "";
  dialogTemplateForm.elements.user_template.value = version?.user_template || "";
  dialogTemplateForm.elements.assistant_template.value = version?.assistant_template || "";
  dialogTemplateForm.elements.narrative_output_template.value = version?.narrative_output_template || "{{text}}";
  dialogTemplateForm.elements.choice_option_template.value = version?.choice_option_template || "{{index}}. {{text}}（{{reason}}）";
  clearDirty(dialogTemplateForm);
  updateDialogTemplatePreview();
}

function readDialogTemplatePayload() {
  return {
    name: dialogTemplateForm.elements.name.value,
    system_template: dialogTemplateForm.elements.system_template.value,
    user_template: dialogTemplateForm.elements.user_template.value,
    assistant_template: dialogTemplateForm.elements.assistant_template.value,
    narrative_output_template: dialogTemplateForm.elements.narrative_output_template.value,
    choice_option_template: dialogTemplateForm.elements.choice_option_template.value,
  };
}

function selectedDialogTemplateVersion() {
  const id = Number(dialogTemplateSelect.value);
  return (configState.prompt_combine_versions || []).find((version) => version.id === id);
}

async function handleDialogTemplateAction(action) {
  const selected = selectedDialogTemplateVersion();
  if (action === "delete") {
    if (!selected) {
      setStatus("请选择对话 prompt 模版版本");
      return;
    }
    if (!confirm(`确认删除对话 prompt 模版版本「${selected.name}」吗？此操作不可撤销。`)) {
      return;
    }
    setStatus("删除中...");
    try {
      await fetchJson(`/api/config/prompt-combine/${selected.id}`, {method: "DELETE"});
      await loadConfig();
      setStatus("对话 prompt 模版版本已删除");
    } catch (error) {
      setStatus(error.message);
    }
    return;
  }
  if (action === "activate") {
    if (!selected) {
      setStatus("请选择对话 prompt 模版版本");
      return;
    }
    setStatus("设置中...");
    try {
      await fetchJson(`/api/config/prompt-combine/${selected.id}/activate`, {method: "POST"});
      await loadConfig();
      setStatus("对话 prompt 模版默认版本已更新");
    } catch (error) {
      setStatus(error.message);
    }
    return;
  }
  setStatus("保存中...");
  try {
    if (action === "create") {
      await fetchJson("/api/config/prompt-combine", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(readDialogTemplatePayload()),
      });
    } else if (action === "update") {
      if (!selected) {
        throw new Error("请选择对话 prompt 模版版本");
      }
      await fetchJson(`/api/config/prompt-combine/${selected.id}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(readDialogTemplatePayload()),
      });
    }
    await loadConfig();
    setStatus("对话 prompt 模版已保存");
  } catch (error) {
    setStatus(error.message);
  }
}

function renderApiVersions(kind) {
  const meta = apiForms[kind];
  const versions = configState[meta.versionsKey] || [];
  meta.select.innerHTML = "";
  for (const version of versions) {
    const option = document.createElement("option");
    option.value = version.id;
    option.textContent = `[ID: ${version.id}] ${version.name}`;
    meta.select.append(option);
  }
  const selected = versions.find((version) => version.id === configState[meta.activeKey]) || versions[0];
  if (selected) {
    meta.select.value = selected.id;
  }
  fillApiForm(kind, selected);
}

function fillApiForm(kind, version) {
  const form = apiForms[kind].form;
  form.elements.name.value = version?.name || "";
  form.elements.base_url.value = version?.config?.base_url || "";
  form.elements.api_key.value = "";
  form.elements.api_key.disabled = true;
  form.elements.api_key.placeholder = version?.config?.api_key ? "留空保留当前密钥" : "输入 API Key";
  const hint = form.querySelector(".api-key-hint");
  if (hint) {
    hint.textContent = version?.config?.api_key ? `当前：${version.config.api_key}` : "当前未配置";
  }
  const revealButton = form.querySelector(".reveal-key-input");
  if (revealButton) {
    revealButton.textContent = "替换";
  }
  form.elements.model.value = version?.config?.model || "";
  form.elements.timeout.value = version?.config?.timeout ?? 60;
  form.elements.thinking.checked = version?.config?.thinking || false;
  form.elements.force_json_only.checked = version?.config?.force_json_only || false;
  form.elements.temperature.value = version?.config?.temperature ?? "";
  form.elements.top_k.value = version?.config?.top_k ?? "";
  form.elements.top_p.value = version?.config?.top_p ?? "";
  clearDirty(form);
}

function parseOptionalFloat(value) {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const num = Number(trimmed);
  return Number.isNaN(num) ? null : num;
}

function parseOptionalInt(value) {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const num = parseInt(trimmed, 10);
  return Number.isNaN(num) ? null : num;
}

function readApiPayload(kind) {
  const form = apiForms[kind].form;
  const selected = selectedApiVersion(kind);
  return {
    name: form.elements.name.value,
    config: {
      base_url: form.elements.base_url.value,
      api_key: form.elements.api_key.value,
      model: form.elements.model.value,
      timeout: Number(form.elements.timeout.value || 60),
      thinking: form.elements.thinking.checked,
      force_json_only: form.elements.force_json_only.checked,
      temperature: parseOptionalFloat(form.elements.temperature.value),
      top_k: parseOptionalInt(form.elements.top_k.value),
      top_p: parseOptionalFloat(form.elements.top_p.value),
    },
    inherit_api_key_from_id: form.elements.api_key.value ? null : (selected?.id ?? null),
  };
}

function selectedApiVersion(kind) {
  const meta = apiForms[kind];
  const id = Number(meta.select.value);
  return (configState[meta.versionsKey] || []).find((version) => version.id === id);
}

async function handleApiAction(kind, action) {
  const meta = apiForms[kind];
  const selected = selectedApiVersion(kind);
  if (action === "delete") {
    if (!selected) {
      setStatus(`请选择${meta.label}版本`);
      return;
    }
    if (!confirm(`确认删除${meta.label}版本「${selected.name}」吗？此操作不可撤销。`)) {
      return;
    }
    setStatus("删除中...");
    try {
      await fetchJson(`${meta.endpoint}/${selected.id}`, {method: "DELETE"});
      await loadConfig();
      setStatus(`${meta.label}版本已删除`);
    } catch (error) {
      setStatus(error.message);
    }
    return;
  }
  if (action === "activate") {
    if (!selected) {
      setStatus(`请选择${meta.label}版本`);
      return;
    }
    setStatus("设置中...");
    try {
      await fetchJson(`${meta.endpoint}/${selected.id}/activate`, {method: "POST"});
      await loadConfig();
      setStatus(`${meta.label}默认版本已更新`);
    } catch (error) {
      setStatus(error.message);
    }
    return;
  }
  setStatus("保存中...");
  try {
    if (action === "create") {
      await fetchJson(meta.endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(readApiPayload(kind)),
      });
    } else if (action === "update") {
      if (!selected) {
        throw new Error(`请选择${meta.label}版本`);
      }
      await fetchJson(`${meta.endpoint}/${selected.id}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(readApiPayload(kind)),
      });
    }
    await loadConfig();
    setStatus(`${meta.label}已保存`);
  } catch (error) {
    setStatus(error.message);
  }
}

function renderPromptVersions(target) {
  const meta = promptForms[target];
  const versions = target === "narrative"
    ? (configState.narrative_judge_prompt_versions || [])
    : (configState.choice_judge_prompt_versions || []);
  const activeId = configState[meta.activeKey] || configState[meta.fallbackActiveKey];
  meta.select.innerHTML = "";
  for (const version of versions) {
    const option = document.createElement("option");
    option.value = version.id;
    option.textContent = `[ID: ${version.id}] ${version.name}`;
    meta.select.append(option);
  }
  const selected = versions.find((version) => version.id === activeId) || versions[0];
  if (selected) {
    meta.select.value = selected.id;
  }
  fillPromptForm(target, selected);
}

function fillPromptForm(target, version) {
  const meta = promptForms[target];
  const form = meta.form;
  form.elements.name.value = version?.name || "";
  form.elements.template.value = version?.template || "";
  renderPromptFields(form, version?.fields || []);
  fillScoreConfig(form, version?.score_config || null);
  clearDirty(form);
  updatePromptPreview(form);
}

function renderPromptFields(form, fields) {
  const list = form.querySelector(".field-mapping-list");
  list.innerHTML = "";
  const rows = fields.length ? fields : [{placeholder: "prompt", source_field: "prompt"}];
  for (const field of rows) {
    addPromptFieldRow(form, field.placeholder || "", field.source_field || "");
  }
}

function addPromptFieldRow(form, placeholder = "", sourceField = "") {
  const list = form.querySelector(".field-mapping-list");
  const row = document.createElement("div");
  row.className = "field-mapping-row";
  row.innerHTML = `
    <label>占位符<input name="placeholder" placeholder="prompt"></label>
    <label>输入字段<input name="source_field" placeholder="prompt"></label>
    <button type="button" class="secondary remove-field-row">删除</button>
  `;
  row.querySelector('[name="placeholder"]').value = placeholder;
  row.querySelector('[name="source_field"]').value = sourceField;
  row.querySelector(".remove-field-row").addEventListener("click", () => {
    row.remove();
    markDirty(form);
    updatePromptPreview(form);
  });
  list.append(row);
}

function readPromptPayload(form) {
  const fields = [...form.querySelectorAll(".field-mapping-row")].map((row) => ({
    placeholder: row.querySelector('[name="placeholder"]').value,
    source_field: row.querySelector('[name="source_field"]').value,
  })).filter((field) => field.placeholder.trim() || field.source_field.trim());
  return {
    name: form.elements.name.value,
    template: form.elements.template.value,
    fields,
    score_config: readScoreConfig(form),
  };
}

function fillScoreConfig(form, scoreConfig) {
  const cfg = scoreConfig || null;
  const mode = cfg?.mode || "default";
  form.elements.score_mode.value = ["direct", "weighted"].includes(mode) ? mode : "default";
  form.elements.evidence_path.value = cfg?.evidence_path || "";
  form.elements.score_path.value = cfg?.score_path || "";
  renderScoreComponents(form, cfg?.components || []);
  updateScoreModeVisibility(form);
}

function renderScoreComponents(form, components) {
  const list = form.querySelector(".score-component-list");
  list.innerHTML = "";
  const rows = components.length ? components : [{path: "", weight: 1}];
  for (const component of rows) {
    addScoreComponentRow(form, component.path || "", component.weight ?? 1);
  }
}

function addScoreComponentRow(form, path = "", weight = 1) {
  const list = form.querySelector(".score-component-list");
  const row = document.createElement("div");
  row.className = "score-component-row";
  row.innerHTML = `
    <label>路径<input name="component_path" placeholder="scores.coherence"></label>
    <label>权重<input name="component_weight" type="number" min="0" step="0.01" placeholder="0.5"></label>
    <button type="button" class="secondary remove-component-row">删除</button>
  `;
  row.querySelector('[name="component_path"]').value = path;
  row.querySelector('[name="component_weight"]').value = weight;
  row.querySelector(".remove-component-row").addEventListener("click", () => {
    row.remove();
    markDirty(form);
  });
  list.append(row);
}

function readScoreConfig(form) {
  const mode = form.elements.score_mode.value;
  if (mode === "default") {
    return null;
  }
  const evidencePath = form.elements.evidence_path.value.trim() || "overall_evidence";
  const scorePath = form.elements.score_path.value.trim() || "overall_score";
  if (mode === "direct") {
    return {
      mode: "direct",
      score_path: scorePath,
      evidence_path: evidencePath,
      components: [],
    };
  }
  const components = [...form.querySelectorAll(".score-component-row")].map((row) => {
    const path = row.querySelector('[name="component_path"]').value.trim();
    const weightRaw = row.querySelector('[name="component_weight"]').value.trim();
    const weight = weightRaw === "" ? 0 : Number(weightRaw);
    return {path, weight};
  }).filter((c) => c.path !== "");
  return {
    mode: "weighted",
    score_path: scorePath,
    evidence_path: evidencePath,
    components,
  };
}

function updateScoreModeVisibility(form) {
  const mode = form.elements.score_mode.value;
  const directRow = form.querySelector(".score-direct-row");
  const evidenceRow = form.querySelector(".score-evidence-row");
  const weightedRow = form.querySelector(".score-weighted-row");
  if (directRow) directRow.hidden = mode !== "direct";
  if (evidenceRow) evidenceRow.hidden = mode === "default";
  if (weightedRow) weightedRow.hidden = mode !== "weighted";
}

function selectedPromptVersion(target) {
  const id = Number(promptForms[target].select.value);
  const versions = target === "narrative"
    ? (configState.narrative_judge_prompt_versions || [])
    : (configState.choice_judge_prompt_versions || []);
  return versions.find((version) => version.id === id);
}

async function handlePromptAction(target, action) {
  const meta = promptForms[target];
  const selected = selectedPromptVersion(target);
  if (action === "delete") {
    if (!selected) {
      setStatus(`请选择${meta.label}版本`);
      return;
    }
    if (!confirm(`确认删除${meta.label}版本「${selected.name}」吗？此操作不可撤销。`)) {
      return;
    }
    setStatus("删除中...");
    try {
      await fetchJson(`/api/config/judge-prompts/${target}/${selected.id}`, {method: "DELETE"});
      await loadConfig();
      setStatus(`${meta.label}版本已删除`);
    } catch (error) {
      setStatus(error.message);
    }
    return;
  }
  if (action === "activate") {
    if (!selected) {
      setStatus(`请选择${meta.label}版本`);
      return;
    }
    setStatus("设置中...");
    try {
      await fetchJson(`/api/config/judge-prompts/${target}/${selected.id}/activate`, {method: "POST"});
      await loadConfig();
      setStatus(`${meta.label}默认版本已更新`);
    } catch (error) {
      setStatus(error.message);
    }
    return;
  }
  setStatus("保存中...");
  try {
    if (action === "create") {
      const created = await fetchJson(`/api/config/judge-prompts/${target}`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(readPromptPayload(meta.form)),
      });
      await fetchJson(`/api/config/judge-prompts/${target}/${created.id}/activate`, {method: "POST"});
    } else if (action === "update") {
      if (!selected) {
        throw new Error(`请选择${meta.label}版本`);
      }
      await fetchJson(`/api/config/judge-prompts/${target}/${selected.id}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(readPromptPayload(meta.form)),
      });
    }
    await loadConfig();
    setStatus(`${meta.label}已保存`);
  } catch (error) {
    setStatus(error.message);
  }
}

function renderTemplate(template, values) {
  let output = String(template || "").replaceAll("\\n", "\n");
  for (const [key, value] of Object.entries(values)) {
    output = output.replaceAll(`{{${key}}}`, value);
  }
  return output;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderInlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function renderMarkdown(markdown) {
  const lines = String(markdown || "").replaceAll("\r\n", "\n").split("\n");
  const html = [];
  let inCodeBlock = false;
  let codeLines = [];
  let listType = null;
  let quoteLines = [];
  let paragraphLines = [];

  function closeParagraph() {
    if (!paragraphLines.length) return;
    html.push(`<p>${paragraphLines.map(renderInlineMarkdown).join("<br>")}</p>`);
    paragraphLines = [];
  }

  function closeList() {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = null;
  }

  function closeQuote() {
    if (!quoteLines.length) return;
    html.push(`<blockquote>${quoteLines.map(renderInlineMarkdown).join("<br>")}</blockquote>`);
    quoteLines = [];
  }

  function closeBlocks() {
    closeParagraph();
    closeQuote();
    closeList();
  }

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCodeBlock) {
        html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        inCodeBlock = false;
      } else {
        closeBlocks();
        inCodeBlock = true;
      }
      continue;
    }
    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    if (!line.trim()) {
      closeBlocks();
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      closeBlocks();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      closeParagraph();
      closeList();
      quoteLines.push(quote[1]);
      continue;
    }

    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    const listItem = unordered || ordered;
    if (listItem) {
      closeParagraph();
      closeQuote();
      const nextListType = unordered ? "ul" : "ol";
      if (listType !== nextListType) {
        closeList();
        html.push(`<${nextListType}>`);
        listType = nextListType;
      }
      html.push(`<li>${renderInlineMarkdown(listItem[1])}</li>`);
      continue;
    }

    closeQuote();
    closeList();
    paragraphLines.push(line);
  }

  if (inCodeBlock) {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  closeBlocks();
  return html.join("");
}

function updatePromptPreview(form) {
  const preview = form.querySelector(".prompt-preview");
  const rendered = renderMarkdown(renderTemplate(form.elements.template.value, samplePromptValues(form)));
  preview.innerHTML = rendered;
  if (form === activePromptPreviewForm && promptPreviewModalBody) {
    promptPreviewModalBody.innerHTML = rendered;
  }
}

function samplePromptValues(form) {
  const sample = {
    prompt: "输入：玩家继续调查书房",
    input: "输入：玩家继续调查书房",
    output: "输出：主角在书房发现暗格",
    bug: "问题：叙事是否连贯",
  };
  for (const row of form.querySelectorAll(".field-mapping-row")) {
    const placeholder = row.querySelector('[name="placeholder"]').value.trim();
    const sourceField = row.querySelector('[name="source_field"]').value.trim();
    if (placeholder && !(placeholder in sample)) {
      sample[placeholder] = `示例 ${sourceField || placeholder}`;
    }
  }
  return sample;
}

function openPromptPreviewModal(form) {
  if (!promptPreviewModal || !promptPreviewModalBody || !promptPreviewModalTitle) return;
  activePromptPreviewForm = form;
  const target = form.dataset.target;
  const meta = promptForms[target];
  promptPreviewModalTitle.textContent = `${meta?.label || "Judge Prompt"} 预览`;
  promptPreviewModalBody.innerHTML = renderMarkdown(renderTemplate(form.elements.template.value, samplePromptValues(form)));
  promptPreviewModal.hidden = false;
  document.body.classList.add("modal-open");
  closePromptPreviewModalButton?.focus();
}

function closePromptPreviewModal() {
  if (!promptPreviewModal || promptPreviewModal.hidden) return;
  promptPreviewModal.hidden = true;
  document.body.classList.remove("modal-open");
  activePromptPreviewForm = null;
}

function updateDialogTemplatePreview() {
  const narrativeTemplate = dialogTemplateForm.elements.narrative_output_template.value;
  const optionTemplate = dialogTemplateForm.elements.choice_option_template.value;
  const sections = [
    renderTemplate(dialogTemplateForm.elements.system_template.value, {system: "系统：古风悬疑背景"}),
    renderTemplate(dialogTemplateForm.elements.user_template.value, {user: "玩家：继续调查书房"}),
    renderTemplate(dialogTemplateForm.elements.assistant_template.value, {assistant: "剧情：主角在书房发现暗格"}),
    renderTemplate(narrativeTemplate, {text: "主角在书房发现暗格"}),
    renderTemplate(optionTemplate, {index: "1", text: "继续调查暗格", reason: "当前线索指向书房"}),
    renderTemplate(optionTemplate, {index: "2", text: "询问管家", reason: "管家可能知道旧事"}),
  ];
  dialogTemplatePreview.textContent = sections.join("");
}

function markDirty(form) {
  form.dataset.dirty = "true";
}

function clearDirty(form) {
  form.dataset.dirty = "false";
}

function setStatus(message) {
  if (configStatus) {
    configStatus.textContent = message;
  }
}

function wireApiForm(kind) {
  const meta = apiForms[kind];
  meta.select.addEventListener("change", () => fillApiForm(kind, selectedApiVersion(kind)));
  meta.form.addEventListener("input", () => markDirty(meta.form));
  meta.form.addEventListener("click", (event) => {
    const action = event.target?.dataset?.action;
    if (action) {
      handleApiAction(kind, action);
    }
  });
  meta.form.addEventListener("submit", (event) => event.preventDefault());
}

function wirePromptForm(target) {
  const meta = promptForms[target];
  meta.select.addEventListener("change", () => fillPromptForm(target, selectedPromptVersion(target)));
  meta.form.addEventListener("input", () => {
    markDirty(meta.form);
    updatePromptPreview(meta.form);
  });
  meta.form.addEventListener("change", (event) => {
    if (event.target?.name === "score_mode") {
      updateScoreModeVisibility(meta.form);
    }
  });
  meta.form.addEventListener("click", (event) => {
    if (event.target?.classList?.contains("add-prompt-field")) {
      addPromptFieldRow(meta.form);
      markDirty(meta.form);
      updatePromptPreview(meta.form);
      return;
    }
    if (event.target?.classList?.contains("add-score-component")) {
      addScoreComponentRow(meta.form);
      markDirty(meta.form);
      return;
    }
    if (event.target?.classList?.contains("full-preview-button")) {
      openPromptPreviewModal(meta.form);
      return;
    }
    const action = event.target?.dataset?.action;
    if (action) {
      handlePromptAction(target, action);
    }
  });
  meta.form.addEventListener("submit", (event) => event.preventDefault());
}

function wireDialogTemplateForm() {
  dialogTemplateSelect.addEventListener("change", () => fillDialogTemplateForm(selectedDialogTemplateVersion()));
  dialogTemplateForm.addEventListener("input", () => {
    markDirty(dialogTemplateForm);
    updateDialogTemplatePreview();
  });
  dialogTemplateForm.addEventListener("click", (event) => {
    const action = event.target?.dataset?.action;
    if (action) {
      handleDialogTemplateAction(action);
    }
  });
  dialogTemplateForm.addEventListener("submit", (event) => event.preventDefault());
}

document.querySelectorAll(".reveal-key-input").forEach((button) => {
  button.addEventListener("click", () => {
    const row = button.closest(".api-key-row");
    const input = row?.querySelector("input[name='api_key']");
    if (!input) return;
    input.disabled = false;
    input.value = "";
    input.focus();
    button.textContent = "正在替换";
  });
});

closePromptPreviewModalButton?.addEventListener("click", closePromptPreviewModal);
promptPreviewModal?.querySelector("[data-close-full-preview]")?.addEventListener("click", closePromptPreviewModal);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closePromptPreviewModal();
  }
});

for (const tab of document.querySelectorAll(".sub-tab")) {
  tab.addEventListener("click", () => {
    document.querySelector(".sub-tab.active")?.classList.remove("active");
    tab.classList.add("active");
    const section = tab.dataset.section;
    document.querySelector("#judgeApiSection").hidden = section !== "judge-api";
    document.querySelector("#narrativePromptSection").hidden = section !== "narrative-prompt";
    document.querySelector("#choicePromptSection").hidden = section !== "choice-prompt";
    document.querySelector("#dialogTemplateSection").hidden = section !== "dialog-template";
  });
}

wireApiForm("judge");
wirePromptForm("narrative");
wirePromptForm("choice");
wireDialogTemplateForm();
loadConfig();
