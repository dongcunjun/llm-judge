// ── DOM references: form ─────────────────────────────────────────
const batchTestForm = document.querySelector("#batchTestForm");
const batchRequestBody = document.querySelector("#batchRequestBody");
const batchRequestUrl = document.querySelector("#batchRequestUrl");
const batchResponseStatus = document.querySelector("#batchResponseStatus");
const batchResponseBody = document.querySelector("#batchResponseBody");
const batchSubmitUrl = document.querySelector("#batchSubmitUrl");
const batchJobStatusSection = document.querySelector("#batchJobStatusSection");
const batchJobStatusUrl = document.querySelector("#batchJobStatusUrl");
const batchJobStatusBody = document.querySelector("#batchJobStatusBody");
const submitBatchRequest = document.querySelector("#submitBatchRequest");
const saveBatchDefaultsBtn = document.querySelector("#saveBatchDefaults");
const batchJsonlFile = document.querySelector("#batchJsonlFile");

// ── DOM references: file list ────────────────────────────────────
const batchFileSortSelect = document.querySelector("#batchFileSortSelect");
const batchFileVersionFilters = document.querySelector("#batchFileVersionFilters");
const batchFileResultCount = document.querySelector("#batchFileResultCount");
const batchFileToolbar = document.querySelector("#batchFileToolbar");
const batchFileList = document.querySelector("#batchFileList");
const batchFileDetailView = document.querySelector("#batchFileDetailView");
const batchDetailFilters = document.querySelector("#batchDetailFilters");
const batchDetailToolbar = document.querySelector("#batchDetailToolbar");
const batchDetailPageInfo = document.querySelector("#batchDetailPageInfo");
const batchDetailPrevPage = document.querySelector("#batchDetailPrevPage");
const batchDetailNextPage = document.querySelector("#batchDetailNextPage");
const batchDetailUrlBlock = document.querySelector("#batchDetailUrlBlock");
const batchDetailResultUrl = document.querySelector("#batchDetailResultUrl");
const batchDetailSummary = document.querySelector("#batchDetailSummary");
const batchDetailList = document.querySelector("#batchDetailList");
const batchCompareBar = document.querySelector("#batchCompareBar");
const batchCompareCount = document.querySelector("#batchCompareCount");
const batchCompareBtn = document.querySelector("#batchCompareBtn");
const batchCompareClear = document.querySelector("#batchCompareClear");
const batchCompareView = document.querySelector("#batchCompareView");
const batchCompareBody = document.querySelector("#batchCompareBody");
const batchCompareBack = document.querySelector("#batchCompareBack");

let latestJobId = "";
let resultsPageSize = 20;
let selectedBatchJobId = null;
let batchDetailPage = 1;
let batchFileRefreshTimer = null;
let batchFilesCache = [];
const selectedCompareJobIds = new Set();

const BATCH_DEFAULTS_KEY = "batch_test_defaults";
const VERSION_SELECT_NAMES = [
  "judge_version_id",
  "narrative_judge_prompt_version_id",
  "choice_judge_prompt_version_id",
  "prompt_combine_version_id",
];

const TERMINAL_STATUSES = new Set([
  "completed",
  "completed_with_errors",
  "failed",
  "cancelled",
]);

// ── Sample records ──────────────────────────────────────────────
const sampleRecords = [
  {
    narrative: {
      input: {
        system: "你是一个视觉交互式小说游戏的剧情师...",
        messages: [
          {role: "user", content: "开始模拟..."},
          {role: "assistant", content: "<div data-kind=\"scratch\">...</div>"},
          {role: "user", content: "玩家选项..."},
        ],
      },
      output: "<div data-kind=\"scratch\">...</div>\n\n<p>正文输出的 HTML 内容...</p>",
    },
    choice: {
      input: {
        system: "你是一个视觉交互式小说游戏的剧情师...",
        messages: [],
      },
      output: {
        options: [
          {text: "选项1", reason: "推进剧情"},
          {text: "选项2", reason: "推进剧情"},
        ],
      },
    },
  },
];

// ── Version selects ─────────────────────────────────────────────
let batchConfigState = null;

