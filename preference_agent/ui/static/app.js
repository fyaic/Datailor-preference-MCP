const state = {
  tab: "all",
  query: "",
  manifesto: null
};

const i18n = {
  en: {
    appTitle: "Personal Preference Manifesto",
    appSubtitle: "Review, correct, and trace the defaults your AI agents use.",
    topbarNote: "Local preference layer",
    sectionMark: "Manifesto UI",
    summaryTitle: "1.1 Executive Summary",
    search: "Search",
    source: "Source",
    tabs: {
      all: "All",
      pending: "Pending",
      conflicts: "Conflicts",
      fitting: "Fitting",
      injection: "Injection",
      settings: "Settings"
    },
    titles: {
      all: "1.2 High-Frequency Preferences",
      pending: "1.3 Pending Confirmation",
      conflicts: "1.4 Conflict Review",
      fitting: "1.6 Fitting Report",
      injection: "1.7 Injection Log",
      settings: "1.8 Settings"
    },
    summary: {
      active: "Active preferences",
      pending: "Pending confirmation",
      conflicts: "Conflicts awaiting resolution",
      capacity: "Preference cap",
      capReview: "Cap review",
      capReviewOk: "OK",
      capReviewNeeded: "Review",
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
    status: {
      connecting: "Connecting to Datailor…",
      connectingHint: "Starting the local preference server. This page will load automatically.",
      unreachable: "Can't reach the Datailor UI server.",
      unreachableHint: "The server may have stopped. Re-run `datailor ui`, then refresh this page."
    },
    capturing: {
      title: "No preferences captured yet",
      body: "Datailor is still learning. Run a history scan with `datailor onboard`, or just keep using your AI — preferences appear here automatically as they are captured."
    },
    table: {
      preference: "Preference",
      frequency: "Freq.",
      sessions: "Sessions",
      confidence: "Conf.",
      liveConfidence: "Tendency",
      status: "Status"
    },
    detail: {
      statement: "Statement",
      theorem: "Theorem.",
      definition: "Definition.",
      scope: "Scope",
      confidence: "Confidence",
      occurrences: "Occurrences",
      liveConfidence: "Apply tendency.",
      factors: "Factors.",
      evidence: "Evidence.",
      evidenceSummary: "Distilled from {count} evidence(s) across {sessions} session(s).",
      evidenceSources: "Evidence sources ({count})",
      notSpecified: "Not specified",
      noEvidence: "No stored evidence in the Markdown view.",
      confirm: "Confirm",
      reject: "Reject",
      correct: "Correct",
      correctionPrompt: "Correction",
      actionFailed: "The feedback was recorded, but the preference store was not updated."
    },
    tooltip: {
      frequency: "How often this preference appears in captured evidence, confirmations, or usage signals.",
      frequencyValue: "Estimated evidence count for this preference. Higher values usually mean the preference has appeared more often.",
      sessions: "How many distinct sessions or sources contributed evidence for this preference.",
      sessionsValue: "Number of separate sessions or sources behind this preference.",
      confidence: "Stored confidence in the preference itself.",
      confidenceValue: "Stored confidence saved with this preference. It describes long-term trust in the preference itself, not whether it matches a running agent task right now.",
      liveConfidence: "General tendency to apply this preference in the current list view.",
      liveValue: "General injection estimate for this preference list. This value is not tied to any currently running terminal; use Injection Log to see each agent session and task match.",
      liveDetail: "This breakdown uses stored confidence, evidence, recency, consistency, and a general relevance estimate for the list view. Per-task matches appear in Injection Log.",
      status: "Review state of the preference in the local store.",
      statusValue: "Shows whether the preference is active, pending review, rejected, or otherwise waiting for a decision."
    },
    conflict: {
      similarity: "Similarity",
      reviewRequired: "Review required.",
      confidence: "Confidence",
      scope: "Scope",
      notSpecified: "Not specified"
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
    },
    fitting: {
      defaultTitle: "Fitting",
      acceptSelected: "Accept Selected",
      acceptAll: "Accept All",
      rejectAll: "Reject All",
      pendingNotice: "changes pending review. Nothing has been written yet.",
      noInstructions: "No instructions were provided.",
      insights: "Insights",
      rot: "Rot",
      conflicts: "Conflicts",
      ignored: "Ignored",
      proposedChanges: "Proposed Changes",
      report: "Report",
      next: "Next",
      reportUnavailable: "The report file is not available from the UI.",
      applyFailed: "Fitting apply failed."
    },
    status: {
      active: "Active",
      pending: "Pending",
      pending_review: "Pending review",
      needs_review: "Needs review",
      rejected: "Rejected",
      archived: "Archived"
    },
    confidence: {
      high: "high",
      medium: "medium",
      low: "low"
    },
    mode: {
      auto: "auto",
      curate: "curate"
    }
  },
  zh: {
    appTitle: "个人偏好清单",
    appSubtitle: "查看、修正并追踪 AI agent 正在使用的个人默认偏好。",
    topbarNote: "本地偏好层",
    sectionMark: "偏好面板",
    summaryTitle: "1.1 摘要",
    search: "搜索",
    source: "来源",
    tabs: {
      all: "全部",
      pending: "待确认",
      conflicts: "冲突",
      fitting: "Fitting",
      injection: "注入日志",
      settings: "设置"
    },
    titles: {
      all: "1.2 高频偏好",
      pending: "1.3 待确认偏好",
      conflicts: "1.4 冲突审查",
      fitting: "1.6 Fitting 报告",
      injection: "1.7 注入日志",
      settings: "1.8 设置"
    },
    summary: {
      active: "已启用偏好",
      pending: "待确认偏好",
      conflicts: "待处理冲突",
      capacity: "偏好容量",
      capReview: "容量审查",
      capReviewOk: "正常",
      capReviewNeeded: "需审查",
      mode: "当前模式"
    },
    empty: {
      section: "当前分区暂无记录。",
      profile: "还没有生成稳定的个人画像。先捕获偏好后，这里会总结系统对用户默认偏好的理解。",
      conflicts: "未检测到 A/B 冲突。待确认条目仍可在“待确认”标签页查看。",
      feedback: "还没有记录反馈。",
      fitting: "还没有生成 Fitting 报告。",
      injection: "还没有注入事件。启动一次会话或调用 get_preference_decision 后，这里会显示时间线。",
      missingSide: "缺少一侧内容。"
    },
    status: {
      connecting: "正在连接 Datailor…",
      connectingHint: "正在启动本地偏好服务，页面会自动加载。",
      unreachable: "无法连接 Datailor UI 服务。",
      unreachableHint: "服务可能已停止。重新运行 `datailor ui`，然后刷新本页。"
    },
    capturing: {
      title: "还没有捕获到偏好",
      body: "Datailor 还在学习。用 `datailor onboard` 跑一次历史扫描，或继续正常使用 AI —— 捕获到的偏好会自动出现在这里。"
    },
    table: {
      preference: "偏好",
      frequency: "频次",
      sessions: "会话",
      confidence: "可信度",
      liveConfidence: "采用倾向",
      status: "状态"
    },
    detail: {
      statement: "偏好内容",
      theorem: "冲突备注",
      definition: "定义",
      scope: "适用范围",
      confidence: "可信度",
      occurrences: "出现次数",
      liveConfidence: "采用倾向",
      factors: "因素",
      evidence: "证据",
      evidenceSummary: "从 {sessions} 个会话的 {count} 条证据中提炼。",
      evidenceSources: "证据来源（{count}）",
      notSpecified: "未指定",
      noEvidence: "Markdown 视图中没有保存证据。",
      confirm: "确认",
      reject: "拒绝",
      correct: "修正",
      correctionPrompt: "修正内容",
      actionFailed: "反馈已记录，但偏好库没有更新。"
    },
    tooltip: {
      frequency: "这条偏好在捕获证据、用户确认或使用信号中出现的频次。",
      frequencyValue: "这条偏好的估算证据次数。数值越高，通常说明它出现得越频繁。",
      sessions: "为这条偏好提供证据的不同会话或来源数量。",
      sessionsValue: "支撑这条偏好的独立会话或来源数量。",
      confidence: "偏好本身在本地库中保存的长期可信度。",
      confidenceValue: "这条偏好保存时的长期可信度，说明偏好本身靠不靠谱；它不代表当前某个正在运行的 agent 任务是否匹配。",
      liveConfidence: "列表页里这条偏好的通用采用倾向。",
      liveValue: "偏好列表里的通用注入估计值。它不绑定你当前任何一个终端；要看每个 agent 会话和任务实际匹配了什么，请看 Injection Log。",
      liveDetail: "这里按存储置信度、证据、更新时间、一致性和列表页通用相关性估算。具体任务的匹配结果在 Injection Log 里。",
      status: "这条偏好在本地偏好库中的审查状态。",
      statusValue: "显示这条偏好是已启用、待审查、已拒绝，还是处在其他待处理状态。"
    },
    conflict: {
      similarity: "相似度",
      reviewRequired: "需要审查。",
      confidence: "可信度",
      scope: "适用范围",
      notSpecified: "未指定"
    },
    injection: {
      time: "时间",
      hook: "钩子",
      agent: "Agent",
      session: "会话",
      decision: "决策",
      matched: "匹配偏好",
      instruction: "注入指令",
      injected: "已注入",
      reason: "原因",
      yes: "是",
      no: "否"
    },
    settings: {
      userMode: "用户模式",
      configuredMode: "配置模式",
      effectiveMode: "生效模式",
      auto: "自动",
      curate: "审查",
      modeAutoTitle: "自动模式",
      modeAutoDesc: "Fitting 会自动运行，高可信偏好可不经审查直接应用。工作流和过期偏好建议会被记录，但不会自动写入。",
      modeAutoFor: "适合：信任系统、优先效率的用户。",
      modeCurateTitle: "审查模式（推荐）",
      modeCurateDesc: "Fitting 会自动运行，但只生成应用计划。写入偏好库前需要你审查并接受变更。",
      modeCurateFor: "适合：更谨慎的用户。这也是默认模式。",
      theme: "主题",
      light: "浅色",
      dark: "深色",
      language: "语言",
      english: "英文",
      chinese: "中文"
    },
    fitting: {
      defaultTitle: "Fitting",
      acceptSelected: "接受所选",
      acceptAll: "全部接受",
      rejectAll: "全部拒绝",
      pendingNotice: "项变更正在等待审查。当前还没有写入偏好库。",
      noInstructions: "没有提供说明。",
      insights: "洞察",
      rot: "过期建议",
      conflicts: "冲突",
      ignored: "已忽略",
      proposedChanges: "拟议变更",
      report: "报告",
      next: "下一步",
      reportUnavailable: "UI 暂时无法读取报告文件。",
      applyFailed: "Fitting 应用失败。"
    },
    status: {
      active: "已启用",
      pending: "待处理",
      pending_review: "待审查",
      needs_review: "需审查",
      rejected: "已拒绝",
      archived: "已归档"
    },
    confidence: {
      high: "高",
      medium: "中",
      low: "低"
    },
    mode: {
      auto: "自动",
      curate: "审查"
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

function formatMessage(path, values = {}) {
  return t(path).replace(/\{(\w+)\}/g, (match, key) => (
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match
  ));
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

const RETRY = { attempts: 0, max: 40, delayMs: 500 };

function showStatus(message, hint, options = {}) {
  const content = document.getElementById("content");
  if (!content) return;
  const spinner = options.spinner === false ? "" : `<div class="status-spinner" aria-hidden="true"></div>`;
  content.innerHTML = `<div class="status-screen">
    ${spinner}
    <p class="status-message">${escapeHtml(message)}</p>
    ${hint ? `<p class="muted status-hint">${escapeHtml(hint)}</p>` : ""}
  </div>`;
}

async function load({ retry = true } = {}) {
  try {
    state.manifesto = await request("/api/manifesto");
    RETRY.attempts = 0;
    document.body.dataset.theme = state.manifesto.theme || "light";
    render();
  } catch (error) {
    // The server may still be starting (browser opened a beat early) or have
    // stopped. Retry quietly with a visible "connecting" state instead of a
    // blank screen, then surface a clear message if it stays unreachable.
    if (retry && RETRY.attempts < RETRY.max) {
      RETRY.attempts += 1;
      showStatus(t("status.connecting"), t("status.connectingHint"));
      setTimeout(() => load({ retry: true }), RETRY.delayMs);
      return;
    }
    const detail = error && error.message ? error.message : "";
    showStatus(t("status.unreachable"), `${t("status.unreachableHint")}${detail ? "\n" + detail : ""}`, { spinner: false });
  }
}

function renderAppBanner(data) {
  const banner = document.getElementById("app-banner");
  if (!banner) return;
  const count = (data.summary && data.summary.preference_count) || 0;
  if (count > 0) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  banner.hidden = false;
  banner.innerHTML = `<div class="app-banner-inner">
    <strong>${escapeHtml(t("capturing.title"))}</strong>
    <p class="muted">${escapeHtml(t("capturing.body"))}</p>
  </div>`;
}

function render() {
  const data = state.manifesto;
  if (!data) return;
  applyStaticLabels();
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.tab);
  });
  renderPendingBadge(data);
  renderAppBanner(data);
  document.querySelectorAll("[data-language]").forEach((button) => {
    button.classList.toggle("active", button.dataset.language === language());
  });
  document.getElementById("active-count").textContent = data.summary.active;
  document.getElementById("pending-count").textContent = data.summary.pending;
  document.getElementById("conflict-count").textContent = data.summary.conflicts;
  document.getElementById("capacity-count").textContent = `${data.summary.preference_count}/${data.summary.preference_limit}`;
  document.getElementById("cap-status").textContent = formatCapStatus(data.summary);
  document.getElementById("mode-label").textContent = formatMode(data.mode);
  document.getElementById("store-path").textContent = `${t("source")}: ${data.store}`;
  document.getElementById("section-title").textContent = t(`titles.${state.tab}`) || t("titles.all");
  document.getElementById("executive-text").innerHTML = renderExecutiveSummary(data.executive_summary);

  const content = document.getElementById("content");
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
      <td>${renderExplainedValue(item.frequency, t("tooltip.frequencyValue"))}</td>
      <td>${renderExplainedValue(item.sessions, t("tooltip.sessionsValue"))}</td>
      <td>${renderExplainedValue(formatConfidence(item.confidence), t("tooltip.confidenceValue"))}</td>
      <td>${renderLiveConfidence(item)}</td>
      <td>${renderExplainedValue(formatStatus(item.status), t("tooltip.statusValue"))}</td>
    </tr>
  `).join("");
  return `
    <table class="table">
      <thead>
        <tr>
          <th>${escapeHtml(t("table.preference"))}</th>
          <th>${renderExplainedValue(t("table.frequency"), t("tooltip.frequency"), "info-label")}</th>
          <th>${renderExplainedValue(t("table.sessions"), t("tooltip.sessions"), "info-label")}</th>
          <th>${renderExplainedValue(t("table.confidence"), t("tooltip.confidence"), "info-label")}</th>
          <th>${renderExplainedValue(t("table.liveConfidence"), t("tooltip.liveConfidence"), "info-label")}</th>
          <th>${renderExplainedValue(t("table.status"), t("tooltip.status"), "info-label")}</th>
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
        <div><dt>${escapeHtml(t("table.status"))}</dt><dd>${escapeHtml(formatStatus(item.status))}</dd></div>
        <div><dt>${escapeHtml(t("conflict.confidence"))}</dt><dd>${escapeHtml(formatConfidence(item.confidence))}</dd></div>
        <div><dt>${escapeHtml(t("conflict.scope"))}</dt><dd>${escapeHtml(item.applies_to || t("conflict.notSpecified"))}</dd></div>
      </dl>
      ${evidence ? `<div class="definition compact"><strong>${escapeHtml(t("detail.evidence"))}</strong><ul>${evidence}</ul></div>` : ""}
      ${feedbackActions}
    </section>
  `;
}

function renderDetail(item) {
  const evidenceItems = Array.isArray(item.evidence) ? item.evidence : [];
  const evidenceCount = evidenceItems.length;
  const sessionCount = Number(item.sessions || item.session_count || 0);
  const occurrences = Number(item.frequency || item.occurrences || evidenceCount || 0);
  const evidenceSummary = evidenceCount
    ? `<p class="evidence-summary">${escapeHtml(formatMessage("detail.evidenceSummary", { count: evidenceCount, sessions: sessionCount }))}</p>`
    : "";
  const evidenceList = evidenceItems
    .map((entry, index) => {
      const text = String(entry.quote || "").trim();
      return text ? `<li>[${index + 1}] ${escapeHtml(text)}</li>` : "";
    })
    .filter(Boolean)
    .join("");
  const evidenceSection = evidenceList
    ? `
      <details class="evidence-fold">
        <summary>${escapeHtml(formatMessage("detail.evidenceSources", { count: evidenceCount }))}</summary>
        <ul>${evidenceList}</ul>
      </details>
    `
    : "";
  const notes = (item.conflict_notes || []).length
    ? `<div class="theorem"><strong>${escapeHtml(t("detail.theorem"))}</strong> ${escapeHtml(item.conflict_notes.join(" "))}</div>`
    : "";
  const factors = renderConfidenceFactors(item);
  return `
    <div class="details">
      <h3>${escapeHtml(t("detail.statement"))}</h3>
      <p>${escapeHtml(item.statement)}</p>
      <dl class="meta-grid">
        <div><dt>${escapeHtml(t("detail.scope"))}</dt><dd>${escapeHtml(item.applies_to || t("detail.notSpecified"))}</dd></div>
        <div><dt>${escapeHtml(t("detail.confidence"))}</dt><dd>${escapeHtml(formatConfidence(item.confidence))}</dd></div>
        <div><dt>${escapeHtml(t("detail.occurrences"))}</dt><dd>${escapeHtml(occurrences)}</dd></div>
      </dl>
      ${notes}
      ${factors}
      <div class="definition">
        <strong>${escapeHtml(t("detail.definition"))}</strong>
        ${evidenceSummary || `<p class="muted">${escapeHtml(t("detail.noEvidence"))}</p>`}
        ${evidenceSection}
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
      ${renderExplainedValue(`${value.toFixed(2)} ${formatConfidence(label)}`, t("tooltip.liveValue"))}
    </div>
  `;
}

function renderExplainedValue(value, tooltip, className = "") {
  const classes = ["explain-value", className].filter(Boolean).join(" ");
  return `
    <span class="${escapeAttr(classes)}" tabindex="0" aria-label="${escapeAttr(`${value}: ${tooltip}`)}" data-tooltip="${escapeAttr(tooltip)}">
      ${escapeHtml(value)}
    </span>
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
      <p class="muted">${escapeHtml(t("tooltip.liveDetail"))}</p>
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

function localizedLookup(namespace, value) {
  const key = String(value ?? "").trim();
  if (!key) return "";
  return t(`${namespace}.${key}`) === `${namespace}.${key}` ? key : t(`${namespace}.${key}`);
}

function formatStatus(value) {
  return localizedLookup("status", value);
}

function formatConfidence(value) {
  return localizedLookup("confidence", value);
}

function formatMode(value) {
  return localizedLookup("mode", value);
}

function formatCapStatus(summary) {
  if (!summary || !summary.cap_review_required) {
    return t("summary.capReviewOk");
  }
  const count = Number(summary.cap_review_candidates || 0);
  return count > 0 ? `${t("summary.capReviewNeeded")} ${count}` : t("summary.capReviewNeeded");
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
      <button data-fitting-apply="selected" data-job-id="${escapeAttr(job.job_id || "")}">${escapeHtml(t("fitting.acceptSelected"))}</button>
      <button data-fitting-apply="all" data-job-id="${escapeAttr(job.job_id || "")}">${escapeHtml(t("fitting.acceptAll"))}</button>
      <button data-fitting-reject data-job-id="${escapeAttr(job.job_id || "")}">${escapeHtml(t("fitting.rejectAll"))}</button>
    </div>
  ` : "";
  const reportMarkdown = String(job.report_markdown || "").trim();
  const reportBlock = reportMarkdown
    ? `<pre class="report-block">${escapeHtml(reportMarkdown)}</pre>`
    : `<p class="empty">${escapeHtml(job.report_error || t("fitting.reportUnavailable"))}</p>`;
  return `
    <div class="definition">
      <h3>${escapeHtml(job.job_id || t("fitting.defaultTitle"))}</h3>
      <p class="muted">${escapeHtml(formatStatus(job.status || "unknown"))} | ${escapeHtml(job.version || "")}</p>
      ${pendingChanges ? `<p class="theorem">${escapeHtml(pendingChanges)} ${escapeHtml(t("fitting.pendingNotice"))}</p>` : ""}
      <p>${escapeHtml(instructions.text || t("fitting.noInstructions"))}</p>
      <dl class="factor-grid">
        <div><dt>${escapeHtml(t("fitting.insights"))}</dt><dd>${escapeHtml(stats.insights_proposed || 0)}</dd></div>
        <div><dt>${escapeHtml(t("fitting.rot"))}</dt><dd>${escapeHtml(stats.rot_suggestions || 0)}</dd></div>
        <div><dt>${escapeHtml(t("fitting.conflicts"))}</dt><dd>${escapeHtml(stats.conflicts || 0)}</dd></div>
        <div><dt>${escapeHtml(t("fitting.ignored"))}</dt><dd>${escapeHtml(stats.ignored_by_instruction || 0)}</dd></div>
      </dl>
      <p class="muted">${escapeHtml(job.report_file || "")}</p>
      ${changeRows ? `<h3>${escapeHtml(t("fitting.proposedChanges"))}</h3><div class="fitting-change-list">${changeRows}</div>${reviewActions}` : ""}
      <h3>${escapeHtml(t("fitting.report"))}</h3>
      ${reportBlock}
      ${commands ? `<div class="definition compact"><strong>${escapeHtml(t("fitting.next"))}</strong><ul>${commands}</ul></div>` : ""}
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
        <small>${escapeHtml(change.change_id || "")} | ${escapeHtml(change.risk || "")} | ${escapeHtml(formatStatus(change.status || "pending"))}</small>
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
      <p class="muted">${escapeHtml(t("settings.configuredMode"))}: ${escapeHtml(formatMode(data.configured_mode))}</p>
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
        window.alert(result.error || t("fitting.applyFailed"));
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

showStatus(t("status.connecting"), t("status.connectingHint"));
load();
