// ── Shared judge rendering utilities ─────────────────────────────
// Used by both llm_judge.html (app.js) and llm_judge_batch_test.html

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function shortJobId(value) {
  const text = String(value || "");
  return text.length > 12 ? `${text.slice(0, 12)}...` : text;
}

function compactText(value, maxLength) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) {
    return text || "无输出内容";
  }
  return `${text.slice(0, maxLength)}...`;
}

// ── Judge normalization ─────────────────────────────────────────
function normalizeJudgeValue(value) {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
      try {
        return normalizeJudgeValue(JSON.parse(trimmed));
      } catch (error) {
        // Fall through to legacy plain-text handling below.
      }
    }
  }

  if (value && typeof value === "object" && !Array.isArray(value)) {
    if ("overall_score" in value || "overall_evidence" in value || "parse_status" in value) {
      const score = value.overall_score ?? "无";
      const evidence = typeof value.overall_evidence === "string" ? value.overall_evidence : "";
      const status = value.parse_status === "valid" ? "有效 JSON" : "其他";
      return {
        state: "other",
        label: `得分：${score} · ${status}`,
        pillLabel: String(score),
        reason: evidence || (value.raw_output ? String(value.raw_output) : ""),
        detail: evidence || status,
      };
    }
    const result = value.result;
    const reason = typeof value.reason === "string" ? value.reason : "";
    if (result === 1 || result === "1" || result === true) {
      return {
        state: "exists",
        label: "存在",
        pillLabel: String(result),
        reason,
        detail: reason ? `存在：${reason}` : "存在",
      };
    }
    if (result === 0 || result === "0" || result === false) {
      return {
        state: "not_exists",
        label: "不存在",
        pillLabel: String(result),
        reason,
        detail: reason ? `不存在：${reason}` : "不存在",
      };
    }
    const fallback = JSON.stringify(value);
    return {
      state: "other",
      label: fallback,
      pillLabel: value.result === undefined ? fallback : String(value.result),
      reason: "",
      detail: fallback,
    };
  }

  const text = String(value || "");
  if (text.includes("不存在")) {
    return {state: "not_exists", label: text, pillLabel: text, reason: "", detail: text};
  }
  if (text.includes("存在")) {
    return {state: "exists", label: text, pillLabel: text, reason: "", detail: text};
  }
  return {state: "other", label: text, pillLabel: text, reason: "", detail: text};
}

function parseJsonFromPossiblyFencedText(text) {
  const trimmed = String(text || "").trim();
  const fenced = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  const candidate = fenced ? fenced[1].trim() : trimmed;
  if (!candidate.startsWith("{") || !candidate.endsWith("}")) {
    return null;
  }
  try {
    return JSON.parse(candidate);
  } catch (error) {
    return null;
  }
}

function parseJudgePayload(value) {
  let payload = value;
  let rawText = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
      try {
        payload = JSON.parse(trimmed);
      } catch (error) {
        payload = null;
      }
    } else {
      payload = null;
    }
  }

  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    if (payload.raw_json && typeof payload.raw_json === "object") {
      return {
        summaryPayload: payload.raw_json,
        rawText,
        parseStatus: payload.parse_status,
        codeFenceParsed: false,
      };
    }
    if (typeof payload.raw_output === "string") {
      const extracted = parseJsonFromPossiblyFencedText(payload.raw_output);
      if (extracted) {
        return {
          summaryPayload: extracted,
          rawText,
          parseStatus: payload.parse_status,
          codeFenceParsed: true,
        };
      }
    }
  }

  return {
    summaryPayload: payload,
    rawText,
    parseStatus: payload && typeof payload === "object" ? payload.parse_status : "",
    codeFenceParsed: false,
  };
}

function summarizeJudgePayload(value) {
  const parsed = parseJudgePayload(value);
  const payload = parsed.summaryPayload;
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const parseStatus = parsed.parseStatus || payload.parse_status || "";
    const score = payload.overall_score ?? null;
    const evidence = typeof payload.overall_evidence === "string" ? payload.overall_evidence : "";
    const statusLabel = parseStatus === "valid"
      ? "有效 JSON"
      : (parsed.codeFenceParsed ? "已从原始内容提取 JSON" : "未结构化");
    return {
      payload,
      rawText: parsed.rawText,
      scoreLabel: score === null || score === undefined ? "无评分" : `评分 ${score}`,
      grade: typeof payload.overall_grade === "string" ? payload.overall_grade : "",
      statusLabel,
      statusClass: parseStatus === "valid" || parsed.codeFenceParsed ? "ok" : "other",
      subtitle: "Judge llm response",
      evidence,
    };
  }

  const normalized = normalizeJudgeValue(value);
  return {
    payload: null,
    rawText: parsed.rawText,
    scoreLabel: normalized.label,
    grade: "",
    statusLabel: normalized.state === "exists" ? "存在" : (normalized.state === "not_exists" ? "不存在" : "其他"),
    statusClass: normalized.state === "not_exists" ? "ok" : (normalized.state === "exists" ? "bad" : "other"),
    subtitle: normalized.reason || normalized.detail || "",
    evidence: normalized.reason,
  };
}

