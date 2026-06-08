// Deprecated /llm-judge page script. Do not change this page unless the request
// explicitly targets /llm-judge; active judge work should use /llm-judge/batch-test.
const uploadForm = document.querySelector("#uploadForm");
const uploadStatus = document.querySelector("#uploadStatus");
const jobProgress = document.querySelector("#jobProgress");
const jobIdEl = document.querySelector("#jobId");
const jobState = document.querySelector("#jobState");
const progressBar = document.querySelector("#progressBar");
const jobErrors = document.querySelector("#jobErrors");
const stopJobButton = document.querySelector("#stopJobButton");
const jsonlFileInput = document.querySelector("#jsonlFile");
const filterForm = document.querySelector("#filterForm");
const filenameFilterField = filterForm?.querySelector('[data-filter-field="filename"]');
const pageSizeFilterField = filterForm?.querySelector('[data-filter-field="page-size"]');
const resultsList = document.querySelector("#resultsList");
const resultCount = document.querySelector("#resultCount");
const resultToolbar = document.querySelector("#resultToolbar");
const pageInfo = document.querySelector("#pageInfo");
const prevPage = document.querySelector("#prevPage");
const nextPage = document.querySelector("#nextPage");
const pageMode = document.body.dataset.mode || "";
const uploadEndpoint = document.body.dataset.uploadEndpoint || "/api/llm-judge/upload";

const generateVersionSelect = null;
const judgeVersionSelect = document.querySelector("#judgeVersionSelect");
const templateVersionSelect = null;
const promptCombineVersionSelect = document.querySelector("#promptCombineVersionSelect");
const judgePromptVersionSelect = document.querySelector("#judgePromptVersionSelect");
const narrativeJudgePromptVersionSelect = document.querySelector("#narrativeJudgePromptVersionSelect");
const choiceJudgePromptVersionSelect = document.querySelector("#choiceJudgePromptVersionSelect");
const concurrencyForm = document.querySelector("#concurrencyForm");
const concurrencyStatus = document.querySelector("#concurrencyStatus");
const fileSortSelect = document.querySelector("#fileSortSelect");
let activeJobsList = null;

let activeJobTimer = null;
let activeJobsTimer = null;
let currentPage = 1;
let currentResultPage = null;
let selectedJudgeJobId = null;
let selectedJudgeState = null;
let activeJobId = null;
let activeJobLabel = "任务";
let activeJobShowsPanel = true;

initStateFromUrl();

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      if (!response.ok) {
        throw new Error(text);
      }
      throw error;
    }
  }
  if (!response.ok) {
    throw new Error(payload?.detail || `HTTP ${response.status}`);
  }
  return payload;
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = jsonlFileInput?.files[0];
  const formData = new FormData();
  const versionId = selectedVersionId(judgeVersionSelect);
  if (!versionId) {
    uploadStatus.textContent = "请选择裁判 LLM API";
    return;
  }
  const narrativeJudgePromptId = selectedVersionId(narrativeJudgePromptVersionSelect || judgePromptVersionSelect);
  const choiceJudgePromptId = selectedVersionId(choiceJudgePromptVersionSelect || judgePromptVersionSelect);
  if (!narrativeJudgePromptId || !choiceJudgePromptId) {
    uploadStatus.textContent = "请选择正文和选项 Judge Prompt";
    return;
  }
  const promptCombineId = selectedVersionId(promptCombineVersionSelect);
  if (!file) {
    uploadStatus.textContent = "请选择 .jsonl 文件";
    return;
  }
  formData.append("file", file);
  formData.append("judge_version_id", versionId);
  formData.append("narrative_judge_prompt_version_id", narrativeJudgePromptId);
  formData.append("choice_judge_prompt_version_id", choiceJudgePromptId);
  if (promptCombineId) {
    formData.append("prompt_combine_version_id", promptCombineId);
  }
  uploadStatus.textContent = "上传中...";
  try {
    const job = await fetchJson(uploadEndpoint, {
      method: "POST",
      body: formData,
    });
    uploadStatus.textContent = "任务已创建";
    watchJob(
      job.job_id,
      "Judge llm response 任务",
      job.total_rows,
      {showPanel: false},
    );
    selectedJudgeJobId = null;
    selectedJudgeState = null;
    await loadLlmJudgeFiles();
  } catch (error) {
    uploadStatus.textContent = error.message;
  }
});

let taskConfigState = null;