async function loadConfigAndPopulateSelects() {
  try {
    const response = await fetch("/api/config");
    const config = await response.json();
    batchConfigState = config;
    populateSelect("judge_version_id", config.judge_versions || []);
    populateSelect("narrative_judge_prompt_version_id", config.narrative_judge_prompt_versions || []);
    populateSelect("choice_judge_prompt_version_id", config.choice_judge_prompt_versions || []);
    populateSelect("prompt_combine_version_id", config.prompt_combine_versions || []);

    markActiveOption("judge_version_id", config.active_judge_id);
    markActiveOption("narrative_judge_prompt_version_id", config.active_narrative_judge_prompt_id);
    markActiveOption("choice_judge_prompt_version_id", config.active_choice_judge_prompt_id);
    markActiveOption("prompt_combine_version_id", config.active_prompt_combine_id);

    populateVersionFilterSelects(config);
  } catch (error) {
    console.error("加载版本列表失败:", error);
  }
}

// Version filter dropdowns on the 裁判结果 list. Each option carries the
// version *name* as its value, since batch files store version names (not ids).
const VERSION_FILTER_CONFIG = [
  {name: "judge_version_id", configKey: "judge_versions"},
  {name: "narrative_judge_prompt_version_id", configKey: "narrative_judge_prompt_versions"},
  {name: "choice_judge_prompt_version_id", configKey: "choice_judge_prompt_versions"},
  {name: "prompt_combine_version_id", configKey: "prompt_combine_versions"},
];

function populateVersionFilterSelects(config) {
  if (!batchFileVersionFilters) return;
  for (const {name, configKey} of VERSION_FILTER_CONFIG) {
    const select = batchFileVersionFilters.elements[name];
    if (!select) continue;
    const previous = select.value;
    const versions = config[configKey] || [];
    select.innerHTML = '<option value="">全部</option>';
    for (const version of versions) {
      const option = document.createElement("option");
      // Value is the version name so it matches batch file version fields.
      option.value = version.name;
      option.textContent = `[ID: ${version.id}] ${version.name}`;
      select.append(option);
    }
    select.value = previous;
  }
}

function populateSelect(selectName, versions) {
  const select = batchTestForm.elements[selectName];
  if (!select) return;
  select.innerHTML = '<option value="">默认</option>';
  for (const version of versions) {
    const option = document.createElement("option");
    option.value = version.id;
    option.textContent = `[ID: ${version.id}] ${version.name}`;
    select.append(option);
  }
}

function markActiveOption(selectName, activeId) {
  if (activeId == null) return;
  const select = batchTestForm.elements[selectName];
  if (!select) return;
  _setOptionMarker(select, activeId, true);
}

function _setOptionMarker(select, value, mark) {
  const option = select.querySelector(`option[value="${value}"]`);
  if (!option || !option.value) return;
  if (mark && !option.textContent.endsWith("（当前默认）")) {
    option.textContent += "（当前默认）";
  } else if (!mark) {
    option.textContent = option.textContent.replace(/（当前默认）$/, "");
  }
}

// ── Defaults persistence ────────────────────────────────────────
function loadDefaults() {
  try {
    const raw = localStorage.getItem(BATCH_DEFAULTS_KEY);
    if (!raw) return;
    const defaults = JSON.parse(raw);
    for (const name of VERSION_SELECT_NAMES) {
      const select = batchTestForm.elements[name];
      if (select) select.value = "";
    }
    if (defaults.body && typeof defaults.body === "string") {
      batchRequestBody.value = defaults.body;
    }
    if (defaults.concurrency != null) {
      const concurrencyInput = batchTestForm.elements["concurrency"];
      if (concurrencyInput) concurrencyInput.value = defaults.concurrency;
    }
    if (defaults.judge_retry_count != null) {
      const retryInput = batchTestForm.elements["judge_retry_count"];
      if (retryInput) retryInput.value = defaults.judge_retry_count;
    }
  } catch (error) {
    console.error("加载默认值失败:", error);
  }
}

// ── Helpers ─────────────────────────────────────────────────────
function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

function renderPayload(element, payload) {
  element.textContent = typeof payload === "string" ? payload : formatJson(payload);
}

async function readJsonResponse(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function batchFetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      if (!response.ok) throw new Error(text);
      throw error;
    }
  }
  if (!response.ok) {
    throw new Error(payload?.detail || `HTTP ${response.status}`);
  }
  return payload;
}

function isTerminalStatus(status) {
  return TERMINAL_STATUSES.has(status);
}

