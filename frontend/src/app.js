const API = window.location.origin;
let sessionId = null;
let turnCount = 0;
let resultsBaseline = null;

const PLACEHOLDERS = {
  citations: "No source citations for this response.",
};

const PLAN_POLL_INTERVAL_MS = 20_000;
const PLAN_POLL_MAX_ATTEMPTS = 30;

const el = (id) => document.getElementById(id);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function loadDisclaimer() {
  try {
    const res = await fetch(`${API}/api/disclaimer`);
    const data = await res.json();
    el("disclaimer-text").textContent = data.text;
  } catch {
    el("disclaimer-text").textContent =
      "Disclaimer: This tool is for informational purposes only. The model can make mistakes. This is not medical advice.";
  }
}

function updatePlanLoadHint(count, message) {
  const hint = el("plan-load-hint");
  if (message) {
    hint.textContent = message;
    return;
  }
  hint.textContent = count > 0 ? `${count} plan(s) loaded` : "No plans in database yet";
}

function populatePlanSelect(plans) {
  const select = el("filter-plan");
  const selected = select.value;
  while (select.options.length > 1) {
    select.remove(1);
  }
  plans.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.plan_key;
    opt.textContent = `${p.plan_name} (${p.plan_key})`;
    select.appendChild(opt);
  });
  if (selected && [...select.options].some((o) => o.value === selected)) {
    select.value = selected;
  }
}

async function loadPlans() {
  const res = await fetch(`${API}/api/plans`);
  if (!res.ok) {
    throw new Error(`plans API ${res.status}`);
  }
  const plans = await res.json();
  if (!Array.isArray(plans)) {
    throw new Error("plans API returned non-array");
  }
  populatePlanSelect(plans);
  updatePlanLoadHint(plans.length);
  return plans.length;
}

async function pollPlansUntilLoaded() {
  for (let attempt = 0; attempt < PLAN_POLL_MAX_ATTEMPTS; attempt += 1) {
    try {
      const count = await loadPlans();
      if (count > 0) {
        return;
      }
    } catch (e) {
      console.warn("Could not load plans", e);
    }
    if (attempt < PLAN_POLL_MAX_ATTEMPTS - 1) {
      updatePlanLoadHint(0, "Waiting for plan data…");
      await sleep(PLAN_POLL_INTERVAL_MS);
    }
  }
  updatePlanLoadHint(0, "No plans yet — click Refresh after ingest finishes");
}

function getFilters() {
  const filters = {};
  const drug = el("filter-drug").value.trim();
  const dosage = el("filter-dosage").value.trim();
  const plan = el("filter-plan").value;
  const year = el("filter-year").value;
  const daysSupply = el("filter-days-supply").value;
  const ytd = el("filter-ytd").value;
  if (drug) filters.drug = drug;
  if (dosage) filters.dosage = dosage;
  if (plan) filters.plan_id = plan;
  if (year) filters.contract_year = parseInt(year, 10);
  if (daysSupply) filters.days_supply = parseInt(daysSupply, 10);
  const ytdNum = parseFloat(ytd);
  if (ytd && !Number.isNaN(ytdNum) && ytdNum > 0) filters.ytd_oop_spend = ytdNum;
  return Object.keys(filters).length ? filters : null;
}

function escapeAttr(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeHtml(value) {
  return escapeAttr(value);
}

function renderMarkdown(text) {
  const escaped = String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return escaped
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/\n\n/g, "</p><p>")
    .replace(/\n/g, "<br>");
}

function renderCitationRef(index) {
  return `<a href="#citation-${index}" class="citation-ref" data-citation="${index}" aria-label="View source ${index}">[${index}]</a>`;
}

function renderCitationRefs(citations) {
  if (!citations?.length) return "";
  return citations.map((_, i) => renderCitationRef(i + 1)).join("");
}

function linkifyCitationMarkers(html, citations) {
  if (!citations?.length) return html;
  return html.replace(/\[(\d+)\]/g, (match, rawIndex) => {
    const index = parseInt(rawIndex, 10);
    if (index >= 1 && index <= citations.length) {
      return renderCitationRef(index);
    }
    return match;
  });
}

function renderExplanationWithCitations(text, citations) {
  const body = renderMarkdown(text);
  if (!citations?.length) return body;

  let linked = linkifyCitationMarkers(body, citations);
  if (!/\[(\d+)\]/.test(text)) {
    linked += ` <span class="citation-refs">${renderCitationRefs(citations)}</span>`;
  }
  return linked;
}