async function loadTaskApiVersions() {
  if (
    !judgeVersionSelect &&
    !promptCombineVersionSelect &&
    !judgePromptVersionSelect &&
    !narrativeJudgePromptVersionSelect &&
    !choiceJudgePromptVersionSelect
  ) {
    return;
  }
  try {
    const state = await fetchJson("/api/config");
    taskConfigState = state;
    fillTaskVersionSelect(
      judgeVersionSelect,
      state.judge_versions || [],
      state.active_judge_id,
      "暂无裁判 LLM API 版本",
    );
    fillTaskVersionSelect(
      promptCombineVersionSelect,
      state.prompt_combine_versions || [],
      state.active_prompt_combine_id,
      "暂无对话 prompt 模版版本",
    );
    fillTaskVersionSelect(
      judgePromptVersionSelect,
      state.judge_prompt_versions || [],
      state.active_judge_prompt_id,
      "暂无 Judge Prompt 版本",
    );
    fillTaskVersionSelect(
      narrativeJudgePromptVersionSelect,
      state.narrative_judge_prompt_versions || [],
      state.active_narrative_judge_prompt_id,
      "暂无正文 Judge Prompt 版本",
    );
    fillTaskVersionSelect(
      choiceJudgePromptVersionSelect,
      state.choice_judge_prompt_versions || [],
      state.active_choice_judge_prompt_id,
      "暂无选项 Judge Prompt 版本",
    );
  } catch (error) {
    if (uploadStatus) {
      uploadStatus.textContent = error.message;
    }
  }
}