// ── File list: load and render batch job history ─────────────────
async function loadBatchFiles() {
  if (selectedBatchJobId) return;
  const sortBy = batchFileSortSelect?.value || "created";
  showBatchFileLoading();
  try {
    const files = await batchFetchJson(`/api/llm-judge/files?source=batch&sort_by=${sortBy}`);
    batchFilesCache = files;
    renderBatchFileList(applyVersionFilters(files));
  } catch (error) {
    batchFileResultCount.textContent = error.message;
    batchFileList.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

// Filter the cached batch files by the selected version dropdowns. Matching is
// by version *name* because batch files persist version names, not ids.
function batchFileVersionFilterValues() {
  const values = {};
  if (!batchFileVersionFilters) return values;
  const form = new FormData(batchFileVersionFilters);
  for (const {name} of VERSION_FILTER_CONFIG) {
    const value = String(form.get(name) || "").trim();
    if (value) values[name] = value;
  }
  return values;
}

function applyVersionFilters(files) {
  const filters = batchFileVersionFilterValues();
  if (!Object.keys(filters).length) return files;
  return files.filter((file) => {
    if (filters.judge_version_id && file.judge_llm_version_name !== filters.judge_version_id) {
      return false;
    }
    if (filters.narrative_judge_prompt_version_id) {
      const narrativeName = file.narrative_judge_prompt_version_name || file.judge_prompt_version_name;
      if (narrativeName !== filters.narrative_judge_prompt_version_id) return false;
    }
    if (filters.choice_judge_prompt_version_id
        && file.choice_judge_prompt_version_name !== filters.choice_judge_prompt_version_id) {
      return false;
    }
    if (filters.prompt_combine_version_id
        && file.prompt_combine_version_name !== filters.prompt_combine_version_id) {
      return false;
    }
    return true;
  });
}

function reapplyVersionFilters() {
  renderBatchFileList(applyVersionFilters(batchFilesCache));
}

function showBatchFileLoading() {
  batchFileList.innerHTML = `
    <div class="skeleton-row"></div>
    <div class="skeleton-row"></div>
    <div class="skeleton-row short"></div>
    <div class="empty-state">加载批次记录中...</div>
  `;
}

function batchFileCallbacks() {
  return {
    deleteJob: (jobId) => deleteBatchJob(jobId),
    cancelJob: (jobId, button) => cancelBatchJob(jobId, button),
    exportExcel: (jobId, filename) => exportBatchExcel(jobId, filename),
    showErrors: (card, jobId, button) => toggleBatchJobErrors(card, jobId, button),
    resumeJob: (jobId, button) => resumeBatchJob(jobId, button),
    retryFailures: (jobId) => retryBatchFailures(jobId),
    onSelectFile: (file) => selectBatchFile(file),
    onToggleSelect: (jobId, checked) => toggleCompareSelection(jobId, checked),
  };
}

function renderBatchFileList(files) {
  batchFileList.innerHTML = "";
  syncBatchFileRefreshTimer(files);
  const hasFilters = Object.keys(batchFileVersionFilterValues()).length > 0;
  const totalCount = batchFilesCache.length;
  batchFileResultCount.textContent = hasFilters
    ? `${files.length} / ${totalCount} 个批次`
    : `${files.length} 个批次`;
  if (!files.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = hasFilters ? "没有匹配筛选条件的批次" : "暂无批次记录";
    batchFileList.append(empty);
    pruneCompareSelection(files);
    return;
  }
  const callbacks = batchFileCallbacks();
  const selectState = {selectable: true, selectedIds: selectedCompareJobIds};
  for (const file of files) {
    // Show created_at as display name since all batch jobs have source_filename="batch"
    file.displayFilename = `Batch ${formatDate(file.created_at)}`;
    batchFileList.append(renderLlmJudgeFileCard(file, callbacks, batchConfigState, selectState));
  }
  pruneCompareSelection(files);
}

// ── Score distribution comparison ────────────────────────────────
function comparableJobIds(files) {
  return new Set(
    (files || [])
      .filter((file) => Array.isArray(file?.score_breakdown_summary?.score_distribution)
        && file.score_breakdown_summary.score_distribution.length)
      .map((file) => file.job_id),
  );
}

function pruneCompareSelection(files) {
  const valid = comparableJobIds(files);
  for (const jobId of Array.from(selectedCompareJobIds)) {
    if (!valid.has(jobId)) selectedCompareJobIds.delete(jobId);
  }
  updateCompareBar();
}

function toggleCompareSelection(jobId, checked) {
  if (checked) {
    selectedCompareJobIds.add(jobId);
  } else {
    selectedCompareJobIds.delete(jobId);
  }
  updateCompareBar();
}

function updateCompareBar() {
  if (!batchCompareBar) return;
  const count = selectedCompareJobIds.size;
  const inListView = !selectedBatchJobId && (batchCompareView?.hidden ?? true);
  batchCompareBar.hidden = !inListView || count === 0;
  if (batchCompareCount) batchCompareCount.textContent = `已选 ${count} 个批次`;
  if (batchCompareBtn) batchCompareBtn.disabled = count < 2;
}

function showCompareView() {
  const files = batchFilesCache.filter((file) => selectedCompareJobIds.has(file.job_id));
  if (files.length < 2) return;
  batchCompareBody.innerHTML = renderScoreDistributionComparison(files, batchConfigState);
  batchFileList.hidden = true;
  if (batchFileToolbar) batchFileToolbar.hidden = true;
  if (batchFileVersionFilters) batchFileVersionFilters.hidden = true;
  batchCompareBar.hidden = true;
  batchFileResultCount.parentElement.style.display = "none";
  batchCompareView.hidden = false;
}

function backFromCompareView() {
  batchCompareView.hidden = true;
  batchFileList.hidden = false;
  if (batchFileVersionFilters) batchFileVersionFilters.hidden = false;
  batchFileResultCount.parentElement.style.display = "";
  updateCompareBar();
}

function syncBatchFileRefreshTimer(files) {
  const hasRunningJobs = files.some((file) => ["pending", "running"].includes(file.status));
  if (hasRunningJobs && !batchFileRefreshTimer) {
    batchFileRefreshTimer = setInterval(refreshRunningBatchFiles, 2000);
  } else if (!hasRunningJobs && batchFileRefreshTimer) {
    clearInterval(batchFileRefreshTimer);
    batchFileRefreshTimer = null;
  }
}

async function refreshRunningBatchFiles() {
  if (selectedBatchJobId) return;
  const runningCards = batchFileList.querySelectorAll(".result-card.running[data-job-id]");
  if (!runningCards.length) {
    if (batchFileRefreshTimer) {
      clearInterval(batchFileRefreshTimer);
      batchFileRefreshTimer = null;
    }
    return;
  }

  let completedSinceLastRefresh = false;
  for (const card of runningCards) {
    try {
      const job = await batchFetchJson(`/api/jobs/${card.dataset.jobId}`);
      if (job && ["pending", "running"].includes(job.status)) {
        updateBatchRunningCardInPlace(job);
      } else {
        completedSinceLastRefresh = true;
      }
    } catch {
      completedSinceLastRefresh = true;
    }
  }

  if (completedSinceLastRefresh) {
    await loadBatchFiles();
  }
}

function updateBatchRunningCardInPlace(job) {
  const card = batchFileList.querySelector(`.result-card.running[data-job-id="${job.job_id}"]`);
  if (!card) return;
  const totalRows = Math.max(Number(job.total_rows) || 0, 0);
  const processedRows = Math.max(Number(job.processed_rows) || 0, 0);
  const percent = totalRows > 0 ? Math.round(processedRows / totalRows * 100) : 0;
  const completedRowText = `${processedRows}/${totalRows} 条`;

  const progress = card.querySelector("progress");
  if (progress) {
    progress.value = processedRows;
    progress.max = Math.max(totalRows, 1);
  }
  const progressText = card.querySelector(".file-progress span");
  if (progressText) {
    progressText.textContent = `${percent}% · ${completedRowText}`;
  }
  const countEl = card.querySelector(".bug-count");
  if (countEl) {
    countEl.textContent = completedRowText;
  }
  const metaEl = card.querySelector(".result-meta");
  if (metaEl) {
    metaEl.textContent = `Job: ${shortJobId(job.job_id)} · ${completedRowText} · ${formatDate(job.created_at)}`;
  }
  const okPill = card.querySelector(".stat-pill.ok");
  if (okPill) okPill.textContent = `成功 ${job.success_count}`;
  const badPill = card.querySelector(".stat-pill.bad");
  if (badPill) badPill.textContent = `失败 ${job.failure_count}`;
}

// ── File detail: drill into results ─────────────────────────────
function selectBatchFile(file) {
  selectedBatchJobId = file.job_id;
  batchDetailPage = 1;
  // Hide file list area (not the entire panel)
  batchFileList.hidden = true;
  batchFileToolbar.hidden = true;
  if (batchCompareBar) batchCompareBar.hidden = true;
  if (batchFileVersionFilters) batchFileVersionFilters.hidden = true;
  batchFileResultCount.parentElement.style.display = "none";
  // Show detail view
  batchFileDetailView.hidden = false;
  batchDetailUrlBlock.hidden = true;
  loadBatchDetailResults();
}

function backToBatchFileList() {
  selectedBatchJobId = null;
  batchDetailPage = 1;
  // Restore file list area
  batchFileList.hidden = false;
  batchFileToolbar.hidden = false;
  if (batchFileVersionFilters) batchFileVersionFilters.hidden = false;
  batchFileResultCount.parentElement.style.display = "";
  // Hide detail view
  batchFileDetailView.hidden = true;
  batchDetailUrlBlock.hidden = true;
  batchDetailList.innerHTML = "";
  loadBatchFiles();
}

async function loadBatchDetailResults() {
  if (!selectedBatchJobId) return;
  batchDetailSummary.textContent = "加载中...";
  try {
    const params = new URLSearchParams();
    params.set("job_id", selectedBatchJobId);
    params.set("result_type", "llm_judged");
    params.set("page", String(batchDetailPage));
    params.set("page_size", String(resultsPageSize));
    const filters = batchDetailFilterParams();
    for (const [key, value] of filters.entries()) {
      params.set(key, value);
    }
    batchDetailResultUrl.textContent = `/api/results?${params.toString()}`;
    batchDetailUrlBlock.hidden = false;
    const page = await batchFetchJson(`/api/results?${params.toString()}`);
    renderBatchDetailResults(page);
  } catch (error) {
    batchDetailSummary.textContent = `加载失败: ${error.message}`;
  }
}

function renderBatchDetailResults(page) {
  batchDetailList.innerHTML = "";
  const items = page.items || [];
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "暂无结果";
    batchDetailList.append(empty);
    batchDetailSummary.textContent = "共 0 条";
  } else {
    for (const item of items) {
      batchDetailList.append(renderResultCard(item));
    }
    batchDetailSummary.textContent = `共 ${page.total} 条`;
  }

  const totalPages = Math.max(Math.ceil((page.total || 0) / resultsPageSize), 1);
  batchDetailToolbar.hidden = page.total === 0;
  batchDetailToolbar.style.display = page.total === 0 ? "none" : "";
  batchDetailPageInfo.innerHTML = `
    <button id="backToBatchFiles" type="button" class="secondary">返回批次列表</button>
    <span>第 ${page.page} / ${totalPages} 页</span>
  `;
  document.querySelector("#backToBatchFiles")?.addEventListener("click", backToBatchFileList);
  batchDetailPrevPage.disabled = page.page <= 1;
  batchDetailNextPage.disabled = page.page >= totalPages;
}

function batchDetailFilterParams() {
  const params = new URLSearchParams();
  if (!batchDetailFilters) return params;
  const form = new FormData(batchDetailFilters);
  const judgeTarget = String(form.get("judge_target") || "").trim();
  const score = String(form.get("score") || "").trim();
  const scoreSort = String(form.get("score_sort") || "").trim();
  if (judgeTarget) params.set("judge_target", judgeTarget);
  if (score) params.set("score", score);
  if (scoreSort) params.set("score_sort", scoreSort);
  return params;
}

// ── File actions ────────────────────────────────────────────────
async function deleteBatchJob(jobId) {
  if (!confirm(`确认删除批次 ${shortJobId(jobId)} 及其所有相关数据吗？此操作不可撤销。`)) return;
  try {
    await batchFetchJson(`/api/jobs/${jobId}`, {method: "DELETE"});
    if (selectedBatchJobId === jobId) backToBatchFileList();
    else await loadBatchFiles();
  } catch (error) {
    alert(`删除失败: ${error.message}`);
  }
}

async function cancelBatchJob(jobId, button) {
  if (!confirm("确认停止该批次任务吗？已完成的项目会保留。")) return;
  button.disabled = true;
  button.textContent = "停止中...";
  try {
    await batchFetchJson(`/api/jobs/${jobId}/cancel`, {method: "POST"});
    button.textContent = "已停止";
    await loadBatchFiles();
  } catch (error) {
    alert(`停止失败: ${error.message}`);
    button.disabled = false;
    button.textContent = "停止";
  }
}

async function exportBatchExcel(jobId, filename) {
  try {
    const response = await fetch(`/api/llm-judge/${jobId}/export`);
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = (filename || jobId).replace(/\.jsonl$/, "") + "_judge.xlsx";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  } catch (error) {
    alert(`导出失败: ${error.message}`);
  }
}

async function toggleBatchJobErrors(card, jobId, button) {
  const existing = card.querySelector(".file-error-panel");
  if (existing) {
    existing.remove();
    button.textContent = "失败原因";
    return;
  }
  button.disabled = true;
  button.textContent = "加载中...";
  try {
    const job = await batchFetchJson(`/api/jobs/${jobId}`);
    const panel = document.createElement("div");
    panel.className = "file-error-panel";
    panel.innerHTML = renderJobErrors(job.errors || []);
    card.append(panel);
    button.textContent = "收起原因";
  } catch (error) {
    alert(`加载失败原因失败: ${error.message}`);
    button.textContent = "失败原因";
  } finally {
    button.disabled = false;
  }
}

async function resumeBatchJob(jobId, button) {
  if (!confirm("确认继续该批次任务的未完成项吗？")) return;
  button.disabled = true;
  button.textContent = "继续中...";
  try {
    const job = await batchFetchJson(`/api/jobs/${jobId}/resume`, {method: "POST"});
    batchResponseStatus.textContent = "批次任务已继续";
    selectedBatchJobId = null;
    await loadBatchFiles();
    watchBatchJob(job.job_id, job.total_rows);
  } catch (error) {
    alert(`继续失败: ${error.message}`);
    button.disabled = false;
    button.textContent = "继续";
  }
}

async function retryBatchFailures(jobId) {
  if (!confirm("确认重试该批次的所有失败项吗？")) return;
  try {
    const job = await batchFetchJson(`/api/llm-judge/${jobId}/retry-failures`, {method: "POST"});
    batchResponseStatus.textContent = "批次失败重试任务已创建";
    selectedBatchJobId = null;
    await loadBatchFiles();
    watchBatchJob(job.job_id, job.total_rows);
  } catch (error) {
    alert(`重试失败: ${error.message}`);
  }
}

// ── Job polling for newly submitted / resumed / retried jobs ────
let batchJobTimer = null;

function watchBatchJob(jobId, totalRows) {
  if (batchJobTimer) clearInterval(batchJobTimer);
  latestJobId = jobId;
  batchJobStatusSection.hidden = false;
  pollBatchJob(jobId);
  batchJobTimer = setInterval(() => pollBatchJob(jobId), 1500);
}

async function pollBatchJob(jobId) {
  try {
    const job = await batchFetchJson(`/api/jobs/${jobId}`);
    batchJobStatusUrl.textContent = `/api/jobs/${jobId}`;
    renderPayload(batchJobStatusBody, job);

    if (job && batchConfigState) {
      const cfg = batchConfigState;
      const judgeId = lookupVersionId(cfg.judge_versions, job.judge_llm_version_name);
      const narrativeId = lookupVersionId(cfg.narrative_judge_prompt_versions, job.narrative_judge_prompt_version_name || job.judge_prompt_version_name);
      const choiceId = lookupVersionId(cfg.choice_judge_prompt_versions, job.choice_judge_prompt_version_name);
      const combineId = lookupVersionId(cfg.prompt_combine_versions, job.prompt_combine_version_name);
      document.querySelector("#batchJobVersionInfo").hidden = false;
      const versionItems = [
        { cls: "ver-judge",   label: "Judge API", id: judgeId,   name: job.judge_llm_version_name || "未知" },
        { cls: "ver-narr",    label: "正文 Prompt", id: narrativeId, name: job.narrative_judge_prompt_version_name || job.judge_prompt_version_name || "未知" },
        { cls: "ver-choice",  label: "选项 Prompt", id: choiceId,   name: job.choice_judge_prompt_version_name || "未知" },
        { cls: "ver-combine", label: "对话模版",     id: combineId,  name: job.prompt_combine_version_name || "未知" },
      ];
      document.querySelector("#batchJobVersionIds").innerHTML = versionItems.map(
        (v) => `<span class="version-item ${v.cls}"><em>${v.label}</em>${formatVersionIdLabel(v.id, v.name, "未知")}</span>`
      ).join("");
    }

    if (job && isTerminalStatus(job.status)) {
      clearInterval(batchJobTimer);
      batchJobTimer = null;
      await loadBatchFiles();
    }
  } catch (error) {
    // Silently retry
  }
}

// ── Event listeners: form ───────────────────────────────────────
batchTestForm.addEventListener("input", updateRequestUrl);
batchTestForm.addEventListener("change", updateRequestUrl);

batchTestForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  let records;
  try {
    records = JSON.parse(batchRequestBody.value);
  } catch (error) {
    batchResponseStatus.textContent = "JSON 解析失败";
    renderPayload(batchResponseBody, {error: error.message});
    return;
  }

  submitBatchRequest.disabled = true;
  batchResponseStatus.textContent = "请求中...";
  latestJobId = "";
  batchJobStatusSection.hidden = true;
  const submitUrl = buildRequestUrl();
  batchSubmitUrl.textContent = submitUrl;
  try {
    const response = await fetch(submitUrl, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(records),
    });
    const payload = await readJsonResponse(response);
    batchResponseStatus.textContent = `HTTP ${response.status} ${response.statusText}`;
    renderPayload(batchResponseBody, payload);
    latestJobId = payload?.job_id || "";
    if (latestJobId) {
      watchBatchJob(latestJobId, (records || []).length);
    }
  } catch (error) {
    batchResponseStatus.textContent = "请求失败";
    renderPayload(batchResponseBody, {error: error.message});
  } finally {
    submitBatchRequest.disabled = false;
  }
});

