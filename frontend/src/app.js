const API = window.location.origin;
let sessionId = null;
let turnCount = 0;
let resultsBaseline = null;
let allPlans = [];
let planListHighlight = -1;
let sessionUsage = { inputTokens: 0, outputTokens: 0, costUsd: 0 };

const DEFAULT_MODEL = "gpt-5.4-nano";
const MODEL_OPTIONS = [
  { id: "gpt-5.6-luna", label: "GPT-5.6 Luna" },
  { id: "gpt-5.4-nano", label: "GPT-5.4 Nano" },
  { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
];

const PLACEHOLDERS = {
  citations: "No source citations for this response.",
};

const PLAN_POLL_INTERVAL_MS = 20_000;
const PLAN_POLL_MAX_ATTEMPTS = 30;

const BENEFIT_PHASE_LABELS = {
  pre_deductible: "Pre-deductible",
  initial_coverage: "Initial coverage",
  catastrophic: "Catastrophic coverage",
};

const PHARMACY_CHANNEL_ROWS = [
  ["preferred_retail", "Preferred retail"],
  ["standard_retail", "Standard retail"],
  ["preferred_mail", "Preferred mail-order"],
  ["standard_mail", "Standard mail-order"],
];

const FIELD_TIPS = {
  section_plan_fill: "Drug, dosage, plan, and fill length used for this estimate.",
  section_benefit: "Formulary coverage, deductible, tier, and Part D benefit phase.",
  section_channel: "Plan cost share and estimated out-of-pocket by pharmacy type.",
  drug: "Medication name on the plan formulary.",
  dosage: "Strength and form you asked about.",
  plan: "Medicare Part D plan name and contract ID.",
  days_supply: "How many days one prescription fill is intended to cover.",
  covered: "Whether the drug is on this plan’s formulary.",
  deductible: "Annual drug deductible before the plan pays its share.",
  tier: "Formulary cost tier; higher tiers usually cost more.",
  ded_applies: "Whether costs for this tier count toward the deductible (Y/N).",
  benefit_phase: "Part D phase from your year-to-date out-of-pocket spend.",
  effective_phase: "Phase used to price this fill after plan rules.",
  ytd_spend: "Out-of-pocket Part D drug costs you entered for this year.",
  annual_oop_cap: "Most you pay out of pocket for Part D drugs in the year.",
  remaining_oop: "Out-of-pocket dollars left before catastrophic coverage.",
  projected_annual_oop: "Rough yearly out-of-pocket if you keep this fill schedule.",
  channel: "Pharmacy type (retail or mail-order, preferred or standard).",
  plan_copay: "Fixed copay from plan data for this tier and channel.",
  plan_coinsurance: "Coinsurance percentage from plan data.",
  applied_copay: "Copay amount used for this fill after benefit rules.",
  applied_coinsurance: "Coinsurance percentage used for this estimate.",
  est_cost: "Estimated amount you pay for this fill at that channel.",
  preferred_retail: "In-network retail pharmacy with the lowest cost share.",
  standard_retail: "Retail pharmacy with standard (non-preferred) cost share.",
  preferred_mail: "Plan’s preferred mail-order pharmacy.",
  standard_mail: "Mail-order with standard cost share.",
};

const el = (id) => document.getElementById(id);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatTokenCount(count) {
  return new Intl.NumberFormat("en-US").format(count);
}

function formatCostUsd(amount) {
  if (amount == null || Number.isNaN(amount)) return "$0.00";
  if (amount < 0.01) return `$${amount.toFixed(4)}`;
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount);
}

function formatUsageMeta(usage) {
  if (!usage) return "";
  const parts = [];
  if (usage.total_tokens != null) {
    parts.push(`${formatTokenCount(usage.total_tokens)} tokens`);
  }
  if (usage.cost_usd != null) {
    parts.push(formatCostUsd(usage.cost_usd));
  }
  return parts.join(" · ");
}

function updateSessionUsageDisplay() {
  const totalTokens = sessionUsage.inputTokens + sessionUsage.outputTokens;
  el("session-usage").textContent = `${formatTokenCount(totalTokens)} tokens · ${formatCostUsd(sessionUsage.costUsd)}`;
}

function accumulateSessionUsage(usage) {
  if (!usage) return;
  sessionUsage.inputTokens += usage.input_tokens || 0;
  sessionUsage.outputTokens += usage.output_tokens || 0;
  sessionUsage.costUsd += usage.cost_usd || 0;
  updateSessionUsageDisplay();
}

function getSelectedModel() {
  return el("model-select").value || DEFAULT_MODEL;
}

function populateModelSelect() {
  const select = el("model-select");
  select.innerHTML = "";
  MODEL_OPTIONS.forEach((model) => {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.label;
    if (model.id === DEFAULT_MODEL) {
      option.selected = true;
    }
    select.appendChild(option);
  });
}

function formatCurrency(amount) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(amount);
}

function formatCostRange(low, high) {
  if (low != null && high != null) {
    if (low === high) return formatCurrency(low);
    return `${formatCurrency(low)}–${formatCurrency(high)}`;
  }
  if (low != null) return formatCurrency(low);
  if (high != null) return formatCurrency(high);
  return null;
}