function fillTaskVersionSelect(select, versions, activeId, emptyText) {
  if (!select) {
    return;
  }
  select.innerHTML = "";
  if (!versions.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = emptyText;
    select.append(option);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "默认";
  select.append(defaultOption);
  for (const version of versions) {
    const option = document.createElement("option");
    option.value = version.id;
    const isDefault = version.id === activeId;
    option.textContent = `[ID: ${version.id}] ${version.name}${isDefault ? "（当前默认）" : ""}`;
    select.append(option);
  }
  select.value = "";
}

function selectedVersionId(select) {
  return select && !select.disabled ? select.value : "";
}

function watchJob(jobId, label = "任务", initialTotalRows = 1, options = {}) {
  if (activeJobTimer) {
    clearInterval(activeJobTimer);
  }
  const showPanel = options.showPanel !== false;
  const storageKey = `activeJob_${pageMode}`;
  activeJobId = jobId;
  activeJobLabel = label;
  activeJobShowsPanel = showPanel;
  localStorage.setItem(storageKey, jobId);
  localStorage.setItem(`${storageKey}_label`, label);
  localStorage.setItem(`${storageKey}_showPanel`, showPanel ? "1" : "0");
  rememberActiveJob(jobId, label, pageMode);
  startActiveJobsTimer();
  if (showPanel) {
    showInitialJobProgress(jobId, initialTotalRows);
  } else {
    hideJobProgressPanel();
  }
  pollJob(jobId);
  activeJobTimer = setInterval(() => pollJob(jobId), 1500);
}

function activeJobsStorageKey(mode = pageMode) {
  return `activeJobs_${mode || "default"}`;
}

function readActiveJobs(mode = pageMode) {
  try {
    const value = JSON.parse(localStorage.getItem(activeJobsStorageKey(mode)) || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function writeActiveJobs(jobs, mode = pageMode) {
  localStorage.setItem(activeJobsStorageKey(mode), JSON.stringify(jobs));
}

function rememberActiveJob(jobId, label, mode) {
  const jobs = readActiveJobs(mode).filter((job) => job.jobId !== jobId);
  jobs.push({jobId, label, mode});
  writeActiveJobs(jobs, mode);
}

function removeActiveJob(jobId, mode = pageMode) {
  writeActiveJobs(readActiveJobs(mode).filter((job) => job.jobId !== jobId), mode);
}

function activeJobsContainer() {
  if (activeJobsList) return activeJobsList;
  const anchor = jobProgress || uploadForm || document.querySelector(".upload-panel");
  if (!anchor) return null;
  activeJobsList = document.createElement("div");
  activeJobsList.className = "active-jobs-list";
  anchor.insertAdjacentElement("afterend", activeJobsList);
  return activeJobsList;
}

function startActiveJobsTimer() {
  if (activeJobsTimer) return;
  refreshActiveJobs();
  activeJobsTimer = setInterval(refreshActiveJobs, 2000);
}

async function refreshActiveJobs() {
  const jobs = readActiveJobs();
  const container = activeJobsContainer();
  if (!container) return;
  if (!jobs.length) {
    container.innerHTML = "";
    if (activeJobsTimer) {
      clearInterval(activeJobsTimer);
      activeJobsTimer = null;
    }
    return;
  }

  const stillRunning = [];
  const rows = [];
  let completedSinceLastRefresh = false;
  for (const saved of jobs) {
    try {
      const job = await fetchJson(`/api/jobs/${saved.jobId}`);
      if (["pending", "running"].includes(job.status)) {
        stillRunning.push(saved);
        updateFileCardInPlace(job, saved.mode);
        rows.push(`
          <div class="active-job-row">
            <span>${escapeHtml(saved.label)} · ${escapeHtml(shortJobId(job.job_id))}</span>
            <progress value="${job.processed_rows}" max="${Math.max(job.total_rows, 1)}"></progress>
            <span>${escapeHtml(jobStatusLabel(job.status))} ${job.processed_rows}/${job.total_rows}</span>
            <button type="button" class="secondary active-job-open" data-job-id="${escapeHtml(job.job_id)}" data-mode="${escapeHtml(saved.mode)}">查看</button>
            <button type="button" class="danger active-job-stop" data-job-id="${escapeHtml(job.job_id)}">停止</button>
          </div>
        `);
      } else {
        completedSinceLastRefresh = true;
      }
    } catch {
      // Drop jobs that no longer exist or cannot be loaded.
    }
  }
  writeActiveJobs(stillRunning);
  container.innerHTML = rows.length ? `<div class="active-jobs-title">运行中任务</div>${rows.join("")}` : "";
  if (completedSinceLastRefresh) {
    await refreshFileListForCurrentMode();
  }
}

function hideJobProgressPanel() {
  if (!jobProgress) return;
  jobProgress.hidden = true;
  if (stopJobButton) {
    stopJobButton.hidden = true;
    stopJobButton.disabled = false;
    stopJobButton.textContent = "停止";
  }
}

function showInitialJobProgress(jobId, totalRows) {
  if (!jobProgress) return;
  const max = Math.max(Number(totalRows) || 0, 1);
  jobProgress.hidden = false;
  progressBar.max = max;
  progressBar.value = 0;
  jobIdEl.textContent = `${activeJobLabel}: ${shortJobId(jobId)}`;
  jobState.textContent = `${jobStatusLabel("pending")} 0/${Number(totalRows) || 0}`;
  jobErrors.textContent = "";
  if (stopJobButton) {
    stopJobButton.hidden = false;
    stopJobButton.disabled = false;
    stopJobButton.textContent = "停止";
  }
}

function updateJobProgressPanel(job) {
  if (!activeJobShowsPanel) {
    hideJobProgressPanel();
    return;
  }
  if (!jobProgress) return;
  const isRunning = ["pending", "running"].includes(job.status);
  const max = Math.max(job.total_rows, 1);
  jobProgress.hidden = false;
  progressBar.max = max;
  progressBar.value = Math.min(job.processed_rows, max);
  jobIdEl.textContent = `${activeJobLabel}: ${shortJobId(job.job_id)}`;
  jobState.textContent = `${jobStatusLabel(job.status)} ${job.processed_rows}/${job.total_rows}`;
  jobErrors.textContent = job.errors.length ? JSON.stringify(job.errors, null, 2) : "";
  if (stopJobButton) {
    stopJobButton.hidden = !isRunning;
    stopJobButton.disabled = false;
    stopJobButton.textContent = "停止";
  }
}

function updateRunningCardInPlace(job) {
  const card = document.querySelector(`.result-card.running[data-job-id="${job.job_id}"]`);
  if (!card) return;
  const percent = job.total_rows > 0 ? Math.round(job.processed_rows / job.total_rows * 100) : 0;
  const progress = card.querySelector("progress");
  if (progress) {
    progress.value = job.processed_rows;
    progress.max = job.total_rows;
  }
  const progressSpan = card.querySelector(".file-progress span");
  if (progressSpan) {
    progressSpan.textContent = `${percent}% · ${job.processed_rows}/${job.total_rows} 条`;
  }
  const countEl = card.querySelector(".bug-count");
  if (countEl) {
    countEl.textContent = `${job.processed_rows}/${job.total_rows} 条`;
  }
  const metaEl = card.querySelector(".result-meta");
  if (metaEl) {
    metaEl.textContent = `Job: ${shortJobId(job.job_id)} · ${job.processed_rows}/${job.total_rows} 条 · ${formatDate(job.created_at)}`;
  }
  const okPill = card.querySelector(".stat-pill.ok");
  if (okPill) okPill.textContent = `成功 ${job.success_count}`;
  const badPill = card.querySelector(".stat-pill.bad");
  if (badPill) badPill.textContent = `失败 ${job.failure_count}`;
}

function updateJudgeRunningCardInPlace(job) {
  updateRunningCardInPlace(job);
}

function updateFileCardInPlace(job, mode) {
  if (mode !== pageMode) {
    return;
  }
  if (mode === "llm-judged") {
    updateJudgeRunningCardInPlace(job);
  }
}

async function refreshFileListForCurrentMode() {
  if (pageMode === "llm-judged" && !selectedJudgeJobId) {
    await loadLlmJudgeFiles();
  }
}

async function pollJob(jobId) {
  try {
    const job = await fetchJson(`/api/jobs/${jobId}`);
    updateJobProgressPanel(job);
    if (!["pending", "running"].includes(job.status)) {
      clearInterval(activeJobTimer);
      activeJobTimer = null;
      activeJobId = null;
      removeActiveJob(job.job_id);
      refreshActiveJobs();
      if (stopJobButton) stopJobButton.hidden = true;
      const storageKey = `activeJob_${pageMode}`;
      localStorage.removeItem(storageKey);
      localStorage.removeItem(`${storageKey}_label`);
      localStorage.removeItem(`${storageKey}_showPanel`);
      if (pageMode === "llm-judged") {
        if (selectedJudgeJobId === job.job_id) {
          await loadResults();
        } else {
          selectedJudgeJobId = null;
          selectedJudgeState = null;
          await loadLlmJudgeFiles();
        }
      } else {
        await loadResults();
      }
    } else if (pageMode === "llm-judged") {
      updateJudgeRunningCardInPlace(job);
    }
  } catch (error) {
    if (jobState) jobState.textContent = error.message;
  }
}

filterForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  currentPage = 1;
  await loadResults();
});

prevPage.addEventListener("click", () => {
  if (currentPage > 1) {
    currentPage -= 1;
    loadResults();
  }
});
nextPage.addEventListener("click", () => {
  if (currentResultPage && currentPage * currentResultPage.page_size < currentResultPage.total) {
    currentPage += 1;
    loadResults();
  }
});

async function loadResults() {
  if (pageMode === "llm-judged" && !selectedJudgeJobId) {
    await loadLlmJudgeFiles();
    return;
  }

  const params = new URLSearchParams();
  const form = new FormData(filterForm);
  const filename = form.get("source_filename")?.trim();
  const pageSize = form.get("page_size") || "20";
  if (filename) {
    params.set("source_filename", filename);
  }
  params.set("result_type", "llm_judged");
  if (selectedJudgeJobId) {
    params.set("job_id", selectedJudgeJobId);
  }
  if (selectedJudgeState) {
    params.set("judge_state", selectedJudgeState);
  }
  params.set("page", String(currentPage));
  params.set("page_size", pageSize);
  try {
    setResultsListMode();
    syncStateToUrl();
    showLoadingRows("加载结果中...");
    const page = await fetchJson(`/api/results?${params.toString()}`);
    currentResultPage = page;
    const stateText = selectedJudgeState ? ` · ${judgeStateLabel(selectedJudgeState)}` : "";
    resultCount.textContent = selectedJudgeJobId
      ? `${page.total} 条明细${stateText}`
      : `${page.total} 条`;
    renderPager(page);
    renderResults(page.items);
  } catch (error) {
    resultCount.textContent = error.message;
  }
}

async function loadLlmJudgeFiles() {
  if (pageMode !== "llm-judged" || selectedJudgeJobId) {
    return;
  }
  setResultsListMode();
  syncStateToUrl();
  showLoadingRows("加载 Judge llm response 文件中...");
  try {
    const sortBy = fileSortSelect?.value || "created";
    const allFiles = await fetchJson(`/api/llm-judge/files?sort_by=${sortBy}`);
    // Exclude batch-generated files; they belong to the Batch API 测试 page
    const files = allFiles.filter((f) => (f.filename || "") !== "batch");
    resultCount.textContent = `${files.length} 个文件`;
    renderLlmJudgeFiles(files);
  } catch (error) {
    resultCount.textContent = error.message;
  }
}

function showLoadingRows(text) {
  if (!resultsList) return;
  resultsList.innerHTML = `
    <div class="skeleton-row"></div>
    <div class="skeleton-row"></div>
    <div class="skeleton-row short"></div>
    <div class="empty-state">${escapeHtml(text)}</div>
  `;
}

function filterFilesByFilename(files) {
  const keyword = filterForm.querySelector("[name='source_filename']")?.value?.trim();
  if (!keyword) return files;
  return files.filter((f) => (f.filename || "").includes(keyword));
}

function getJudgeFileCallbacks() {
  return {
    deleteJob: (jobId) => deleteJob(jobId),
    cancelJob: (jobId, button) => cancelJobFromCard(jobId, button),
    exportExcel: (jobId, filename) => exportJudgeExcel(jobId, filename),
    showErrors: (card, jobId, button) => toggleJobErrors(card, jobId, button),
    resumeJob: (jobId, button) => resumeJobFromCard(jobId, button),
    retryFailures: (jobId) => retryLlmJudgeFailures(jobId),
    onSelectFile: (file) => {
      selectedJudgeJobId = file.job_id;
      selectedJudgeState = null;
      currentPage = 1;
      loadResults();
    },
  };
}

function renderLlmJudgeFiles(files) {
  const filtered = filterFilesByFilename(files);
  resultsList.innerHTML = "";
  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = files.length ? "无匹配的文件" : "暂无文件统计";
    resultsList.append(empty);
    resultCount.textContent = `0 / ${files.length} 个文件`;
    return;
  }
  resultCount.textContent = filtered.length < files.length
    ? `${filtered.length} / ${files.length} 个文件`
    : `${files.length} 个文件`;
  const callbacks = getJudgeFileCallbacks();
  for (const file of filtered) {
    resultsList.append(renderLlmJudgeFileCard(file, callbacks, taskConfigState));
  }
}

async function toggleJobErrors(card, jobId, button) {
  const existing = card.querySelector(".file-error-panel");
  if (existing) {
    existing.remove();
    button.textContent = "失败原因";
    return;
  }
  button.disabled = true;
  button.textContent = "加载中...";
  try {
    const job = await fetchJson(`/api/jobs/${jobId}`);
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

function setResultsListMode() {
  filterForm.hidden = false;
  filterForm.style.display = "";
  if (!selectedJudgeJobId) {
    setResultToolbarVisible(false);
    pageInfo.textContent = "";
  }
}

function initStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  currentPage = Math.max(Number(params.get("page")) || 1, 1);
  if (pageMode === "llm-judged") {
    selectedJudgeJobId = params.get("job") || null;
  }
}

function syncStateToUrl() {
  const params = new URLSearchParams();
  if (pageMode === "llm-judged" && selectedJudgeJobId) {
    params.set("job", selectedJudgeJobId);
  }
  if (currentPage > 1) {
    params.set("page", String(currentPage));
  }
  const query = params.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}`;
  if (nextUrl !== `${window.location.pathname}${window.location.search}`) {
    window.history.replaceState({}, "", nextUrl);
  }
}

function setResultToolbarVisible(visible) {
  resultToolbar.hidden = !visible;
  resultToolbar.style.display = visible ? "" : "none";
}

function renderPager(page) {
  const totalPages = Math.max(Math.ceil(page.total / page.page_size), 1);
  setResultToolbarVisible(page.total > 0);
  if (selectedJudgeJobId) {
    const backId = "backToJudgeFiles";
    const backText = "返回文件统计";
    pageInfo.innerHTML = `
      <button id="${backId}" type="button" class="secondary">${backText}</button>
      <span>第 ${page.page} / ${totalPages} 页</span>
    `;
    document.querySelector(`#${backId}`).addEventListener("click", () => {
      currentPage = 1;
      selectedJudgeJobId = null;
      selectedJudgeState = null;
      loadLlmJudgeFiles();
    });
  } else {
    pageInfo.textContent = `第 ${page.page} / ${totalPages} 页`;
  }
  prevPage.disabled = page.page <= 1;
  nextPage.disabled = page.page >= totalPages;
}