document.querySelector("#formatBatchBody").addEventListener("click", () => {
  try {
    batchRequestBody.value = formatJson(JSON.parse(batchRequestBody.value));
    batchResponseStatus.textContent = "JSON 已格式化";
  } catch (error) {
    batchResponseStatus.textContent = "JSON 解析失败";
    renderPayload(batchResponseBody, {error: error.message});
  }
});

batchJsonlFile.addEventListener("change", async () => {
  const file = batchJsonlFile.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const lines = text.split(/\r?\n/).filter((line) => line.trim());
    const records = [];
    for (const [index, line] of lines.entries()) {
      try {
        records.push(JSON.parse(line));
      } catch (error) {
        batchResponseStatus.textContent = `JSONL 解析失败：第 ${index + 1} 行不是合法 JSON`;
        renderPayload(batchResponseBody, {error: `Line ${index + 1}: ${error.message}`});
        return;
      }
    }
    batchRequestBody.value = formatJson(records);
    batchResponseStatus.textContent = `已从 ${file.name} 导入 ${records.length} 条记录`;
    updateRequestUrl();
  } catch (error) {
    batchResponseStatus.textContent = "文件读取失败";
    renderPayload(batchResponseBody, {error: error.message});
  } finally {
    batchJsonlFile.value = "";
  }
});