function formatNa(value, formatter = String) {
  if (value == null || value === "") return "NA";
  return formatter(value);
}

function formatPercent(pct) {
  if (pct == null || Number.isNaN(pct)) return "NA";
  return `${pct}%`;
}

function formatShareCopay(copay) {
  if (copay == null) return "NA";
  return formatCurrency(copay);
}

function formatShareCoinsurance(pct) {
  if (pct == null) return "NA";
  return formatPercent(pct);
}

function formatChannelCost(channel) {
  if (!channel) return "NA";
  if (channel.coinsurance && channel.cost_low == null && channel.cost_high == null) {
    return "NA (coinsurance)";
  }
  return formatCostRange(channel.cost_low, channel.cost_high) || "NA";
}

function formatDedApplies(value) {
  if (value === "Y" || value === "N") return value;
  return "NA";
}

function tierLabel(estimate) {
  const tiers = estimate.tiers_matched || [];
  if (!tiers.length) return null;
  if (estimate.same_tier !== false && tiers.length === 1) {
    return `Tier ${tiers[0]}`;
  }
  return `Tiers ${tiers.join(", ")}`;
}

function benefitPhaseLabel(phase) {
  if (!phase) return null;
  return BENEFIT_PHASE_LABELS[phase] || phase.replace(/_/g, " ");
}

function estimateCardVariant(estimate) {
  if (estimate.quantity_limit_blocked || estimate.covered === false) {
    return "estimate-card--blocked";
  }
  if (estimate.caveats?.length) {
    return "estimate-card--warning";
  }
  return "";
}

const STATUS_ICONS = {
  ok: `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M5 10.5l3.2 3.2L15 6.8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  warning: `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M10 3.5l7.5 13h-15l7.5-13z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M10 8.2v3.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="10" cy="14.3" r="0.9" fill="currentColor"/></svg>`,
  blocked: `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="10" cy="10" r="7.5" stroke="currentColor" stroke-width="1.8"/><path d="M6 6l8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
};

function estimateStatusIconHtml(variant) {
  const key = variant === "estimate-card--blocked" ? "blocked" : variant === "estimate-card--warning" ? "warning" : "ok";
  return `<span class="estimate-status-icon" aria-hidden="true">${STATUS_ICONS[key]}</span>`;
}

function renderEstimateCardHtml(estimate, { compact = false } = {}) {
  if (!estimate) return "";

  const variant = estimateCardVariant(estimate);
  const cost = formatCostRange(estimate.cost_low, estimate.cost_high);
  const tier = tierLabel(estimate);
  const phase = benefitPhaseLabel(estimate.benefit_phase);
  const days = estimate.days_supply ? `${estimate.days_supply}-day fill` : null;
  const drug = escapeHtml(estimate.drug_name || "Drug");
  const plan = escapeHtml(
    estimate.plan_name && estimate.plan_key
      ? `${estimate.plan_name} (${estimate.plan_key})`
      : estimate.plan_key || estimate.plan_name || ""
  );

  const badges = [tier, days, phase].filter(Boolean);
  const badgeHtml = badges
    .map((b) => `<span class="estimate-badge">${escapeHtml(b)}</span>`)
    .join("");

  let costHtml = "";
  if (estimate.quantity_limit_blocked) {
    costHtml = `<div class="estimate-cost estimate-cost--blocked">Fill blocked</div>`;
    if (estimate.max_allowed_days_supply) {
      costHtml += `<p class="estimate-note">Max allowed days supply: ${estimate.max_allowed_days_supply}</p>`;
    }
  } else if (estimate.covered === false) {
    costHtml = `<div class="estimate-cost estimate-cost--blocked">Not covered</div>`;
  } else if (cost) {
    costHtml = `<div class="estimate-cost">${escapeHtml(cost)}</div>`;
  }

  const caveats = estimate.caveats || [];
  const caveatHtml = caveats.length
    ? `<ul class="estimate-caveats">${caveats.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>`
    : "";

  const compactClass = compact ? " estimate-card--compact" : "";

  return `
    <div class="estimate-card ${variant}${compactClass}" role="region" aria-label="Cost estimate">
      <div class="estimate-card-header">
        ${estimateStatusIconHtml(variant)}
        <div class="estimate-card-header-text">
          <span class="estimate-drug">${drug}</span>
          ${plan ? `<span class="estimate-plan">${plan}</span>` : ""}
        </div>
      </div>
      ${costHtml}
      ${badgeHtml ? `<div class="estimate-meta">${badgeHtml}</div>` : ""}
      ${caveatHtml}
    </div>`;
}

