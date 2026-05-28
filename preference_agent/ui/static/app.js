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
      curate: "Curate",
      modeAutoTitle: "Auto Mode",
      modeAutoDesc: "Fitting runs automatically and high-confidence preferences are applied without review. Workflows and rot suggestions are logged but not auto-written.",
      modeAutoFor: "Best for: users who trust the system and prefer efficiency.",
      modeCurateTitle: "Curate Mode (Recommended)",
      modeCurateDesc: "Fitting runs automatically but only generates a plan. You must review and accept changes before they are written to your preference store.",
      modeCurateFor: "Best for: cautious users. This is the default.",
      theme: "Theme",
      light: "Light",
      dark: "Dark",
      language: "Language",
      english: "English",
      chinese: "Chinese"
    }
  },
  zh: {
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
      curate: "Curate",
      modeAutoTitle: "Auto Mode",
      modeAutoDesc: "Fitting runs automatically and high-confidence preferences are applied without review. Workflows and rot suggestions are logged but not auto-written.",
      modeAutoFor: "Best for: users who trust the system and prefer efficiency.",
      modeCurateTitle: "Curate Mode (Recommended)",
      modeCurateDesc: "Fitting runs automatically but only generates a plan. You must review and accept changes before they are written to your preference store.",
      modeCurateFor: "Best for: cautious users. This is the default.",
      theme: "Theme",
      light: "Light",
      dark: "Dark",
      language: "Language",
      english: "English",
      chinese: "English"
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
  renderPendingBadge(data);
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
    bindFittingReview();
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
  const pendingChanges = Number(job.pending_changes || 0);
  const changes = Array.isArray(job.changes) ? job.changes : [];
  const changeRows = changes.map(renderFittingChange).join("");
  const reviewActions = pendingChanges ? `
    <div class="actions fitting-actions">
      <button data-fitting-apply="selected" data-job-id="${escapeAttr(job.job_id || "")}">Accept Selected</button>
      <button data-fitting-apply="all" data-job-id="${escapeAttr(job.job_id || "")}">Accept All</button>
      <button data-fitting-reject data-job-id="${escapeAttr(job.job_id || "")}">Reject All</button>
    </div>
  ` : "";
  const reportMarkdown = String(job.report_markdown || "").trim();
  const reportBlock = reportMarkdown
    ? `<pre class="report-block">${escapeHtml(reportMarkdown)}</pre>`
    : `<p class="empty">${escapeHtml(job.report_error || "The report file is not available from the UI.")}</p>`;
  return `
    <div class="definition">
      <h3>${escapeHtml(job.job_id || "Fitting")}</h3>
      <p class="muted">${escapeHtml(job.status || "unknown")} | ${escapeHtml(job.version || "")}</p>
      ${pendingChanges ? `<p class="theorem">${escapeHtml(pendingChanges)} changes pending review. Nothing has been written yet.</p>` : ""}
      <p>${escapeHtml(instructions.text || "No instructions were provided.")}</p>
      <dl class="factor-grid">
        <div><dt>Insights</dt><dd>${escapeHtml(stats.insights_proposed || 0)}</dd></div>
        <div><dt>Rot</dt><dd>${escapeHtml(stats.rot_suggestions || 0)}</dd></div>
        <div><dt>Conflicts</dt><dd>${escapeHtml(stats.conflicts || 0)}</dd></div>
        <div><dt>Ignored</dt><dd>${escapeHtml(stats.ignored_by_instruction || 0)}</dd></div>
      </dl>
      <p class="muted">${escapeHtml(job.report_file || "")}</p>
      ${changeRows ? `<h3>Proposed Changes</h3><div class="fitting-change-list">${changeRows}</div>${reviewActions}` : ""}
      <h3>Report</h3>
      ${reportBlock}
      ${commands ? `<div class="definition compact"><strong>Next</strong><ul>${commands}</ul></div>` : ""}
    </div>
  `;
}