function renderResults(items) {
  resultsList.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "暂无结果";
    resultsList.append(empty);
    return;
  }
  for (const item of items) {
    resultsList.append(renderResultCard(item));
  }
}

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

document.addEventListener("click", async (event) => {
  const stop = event.target.closest(".active-job-stop");
  if (stop) {
    const jobId = stop.dataset.jobId;
    if (!jobId || !confirm("确认停止该任务吗？已完成的项目会保留。")) return;
    stop.disabled = true;
    await fetchJson(`/api/jobs/${jobId}/cancel`, {method: "POST"}).catch((error) => alert(`停止失败: ${error.message}`));
    await refreshActiveJobs();
    return;
  }
  const open = event.target.closest(".active-job-open");
  if (open) {
    const jobId = open.dataset.jobId;
    window.location.href = `/llm-judge?job=${encodeURIComponent(jobId)}`;
  }
});

function renderJudgeResultTable(results) {
  const rows = results.map((result) => `
    <tr>
      <td>${escapeHtml(judgeTargetLabel(result))}</td>
      <td>${renderJudgeText(result.judge)}</td>
    </tr>
  `).join("");
  return `
    <table class="bug-table">
      <thead><tr><th>Target</th><th>Judge</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderJudgeText(value) {
  const judge = normalizeJudgeValue(value);
  const state = judge.state === "exists" ? "bad" : (judge.state === "not_exists" ? "ok" : "other");
  const reason = judge.reason
    ? `<div class="judge-reason">${escapeHtml(judge.reason)}</div>`
    : "";
  return `<span class="judge-text ${state}">${escapeHtml(judge.label)}</span>${reason}`;
}

async function retryLlmJudgeFailures(jobId) {
  if (!confirm(`确认重试该 Judge llm response 文件的所有失败项吗？`)) {
    return;
  }
  try {
    const job = await fetchJson(`/api/llm-judge/${jobId}/retry-failures`, {
      method: "POST",
    });
    uploadStatus.textContent = "Judge llm response 失败重试任务已创建";
    selectedJudgeJobId = null;
    selectedJudgeState = null;
    watchJob(job.job_id, "Judge llm response 失败重试", job.total_rows, {showPanel: false});
    await loadLlmJudgeFiles();
  } catch (error) {
    alert(`重试失败: ${error.message}`);
  }
}

async function cancelJobFromCard(jobId, button) {
  if (!confirm(`确认停止该任务吗？已完成的项目会保留。`)) return;
  button.disabled = true;
  button.textContent = "停止中...";
  try {
    await fetchJson(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    button.textContent = "已停止";
    await refreshAfterJobCancel(jobId);
  } catch (error) {
    alert(`停止失败: ${error.message}`);
    button.disabled = false;
    button.textContent = "停止";
  }
}

async function refreshAfterJobCancel(jobId) {
  if (pageMode === "llm-judged") {
    if (selectedJudgeJobId === jobId) {
      selectedJudgeJobId = null;
      selectedJudgeState = null;
    }
    await loadLlmJudgeFiles();
  } else {
    await loadResults();
  }
}

async function resumeJobFromCard(jobId, button) {
  if (!confirm(`确认继续该任务的未完成项吗？`)) return;
  button.disabled = true;
  button.textContent = "继续中...";
  try {
    const job = await fetchJson(`/api/jobs/${jobId}/resume`, { method: "POST" });
    uploadStatus.textContent = "任务已继续";
    if (pageMode === "llm-judged") {
      selectedJudgeJobId = null;
      selectedJudgeState = null;
      await loadLlmJudgeFiles();
      watchJob(job.job_id, "Judge llm response 任务", job.total_rows, {showPanel: false});
    } else {
      watchJob(job.job_id, "任务", job.total_rows, {showPanel: true});
    }
  } catch (error) {
    alert(`继续失败: ${error.message}`);
    button.disabled = false;
    button.textContent = "继续";
  }
}

async function deleteJob(jobId) {
  if (!confirm(`确认删除任务 ${shortJobId(jobId)} 及其所有相关数据吗？此操作不可撤销。`)) {
    return;
  }
  try {
    await fetchJson(`/api/jobs/${jobId}`, {method: "DELETE"});
    if (pageMode === "llm-judged") {
      selectedJudgeJobId = null;
      selectedJudgeState = null;
      await loadLlmJudgeFiles();
    }
  } catch (error) {
    alert(`删除失败: ${error.message}`);
  }
}

async function exportJudgeExcel(jobId, filename) {
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

if (pageMode === "llm-judged") {
  loadLlmJudgeFiles();
} else {
  loadResults();
}
loadTaskApiVersions();
loadConcurrencySetting();

(async () => {
  const storageKey = `activeJob_${pageMode}`;
  const savedJobId = localStorage.getItem(storageKey);
  if (!savedJobId) return;
  try {
    const job = await fetchJson(`/api/jobs/${savedJobId}`);
    if (["pending", "running"].includes(job.status)) {
      const savedLabel = localStorage.getItem(`${storageKey}_label`) || "任务";
      const showPanel = localStorage.getItem(`${storageKey}_showPanel`) !== "0";
      watchJob(savedJobId, savedLabel, job.total_rows, {showPanel});
    } else {
      localStorage.removeItem(storageKey);
      localStorage.removeItem(`${storageKey}_label`);
      localStorage.removeItem(`${storageKey}_showPanel`);
    }
  } catch {
    localStorage.removeItem(storageKey);
    localStorage.removeItem(`${storageKey}_label`);
    localStorage.removeItem(`${storageKey}_showPanel`);
  }
})();

if (fileSortSelect) {
  fileSortSelect.addEventListener("change", () => {
    if (pageMode === "llm-judged") loadLlmJudgeFiles();
  });
}

startActiveJobsTimer();

if (stopJobButton) {
  stopJobButton.addEventListener("click", async () => {
    if (!activeJobId) return;
    if (!confirm(`确认停止当前任务吗？已完成的项目会保留。`)) return;
    const jobId = activeJobId;
    stopJobButton.disabled = true;
    try {
      await fetchJson(`/api/jobs/${jobId}/cancel`, { method: "POST" });
      stopJobButton.textContent = "停止中...";
      await pollJob(jobId);
    } catch (error) {
      alert(`停止失败: ${error.message}`);
      stopJobButton.disabled = false;
    }
  });
}

document.querySelectorAll(".set-default-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const selectId = btn.dataset.select;
    const select = selectId ? document.querySelector(`#${selectId}`) : null;
    const versionId = select && !select.disabled ? select.value : "";
    if (!versionId) {
      alert("请先选择要设为默认的版本");
      return;
    }
    btn.disabled = true;
    try {
      const url = btn.dataset.endpoint.replace("{id}", versionId);
      await fetchJson(url, { method: "POST" });
      await loadTaskApiVersions();
    } catch (error) {
      alert(`设置失败: ${error.message}`);
    } finally {
      btn.disabled = false;
    }
  });
});