function renderMultiChannelEstimateCardHtml(data, { compact = false } = {}) {
  if (!data) return "";

  const drug = escapeHtml(data.drug_name || "Drug");
  const dosageLine = data.dosage
    ? `<span class="estimate-dosage">${escapeHtml(data.dosage)}</span>`
    : "";
  const plan = escapeHtml(
    data.plan_name && data.plan_key
      ? `${data.plan_name} (${data.plan_key})`
      : data.plan_key || data.plan_name || ""
  );
  const covered =
    data.covered === true ? "Yes" : data.covered === false ? "No" : "NA";
  const deductible = formatNa(data.deductible, (v) => formatCurrency(v));
  const tier = data.tier != null ? `Tier ${data.tier}` : "NA";
  const benefitPhase = benefitPhaseLabel(data.benefit_phase) || "NA";
  const effectivePhase = benefitPhaseLabel(data.effective_phase) || "NA";
  const days = data.days_supply ? `${data.days_supply}-day fill` : "NA";
  const ytd =
    data.ytd_oop_spend != null ? formatCurrency(data.ytd_oop_spend) : "NA";

  const channelRows = PHARMACY_CHANNEL_ROWS.map(([key, label]) => {
    const channel = data.channels?.[key];
    return `<tr>
      <th scope="row">${withFieldInfo(label, key)}</th>
      <td>${escapeHtml(formatShareCopay(channel?.plan_copay))}</td>
      <td>${escapeHtml(formatShareCoinsurance(channel?.plan_coinsurance_pct))}</td>
      <td>${escapeHtml(formatShareCopay(channel?.applied_copay))}</td>
      <td>${escapeHtml(formatShareCoinsurance(channel?.applied_coinsurance_pct))}</td>
      <td>${escapeHtml(formatChannelCost(channel))}</td>
    </tr>`;
  }).join("");

  const annualFacts = [
    data.annual_oop_cap != null
      ? `<div><dt>${withFieldInfo("Annual OOP cap", "annual_oop_cap")}</dt><dd>${escapeHtml(formatCurrency(data.annual_oop_cap))}</dd></div>`
      : "",
    data.remaining_oop_headroom != null
      ? `<div><dt>${withFieldInfo("Remaining before cap", "remaining_oop")}</dt><dd>${escapeHtml(formatCurrency(data.remaining_oop_headroom))}</dd></div>`
      : "",
    data.annual_budget_cost_low != null
      ? `<div><dt>${withFieldInfo("Projected annual OOP (this drug)", "projected_annual_oop")}</dt><dd>${escapeHtml(
          data.annual_budget_cost_low === data.annual_budget_cost_high
            ? formatCurrency(data.annual_budget_cost_low)
            : `${formatCurrency(data.annual_budget_cost_low)}–${formatCurrency(data.annual_budget_cost_high)}`
        )}</dd></div>`
      : "",
  ]
    .filter(Boolean)
    .join("");

  const caveats = data.caveats || [];
  const caveatHtml = caveats.length
    ? `<ul class="estimate-caveats">${caveats.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>`
    : "";

  const blockedHtml = data.quantity_limit_blocked
    ? `<p class="estimate-note estimate-note--blocked">Fill blocked${
        data.max_allowed_days_supply
          ? ` — max ${data.max_allowed_days_supply}-day supply`
          : ""
      }</p>`
    : "";

  const compactClass = compact ? " estimate-card--compact" : "";
  const variant =
    data.quantity_limit_blocked || data.covered === false
      ? "estimate-card--blocked"
      : caveats.length
        ? "estimate-card--warning"
        : "";

  return `
    <div class="estimate-card ${variant}${compactClass}" role="region" aria-label="Multi-channel cost estimate">
      <div class="estimate-card-header">
        ${estimateStatusIconHtml(variant)}
        <div class="estimate-card-header-text">
          <span class="estimate-drug">${drug}</span>
          ${dosageLine}
          ${plan ? `<span class="estimate-plan">${plan}</span>` : ""}
        </div>
      </div>
      ${blockedHtml}
      <section class="estimate-section" aria-labelledby="estimate-plan-fill-heading">
        <h4 class="estimate-section-title" id="estimate-plan-fill-heading">${withFieldInfo("Plan & fill", "section_plan_fill")}</h4>
        <dl class="estimate-facts">
          <div><dt>${withFieldInfo("Drug", "drug")}</dt><dd>${drug}</dd></div>
          <div><dt>${withFieldInfo("Dosage", "dosage")}</dt><dd>${escapeHtml(data.dosage ? data.dosage : "NA")}</dd></div>
          <div><dt>${withFieldInfo("Plan", "plan")}</dt><dd>${plan || "NA"}</dd></div>
          <div><dt>${withFieldInfo("Days supply", "days_supply")}</dt><dd>${escapeHtml(days)}</dd></div>
        </dl>
      </section>
      <section class="estimate-section" aria-labelledby="estimate-benefit-heading">
        <h4 class="estimate-section-title" id="estimate-benefit-heading">${withFieldInfo("Benefit context", "section_benefit")}</h4>
        <dl class="estimate-facts">
          <div><dt>${withFieldInfo("Covered", "covered")}</dt><dd>${escapeHtml(covered)}</dd></div>
          <div><dt>${withFieldInfo("Deductible", "deductible")}</dt><dd>${escapeHtml(deductible)}</dd></div>
          <div><dt>${withFieldInfo("Tier", "tier")}</dt><dd>${escapeHtml(tier)}</dd></div>
          <div><dt>${withFieldInfo("Deductible applies to tier", "ded_applies")}</dt><dd>${escapeHtml(formatDedApplies(data.ded_applies_yn))}</dd></div>
          <div><dt>${withFieldInfo("Benefit phase", "benefit_phase")}</dt><dd>${escapeHtml(benefitPhase)}</dd></div>
          <div><dt>${withFieldInfo("Effective phase", "effective_phase")}</dt><dd>${escapeHtml(effectivePhase)}</dd></div>
          <div><dt>${withFieldInfo("YTD spend", "ytd_spend")}</dt><dd>${escapeHtml(ytd)}</dd></div>
          ${annualFacts}
        </dl>
      </section>
      <section class="estimate-section" aria-labelledby="estimate-channel-heading">
        <h4 class="estimate-section-title" id="estimate-channel-heading">${withFieldInfo("This fill by channel", "section_channel")}</h4>
        <div class="channel-cost-table-wrap">
          <table class="channel-cost-table channel-cost-table--wide">
            <caption class="sr-only">Cost share and estimated cost by pharmacy channel</caption>
            <thead>
              <tr>
                <th scope="col">${withFieldInfo("Channel", "channel")}</th>
                <th scope="col">${withFieldInfo("Plan copay", "plan_copay")}</th>
                <th scope="col">${withFieldInfo("Plan coinsurance", "plan_coinsurance")}</th>
                <th scope="col">${withFieldInfo("Applied copay", "applied_copay")}</th>
                <th scope="col">${withFieldInfo("Applied coinsurance", "applied_coinsurance")}</th>
                <th scope="col">${withFieldInfo("Est. cost", "est_cost")}</th>
              </tr>
            </thead>
            <tbody>${channelRows}</tbody>
          </table>
        </div>
      </section>
      ${caveatHtml}
    </div>`;
}