function openCitation(index) {
  const citationEl = document.getElementById(`citation-${index}`);
  if (!citationEl) return;
  citationEl.open = true;
  citationEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  const summary = citationEl.querySelector("summary");
  if (summary) summary.focus();
}

function appendMessage(role, text, source, citations) {
  const empty = el("empty-state");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = `message ${role}`;
  if (role === "assistant") {
    div.innerHTML = `<div class="message-body"><p>${renderExplanationWithCitations(text, citations)}</p></div>`;
  } else {
    div.textContent = text;
  }
  if (role === "assistant" && source) {
    const sourceEl = document.createElement("div");
    sourceEl.className = "message-source";
    sourceEl.textContent = `via ${source}`;
    div.appendChild(sourceEl);
  }
  el("chat-messages").appendChild(div);
  el("chat-messages").scrollTop = el("chat-messages").scrollHeight;
}

function showLoading(text) {
  el("loading-text").textContent = text;
  el("loading").classList.remove("hidden");
}

function hideLoading() {
  el("loading").classList.add("hidden");
}

function drugKeyFromResp(resp) {
  if (resp.rxcui) return resp.rxcui;
  if (resp.drug_name) return resp.drug_name.toLowerCase();
  const filters = getFilters() || {};
  if (filters.drug) return `${filters.drug}${filters.dosage || ""}`.toLowerCase();
  return null;
}

function establishBaseline(resp) {
  return {
    drugKey: drugKeyFromResp(resp),
    drug_name: resp.drug_name || null,
    citations: resp.citations?.length ? resp.citations : null,
    data_as_of: resp.data_as_of || {},
    tool_statuses: resp.tool_statuses || {},
  };
}

function mergeResults(baseline, resp) {
  const merged = { ...baseline, data_as_of: { ...baseline.data_as_of }, tool_statuses: { ...baseline.tool_statuses } };
  if (resp.drug_name) merged.drug_name = resp.drug_name;
  const key = drugKeyFromResp(resp);
  if (key) merged.drugKey = key;
  if (resp.citations?.length) merged.citations = resp.citations;
  if (resp.data_as_of) Object.assign(merged.data_as_of, resp.data_as_of);
  if (resp.tool_statuses) Object.assign(merged.tool_statuses, resp.tool_statuses);
  return merged;
}

function renderCitationsCard(citations) {
  if (!citations?.length) {
    return `<div class="card"><h3>Citations</h3><p class="card-placeholder">${PLACEHOLDERS.citations}</p></div>`;
  }
  const items = citations
    .map((c, i) => {
      const index = i + 1;
      const link = c.url
        ? `<a href="${escapeAttr(c.url)}" target="_blank" rel="noopener noreferrer">View source documentation</a>`
        : "";
      const sourceName = c.source_label || c.source_id;
      return `
      <details class="citation-item" id="citation-${index}">
        <summary>[${index}] ${escapeHtml(c.claim)}</summary>
        <div class="citation-body">
          <div><strong>${escapeHtml(sourceName)}</strong></div>
          <div>As of ${escapeHtml(c.as_of_date)}</div>
          ${link ? `<div class="citation-link">${link}</div>` : ""}
        </div>
      </details>`;
    })
    .join("");
  return `<div class="card"><h3>Citations</h3><div class="citation-list">${items}</div></div>`;
}

