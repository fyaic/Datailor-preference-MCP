const state = {
  tab: "all",
  query: "",
  manifesto: null
};

const i18n = {
  en: {
    appTitle: "Personal Preference Manifesto",
    summaryTitle: "1.1 Executive Summary",
    search: "Search",
    source: "Source",
    tabs: {
      all: "All",
      pending: "Pending",
      conflicts: "Conflicts",
      evolution: "Evolution",
      fitting: "Fitting",
      injection: "Injection",
      settings: "Settings"
    },
    titles: {
      all: "1.2 High-Frequency Preferences",
      pending: "1.3 Pending Confirmation",
      conflicts: "1.4 Conflict Review",
      evolution: "1.5 Evolution",
      fitting: "1.6 Fitting Report",
      injection: "1.7 Injection Log",
      settings: "1.8 Settings"
    },
    summary: {
      active: "Active preferences",
      pending: "Pending confirmation",
      conflicts: "Conflicts awaiting resolution",
      mode: "Effective mode"
    },
    empty: {
      section: "No records in this section.",
      profile: "No stable personal profile has been generated yet. Capture preferences first, then this section will summarize what the system understands about the user.",
      conflicts: "No A/B conflicts were detected. Pending items remain available in the Pending tab.",
      feedback: "No feedback has been recorded.",
      fitting: "No Fitting report has been generated yet.",
      injection: "No injection events have been recorded yet. Start a session or call get_preference_decision to populate this timeline.",
      missingSide: "Missing side."
    },
    table: {
      preference: "Preference",
      frequency: "Freq.",
      sessions: "Sessions",
      confidence: "Conf.",
      liveConfidence: "Live",
      status: "Status"
    },
    detail: {
      statement: "Statement",
      theorem: "Theorem.",
      definition: "Definition.",
      liveConfidence: "Live confidence.",
      factors: "Factors.",
      evidence: "Evidence.",
      noEvidence: "No stored evidence in the Markdown view.",
      confirm: "Confirm",
      reject: "Reject",
      correct: "Correct",
      correctionPrompt: "Correction",
      actionFailed: "The feedback was recorded, but the preference store was not updated."
    },
    conflict: {
      similarity: "Similarity",
      reviewRequired: "Review required.",
      confidence: "Confidence",
      scope: "Scope",
      notSpecified: "Not specified"
    },
    evolution: {
      preference: "Preference",
      use: "Use",
      confirm: "Confirm",
      correct: "Correct",
      reject: "Reject",
      recommendation: "Recommendation"
    },
    injection: {
      time: "Time",
      hook: "Hook",
      agent: "Agent",
      session: "Session",
      decision: "Decision",
      matched: "Matched",
      instruction: "Instruction",
      injected: "Injected",
      reason: "Reason",
      yes: "Yes",
      no: "No"
    },
    settings: {
      userMode: "User Mode",
      configuredMode: "Configured mode",
      effectiveMode: "Effective mode",
      auto: "Auto",
      autonomous: "Autonomous Trust",
      curated: "Curated Autonomy",
      theme: "Theme",
      light: "Light",
      dark: "Dark",
      language: "Language",
      english: "English",
      chinese: "Chinese"
    }
  },
  zh: {
    appTitle: "个人偏好画像",
    summaryTitle: "1.1 执行摘要",
    search: "搜索",
    source: "来源",
    tabs: {
      all: "全部",
      pending: "待确认",
      conflicts: "冲突",
      evolution: "演化",
      fitting: "Fitting",
      injection: "注入日志",
      settings: "设置"
    },
    titles: {
      all: "1.2 高频偏好",
      pending: "1.3 待确认偏好",
      conflicts: "1.4 冲突对比",
      evolution: "1.5 偏好演化",
      fitting: "1.6 Fitting 报告",
      injection: "1.7 注入日志",
      settings: "1.8 设置"
    },
    summary: {
      active: "已生效偏好",
      pending: "待确认偏好",
      conflicts: "待解决冲突",
      mode: "当前模式"
    },
    empty: {
      section: "本分区暂无记录。",
      profile: "还没有生成稳定的个人画像。请先捕获偏好，之后这里会总结系统当前对用户的理解。",
      conflicts: "未检测到 A/B 冲突。待确认内容仍可在“待确认”页查看。",
      feedback: "还没有记录反馈。",
      fitting: "还没有生成 Fitting 报告。",
      injection: "还没有注入事件。启动 session 或调用 get_preference_decision 后，这里会显示注入时间线。",
      missingSide: "缺少对比项。"
    },
    table: {
      preference: "偏好",
      frequency: "频次",
      sessions: "会话",
      confidence: "置信度",
      liveConfidence: "动态",
      status: "状态"
    },
    detail: {
      statement: "陈述",
      theorem: "注意。",
      definition: "证据。",
      liveConfidence: "动态置信度。",
      factors: "因子。",
      evidence: "证据。",
      noEvidence: "Markdown 视图中没有保存证据。",
      confirm: "确认",
      reject: "拒绝",
      correct: "纠正",
      correctionPrompt: "纠正内容",
      actionFailed: "反馈已记录，但偏好库未更新。"
    },
    conflict: {
      similarity: "相似度",
      reviewRequired: "需要人工确认。",
      confidence: "置信度",
      scope: "适用场景",
      notSpecified: "未指定"
    },
    evolution: {
      preference: "偏好",
      use: "使用",
      confirm: "确认",
      correct: "纠正",
      reject: "拒绝",
      recommendation: "建议"
    },
    injection: {
      time: "时间",
      hook: "Hook",
      agent: "Agent",
      session: "Session",
      decision: "决策",
      matched: "命中",
      instruction: "注入指令",
      injected: "已注入",
      reason: "原因",
      yes: "是",
      no: "否"
    },
    settings: {
      userMode: "用户模式",
      configuredMode: "配置模式",
      effectiveMode: "当前模式",
      auto: "自动",
      autonomous: "自主信任",
      curated: "审慎自主",
      theme: "主题",
      light: "浅色",
      dark: "深色",
      language: "语言",
      english: "英文",
      chinese: "中文"
    }
  }
};