function judgeRawText(result, summary) {
  if (result?.raw_output) {
    return String(result.raw_output);
  }
  if (result?.judge?.raw_output) {
    return String(result.judge.raw_output);
  }
  return summary.rawText || JSON.stringify(result, null, 2);
}

function judgeTargetLabel(result) {
  return result?.target || "Judge";
}

function judgeResultsForItem(item) {
  if (item?.judge_result && typeof item.judge_result === "object" && Object.keys(item.judge_result).length) {
    return [item.judge_result];
  }
  return [];
}

// ── Judge card rendering ────────────────────────────────────────
function renderJudgePills(results) {
  return results.map((result) => {
    const judge = normalizeJudgeValue(result.judge);
    const state = judge.state === "exists" ? "bad" : (judge.state === "not_exists" ? "ok" : "other");
    const target = judgeTargetLabel(result);
    return `
      <span class="judge-pill ${state}" title="${escapeHtml(target)}：${escapeHtml(judge.detail)}">
        <span class="judge-bug-name">${escapeHtml(target)}</span>
        <span class="judge-value">${escapeHtml(judge.pillLabel)}</span>
      </span>
    `;
  }).join("");
}

function renderDimensionScores(payload) {
  const scores = payload?.dimension_scores;
  if (!scores || typeof scores !== "object" || Array.isArray(scores)) {
    return "";
  }
  return `
    <div class="dimension-score-grid">
      ${Object.entries(scores).map(([name, value]) => {
        const score = value && typeof value === "object" ? value.score : "";
        const evidence = value && typeof value === "object" ? value.evidence : "";
        return `
          <div class="dimension-score" title="${escapeHtml(String(evidence || ""))}">
            <span>${escapeHtml(name)}</span>
            <strong>${escapeHtml(String(score || "-"))}</strong>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function isFiniteScore(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function formatScoreValue(value) {
  if (!isFiniteScore(value)) {
    return "-";
  }
  return Number(value.toFixed(4)).toString();
}

function formatWeightValue(value) {
  if (!isFiniteScore(value)) {
    return "";
  }
  return `${Number((value * 100).toFixed(2)).toString()}%`;
}

function scoreBreakdownFromDimensions(payload) {
  const scores = payload?.dimension_scores;
  if (!scores || typeof scores !== "object" || Array.isArray(scores)) {
    return null;
  }
  const components = Object.entries(scores).map(([name, value]) => {
    const score = value && typeof value === "object" ? value.score : value;
    const evidence = value && typeof value === "object" ? value.evidence : "";
    return {
      label: name,
      score,
      contribution: score,
      meta: evidence ? `依据：${evidence}` : "",
    };
  }).filter((item) => isFiniteScore(item.score));

  if (!components.length) {
    return null;
  }
  return {
    mode: "dimensions",
    title: "分项评分",
    components,
  };
}

function scoreBreakdownFromJudge(judge, payload) {
  if (judge?.components && Array.isArray(judge.components)) {
    const components = judge.components.map((component) => ({
      label: component.label || component.path || component.name || "score",
      score: component.score,
      contribution: component.contribution,
      meta: [
        isFiniteScore(component.normalized_weight) ? `平均权重 ${formatWeightValue(component.normalized_weight)}` : "",
        isFiniteScore(component.contribution) ? `平均贡献 ${formatScoreValue(component.contribution)}` : "",
      ].filter(Boolean).join(" · "),
    })).filter((item) => isFiniteScore(item.score) || isFiniteScore(item.contribution));
    if (components.length) {
      return {
        mode: "summary",
        title: judge.title || "分数构成概览",
        totalScore: judge.total_score,
        components,
      };
    }
  }

  const breakdown = judge?.score_breakdown;
  const components = Array.isArray(breakdown?.components) ? breakdown.components : [];
  const usableComponents = components.map((component) => {
    const score = component.score;
    const contribution = component.contribution;
    const normalizedWeight = component.normalized_weight;
    const label = component.path || component.name || "score";
    const contributionText = isFiniteScore(contribution)
      ? `贡献 ${formatScoreValue(contribution)}`
      : "贡献 -";
    const weightText = isFiniteScore(normalizedWeight)
      ? `权重 ${formatWeightValue(normalizedWeight)}`
      : "";
    return {
      label,
      score,
      contribution,
      meta: [weightText, contributionText].filter(Boolean).join(" · "),
    };
  }).filter((item) => isFiniteScore(item.score) || isFiniteScore(item.contribution));

  if (usableComponents.length) {
    const totalScore = breakdown?.total_score ?? judge?.overall_score;
    return {
      mode: breakdown?.mode || "weighted",
      title: "分数构成",
      totalScore,
      components: usableComponents,
    };
  }
  return scoreBreakdownFromDimensions(payload);
}

function renderScoreBreakdownChart(judge, payload) {
  const breakdown = scoreBreakdownFromJudge(judge, payload);
  if (!breakdown) {
    return "";
  }
  const values = breakdown.components
    .map((item) => isFiniteScore(item.contribution) ? item.contribution : item.score)
    .filter(isFiniteScore)
    .map((value) => Math.abs(value));
  const maxValue = Math.max(...values, 1);
  const total = isFiniteScore(breakdown.totalScore)
    ? `<span>总分 ${escapeHtml(formatScoreValue(breakdown.totalScore))}</span>`
    : "";

  return `
    <details class="score-breakdown-details">
      <summary>
        <span>${escapeHtml(breakdown.title)}</span>
        ${total}
      </summary>
      <div class="score-breakdown-chart">
        ${breakdown.components.map((item) => {
          const value = isFiniteScore(item.contribution) ? item.contribution : item.score;
          const width = isFiniteScore(value) ? Math.max(3, Math.min(100, Math.abs(value) / maxValue * 100)) : 0;
          const valueLabel = isFiniteScore(item.contribution)
            ? `${formatScoreValue(item.score)} -> ${formatScoreValue(item.contribution)}`
            : formatScoreValue(item.score);
          return `
            <div class="score-breakdown-row">
              <div class="score-breakdown-label" title="${escapeHtml(item.label)}">${escapeHtml(item.label)}</div>
              <div class="score-breakdown-track" aria-hidden="true">
                <div class="score-breakdown-bar" style="width:${width}%"></div>
              </div>
              <div class="score-breakdown-value">${escapeHtml(valueLabel)}</div>
              ${item.meta ? `<div class="score-breakdown-meta">${escapeHtml(item.meta)}</div>` : ""}
            </div>
          `;
        }).join("")}
      </div>
    </details>
  `;
}

function renderFileScoreBreakdownSummary(file) {
  const summary = file?.score_breakdown_summary;
  if (summary && Array.isArray(summary.score_distribution) && summary.score_distribution.length) {
    return renderFileScoreDistribution(summary);
  }
  if (!summary || typeof summary !== "object" || !Array.isArray(summary.components) || !summary.components.length) {
    return "";
  }
  return `<div class="file-score-breakdown">${renderScoreBreakdownChart(summary, null)}</div>`;
}

function renderFileScoreDistribution(summary) {
  const distributions = summary.score_distribution || [];
  const maxCount = Math.max(
    ...distributions.flatMap((group) => (group.buckets || []).map((bucket) => Number(bucket.count) || 0)),
    1,
  );
  return `
    <div class="file-score-breakdown score-distribution-summary">
      <div class="score-distribution-title">${escapeHtml(summary.title || "最终评分分布")}</div>
      <div class="score-distribution-grid">
        ${distributions.map((group) => `
          <div class="score-distribution-group">
            <div class="score-distribution-group-title">
              <span>${escapeHtml(judgeTargetDisplayLabel(group.target))}</span>
              <span>${escapeHtml(group.total || 0)} 条</span>
            </div>
            <div class="score-distribution-bars">
              ${(group.buckets || []).map((bucket) => {
                const count = Number(bucket.count) || 0;
                const width = Math.max(4, Math.min(100, count / maxCount * 100));
                return `
                  <div class="score-distribution-row">
                    <div class="score-distribution-label">${escapeHtml(bucket.score)}分</div>
                    <div class="score-distribution-track" aria-hidden="true">
                      <div class="score-distribution-bar" style="width:${width}%"></div>
                    </div>
                    <div class="score-distribution-count">${escapeHtml(count)}条</div>
                  </div>
                `;
              }).join("")}
            </div>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function judgeTargetDisplayLabel(target) {
  if (target === "narrative") return "叙事";
  if (target === "choice") return "选项";
  return target || "未知";
}

// ── Multi-job score distribution comparison ─────────────────────
const COMPARE_PALETTE = [
  "#3b82f6", "#ef4444", "#22c55e", "#f59e0b",
  "#a855f7", "#06b6d4", "#ec4899", "#84cc16",
];

function compareFileLabel(file) {
  return file.displayFilename || file.filename || shortJobId(file.job_id);
}

function compareFileMetaRows(file, taskConfigState) {
  const cfg = taskConfigState || {};
  const judgeId = lookupVersionId(cfg.judge_versions, file.judge_llm_version_name);
  const narrativePromptId = lookupVersionId(cfg.narrative_judge_prompt_versions, file.narrative_judge_prompt_version_name || file.judge_prompt_version_name);
  const choicePromptId = lookupVersionId(cfg.choice_judge_prompt_versions, file.choice_judge_prompt_version_name);
  const promptCombineId = lookupVersionId(cfg.prompt_combine_versions, file.prompt_combine_version_name);
  return [
    ["有效 JSON", String(file.valid_json_count || 0)],
    ["Judge API", formatVersionIdLabel(judgeId, escapeHtml(file.judge_llm_version_name || "未知版本"), "未知版本")],
    ["正文 Prompt", formatVersionIdLabel(narrativePromptId, escapeHtml(file.narrative_judge_prompt_version_name || file.judge_prompt_version_name || "未知"), "未知")],
    ["选项 Prompt", formatVersionIdLabel(choicePromptId, escapeHtml(file.choice_judge_prompt_version_name || "未知"), "未知")],
    ["对话模版", formatVersionIdLabel(promptCombineId, escapeHtml(file.prompt_combine_version_name || "未知"), "未知")],
  ];
}

function renderScoreDistributionComparison(files, taskConfigState) {
  const usable = (files || []).filter(
    (file) => Array.isArray(file?.score_breakdown_summary?.score_distribution)
      && file.score_breakdown_summary.score_distribution.length,
  );
  if (usable.length < 2) {
    return `<div class="empty-state">所选批次中至少需要 2 个包含评分分布的批次才能比对</div>`;
  }

  // Color each selected file consistently across all target groups.
  const colorByJob = new Map();
  usable.forEach((file, index) => {
    colorByJob.set(file.job_id, COMPARE_PALETTE[index % COMPARE_PALETTE.length]);
  });

  const legend = `
    <div class="compare-legend">
      ${usable.map((file) => `
        <div class="compare-legend-item">
          <div class="compare-legend-head">
            <span class="compare-legend-swatch" style="background:${colorByJob.get(file.job_id)}"></span>
            <span class="compare-legend-label" title="${escapeHtml(compareFileLabel(file))}">${escapeHtml(compareFileLabel(file))}</span>
          </div>
          <dl class="compare-legend-meta">
            ${compareFileMetaRows(file, taskConfigState).map(([key, value]) => `
              <div class="compare-legend-meta-row">
                <dt>${escapeHtml(key)}</dt>
                <dd title="${escapeHtml(value)}">${value}</dd>
              </div>
            `).join("")}
          </dl>
        </div>
      `).join("")}
    </div>
  `;

  // Aggregate buckets keyed by target, then by score label.
  // targetMap: target -> { scoreLabel -> { jobId -> count } }
  const targetMap = new Map();
  for (const file of usable) {
    for (const group of file.score_breakdown_summary.score_distribution) {
      const target = String(group.target || "unknown");
      const scoreMap = targetMap.get(target) || new Map();
      for (const bucket of (group.buckets || [])) {
        const label = String(bucket.score);
        const jobCounts = scoreMap.get(label) || new Map();
        jobCounts.set(file.job_id, (jobCounts.get(file.job_id) || 0) + (Number(bucket.count) || 0));
        scoreMap.set(label, jobCounts);
      }
      targetMap.set(target, scoreMap);
    }
  }

  const maxCount = Math.max(
    1,
    ...Array.from(targetMap.values()).flatMap((scoreMap) =>
      Array.from(scoreMap.values()).flatMap((jobCounts) => Array.from(jobCounts.values()))),
  );

  const targetOrder = {narrative: 0, choice: 1};
  const targets = Array.from(targetMap.keys()).sort(
    (a, b) => (targetOrder[a] ?? 99) - (targetOrder[b] ?? 99) || a.localeCompare(b),
  );

  const groupsHtml = targets.map((target) => {
    const scoreMap = targetMap.get(target);
    const scoreLabels = Array.from(scoreMap.keys()).sort((a, b) => {
      const na = Number(a);
      const nb = Number(b);
      const aNum = Number.isFinite(na);
      const bNum = Number.isFinite(nb);
      if (aNum && bNum) return na - nb;
      if (aNum) return -1;
      if (bNum) return 1;
      return a.localeCompare(b);
    });

    const rows = scoreLabels.map((label) => {
      const jobCounts = scoreMap.get(label);
      const bars = usable.map((file) => {
        const count = Number(jobCounts.get(file.job_id) || 0);
        const width = Math.max(0, Math.min(100, count / maxCount * 100));
        return `
          <div class="compare-bar-row" title="${escapeHtml(compareFileLabel(file))}：${count} 条">
            <div class="compare-bar-track" aria-hidden="true">
              <div class="compare-bar" style="width:${width}%;background:${colorByJob.get(file.job_id)}"></div>
            </div>
            <div class="compare-bar-count">${count}</div>
          </div>
        `;
      }).join("");
      return `
        <div class="compare-score-row">
          <div class="compare-score-label">${escapeHtml(label)}分</div>
          <div class="compare-bar-group">${bars}</div>
        </div>
      `;
    }).join("");

    return `
      <div class="compare-target-group">
        <div class="compare-target-title">${escapeHtml(judgeTargetDisplayLabel(target))}</div>
        <div class="compare-score-rows">${rows}</div>
      </div>
    `;
  }).join("");

  return `
    <div class="score-distribution-compare">
      ${legend}
      <div class="compare-target-grid">${groupsHtml}</div>
    </div>
  `;
}

function renderJudgeDetailLists(payload) {
  if (!payload || typeof payload !== "object") {
    return "";
  }
  const sections = [
    ["main_strengths", "主要优点"],
    ["main_weaknesses", "主要问题"],
    ["revision_suggestions", "修改建议"],
  ].map(([key, title]) => {
    const items = Array.isArray(payload[key]) ? payload[key].filter(Boolean).slice(0, 3) : [];
    if (!items.length) return "";
    return `
      <div class="judge-detail-list">
        <h4>${escapeHtml(title)}</h4>
        <ul>${items.map((item) => `<li>${escapeHtml(String(item))}</li>`).join("")}</ul>
      </div>
    `;
  }).join("");

  if (!sections.trim()) {
    return "";
  }
  return `<div class="judge-detail-lists">${sections}</div>`;
}

function renderJudgeCards(results) {
  return `
    <div class="judge-card-list">
      ${results.map((result) => renderJudgeCard(result)).join("")}
    </div>
  `;
}

function renderJudgeCard(result) {
  const summary = summarizeJudgePayload(result.judge);
  const rawText = judgeRawText(result, summary);
  const dimensions = renderDimensionScores(summary.payload);
  const scoreBreakdown = renderScoreBreakdownChart(result.judge, summary.payload);
  const detailLists = renderJudgeDetailLists(summary.payload);
  const evidence = summary.evidence
    ? `<p class="judge-summary-evidence">${escapeHtml(summary.evidence)}</p>`
    : "";
  return `
    <article class="judge-card">
      <div class="judge-card-head">
        <div>
          <div class="judge-card-title">${escapeHtml(judgeTargetLabel(result))}</div>
          <div class="judge-card-subtitle">${escapeHtml(summary.subtitle)}</div>
        </div>
        <div class="judge-card-metrics">
          ${summary.scoreLabel ? `<span class="judge-metric primary">${escapeHtml(summary.scoreLabel)}</span>` : ""}
          ${summary.grade ? `<span class="judge-metric">${escapeHtml(summary.grade)}</span>` : ""}
          <span class="judge-metric ${summary.statusClass}">${escapeHtml(summary.statusLabel)}</span>
        </div>
      </div>
      ${evidence}
      ${scoreBreakdown}
      ${dimensions}
      ${detailLists}
      <details class="raw-judge-details">
        <summary>查看原始内容</summary>
        <div class="text-block">${escapeHtml(rawText)}</div>
      </details>
    </article>
  `;
}

// ── Detail panel rendering ──────────────────────────────────────
function rawPayloadFieldText(item, fieldName) {
  const directValue = item?.[fieldName];
  if (typeof directValue === "string" && directValue.length) {
    return directValue;
  }
  let rawPayload = null;
  try {
    // Legacy rows stored the whole raw payload JSON in input.
    rawPayload = JSON.parse(item.input || item.output1 || "{}");
  } catch (error) {
    rawPayload = null;
  }
  if (!rawPayload) {
    return `原始数据中没有 ${fieldName} 字段`;
  }

  // Exact field name match
  if (Object.prototype.hasOwnProperty.call(rawPayload, fieldName)) {
    const value = rawPayload[fieldName];
    return typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }

  // Fallback: try common alternative field names inside raw_payload
  const fallbacks = {
    input: ["prompt"],
    output: ["response"],
  };
  for (const alt of (fallbacks[fieldName] || [])) {
    if (Object.prototype.hasOwnProperty.call(rawPayload, alt)) {
      const value = rawPayload[alt];
      return typeof value === "string" ? value : JSON.stringify(value, null, 2);
    }
  }

  return `原始数据中没有 ${fieldName} 字段`;
}

function renderPromptSection(item) {
  return `<div class="text-block">${escapeHtml(item.prompt)}</div>`;
}

function renderJudgeSection(item) {
  const judgeResults = judgeResultsForItem(item);
  if (judgeResults.length) {
    return `<h3>裁判结果</h3>${renderJudgeCards(judgeResults)}`;
  }
  return "";
}

function renderBugCountToken(item) {
  const judgeResults = judgeResultsForItem(item);
  if (!judgeResults.length) {
    return `<span class="title-token bug-count">no judge result</span>`;
  }
  return `<span class="title-token bug-count">${judgeResults.length} judge result</span>`;
}

function renderLlmJudgeDetails(item) {
  const inputText = rawPayloadFieldText(item, "input");
  const outputText = rawPayloadFieldText(item, "output");
  return `
    <div class="detail-tabs">
      <button type="button" class="detail-tab active" data-detail-tab="input">Input</button>
      <button type="button" class="detail-tab" data-detail-tab="output">Output</button>
      <button type="button" class="detail-tab" data-detail-tab="prompt">Prompt</button>
      <button type="button" class="detail-tab" data-detail-tab="judge">裁判</button>
    </div>
    <section class="detail-panel active" data-detail-panel="input">
      <div class="text-block">${escapeHtml(inputText)}</div>
    </section>
    <section class="detail-panel" data-detail-panel="output">
      <div class="text-block">${escapeHtml(outputText)}</div>
    </section>
    <section class="detail-panel" data-detail-panel="prompt">
      ${renderPromptSection(item)}
    </section>
    <section class="detail-panel" data-detail-panel="judge">
      ${renderJudgeSection(item)}
    </section>
  `;
}

function renderResultCard(item) {
  const judgeResults = judgeResultsForItem(item);
  const isJudgedResult = judgeResults.length > 0;
  const details = renderLlmJudgeDetails(item);
  const card = document.createElement("article");
  card.className = "result-card";
  card.innerHTML = `
    <div class="result-header">
      <div>
        <div class="result-title-row">
          <span class="title-token playthrough-token">${escapeHtml(item.playthrough_id)}</span>
          <span class="title-token turn-pill">Turn ${item.turn}</span>
          ${renderBugCountToken(item)}
        </div>
        <span class="result-meta">#${item.id} · ${escapeHtml(shortJobId(item.job_id))} · ${formatDate(item.created_at)}</span>
      </div>
      ${judgeResults.length ? `<div class="judge-strip">${renderJudgePills(judgeResults)}</div>` : ""}
    </div>
    ${isJudgedResult ? "" : `<div class="result-preview">${escapeHtml(compactText(item.input, 260))}</div>`}
    <details class="result-details">
      <summary>查看完整内容</summary>
      ${details}
    </details>
  `;
  return card;
}

// ── Shared utility functions ────────────────────────────────────
function lookupVersionId(versions, name) {
  if (!name || !versions) return null;
  const found = versions.find((v) => v.name === name);
  return found ? found.id : null;
}

function formatVersionIdLabel(id, name, fallback) {
  if (id != null) return `[ID:${id}] ${name}`;
  if (name) return `(已删除) ${name}`;
  return fallback || "未知";
}

function jobStatusLabel(value) {
  return {"pending": "等待中", "running": "运行中", "completed": "已完成", "completed_with_errors": "部分失败", "failed": "失败", "cancelled": "已取消"}[value] || value;
}

function judgeStateLabel(value) {
  if (value === "exists") return "存在";
  if (value === "not_exists") return "不存在";
  if (value === "other") return "其他";
  return value || "";
}

function renderJobErrors(errors) {
  if (!errors.length) {
    return `<div class="empty-state">该任务没有记录失败原因</div>`;
  }
  return `
    <div class="file-error-title">失败原因</div>
    <div class="file-error-list">
      ${errors.map((error, index) => `
        <div class="file-error-item">
          <div class="file-error-meta">
            #${index + 1}
            ${error.line_number !== undefined ? ` · line ${escapeHtml(error.line_number)}` : ""}
            ${error.playthrough_id !== undefined ? ` · ${escapeHtml(error.playthrough_id)}` : ""}
            ${error.turn !== undefined ? ` · Turn ${escapeHtml(error.turn)}` : ""}
          </div>
          <div class="file-error-message">${escapeHtml(error.message || JSON.stringify(error))}</div>
        </div>
      `).join("")}
    </div>
  `;
}

// ── Shared file card rendering (callback-based) ─────────────────
// callbacks: { deleteJob, cancelJob, exportExcel, showErrors, resumeJob, retryFailures }

function renderFileCard({file, mode, countText, statsHtml, actionText, onClick, onStatClick, isRunning, progressHtml, callbacks, taskConfigState, selectable, selected}) {
  const card = document.createElement("article");
  card.className = `result-card ${mode}-file-card${isRunning ? " running" : ""}`;
  card.dataset.jobId = file.job_id;
  const canSelect = selectable && !isRunning && Array.isArray(file?.score_breakdown_summary?.score_distribution) && file.score_breakdown_summary.score_distribution.length > 0;
  const selectCheckboxHtml = canSelect
    ? `<label class="file-compare-select" title="加入评分分布比对"><input type="checkbox" class="file-compare-checkbox" data-job-id="${escapeHtml(file.job_id)}"${selected ? " checked" : ""}><span>比对</span></label>`
    : "";
  const isJudgeLike = mode === "llm_judge";
  const detailDisabled = isJudgeLike && !isRunning && Number(file.result_count || 0) === 0;
  const emptyJudgeResult = isJudgeLike && !isRunning && Number(file.result_count || 0) === 0;
  const canResume = file.status === "cancelled" && Number(file.processed_rows || 0) < Number(file.total_rows || 0);
  const cfg = taskConfigState || {};
  const judgeId = lookupVersionId(cfg.judge_versions, file.judge_llm_version_name);
  const narrativePromptId = lookupVersionId(cfg.narrative_judge_prompt_versions, file.narrative_judge_prompt_version_name || file.judge_prompt_version_name);
  const choicePromptId = lookupVersionId(cfg.choice_judge_prompt_versions, file.choice_judge_prompt_version_name);
  const promptCombineId = lookupVersionId(cfg.prompt_combine_versions, file.prompt_combine_version_name);
  const versionToken = `
      <span class="title-token judge-source-token">上传</span>
      <span class="title-token judge-version-token">Judge API：${formatVersionIdLabel(judgeId, escapeHtml(file.judge_llm_version_name || "未知版本"), "未知版本")}</span>
      <span class="title-token judge-version-token">正文 Prompt：${formatVersionIdLabel(narrativePromptId, escapeHtml(file.narrative_judge_prompt_version_name || file.judge_prompt_version_name || "未知"), "未知")}</span>
      <span class="title-token judge-version-token">选项 Prompt：${formatVersionIdLabel(choicePromptId, escapeHtml(file.choice_judge_prompt_version_name || "未知"), "未知")}</span>
      <span class="title-token judge-version-token">对话模版：${formatVersionIdLabel(promptCombineId, escapeHtml(file.prompt_combine_version_name || "未知"), "未知")}</span>
      ${Number(file.concurrency) > 0 ? `<span class="title-token judge-version-token">并发：${escapeHtml(file.concurrency)}</span>` : ""}
      <span class="title-token judge-version-token">重试：${escapeHtml(file.judge_retry_count ?? 2)}次</span>
    `;
  const statusLabel = jobStatusLabel(file.status);
  const exportButtonHtml = isJudgeLike
    ? `<button type="button" class="file-export-button secondary" data-job-id="${escapeHtml(file.job_id)}"${emptyJudgeResult ? ' disabled title="没有可导出的裁判结果"' : ""}>导出Excel</button>`
    : "";
  const failureButtonHtml = !isRunning && file.failure_count > 0
    ? `<button type="button" class="file-error-button secondary" data-job-id="${escapeHtml(file.job_id)}">失败原因</button>`
    : "";
  const retryButtonHtml = isJudgeLike && !isRunning && file.failure_count > 0
    ? `<button type="button" class="retry-failures-button" data-job-id="${escapeHtml(file.job_id)}">失败重试</button>`
    : "";
  const resumeButtonHtml = canResume
    ? `<button type="button" class="file-resume-button secondary" data-job-id="${escapeHtml(file.job_id)}">继续</button>`
    : "";
  const runningDetailButtonHtml = mode === "llm_judge"
    ? `<button type="button" class="file-detail-button" data-job-id="${escapeHtml(file.job_id)}"${detailDisabled ? ' disabled title="没有可查看的裁判明细"' : ""}>${escapeHtml(actionText)}</button>`
    : "";
  const actionsHtml = isRunning
    ? `${runningDetailButtonHtml}
      <button type="button" class="file-stop-button danger" data-job-id="${escapeHtml(file.job_id)}">停止</button>
      <button type="button" class="file-delete-button secondary" data-job-id="${escapeHtml(file.job_id)}">删除</button>`
      : `<button type="button" class="file-detail-button" data-job-id="${escapeHtml(file.job_id)}"${detailDisabled ? ' disabled title="没有可查看的裁判明细"' : ""}>${escapeHtml(actionText)}</button>
      ${exportButtonHtml}
      ${failureButtonHtml}
      ${retryButtonHtml}
      ${resumeButtonHtml}
      <button type="button" class="file-delete-button secondary" data-job-id="${escapeHtml(file.job_id)}">删除</button>`;
  card.innerHTML = `
    <div class="result-header">
      <div>
        <div class="result-title-row">
          ${selectCheckboxHtml}
          <span class="title-token playthrough-token">${escapeHtml(file.filename || file.job_id)}</span>
          <span class="title-token turn-pill">${escapeHtml(statusLabel)}</span>
          <span class="title-token bug-count">${escapeHtml(countText)}</span>
          ${versionToken}
        </div>
        <span class="result-meta">Job: ${escapeHtml(shortJobId(file.job_id))} · ${escapeHtml(countText)} · ${formatDate(file.created_at)}</span>
      </div>
      <div class="file-stats">${statsHtml}</div>
    </div>
    ${progressHtml || ""}
    ${isJudgeLike ? renderFileScoreBreakdownSummary(file) : ""}
    <div class="file-card-actions">${actionsHtml}</div>
  `;
  const detailBtn = card.querySelector(".file-detail-button");
  if (detailBtn && !detailDisabled) {
    detailBtn.addEventListener("click", onClick);
  }
  card.querySelector(".file-delete-button").addEventListener("click", () => {
    if (callbacks && callbacks.deleteJob) callbacks.deleteJob(file.job_id);
  });
  const stopBtn = card.querySelector(".file-stop-button");
  if (stopBtn) {
    stopBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (callbacks && callbacks.cancelJob) callbacks.cancelJob(file.job_id, stopBtn);
    });
  }
  const exportBtn = card.querySelector(".file-export-button");
  if (exportBtn && !emptyJudgeResult) {
    exportBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (callbacks && callbacks.exportExcel) callbacks.exportExcel(file.job_id, file.filename || file.job_id);
    });
  }
  const errorBtn = card.querySelector(".file-error-button");
  if (errorBtn) {
    errorBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (callbacks && callbacks.showErrors) callbacks.showErrors(card, file.job_id, errorBtn);
    });
  }
  const resumeBtn = card.querySelector(".file-resume-button");
  if (resumeBtn) {
    resumeBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (callbacks && callbacks.resumeJob) callbacks.resumeJob(file.job_id, resumeBtn);
    });
  }
  const retryBtn = isJudgeLike ? card.querySelector(".retry-failures-button") : null;
  if (retryBtn) {
    retryBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (callbacks && callbacks.retryFailures) callbacks.retryFailures(file.job_id);
    });
  }
  const compareCheckbox = card.querySelector(".file-compare-checkbox");
  if (compareCheckbox) {
    compareCheckbox.addEventListener("click", (event) => event.stopPropagation());
    compareCheckbox.addEventListener("change", (event) => {
      if (callbacks && callbacks.onToggleSelect) {
        callbacks.onToggleSelect(file.job_id, event.target.checked);
      }
    });
  }
  if (onStatClick) {
    for (const button of card.querySelectorAll("[data-judge-state]")) {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        onStatClick(button.dataset.judgeState);
      });
    }
  }
  return card;
}