document.querySelector("#resetBatchBody").addEventListener("click", () => {
  resetBody();
  batchResponseStatus.textContent = "已恢复示例";
});

saveBatchDefaultsBtn.addEventListener("click", async () => {
  try {
    JSON.parse(batchRequestBody.value);
  } catch (error) {
    batchResponseStatus.textContent = "JSON 格式错误，无法保存默认值";
    return;
  }
  saveBatchDefaultsBtn.disabled = true;
  batchResponseStatus.textContent = "设置中...";
  try {
    const activatePairs = [
      { name: "judge_version_id", url: "/api/config/judge/{id}/activate" },
      { name: "narrative_judge_prompt_version_id", url: "/api/config/judge-prompts/{id}/activate/narrative" },
      { name: "choice_judge_prompt_version_id", url: "/api/config/judge-prompts/{id}/activate/choice" },
      { name: "prompt_combine_version_id", url: "/api/config/prompt-combine/{id}/activate" },
    ];
    for (const { name, url } of activatePairs) {
      const select = batchTestForm.elements[name];
      const versionId = select && !select.disabled ? select.value : "";
      if (!versionId) continue;
      await fetch(url.replace("{id}", versionId), { method: "POST" });
    }
    const defaults = {};
    defaults.body = batchRequestBody.value;
    const concurrencyInput = batchTestForm.elements["concurrency"];
    if (concurrencyInput && concurrencyInput.value) {
      defaults.concurrency = concurrencyInput.value;
    }
    const retryInput = batchTestForm.elements["judge_retry_count"];
    if (retryInput && retryInput.value) {
      defaults.judge_retry_count = retryInput.value;
    }
    localStorage.setItem(BATCH_DEFAULTS_KEY, JSON.stringify(defaults));
    await loadConfigAndPopulateSelects();
    batchResponseStatus.textContent = "已保存为默认值";
  } catch (error) {
    batchResponseStatus.textContent = `设置失败: ${error.message}`;
  } finally {
    saveBatchDefaultsBtn.disabled = false;
  }
});