function buildEstimatePayload() {
  const drug = el("filter-drug").value.trim();
  const dosage = el("filter-dosage").value.trim();
  const plan = el("filter-plan").value;
  const daysSupply = parseInt(el("filter-days-supply").value, 10) || 30;
  const ytdRaw = el("filter-ytd").value;
  const payload = {
    plan_id: plan,
    drug,
    days_supply: daysSupply,
    ytd_oop_spend: 0,
  };
  if (dosage) payload.dosage = dosage;
  const ytdNum = parseFloat(ytdRaw);
  if (ytdRaw && !Number.isNaN(ytdNum) && ytdNum >= 0) {
    payload.ytd_oop_spend = ytdNum;
  }
  return payload;
}

function syncGuidedFormFromEstimate(data) {
  if (!data) return;
  if (data.drug_name) {
    el("filter-drug").value = data.drug_name;
  }
  if (data.dosage != null) {
    el("filter-dosage").value = data.dosage;
  }
  if (data.plan_key && allPlans.length) {
    const plan = allPlans.find((p) => p.plan_key === data.plan_key);
    if (plan) selectPlan(plan);
    else {
      el("filter-plan").value = data.plan_key;
      el("filter-plan-input").value = data.plan_name
        ? `${data.plan_name} (${data.plan_key})`
        : data.plan_key;
    }
  }
  if (data.days_supply != null) {
    const daysEl = el("filter-days-supply");
    const val = String(data.days_supply);
    if ([...daysEl.options].some((o) => o.value === val)) {
      daysEl.value = val;
    }
  }
  if (data.ytd_oop_spend != null && !Number.isNaN(data.ytd_oop_spend)) {
    el("filter-ytd").value = String(data.ytd_oop_spend);
  }
}

function renderDeterministicEstimate(resp, { citations, toolStatuses, dataAsOf } = {}) {
  const asOf = dataAsOf || {};
  const dates = [resp.as_of_date, ...Object.values(asOf)].filter(Boolean);
  const badge = el("data-as-of");
  if (dates.length) {
    badge.textContent = `Data as of ${dates[0]}`;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
  const container = el("results-content");
  if (!resp.data) {
    container.innerHTML = `<p class="card-placeholder">${escapeHtml(resp.message || "No estimate available.")}</p>`;
    container.innerHTML += renderCitationsCard(citations);
    return;
  }
  const estimateHtml = renderMultiChannelEstimateCardHtml(resp.data, { compact: true });
  container.innerHTML = estimateHtml + renderCitationsCard(citations);
  syncGuidedFormFromEstimate(resp.data);
  if (toolStatuses && Object.keys(toolStatuses).length) {
    const statuses = Object.entries(toolStatuses)
      .map(([k, v]) => `${k}: ${v}`)
      .join(" · ");
    container.innerHTML += `<p style="font-size:0.75rem;color:var(--muted);margin-top:0.5rem">Tools: ${statuses}</p>`;
  }
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

function initDisclaimerCollapse() {
  const banner = el("disclaimer-banner");
  if (!banner) return;
  const mobile = window.matchMedia("(max-width: 639px)");
  const setCollapsed = () => {
    if (mobile.matches) {
      banner.removeAttribute("open");
    } else {
      banner.setAttribute("open", "");
    }
  };
  setCollapsed();
  mobile.addEventListener("change", setCollapsed);
}

function updatePlanLoadHint(count, message) {
  const hint = el("plan-load-hint");
  if (message) {
    hint.textContent = message;
    return;
  }
  hint.textContent = count > 0 ? `${count} plan(s) loaded` : "No plans in database yet";
}

function formatPlanLabel(plan) {
  const state = plan.state || "";
  const prefix = state ? `${state} — ` : "";
  return `${prefix}${plan.plan_name} (${plan.plan_key})`;
}

function planSearchText(plan) {
  return `${plan.state || ""} ${plan.plan_name} ${plan.plan_key}`.toLowerCase();
}

function sortPlans(plans) {
  return [...plans].sort((a, b) => {
    const stateCmp = (a.state || "").localeCompare(b.state || "");
    if (stateCmp !== 0) return stateCmp;
    return (a.plan_name || "").localeCompare(b.plan_name || "");
  });
}

function clearPlanSelection() {
  el("filter-plan").value = "";
  el("filter-plan-input").value = "";
}

function selectPlan(plan) {
  el("filter-plan").value = plan.plan_key;
  el("filter-plan-input").value = formatPlanLabel(plan);
  closePlanListbox();
}

function openPlanListbox() {
  el("filter-plan-listbox").classList.remove("hidden");
  el("filter-plan-input").setAttribute("aria-expanded", "true");
}

function closePlanListbox() {
  el("filter-plan-listbox").classList.add("hidden");
  el("filter-plan-input").setAttribute("aria-expanded", "false");
  el("filter-plan-input").removeAttribute("aria-activedescendant");
  planListHighlight = -1;
}

function filterPlans(query) {
  const q = query.trim().toLowerCase();
  if (!q) return allPlans;
  return allPlans.filter((p) => planSearchText(p).includes(q));
}

function highlightPlanOption(options) {
  options.forEach((opt, i) => {
    opt.classList.toggle("plan-option--active", i === planListHighlight);
    if (i === planListHighlight) {
      opt.scrollIntoView({ block: "nearest" });
      el("filter-plan-input").setAttribute("aria-activedescendant", opt.id);
    }
  });
}

function renderPlanListbox(plans) {
  const listbox = el("filter-plan-listbox");
  listbox.innerHTML = "";
  plans.forEach((p, i) => {
    const li = document.createElement("li");
    li.className = "plan-option";
    li.role = "option";
    li.id = `plan-option-${i}`;
    li.dataset.planKey = p.plan_key;
    li.textContent = formatPlanLabel(p);
    li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      selectPlan(p);
    });
    listbox.appendChild(li);
  });
}