function language() {
  const lang = state.manifesto && state.manifesto.language;
  return i18n[lang] ? lang : "en";
}

function t(path) {
  return path.split(".").reduce((value, key) => value && value[key], i18n[language()]) || path;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json"},
    ...options
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

async function load() {
  state.manifesto = await request("/api/manifesto");
  document.body.dataset.theme = state.manifesto.theme || "light";
  render();
}

function render() {
  const data = state.manifesto;
  if (!data) return;
  applyStaticLabels();
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.tab);
  });
  document.querySelectorAll("[data-language]").forEach((button) => {
    button.classList.toggle("active", button.dataset.language === language());
  });
  document.getElementById("active-count").textContent = data.summary.active;
  document.getElementById("pending-count").textContent = data.summary.pending;
  document.getElementById("conflict-count").textContent = data.summary.conflicts;
  document.getElementById("mode-label").textContent = data.mode;
  document.getElementById("store-path").textContent = `${t("source")}: ${data.store}`;
  document.getElementById("section-title").textContent = t(`titles.${state.tab}`) || t("titles.all");
  document.getElementById("executive-text").innerHTML = renderExecutiveSummary(data.executive_summary);

  const content = document.getElementById("content");
  if (state.tab === "evolution") {
    content.innerHTML = renderEvolution(data);
    return;
  }
  if (state.tab === "injection") {
    content.innerHTML = renderInjectionLog(data);
    return;
  }
  if (state.tab === "fitting") {
    content.innerHTML = renderFitting(data);
    return;
  }
  if (state.tab === "settings") {
    content.innerHTML = renderSettings(data);
    bindSettings();
    return;
  }
  if (state.tab === "conflicts") {
    content.innerHTML = renderConflictView(data);
    bindActions();
    return;
  }
  const rows = filteredPreferences(data);
  content.innerHTML = rows.length ? renderTable(rows) : `<p class="empty">${escapeHtml(t("empty.section"))}</p>`;
  bindActions();
}

function applyStaticLabels() {
  const lang = language();
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  document.title = t("appTitle");
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  const search = document.getElementById("search");
  if (search) {
    search.placeholder = t("search");
  }
}

function filteredPreferences(data) {
  let rows = [...data.preferences];
  if (state.tab === "pending") {
    rows = rows.filter((item) => item.status !== "active");
  }
  const query = state.query.trim().toLowerCase();
  if (query) {
    rows = rows.filter((item) => `${item.statement} ${item.applies_to} ${item.status}`.toLowerCase().includes(query));
  }
  return rows.sort((a, b) => b.frequency - a.frequency || scoreValue(b) - scoreValue(a) || b.confidence_score - a.confidence_score);
}