document.querySelector("#refreshBatchJob").addEventListener("click", async () => {
  if (latestJobId) await pollBatchJob(latestJobId);
});

// ── Event listeners: file list ──────────────────────────────────
if (batchFileSortSelect) {
  batchFileSortSelect.addEventListener("change", () => loadBatchFiles());
}

if (batchCompareBtn) {
  batchCompareBtn.addEventListener("click", showCompareView);
}
if (batchCompareClear) {
  batchCompareClear.addEventListener("click", () => {
    selectedCompareJobIds.clear();
    for (const checkbox of batchFileList.querySelectorAll(".file-compare-checkbox")) {
      checkbox.checked = false;
    }
    updateCompareBar();
  });
}
if (batchCompareBack) {
  batchCompareBack.addEventListener("click", backFromCompareView);
}

if (batchFileVersionFilters) {
  // Filter the already-fetched list client-side; no refetch needed.
  batchFileVersionFilters.addEventListener("change", reapplyVersionFilters);
  document.querySelector("#resetBatchFileVersionFilters")?.addEventListener("click", () => {
    batchFileVersionFilters.reset();
    reapplyVersionFilters();
  });
}

if (batchDetailFilters) {
  batchDetailFilters.addEventListener("submit", async (event) => {
    event.preventDefault();
    batchDetailPage = 1;
    await loadBatchDetailResults();
  });
}