function populatePlanSelect(plans) {
  const selected = el("filter-plan").value;
  allPlans = sortPlans(plans);
  renderPlanListbox(allPlans);
  if (selected) {
    const plan = allPlans.find((p) => p.plan_key === selected);
    if (plan) {
      el("filter-plan-input").value = formatPlanLabel(plan);
    } else {
      clearPlanSelection();
    }
  }
}

function initPlanCombobox() {
  const input = el("filter-plan-input");
  const listbox = el("filter-plan-listbox");

  input.addEventListener("focus", () => {
    renderPlanListbox(filterPlans(input.value));
    openPlanListbox();
  });

  input.addEventListener("input", () => {
    el("filter-plan").value = "";
    renderPlanListbox(filterPlans(input.value));
    planListHighlight = -1;
    openPlanListbox();
  });

  input.addEventListener("blur", () => {
    setTimeout(() => {
      closePlanListbox();
      const selected = el("filter-plan").value;
      if (selected) {
        const plan = allPlans.find((p) => p.plan_key === selected);
        if (plan) input.value = formatPlanLabel(plan);
      }
    }, 150);
  });

  input.addEventListener("keydown", (e) => {
    const options = [...listbox.querySelectorAll(".plan-option")];
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (listbox.classList.contains("hidden")) {
        renderPlanListbox(filterPlans(input.value));
        openPlanListbox();
      }
      planListHighlight = Math.min(planListHighlight + 1, options.length - 1);
      highlightPlanOption(options);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      planListHighlight = Math.max(planListHighlight - 1, 0);
      highlightPlanOption(options);
    } else if (e.key === "Enter" && planListHighlight >= 0) {
      e.preventDefault();
      const opt = options[planListHighlight];
      if (opt) {
        const plan = allPlans.find((p) => p.plan_key === opt.dataset.planKey);
        if (plan) selectPlan(plan);
      }
    } else if (e.key === "Escape") {
      closePlanListbox();
    }
  });
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

function withFieldInfo(label, tipId) {
  const tip = FIELD_TIPS[tipId];
  const labelHtml = escapeHtml(label);
  if (!tip) return labelHtml;
  const attr = escapeAttr(tip);
  return `${labelHtml}<button type="button" class="field-info" aria-label="${attr}" data-tip="${attr}"><span aria-hidden="true">i</span></button>`;
}

let fieldInfoTooltipAnchor = null;
let fieldInfoTooltipHideTimer = null;

function fieldInfoTooltipEl() {
  let node = document.getElementById("field-info-tooltip");
  if (!node) {
    node = document.createElement("div");
    node.id = "field-info-tooltip";
    node.className = "field-info-tooltip hidden";
    node.setAttribute("role", "tooltip");
    document.body.appendChild(node);
  }
  return node;
}