function renderLlmJudgeFileCard(file, callbacks, taskConfigState, selectState) {
  const isRunning = ["pending", "running"].includes(file.status);
  const progressPercent = file.total_rows > 0
    ? Math.round(file.processed_rows / file.total_rows * 100)
    : 0;
  const completedRowText = `${file.processed_rows}/${file.total_rows} 条`;
  const progressHtml = isRunning
    ? `<div class="file-progress">
        <progress value="${file.processed_rows}" max="${file.total_rows}"></progress>
        <span>${progressPercent}% · ${completedRowText}</span>
      </div>`
    : "";
  return renderFileCard({
    file,
    mode: "llm_judge",
    isRunning,
    countText: isRunning
      ? completedRowText
      : `${completedRowText} · 有效 JSON ${file.valid_json_count || 0}`,
    statsHtml: isRunning
      ? `
        <span class="stat-pill ok">成功 ${file.success_count}</span>
        <span class="stat-pill bad">失败 ${file.failure_count}</span>
      `
      : `
        <span class="stat-pill ok">有效 JSON ${file.valid_json_count || 0}</span>
        <span class="stat-pill neutral">其他 ${file.other_count || 0}</span>
      `,
    progressHtml,
    actionText: "查看文件明细",
    onClick: () => {
      if (callbacks && callbacks.onSelectFile) callbacks.onSelectFile(file);
    },
    callbacks,
    taskConfigState,
    selectable: Boolean(selectState?.selectable),
    selected: Boolean(selectState?.selectedIds?.has?.(file.job_id)),
  });
}
