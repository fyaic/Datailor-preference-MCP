const state = {
  tab: "all",
  query: "",
  manifesto: null
};

const titles = {
  all: "1.2 High-Frequency Preferences",
  pending: "1.3 Pending Confirmation",
  conflicts: "1.4 Attention Required",
  evolution: "1.5 Evolution",
  settings: "1.6 Settings"
};

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
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.tab);
  });
  document.getElementById("active-count").textContent = data.summary.active;
  document.getElementById("pending-count").textContent = data.summary.pending;
  document.getElementById("conflict-count").textContent = data.summary.conflicts;
  document.getElementById("mode-label").textContent = data.mode;
  document.getElementById("store-path").textContent = `Source: ${data.store}`;
  document.getElementById("section-title").textContent = titles[state.tab] || titles.all;

  const content = document.getElementById("content");
  if (state.tab === "evolution") {
    content.innerHTML = renderEvolution(data);
    return;
  }
  if (state.tab === "settings") {
    content.innerHTML = renderSettings(data);
    bindSettings();
    return;
  }
  const rows = filteredPreferences(data);
  content.innerHTML = rows.length ? renderTable(rows) : '<p class="empty">No records in this section.</p>';
  bindActions();
}

function filteredPreferences(data) {
  let rows = [...data.preferences];
  if (state.tab === "pending") {
    rows = rows.filter((item) => item.status !== "active");
  } else if (state.tab === "conflicts") {
    rows = rows.filter((item) => item.attention);
  }
  const query = state.query.trim().toLowerCase();
  if (query) {
    rows = rows.filter((item) => `${item.statement} ${item.applies_to} ${item.status}`.toLowerCase().includes(query));
  }
  return rows.sort((a, b) => b.frequency - a.frequency || b.confidence_score - a.confidence_score);
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
      <td>${escapeHtml(item.status)}</td>
    </tr>
  `).join("");
  return `
    <table class="table">
      <thead>
        <tr>
          <th>Preference</th>
          <th>Freq.</th>
          <th>Sessions</th>
          <th>Conf.</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

function renderDetail(item) {
  const evidence = item.evidence.length
    ? item.evidence.map((entry, index) => `<li>[${index + 1}] ${escapeHtml(entry.quote || entry.source)}</li>`).join("")
    : "<li>No stored evidence in the Markdown view.</li>";
  const notes = item.conflict_notes.length
    ? `<div class="theorem"><strong>Theorem.</strong> ${escapeHtml(item.conflict_notes.join(" "))}</div>`
    : "";
  return `
    <div class="details">
      <h3>Statement</h3>
      <p>${escapeHtml(item.statement)}</p>
      ${notes}
      <div class="definition">
        <strong>Definition.</strong>
        <ul>${evidence}</ul>
      </div>
      <div class="actions">
        <button data-feedback="confirmation" data-id="${escapeHtml(item.id)}" data-text="${escapeAttr(item.statement)}">Confirm</button>
        <button data-feedback="rejection" data-id="${escapeHtml(item.id)}" data-text="${escapeAttr(item.statement)}">Reject</button>
        <button data-feedback="correction" data-id="${escapeHtml(item.id)}" data-text="${escapeAttr(item.statement)}">Correct</button>
      </div>
    </div>
  `;
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
      <thead><tr><th>Preference</th><th>Use</th><th>Confirm</th><th>Correct</th><th>Reject</th><th>Recommendation</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  ` : '<p class="empty">No feedback has been recorded.</p>';
}

function renderSettings(data) {
  return `
    <div class="definition">
      <h3>User Mode</h3>
      <p class="muted">Configured mode: ${escapeHtml(data.configured_mode)}. Effective mode: ${escapeHtml(data.mode)}.</p>
      <div class="actions">
        <button data-setting="mode" data-value="auto">Auto</button>
        <button data-setting="mode" data-value="autonomous">Autonomous Trust</button>
        <button data-setting="mode" data-value="curated">Curated Autonomy</button>
      </div>
    </div>
    <div class="definition">
      <h3>Paper</h3>
      <div class="actions">
        <button data-setting="theme" data-value="light">Light</button>
        <button data-setting="theme" data-value="dark">Dark</button>
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
        feedback = window.prompt("Correction", button.dataset.text) || "";
      }
      if (!feedback) return;
      await request("/api/feedback", {
        method: "POST",
        body: JSON.stringify({
          feedback_type: type,
          user_feedback: feedback,
          preference_id: button.dataset.id,
          preference_text: button.dataset.text
        })
      });
      await load();
    });
  });
}

function bindSettings() {
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

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    render();
  });
});

document.getElementById("search").addEventListener("input", (event) => {
  state.query = event.target.value;
  render();
});

load().catch((error) => {
  document.getElementById("content").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
});