function showFieldInfoTooltip(anchor) {
  const tip = anchor.dataset.tip;
  if (!tip) return;
  clearTimeout(fieldInfoTooltipHideTimer);
  fieldInfoTooltipAnchor = anchor;
  const tipEl = fieldInfoTooltipEl();
  tipEl.textContent = tip;
  tipEl.classList.remove("hidden");
  tipEl.style.left = "0";
  tipEl.style.top = "0";
  tipEl.style.visibility = "hidden";
  const anchorRect = anchor.getBoundingClientRect();
  const tipRect = tipEl.getBoundingClientRect();
  const margin = 8;
  let left = anchorRect.left + anchorRect.width / 2 - tipRect.width / 2;
  let top = anchorRect.top - tipRect.height - margin;
  left = Math.max(margin, Math.min(left, window.innerWidth - tipRect.width - margin));
  if (top < margin) {
    top = anchorRect.bottom + margin;
  }
  tipEl.style.left = `${Math.round(left)}px`;
  tipEl.style.top = `${Math.round(top)}px`;
  tipEl.style.visibility = "visible";
}

function hideFieldInfoTooltip() {
  clearTimeout(fieldInfoTooltipHideTimer);
  fieldInfoTooltipAnchor = null;
  const tipEl = document.getElementById("field-info-tooltip");
  if (tipEl) tipEl.classList.add("hidden");
}

function scheduleHideFieldInfoTooltip() {
  clearTimeout(fieldInfoTooltipHideTimer);
  fieldInfoTooltipHideTimer = setTimeout(hideFieldInfoTooltip, 80);
}