function renderFittingChange(change) {
  const insight = change && change.payload && change.payload.insight || {};
  const suggestion = change && change.payload && change.payload.suggestion || {};
  const title = insight.title || suggestion.type || change.type || change.change_id;
  const detail = insight.guidance || suggestion.reason || "";
  const pending = (change.status || "pending") === "pending";
  return `
    <label class="fitting-change ${pending ? "" : "applied"}">
      <input type="checkbox" data-change-id="${escapeAttr(change.change_id || "")}" ${pending ? "checked" : "disabled"}>
      <span>
        <strong>${escapeHtml(change.type || "")}: ${escapeHtml(title)}</strong>
        <small>${escapeHtml(change.change_id || "")} | ${escapeHtml(change.risk || "")} | ${escapeHtml(change.status || "pending")}</small>
        ${detail ? `<em>${escapeHtml(detail)}</em>` : ""}
      </span>
    </label>
  `;
}

function renderPendingBadge(data) {
  const button = document.querySelector('[data-tab="fitting"]');
  if (!button) return;
  const count = Number(data.fitting && data.fitting.pending_changes || 0);
  button.textContent = count ? `${t("tabs.fitting")} (${count})` : t("tabs.fitting");
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
    : "-";

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
          <dd class="instruction-block">${escapeHtml(item.agent_instruction || "-")}</dd>
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
      <p class="muted">${escapeHtml(t("settings.configuredMode"))}: ${escapeHtml(data.configured_mode)}</p>
      <div class="select-row">
        <select id="mode-select" data-setting="mode">
          <option value="curate" ${data.configured_mode === "curate" ? "selected" : ""}>${escapeHtml(t("settings.curate"))}</option>
          <option value="auto" ${data.configured_mode === "auto" ? "selected" : ""}>${escapeHtml(t("settings.auto"))}</option>
        </select>
      </div>
      <div class="mode-cards">
        <div class="mode-card ${data.configured_mode === "auto" ? "active" : ""}" data-mode="auto">
          <h4>${escapeHtml(t("settings.modeAutoTitle"))}</h4>
          <p>${escapeHtml(t("settings.modeAutoDesc"))}</p>
          <p class="muted">${escapeHtml(t("settings.modeAutoFor"))}</p>
        </div>
        <div class="mode-card ${data.configured_mode === "curate" ? "active" : ""}" data-mode="curate">
          <h4>${escapeHtml(t("settings.modeCurateTitle"))}</h4>
          <p>${escapeHtml(t("settings.modeCurateDesc"))}</p>
          <p class="muted">${escapeHtml(t("settings.modeCurateFor"))}</p>
        </div>
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
  // Buttons (theme, language)
  document.querySelectorAll("button[data-setting]").forEach((button) => {
    const current = state.manifesto && state.manifesto[button.dataset.setting];
    const configured = button.dataset.setting === "mode" ? state.manifesto.configured_mode : current;
    button.classList.toggle("active", configured === button.dataset.value);
    button.addEventListener("click", async () => {
      await request("/api/settings", {
        method: "POST",
        body: JSON.stringify({[button.dataset.setting]: button.dataset.value})
      });
      await load();
    });
  });

  // Mode select dropdown
  const select = document.getElementById("mode-select");
  if (select) {
    const mode = state.manifesto.configured_mode || "curate";
    select.value = mode;
    select.addEventListener("change", async (e) => {
      const value = e.target.value;
      await request("/api/settings", {
        method: "POST",
        body: JSON.stringify({mode: value})
      });
      await load();
    });
    // Highlight active mode card
    document.querySelectorAll(".mode-card").forEach((card) => {
      card.classList.toggle("active", card.dataset.mode === mode);
    });
  }
}

function bindFittingReview() {
  document.querySelectorAll("[data-fitting-apply]").forEach((button) => {
    button.addEventListener("click", async () => {
      const jobId = button.dataset.jobId;
      const all = button.dataset.fittingApply === "all";
      const checkboxes = Array.from(document.querySelectorAll("[data-change-id]"));
      const accepted = checkboxes
        .filter((node) => all || node.checked)
        .map((node) => node.dataset.changeId)
        .filter(Boolean);
      if (!accepted.length) return;
      const result = await request("/api/fitting/apply", {
        method: "POST",
        body: JSON.stringify({job_id: jobId, accepted_change_ids: accepted})
      });
      if (result.ok === false) {
        window.alert(result.error || "Fitting apply failed.");
      }
      await load();
    });
  });
  document.querySelectorAll("[data-fitting-reject]").forEach((button) => {
    button.addEventListener("click", async () => {
      await request("/api/fitting/reject", {
        method: "POST",
        body: JSON.stringify({job_id: button.dataset.jobId})
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