function renderBaseline(baseline, warningHtml) {
  const container = el("results-content");
  container.innerHTML = warningHtml || "";

  const asOf = baseline.data_as_of || {};
  const dates = Object.values(asOf);
  const badge = el("data-as-of");
  if (dates.length) {
    badge.textContent = `Data as of ${dates[0]}`;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  container.innerHTML += renderCitationsCard(baseline.citations);

  if (baseline.tool_statuses && Object.keys(baseline.tool_statuses).length) {
    const statuses = Object.entries(baseline.tool_statuses)
      .map(([k, v]) => `${k}: ${v}`)
      .join(" · ");
    container.innerHTML += `<p style="font-size:0.75rem;color:var(--muted);margin-top:0.5rem">Tools: ${statuses}</p>`;
  }
}

function renderResults(resp) {
  const container = el("results-content");

  if (resp.status === "needs_clarification" || resp.status === "not_found") {
    if (!resultsBaseline) {
      container.innerHTML = `<p class="status-warning">${resp.clarification_message || resp.explanation}</p>`;
      el("data-as-of").classList.add("hidden");
      return;
    }
    const warning = `<p class="status-warning">${resp.clarification_message || resp.explanation}</p>`;
    renderBaseline(resultsBaseline, warning);
    return;
  }

  if (resp.status === "ok") {
    const key = drugKeyFromResp(resp);
    if (!resultsBaseline) {
      resultsBaseline = establishBaseline(resp);
    } else if (key && resultsBaseline.drugKey && key !== resultsBaseline.drugKey) {
      resultsBaseline = establishBaseline(resp);
    } else {
      resultsBaseline = mergeResults(resultsBaseline, resp);
    }
    renderBaseline(resultsBaseline);
    return;
  }

  if (resultsBaseline) {
    const warning =
      resp.status === "limit_reached"
        ? `<p class="status-warning">${resp.explanation}</p>`
        : "";
    renderBaseline(resultsBaseline, warning);
    return;
  }

  container.innerHTML = `<p class="status-warning">${resp.explanation || "No response."}</p>`;
}

function switchMode(mode) {
  const isChat = mode === "chat";
  el("mode-chat").classList.toggle("hidden", !isChat);
  el("mode-chat").hidden = !isChat;
  el("mode-guided").classList.toggle("hidden", isChat);
  el("mode-guided").hidden = isChat;
  el("mode-tab-chat").classList.toggle("active", isChat);
  el("mode-tab-chat").setAttribute("aria-selected", String(isChat));
  el("mode-tab-guided").classList.toggle("active", !isChat);
  el("mode-tab-guided").setAttribute("aria-selected", String(!isChat));
}

function composeGuidedMessage() {
  const drug = el("filter-drug").value.trim();
  const dosage = el("filter-dosage").value.trim();
  const plan = el("filter-plan").value;
  const daysSupply = el("filter-days-supply").value;
  const ytd = el("filter-ytd").value;

  const drugPart = dosage ? `${drug} ${dosage}` : drug;
  let message = `What's the cost for ${drugPart} on plan ${plan}?`;
  if (daysSupply && daysSupply !== "30") {
    message += ` ${daysSupply}-day supply.`;
  }
  const ytdNum = parseFloat(ytd);
  if (ytd && !Number.isNaN(ytdNum) && ytdNum > 0) {
    message += ` YTD spend: $${ytdNum}.`;
  }
  return message;
}

function showGuidedError(message) {
  const err = el("guided-error");
  if (!message) {
    err.textContent = "";
    err.classList.add("hidden");
    return;
  }
  err.textContent = message;
  err.classList.remove("hidden");
}

async function sendMessage(message, { switchToChat = false } = {}) {
  if (!message.trim()) return;
  appendMessage("user", message);
  el("chat-input").value = "";
  el("send-btn").disabled = true;
  el("guided-submit").disabled = true;
  showLoading("Estimating cost…");

  try {
    const body = { message, session_id: sessionId, filters: getFilters() };
    const res = await fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    sessionId = data.session_id;
    turnCount = data.turn_count;
    el("turn-counter").textContent = `${turnCount}/5 turns`;

    const resp = data.response;
    appendMessage(
      "assistant",
      resp.explanation || resp.clarification_message || "No response.",
      resp.response_source,
      resp.citations
    );
    renderResults(resp);
    if (switchToChat) {
      switchMode("chat");
    }
  } catch (err) {
    appendMessage("assistant", "Sorry, something went wrong. Please try again.");
    console.error(err);
  } finally {
    hideLoading();
    el("send-btn").disabled = false;
    el("guided-submit").disabled = false;
  }
}

function submitGuidedEstimate() {
  showGuidedError("");
  const drug = el("filter-drug").value.trim();
  const plan = el("filter-plan").value;
  if (!drug || !plan) {
    showGuidedError("Please enter a drug name and select a plan.");
    return;
  }
  sendMessage(composeGuidedMessage(), { switchToChat: true });
}

el("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(el("chat-input").value);
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => sendMessage(chip.dataset.prompt));
});

el("mode-tab-chat").addEventListener("click", () => switchMode("chat"));
el("mode-tab-guided").addEventListener("click", () => switchMode("guided"));
el("guided-submit").addEventListener("click", submitGuidedEstimate);

document.addEventListener("click", (event) => {
  const ref = event.target.closest(".citation-ref");
  if (!ref) return;
  event.preventDefault();
  openCitation(ref.dataset.citation);
});

el("refresh-plans").addEventListener("click", async () => {
  const btn = el("refresh-plans");
  btn.disabled = true;
  updatePlanLoadHint(0, "Loading plans…");
  try {
    await loadPlans();
  } catch (e) {
    console.warn("Could not load plans", e);
    updatePlanLoadHint(0, "Could not load plans — try again shortly");
  } finally {
    btn.disabled = false;
  }
});

loadDisclaimer();
pollPlansUntilLoaded();
switchMode("chat");