function initFieldInfoTooltips() {
  document.addEventListener("mouseover", (event) => {
    const anchor = event.target.closest(".field-info");
    if (!anchor?.dataset.tip) return;
    showFieldInfoTooltip(anchor);
  });
  document.addEventListener("mouseout", (event) => {
    const anchor = event.target.closest(".field-info");
    if (!anchor) return;
    const related = event.relatedTarget;
    if (related && anchor.contains(related)) return;
    if (fieldInfoTooltipAnchor === anchor) scheduleHideFieldInfoTooltip();
  });
  document.addEventListener("focusin", (event) => {
    const anchor = event.target.closest(".field-info");
    if (anchor?.dataset.tip) showFieldInfoTooltip(anchor);
  });
  document.addEventListener("focusout", (event) => {
    if (event.target.closest(".field-info")) scheduleHideFieldInfoTooltip();
  });
  window.addEventListener("scroll", hideFieldInfoTooltip, true);
  window.addEventListener("resize", hideFieldInfoTooltip);
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

function canFetchDeterministicEstimate() {
  const payload = buildEstimatePayload();
  return Boolean(payload.drug && payload.plan_id);
}

async function fetchDeterministicEstimate(payload) {
  const res = await fetch(`${API}/api/estimate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json();
  if (!res.ok) {
    return { ok: false, body, status: res.status };
  }
  return { ok: true, body };
}


function chatEstimateBody(resp) {
  const view = estimateResponseFromChat(resp);
  return view?.body ?? null;
}

function renderPanelFromChatResponse(resp, { citations, toolStatuses, dataAsOf } = {}) {
  const body = chatEstimateBody(resp);
  if (body?.data) {
    renderDeterministicEstimate(body, { citations, toolStatuses, dataAsOf });
    return true;
  }
  renderSourcesPanel({
    estimate: resp.estimate,
    citations,
    dataAsOf,
    toolStatuses,
  });
  return false;
}

function estimateResponseFromChat(resp) {
  if (!resp?.channel_estimate) return null;
  const toolStatus =
    resp.tool_statuses?.estimate_drug_cost_all_channels ||
    resp.tool_statuses?.estimate_drug_cost ||
    "ok";
  return {
    ok: true,
    body: {
      status: toolStatus,
      data: resp.channel_estimate,
      as_of_date:
        resp.data_as_of?.estimate ||
        resp.data_as_of?.estimate_drug_cost_all_channels ||
        resp.data_as_of?.estimate_drug_cost ||
        "",
    },
  };
}

function formatMultiChannelSummary(data) {
  if (!data) return "No estimate could be computed.";

  const drug = data.drug_name || "This drug";
  const plan =
    data.plan_name && data.plan_key
      ? `${data.plan_name} (${data.plan_key})`
      : data.plan_key || data.plan_name || "this plan";
  const daysSupply = data.days_supply || 30;
  const phase = benefitPhaseLabel(data.benefit_phase) || "current benefit";
  const ytd =
    data.ytd_oop_spend != null ? formatCurrency(data.ytd_oop_spend) : "$0.00";

  const channelCosts = PHARMACY_CHANNEL_ROWS.map(([key]) => data.channels?.[key]).filter(
    (c) => c?.cost_low != null
  );
  if (!channelCosts.length) {
    return data.caveats?.length
      ? data.caveats.join("\n\n")
      : `${drug} on ${plan}: no dollar estimate available for this fill.`;
  }

  const low = Math.min(...channelCosts.map((c) => c.cost_low));
  const high = Math.max(
    ...channelCosts.map((c) => (c.cost_high != null ? c.cost_high : c.cost_low))
  );
  const costText =
    low === high ? formatCurrency(low) : `${formatCurrency(low)}–${formatCurrency(high)}`;

  const parts = [
    `${drug} for a ${daysSupply}-day supply on ${plan} is estimated at ${costText} depending on pharmacy channel (${phase} phase), assuming ${ytd} spent so far this year.`,
  ];
  if (data.tier != null) {
    parts.push(`Formulary tier: ${data.tier}.`);
  }
  for (const caveat of data.caveats || []) {
    parts.push(caveat);
  }
  return parts.join("\n\n");
}

const COPY_ICON_SVG = `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="7" y="7" width="9.5" height="9.5" rx="1.5" stroke="currentColor" stroke-width="1.4"/><path d="M13 7V4.5A1.5 1.5 0 0 0 11.5 3h-6A1.5 1.5 0 0 0 4 4.5v6A1.5 1.5 0 0 0 5.5 12H7" stroke="currentColor" stroke-width="1.4"/></svg>`;
const CHECK_ICON_SVG = `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M5 10.5l3.2 3.2L15 6.8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function appendMessage(role, text, source, citations, usage) {
  const empty = el("empty-state");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.dataset.copyText = text;
  if (role === "assistant") {
    div.innerHTML = `<div class="message-body"><p>${renderExplanationWithCitations(text, citations)}</p></div>`;
  } else {
    const textEl = document.createElement("div");
    textEl.className = "message-text";
    textEl.textContent = text;
    div.appendChild(textEl);
  }

  const metaParts = [];
  if (role === "assistant" && source) metaParts.push(`via ${source}`);
  if (role === "assistant") {
    const usageText = formatUsageMeta(usage);
    if (usageText) metaParts.push(usageText);
  }

  const footer = document.createElement("div");
  footer.className = metaParts.length ? "message-footer message-footer--with-source" : "message-footer message-footer--icon-only";
  if (metaParts.length) {
    const sourceEl = document.createElement("span");
    sourceEl.className = "message-source-text";
    sourceEl.textContent = metaParts.join(" · ");
    footer.appendChild(sourceEl);
  }
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "message-copy-btn";
  copyBtn.dataset.action = "copy-message";
  copyBtn.setAttribute("aria-label", "Copy message");
  copyBtn.title = "Copy message";
  copyBtn.innerHTML = COPY_ICON_SVG;
  footer.appendChild(copyBtn);
  div.appendChild(footer);

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

function renderResultsSkeleton() {
  const container = el("results-content");
  if (!container) return;
  container.innerHTML = `
    <div class="skeleton-card" aria-hidden="true">
      <div class="skeleton-line skeleton-line--title"></div>
      <div class="skeleton-line skeleton-line--short"></div>
      <div class="skeleton-line skeleton-line--cost"></div>
      <div class="skeleton-line skeleton-line--wide"></div>
      <div class="skeleton-line skeleton-line--wide"></div>
      <div class="skeleton-line skeleton-line--short"></div>
    </div>`;
}

function resetResultsPlaceholderIfEmpty() {
  if (resultsBaseline) return;
  const container = el("results-content");
  if (!container) return;
  container.innerHTML = `<p class="placeholder">Your cost estimate and sources will appear here after you get an estimate.</p>`;
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
    estimate: resp.estimate || null,
    channel_estimate: resp.channel_estimate || null,
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
  if (resp.estimate) merged.estimate = resp.estimate;
  if (resp.channel_estimate) merged.channel_estimate = resp.channel_estimate;
  if (resp.citations?.length) merged.citations = resp.citations;
  if (resp.data_as_of) Object.assign(merged.data_as_of, resp.data_as_of);
  if (resp.tool_statuses) Object.assign(merged.tool_statuses, resp.tool_statuses);
  return merged;
}

function renderCitationsCard(citations) {
  if (!citations?.length) {
    return `<div class="card card--sources"><h3>Sources</h3><p class="card-placeholder">${PLACEHOLDERS.citations}</p></div>`;
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
  return `<div class="card card--sources"><h3>Sources</h3><div class="citation-list">${items}</div></div>`;
}

function renderSourcesPanel({ estimate, citations, dataAsOf, toolStatuses } = {}) {
  const container = el("results-content");
  const asOf = dataAsOf || {};
  const dates = Object.values(asOf).filter(Boolean);
  const badge = el("data-as-of");
  if (dates.length) {
    badge.textContent = `Data as of ${dates[0]}`;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  const estimateHtml = estimate ? renderEstimateCardHtml(estimate, { compact: true }) : "";
  container.innerHTML = estimateHtml + renderCitationsCard(citations);

  if (toolStatuses && Object.keys(toolStatuses).length) {
    const statuses = Object.entries(toolStatuses)
      .map(([k, v]) => `${k}: ${v}`)
      .join(" · ");
    container.innerHTML += `<p style="font-size:0.75rem;color:var(--muted);margin-top:0.5rem">Tools: ${statuses}</p>`;
  }
}

function renderBaseline(baseline) {
  const syntheticResp = {
    channel_estimate: baseline.channel_estimate,
    estimate: baseline.estimate,
    tool_statuses: baseline.tool_statuses,
    data_as_of: baseline.data_as_of,
  };
  if (!renderPanelFromChatResponse(syntheticResp, {
    citations: baseline.citations,
    dataAsOf: baseline.data_as_of,
    toolStatuses: baseline.tool_statuses,
  })) {
    // renderPanelFromChatResponse already called renderSourcesPanel when no channel_estimate
  }
}

function renderResults(resp) {
  if (resp.status === "needs_clarification" || resp.status === "not_found") {
    if (!resultsBaseline) {
      renderSourcesPanel({
        estimate: resp.estimate,
        citations: resp.citations,
        dataAsOf: resp.data_as_of,
        toolStatuses: resp.tool_statuses,
      });
      return;
    }
    renderBaseline(resultsBaseline);
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
    renderBaseline(resultsBaseline);
    return;
  }

  renderSourcesPanel();
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

function chatErrorMessage(res, data) {
  if (typeof data === "string" && data.trim()) {
    return data.trim();
  }
  if (data && typeof data === "object") {
    const detail = data.detail ?? data.error ?? data.message;
    if (typeof detail === "string" && detail.trim()) {
      return detail.trim();
    }
    if (Array.isArray(detail) && detail.length) {
      return detail.map((item) => item.msg || String(item)).join(" ");
    }
  }
  return `The server could not complete that request (${res.status}).`;
}

async function sendMessage(message, { switchToChat = false } = {}) {
  if (!message.trim()) return;
  appendMessage("user", message);
  el("chat-input").value = "";
  el("send-btn").disabled = true;
  el("guided-submit").disabled = true;
  showLoading("Estimating cost…");
  if (!resultsBaseline) renderResultsSkeleton();

  try {
    const body = {
      message,
      session_id: sessionId,
      filters: getFilters(),
      model: getSelectedModel(),
    };

    const res = await fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const contentType = res.headers.get("content-type") || "";
    let data = null;
    if (contentType.includes("application/json")) {
      data = await res.json();
    } else {
      const text = await res.text();
      if (!res.ok) {
        appendMessage("assistant", `Sorry — ${chatErrorMessage(res, text)} Please try again.`);
        return;
      }
      throw new Error(`Unexpected response format (${res.status})`);
    }

    if (!res.ok) {
      appendMessage("assistant", `Sorry — ${chatErrorMessage(res, data)} Please try again.`);
      return;
    }

    if (!data?.response) {
      appendMessage("assistant", "Sorry, something went wrong. Please try again.");
      return;
    }

    sessionId = data.session_id;
    turnCount = data.turn_count;
    el("turn-counter").textContent = `${turnCount}/5 turns`;

    const resp = data.response;
    const explanation = resp.explanation || resp.clarification_message || "No response.";
    appendMessage(
      "assistant",
      explanation,
      resp.channel_estimate ? resp.response_source || "CMS data" : resp.response_source,
      resp.citations,
      resp.llm_usage
    );
    if (resp.status === "ok") {
      const key = drugKeyFromResp(resp);
      if (!resultsBaseline) {
        resultsBaseline = establishBaseline(resp);
      } else if (key && resultsBaseline.drugKey && key !== resultsBaseline.drugKey) {
        resultsBaseline = establishBaseline(resp);
      } else {
        resultsBaseline = mergeResults(resultsBaseline, resp);
      }
    }
    renderPanelFromChatResponse(resp, {
      citations: resp.citations,
      toolStatuses: resp.tool_statuses,
      dataAsOf: resp.data_as_of,
    });
    accumulateSessionUsage(resp.llm_usage);
    if (switchToChat) {
      switchMode("chat");
    }
  } catch (err) {
    appendMessage("assistant", "Sorry, something went wrong. Please try again.");
    console.error(err);
  } finally {
    hideLoading();
    resetResultsPlaceholderIfEmpty();
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
  void runDeterministicEstimate({ switchToChat: true });
}

async function runDeterministicEstimate({ switchToChat = false } = {}) {
  const message = composeGuidedMessage();
  el("guided-submit").disabled = true;
  el("send-btn").disabled = true;
  showLoading("Computing costs…");
  showGuidedError("");
  if (!resultsBaseline) renderResultsSkeleton();

  try {
    const res = await fetch(`${API}/api/estimate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildEstimatePayload()),
    });
    const body = await res.json();
    if (!res.ok) {
      showGuidedError(chatErrorMessage(res, body));
      resetResultsPlaceholderIfEmpty();
      return;
    }

    appendMessage("user", message);
    if (switchToChat) {
      switchMode("chat");
    }
    appendMessage(
      "assistant",
      body.data ? formatMultiChannelSummary(body.data) : body.message || "No estimate could be computed.",
      "CMS data"
    );
    renderDeterministicEstimate(body);
  } catch (err) {
    showGuidedError("Could not load estimate. Please try again.");
    resetResultsPlaceholderIfEmpty();
    console.error(err);
  } finally {
    hideLoading();
    el("guided-submit").disabled = false;
    el("send-btn").disabled = false;
  }
}

el("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(el("chat-input").value);
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => sendMessage(chip.textContent.trim()));
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

document.addEventListener("click", async (event) => {
  const btn = event.target.closest("[data-action='copy-message']");
  if (!btn) return;
  const text = btn.closest(".message")?.dataset.copyText;
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
    btn.innerHTML = CHECK_ICON_SVG;
    btn.classList.add("message-copy-btn--done");
    setTimeout(() => {
      btn.innerHTML = COPY_ICON_SVG;
      btn.classList.remove("message-copy-btn--done");
    }, 1500);
  } catch {
    btn.title = "Could not copy";
  }
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
initDisclaimerCollapse();
initFieldInfoTooltips();
initPlanCombobox();
populateModelSelect();
updateSessionUsageDisplay();
pollPlansUntilLoaded();
switchMode("chat");