document.querySelector("#resetBatchDetailFilters")?.addEventListener("click", async () => {
  if (!batchDetailFilters) return;
  batchDetailFilters.reset();
  batchDetailPage = 1;
  await loadBatchDetailResults();
});

batchDetailPrevPage.addEventListener("click", async () => {
  if (batchDetailPage > 1) {
    batchDetailPage -= 1;
    await loadBatchDetailResults();
  }
});

batchDetailNextPage.addEventListener("click", async () => {
  batchDetailPage += 1;
  await loadBatchDetailResults();
});

// ── URL / form helpers ──────────────────────────────────────────
function buildRequestUrl() {
  const params = new URLSearchParams();
  for (const element of batchTestForm.elements) {
    if (!element.name || element.name === "records") continue;
    const value = element.value.trim();
    if (value) params.set(element.name, value);
  }
  const query = params.toString();
  return `/api/llm-judge/batch${query ? `?${query}` : ""}`;
}

function updateRequestUrl() {
  batchRequestUrl.textContent = buildRequestUrl();
}

function resetBody() {
  batchRequestBody.value = formatJson(sampleRecords);
  for (const name of VERSION_SELECT_NAMES) {
    const select = batchTestForm.elements[name];
    if (select) select.value = "";
  }
  const concurrencyInput = batchTestForm.elements["concurrency"];
  if (concurrencyInput) concurrencyInput.value = "";
  const retryInput = batchTestForm.elements["judge_retry_count"];
  if (retryInput) retryInput.value = "";
  updateRequestUrl();
}

// ── Event delegation: copy judge prompt ─────────────────────────
document.addEventListener("click", (event) => {
  const btn = event.target.closest(".copy-prompt-btn");
  if (!btn) return;
  const content = btn.dataset.content;
  if (!content) return;
  navigator.clipboard.writeText(content).then(() => {
    btn.classList.add("copied");
    setTimeout(() => btn.classList.remove("copied"), 1500);
  }).catch(() => {});
});

// ── Event delegation: detail tab switching ──────────────────────
document.addEventListener("click", (event) => {
  const tabButton = event.target.closest(".detail-tab");
  if (!tabButton) return;
  const details = tabButton.closest(".result-details");
  if (!details) return;
  details.querySelectorAll(".detail-tab").forEach((button) => {
    button.classList.toggle("active", button === tabButton);
  });
  details.querySelectorAll(".detail-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.detailPanel === tabButton.dataset.detailTab);
  });
});

// ── Init ────────────────────────────────────────────────────────
(async function init() {
  await loadConfigAndPopulateSelects();
  loadDefaults();
  if (!localStorage.getItem(BATCH_DEFAULTS_KEY)) {
    resetBody();
  } else {
    updateRequestUrl();
  }
  // Load batch file history on page load
  await loadBatchFiles();
})();