function renderExecutiveSummary(summary) {
  const text = (summary && summary.text || "").trim();
  if (!text) {
    return `
      <p class="muted">${escapeHtml(t("empty.profile"))}</p>
    `;
  }
  const lines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const html = [];
  let list = [];
  const flushList = () => {
    if (list.length) {
      html.push(`<ul>${list.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`);
      list = [];
    }
  };
  for (const line of lines) {
    if (line.startsWith("- ")) {
      list.push(line.slice(2).trim());
    } else {
      flushList();
      html.push(`<p>${escapeHtml(line.replace(/^#+\s*/, ""))}</p>`);
    }
  }
  flushList();
  return html.join("");
}

function renderTable(rows) {
  const body = rows.map((item) => `
    <tr>
      <td class="statement">
        <details data-id="${escapeHtml(item.id)}">
          <summary>${escapeHtml(item.statement)}</summary>
          ${renderDetail(item)}
        </details>
      </td>
      <td>${item.frequency}</td>
      <td>${item.sessions}</td>
      <td>${escapeHtml(item.confidence)}</td>
      <td>${renderLiveConfidence(item)}</td>
      <td>${escapeHtml(item.status)}</td>
    </tr>
  `).join("");
  return `
    <table class="table">
      <thead>
        <tr>
          <th>${escapeHtml(t("table.preference"))}</th>
          <th>${escapeHtml(t("table.frequency"))}</th>
          <th>${escapeHtml(t("table.sessions"))}</th>
          <th>${escapeHtml(t("table.confidence"))}</th>
          <th>${escapeHtml(t("table.liveConfidence"))}</th>
          <th>${escapeHtml(t("table.status"))}</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

function renderConflictView(data) {
  const groups = filteredConflictGroups(data);
  if (!groups.length) {
    return `<p class="empty">${escapeHtml(t("empty.conflicts"))}</p>`;
  }
  return `
    <div class="conflict-board">
      ${groups.map(renderConflictGroup).join("")}
    </div>
  `;
}

function filteredConflictGroups(data) {
  let groups = [...(data.conflict_groups || [])];
  const query = state.query.trim().toLowerCase();
  if (query) {
    groups = groups.filter((group) => conflictSearchText(group).includes(query));
  }
  return groups;
}

function conflictSearchText(group) {
  return [
    group.title,
    group.reason,
    group.left_label,
    group.right_label,
    group.left && group.left.statement,
    group.left && group.left.applies_to,
    group.right && group.right.statement,
    group.right && group.right.applies_to
  ].filter(Boolean).join(" ").toLowerCase();
}

function renderConflictGroup(group) {
  const score = group.score === null || group.score === undefined ? "" : `<span>${escapeHtml(t("conflict.similarity"))} ${escapeHtml(group.score)}</span>`;
  return `
    <article class="conflict-pair">
      <header class="conflict-header">
        <h3>${escapeHtml(group.title)}</h3>
        <div class="conflict-meta">
          ${score}
          <span>${escapeHtml(group.reason || t("conflict.reviewRequired"))}</span>
        </div>
      </header>
      <div class="conflict-grid">
        ${renderConflictSide(group.left, group.left_label || "A")}
        <div class="conflict-vs" aria-hidden="true">vs</div>
        ${renderConflictSide(group.right, group.right_label || "B")}
      </div>
    </article>
  `;
}

function renderConflictSide(item, label) {
  if (!item) {
    return `<section class="conflict-side"><p class="empty">${escapeHtml(t("empty.missingSide"))}</p></section>`;
  }
  const evidence = (item.evidence || []).slice(0, 2).map((entry) => `<li>${escapeHtml(entry.quote || entry.source)}</li>`).join("");
  const feedbackActions = item.synthetic ? "" : `
    <div class="actions">
      <button data-feedback="confirmation" data-id="${escapeHtml(item.id)}" data-text="${escapeAttr(item.statement)}">${escapeHtml(t("detail.confirm"))}</button>
      <button data-feedback="rejection" data-id="${escapeHtml(item.id)}" data-text="${escapeAttr(item.statement)}">${escapeHtml(t("detail.reject"))}</button>
      <button data-feedback="correction" data-id="${escapeHtml(item.id)}" data-text="${escapeAttr(item.statement)}">${escapeHtml(t("detail.correct"))}</button>
    </div>
  `;
  return `
    <section class="conflict-side">
      <p class="side-label">${escapeHtml(label)}</p>
      <p class="conflict-statement">${escapeHtml(item.statement)}</p>
      <dl class="side-facts">
        <div><dt>${escapeHtml(t("table.status"))}</dt><dd>${escapeHtml(item.status)}</dd></div>
        <div><dt>${escapeHtml(t("conflict.confidence"))}</dt><dd>${escapeHtml(item.confidence)}</dd></div>
        <div><dt>${escapeHtml(t("conflict.scope"))}</dt><dd>${escapeHtml(item.applies_to || t("conflict.notSpecified"))}</dd></div>
      </dl>
      ${evidence ? `<div class="definition compact"><strong>${escapeHtml(t("detail.evidence"))}</strong><ul>${evidence}</ul></div>` : ""}
      ${feedbackActions}
    </section>
  `;
}

function renderDetail(item) {
  const evidence = item.evidence.length
    ? item.evidence.map((entry, index) => `<li>[${index + 1}] ${escapeHtml(entry.quote || entry.source)}</li>`).join("")
    : `<li>${escapeHtml(t("detail.noEvidence"))}</li>`;
  const notes = item.conflict_notes.length
    ? `<div class="theorem"><strong>${escapeHtml(t("detail.theorem"))}</strong> ${escapeHtml(item.conflict_notes.join(" "))}</div>`
    : "";
  const factors = renderConfidenceFactors(item);
  return `
    <div class="details">
      <h3>${escapeHtml(t("detail.statement"))}</h3>
      <p>${escapeHtml(item.statement)}</p>
      ${notes}
      ${factors}
      <div class="definition">
        <strong>${escapeHtml(t("detail.definition"))}</strong>
        <ul>${evidence}</ul>
      </div>
      <div class="actions">
        <button data-feedback="confirmation" data-id="${escapeHtml(item.id)}" data-text="${escapeAttr(item.statement)}">${escapeHtml(t("detail.confirm"))}</button>
        <button data-feedback="rejection" data-id="${escapeHtml(item.id)}" data-text="${escapeAttr(item.statement)}">${escapeHtml(t("detail.reject"))}</button>
        <button data-feedback="correction" data-id="${escapeHtml(item.id)}" data-text="${escapeAttr(item.statement)}">${escapeHtml(t("detail.correct"))}</button>
      </div>
    </div>
  `;
}

function renderLiveConfidence(item) {
  const value = scoreValue(item);
  const label = item.live_confidence_label || item.confidence || "";
  return `
    <div class="confidence-cell">
      <meter min="0" max="1" value="${escapeAttr(value.toFixed(4))}"></meter>
      <span>${escapeHtml(value.toFixed(2))} ${escapeHtml(label)}</span>
    </div>
  `;
}

function renderConfidenceFactors(item) {
  const factors = item.live_confidence_factors || {};
  const entries = Object.entries(factors);
  if (!entries.length) return "";
  const reasons = (item.live_confidence_reasons || []).join(", ");
  const rows = entries.map(([key, value]) => `
    <div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(formatSigned(value))}</dd></div>
  `).join("");
  return `
    <div class="definition compact">
      <strong>${escapeHtml(t("detail.liveConfidence"))}</strong>
      <div class="confidence-cell detail-confidence">
        <meter min="0" max="1" value="${escapeAttr(scoreValue(item).toFixed(4))}"></meter>
        <span>${escapeHtml(scoreValue(item).toFixed(2))}</span>
      </div>
      <dl class="factor-grid">${rows}</dl>
      ${reasons ? `<p class="muted">${escapeHtml(reasons)}</p>` : ""}
    </div>
  `;
}

function scoreValue(item) {
  const value = Number(item && item.live_confidence);
  if (Number.isFinite(value)) {
    return Math.max(0, Math.min(1, value));
  }
  const fallback = Number(item && item.confidence_score);
  return Number.isFinite(fallback) ? Math.max(0, Math.min(1, fallback)) : 0;
}

function formatSigned(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}`;
}