document.querySelectorAll(".set-defaults-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const selectIds = (btn.dataset.selects || "").split(",");
    const endpoints = (btn.dataset.endpoints || "").split(",");
    const pairs = [];
    for (let i = 0; i < selectIds.length; i++) {
      const select = document.querySelector(`#${selectIds[i]}`);
      const versionId = select && !select.disabled ? select.value : "";
      if (!versionId) {
        alert("请先选择所有版本");
        return;
      }
      pairs.push({ versionId, url: endpoints[i].replace("{id}", versionId) });
    }
    btn.disabled = true;
    try {
      for (const { url } of pairs) {
        await fetchJson(url, { method: "POST" });
      }
      await loadTaskApiVersions();
    } catch (error) {
      alert(`设置失败: ${error.message}`);
    } finally {
      btn.disabled = false;
    }
  });
});

async function loadConcurrencySetting() {
  if (!concurrencyForm) return;
  try {
    const state = await fetchJson("/api/config");
    const settings = state.runtime_settings || {};
    if (pageMode === "llm-judged") {
      concurrencyForm.elements.judge_concurrency.value = settings.judge_concurrency ?? 50;
    }
  } catch (error) {
    if (concurrencyStatus) concurrencyStatus.textContent = error.message;
  }
}

if (concurrencyForm) {
  concurrencyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (concurrencyStatus) concurrencyStatus.textContent = "保存中...";
    try {
      const payload = {
        judge_concurrency: Number(concurrencyForm.elements.judge_concurrency.value || 50),
      };
      const settings = await fetchJson("/api/config/runtime", {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      if (pageMode === "llm-judged") {
        concurrencyForm.elements.judge_concurrency.value = settings.judge_concurrency;
      }
      if (concurrencyStatus) concurrencyStatus.textContent = "已保存";
    } catch (error) {
      if (concurrencyStatus) concurrencyStatus.textContent = error.message;
    }
  });
}