function renderEvolution(data) {
  const rows = (data.feedback.by_preference || []).map((item) => `
    <tr>
      <td>${escapeHtml(item.preference)}</td>
      <td>${item.usage}</td>
      <td>${item.confirmation}</td>
      <td>${item.correction}</td>
      <td>${item.rejection}</td>
      <td>${escapeHtml(item.recommendation)}</td>
    </tr>
  `).join("");
  return rows ? `
    <table class="table">
      <thead><tr><th>${escapeHtml(t("evolution.preference"))}</th><th>${escapeHtml(t("evolution.use"))}</th><th>${escapeHtml(t("evolution.confirm"))}</th><th>${escapeHtml(t("evolution.correct"))}</th><th>${escapeHtml(t("evolution.reject"))}</th><th>${escapeHtml(t("evolution.recommendation"))}</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  ` : `<p class="empty">${escapeHtml(t("empty.feedback"))}</p>`;
}

function renderFitting(data) {
  const job = data.fitting;
  if (!job) {
    return `<p class="empty">${escapeHtml(t("empty.fitting"))}</p>`;
  }
  const stats = job.stats || {};
  const instructions = job.instructions || {};
  const commands = (job.next_commands || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  return `
    <div class="definition">
      <h3>${escapeHtml(job.job_id || "Fitting")}</h3>
      <p>${escapeHtml(instructions.text || "No instructions were provided.")}</p>
      <dl class="factor-grid">
        <div><dt>Insights</dt><dd>${escapeHtml(stats.insights_proposed || 0)}</dd></div>
        <div><dt>Rot</dt><dd>${escapeHtml(stats.rot_suggestions || 0)}</dd></div>
        <div><dt>Conflicts</dt><dd>${escapeHtml(stats.conflicts || 0)}</dd></div>
        <div><dt>Ignored</dt><dd>${escapeHtml(stats.ignored_by_instruction || 0)}</dd></div>
      </dl>
      <p class="muted">${escapeHtml(job.report_file || "")}</p>
      ${commands ? `<div class="definition compact"><strong>Next</strong><ul>${commands}</ul></div>` : ""}
    </div>
  `;
}

function renderInjectionLog(data) {
  const rows = filteredInjectionEvents(data);
  if (!rows.length) {
    return `<p class="empty">${escapeHtml(t("empty.injection"))}</p>`;
  }
  return `
    <div class="injection-list">
      ${rows.map((item) => renderInjectionEntry(item)).join("")}
    </div>
  `;
}

function renderInjectionEntry(item) {
  const matched = item.matched_preferences || [];
  const matchedCount = item.matched_count || matched.length;
  const matchedText = matched.length
    ? `<ul class="matched-list">${matched.map((m) => `<li>${escapeHtml(m.title || m.id || m.instruction || "")}</li>`).join("")}</ul>`
    : "—";

  const sessionHtml = item.session_id ? `
    <div class="injection-field">
      <dt>${escapeHtml(t("injection.session"))}</dt>
      <dd class="muted">${escapeHtml(item.session_id)}</dd>
    </div>
  ` : "";

  const reasonHtml = item.reason ? `
    <div class="injection-field">
      <dt>${escapeHtml(t("injection.reason"))}</dt>
      <dd class="muted">${escapeHtml(item.reason)}</dd>
    </div>
  ` : "";

  return `
    <article class="injection-entry">
      <div class="injection-header">
        <span class="injection-time">${escapeHtml(item.timestamp || "")}</span>
        <span class="injection-tag" data-label="${escapeHtml(t("injection.hook"))}">${escapeHtml(item.hook || "")}</span>
        <span class="injection-tag" data-label="${escapeHtml(t("injection.agent"))}">${escapeHtml(item.agent || "")}</span>
        <span class="injection-tag" data-label="${escapeHtml(t("injection.decision"))}">${escapeHtml(item.decision || "")}</span>
        <span class="injection-tag" data-label="${escapeHtml(t("injection.injected"))}">${escapeHtml(item.injected ? t("injection.yes") : t("injection.no"))}</span>
      </div>
      <div class="injection-body">
        <div class="injection-field">
          <dt>${escapeHtml(t("injection.matched"))} (${matchedCount})</dt>
          <dd>${matchedText}</dd>
        </div>
        <div class="injection-field">
          <dt>${escapeHtml(t("injection.instruction"))}</dt>
          <dd class="instruction-block">${escapeHtml(item.agent_instruction || "—")}</dd>
        </div>
        ${reasonHtml}
        ${sessionHtml}
      </div>
    </article>
  `;
}

function filteredInjectionEvents(data) {
  let rows = [...((data.injection_log && data.injection_log.items) || [])];
  const query = state.query.trim().toLowerCase();
  if (query) {
    rows = rows.filter((item) => injectionSearchText(item).includes(query));
  }
  return rows;
}

function injectionSearchText(item) {
  return [
    item.timestamp,
    item.hook,
    item.agent,
    item.session_id,
    item.task,
    item.decision,
    item.agent_instruction,
    item.reason,
    JSON.stringify(item.matched_preferences || [])
  ].filter(Boolean).join(" ").toLowerCase();
}

function renderSettings(data) {
  return `
    <div class="definition">
      <h3>${escapeHtml(t("settings.userMode"))}</h3>
      <p class="muted">${escapeHtml(t("settings.configuredMode"))}: ${escapeHtml(data.configured_mode)}. ${escapeHtml(t("settings.effectiveMode"))}: ${escapeHtml(data.mode)}.</p>
      <div class="actions">
        <button data-setting="mode" data-value="auto">${escapeHtml(t("settings.auto"))}</button>
        <button data-setting="mode" data-value="autonomous">${escapeHtml(t("settings.autonomous"))}</button>
        <button data-setting="mode" data-value="curated">${escapeHtml(t("settings.curated"))}</button>
      </div>
    </div>
    <div class="definition">
      <h3>${escapeHtml(t("settings.theme"))}</h3>
      <div class="actions">
        <button data-setting="theme" data-value="light">${escapeHtml(t("settings.light"))}</button>
        <button data-setting="theme" data-value="dark">${escapeHtml(t("settings.dark"))}</button>
      </div>
    </div>
    <div class="definition">
      <h3>${escapeHtml(t("settings.language"))}</h3>
      <div class="actions">
        <button data-setting="language" data-value="en">${escapeHtml(t("settings.english"))}</button>
        <button data-setting="language" data-value="zh">${escapeHtml(t("settings.chinese"))}</button>
      </div>
    </div>
  `;
}

function bindActions() {
  document.querySelectorAll("details").forEach((node) => {
    node.addEventListener("toggle", () => {
      if (node.open) {
        request("/api/event", {
          method: "POST",
          body: JSON.stringify({event_type: "review_click", metadata: {id: node.dataset.id}})
        }).catch(console.error);
      }
    });
  });
  document.querySelectorAll("[data-feedback]").forEach((button) => {
    button.addEventListener("click", async () => {
      const type = button.dataset.feedback;
      let feedback = type;
      if (type === "correction") {
        feedback = window.prompt(t("detail.correctionPrompt"), button.dataset.text) || "";
      }
      if (!feedback) return;
      const result = await request("/api/feedback", {
        method: "POST",
        body: JSON.stringify({
          feedback_type: type,
          user_feedback: feedback,
          preference_id: button.dataset.id,
          preference_text: button.dataset.text
        })
      });
      if (result.preference_update && result.preference_update.ok === false) {
        window.alert(`${t("detail.actionFailed")} ${result.preference_update.error || ""}`.trim());
      }
      await load();
    });
  });
}

function bindSettings() {
  document.querySelectorAll("[data-setting]").forEach((button) => {
    const current = state.manifesto && state.manifesto[button.dataset.setting];
    const configured = button.dataset.setting === "mode" ? state.manifesto.configured_mode : current;
    button.classList.toggle("active", configured === button.dataset.value);
  });
  document.querySelectorAll("[data-setting]").forEach((button) => {
    button.addEventListener("click", async () => {
      await request("/api/settings", {
        method: "POST",
        body: JSON.stringify({[button.dataset.setting]: button.dataset.value})
      });
      await load();
    });
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

async function withContentFade(callback) {
  const content = document.getElementById("content");
  content.classList.add("switching");
  await new Promise((resolve) => setTimeout(resolve, 140));
  callback();
  content.classList.remove("switching");
}

async function withPageFade(callback) {
  const page = document.querySelector(".page");
  page.classList.add("switching");
  await new Promise((resolve) => setTimeout(resolve, 180));
  await callback();
  page.classList.remove("switching");
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    const newTab = button.dataset.tab;
    if (newTab === state.tab) return;
    withContentFade(() => {
      state.tab = newTab;
      render();
    });
  });
});

document.querySelectorAll("[data-language]").forEach((button) => {
  button.addEventListener("click", async () => {
    await withPageFade(async () => {
      await request("/api/settings", {
        method: "POST",
        body: JSON.stringify({language: button.dataset.language})
      });
      await load();
    });
  });
});

document.getElementById("search").addEventListener("input", (event) => {
  state.query = event.target.value;
  render();
});

load().catch((error) => {
  document.getElementById("content").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
});
