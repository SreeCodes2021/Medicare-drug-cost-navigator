const API = window.location.origin;
let sessionId = null;
let turnCount = 0;
let resultsBaseline = null;
let resultsBatch = null;
let resultsComparison = null;
let allPlans = [];
let currentDataRelease = null;
let sessionUsage = { inputTokens: 0, outputTokens: 0, costUsd: 0 };
// Mediator usage is zero on the majority of turns (it only ran, in mock/live terms, when
// MEDIATOR_ENABLED is on) — tracked separately so the primary total is never double-counted
// with the combined total.
let sessionMediatorUsage = { inputTokens: 0, outputTokens: 0, costUsd: 0 };
let cachedDisclaimerText = "";
let cachedPrivacyText = "";
let emptyStateHtml = "";
let guidedSessionId = null;
let guidedTurnCount = 0;

const DEFAULT_MODEL = "gpt-5.6-luna";
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
  insulin_cap: "Insulin cap ($35/30-day)",
};

const PHARMACY_CHANNEL_ROWS = [
  ["preferred_retail", "Preferred retail"],
  ["standard_retail", "Standard retail"],
  ["preferred_mail", "Preferred mail-order"],
  ["standard_mail", "Standard mail-order"],
];

const FIELD_TIPS = {
  drug: "Medication name (type or click to browse).",
  dosage: "Strength and form you asked about.",
  plan: "Medicare Part D plan name and contract ID.",
  days_supply: "How many days one prescription fill is intended to cover.",
  covered: "Whether the drug is on this plan’s formulary.",
  deductible: "Annual drug deductible before the plan pays its share.",
  tier: "Formulary cost tier; higher tiers usually cost more.",
  benefit_phase: "Part D phase from your year-to-date out-of-pocket spend.",
  effective_phase: "Phase used to price this fill after plan rules.",
  ytd_spend: "Out-of-pocket Part D drug costs you entered for this year.",
  annual_oop_cap: "Most you pay out of pocket for Part D drugs in the year.",
  remaining_oop: "Out-of-pocket dollars left before catastrophic coverage.",
  projected_annual_oop: "Rough yearly out-of-pocket if you keep this fill schedule.",
  projected_remaining_year_oop: "Estimated out-of-pocket from today through year-end at this fill schedule.",
  channel: "Pharmacy type (retail or mail-order, preferred or standard).",
  channel_rate: "Copay (fixed $) or coinsurance (%) the plan charges at this channel.",
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

function formatSingleUsage(usage) {
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

// resp-shaped usage: { llm_usage, mediator_llm_usage, total_llm_usage }. When the mediator
// didn't run this turn (the common case today), this renders exactly as before — a single
// "N tokens · $X" line — since mediator_llm_usage is absent. Only turns where it did run
// show the three-way breakdown.
function formatUsageMeta(usage) {
  if (!usage) return "";
  if (!usage.mediator_llm_usage) {
    return formatSingleUsage(usage.llm_usage || usage);
  }
  const parts = [];
  const mediatorText = formatSingleUsage(usage.mediator_llm_usage);
  if (mediatorText) parts.push(`Mediator: ${mediatorText}`);
  const primaryText = formatSingleUsage(usage.llm_usage);
  if (primaryText) parts.push(`Response: ${primaryText}`);
  const combinedText = formatSingleUsage(usage.total_llm_usage);
  if (combinedText) parts.push(`Combined: ${combinedText}`);
  return parts.join(" · ");
}

function updateSessionUsageDisplay() {
  const totalTokens = sessionUsage.inputTokens + sessionUsage.outputTokens;
  const el_ = el("session-usage");
  el_.textContent = `${formatTokenCount(totalTokens)} tokens · ${formatCostUsd(sessionUsage.costUsd)}`;
  const mediatorTokens = sessionMediatorUsage.inputTokens + sessionMediatorUsage.outputTokens;
  if (mediatorTokens > 0) {
    const combinedTokens = totalTokens + mediatorTokens;
    const combinedCost = sessionUsage.costUsd + sessionMediatorUsage.costUsd;
    el_.title = `Mediator: ${formatTokenCount(mediatorTokens)} tokens · ${formatCostUsd(sessionMediatorUsage.costUsd)} — ` +
      `Response: ${formatTokenCount(totalTokens)} tokens · ${formatCostUsd(sessionUsage.costUsd)} — ` +
      `Combined: ${formatTokenCount(combinedTokens)} tokens · ${formatCostUsd(combinedCost)}`;
  } else {
    el_.title = "Session token and cost totals";
  }
}

function accumulateSessionUsage(usage) {
  if (!usage) return;
  sessionUsage.inputTokens += usage.input_tokens || 0;
  sessionUsage.outputTokens += usage.output_tokens || 0;
  sessionUsage.costUsd += usage.cost_usd || 0;
  updateSessionUsageDisplay();
}

function accumulateMediatorUsage(usage) {
  if (!usage) return;
  sessionMediatorUsage.inputTokens += usage.input_tokens || 0;
  sessionMediatorUsage.outputTokens += usage.output_tokens || 0;
  sessionMediatorUsage.costUsd += usage.cost_usd || 0;
  updateSessionUsageDisplay();
}

// Guided form has its own model selector (guided-model-select) alongside the plain
// Chat tab's (model-select) — both default to the same model but can be changed
// independently since they're separate conversations.
function getSelectedModel(selectId = "model-select") {
  return el(selectId).value || DEFAULT_MODEL;
}

function populateModelSelect(selectId = "model-select") {
  const select = el(selectId);
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

function formatPercent(pct) {
  if (pct == null || Number.isNaN(pct)) return "NA";
  return `${pct}%`;
}

function formatChannelCost(channel) {
  if (!channel) return "NA";
  if (channel.coinsurance && channel.cost_low == null && channel.cost_high == null) {
    return "NA (coinsurance)";
  }
  return formatCostRange(channel.cost_low, channel.cost_high) || "NA";
}

// A channel is priced one way or the other (never both) — pick whichever applied value is
// set and render just that, instead of four separate copay/coinsurance columns.
function channelRateHtml(channel) {
  const copay = channel?.applied_copay ?? channel?.plan_copay;
  const pct = channel?.applied_coinsurance_pct ?? channel?.plan_coinsurance_pct;
  if (copay != null) {
    return `<span class="channel-rate">${escapeHtml(formatCurrency(copay))} copay</span>`;
  }
  if (pct != null) {
    return (
      `<span class="channel-rate">${escapeHtml(formatPercent(pct))} coinsurance</span>` +
      `<span class="channel-rate-note">$ not available — see note below</span>`
    );
  }
  return `<span class="channel-rate channel-rate--na">NA</span>`;
}

// Only a real dollar figure earns the "success" green — NA/unavailable states read as muted so
// they don't compete visually with the numbers that actually matter.
function channelCostHtml(channel) {
  const text = formatChannelCost(channel);
  const isReal = channel && (channel.cost_low != null || channel.cost_high != null);
  return `<span class="channel-cost${isReal ? "" : " channel-cost--na"}">${escapeHtml(text)}</span>`;
}

function channelHasData(channel) {
  return (
    channel &&
    (channel.applied_copay != null ||
      channel.plan_copay != null ||
      channel.applied_coinsurance_pct != null ||
      channel.plan_coinsurance_pct != null ||
      channel.cost_low != null ||
      channel.cost_high != null)
  );
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

// These caveats are always-present, purely informational/methodological notes, not signs of
// an actual problem — so their presence alone shouldn't turn a card "warning" colored. Text must
// stay byte-for-byte in sync with disclaimers.py (BUG2_CAVEAT, CATASTROPHIC_PHASE_NOTE,
// INSULIN_STATUTORY_CAP_CAVEAT). Every caveat, routine or not, still renders in full in the
// caveats list — this only affects color.
const ROUTINE_CAVEAT_TEXTS = new Set([
  "This estimate assumes the deductible-phase determination is based on your reported YTD spend and this plan's per-tier deductible rule as published by CMS. Some plans exempt certain tiers from the deductible; if your actual pharmacy charge differs from this estimate, your plan's tier-specific deductible treatment is the most likely reason. Confirm with your plan.",
  "Your reported year-to-date out-of-pocket spend meets or exceeds the CMS annual Part D out-of-pocket maximum for this contract year. This fill is estimated using catastrophic coverage cost-sharing (COVERAGE_LEVEL 3 in CMS data), which is typically $0 for covered drugs on the regular formulary.",
  "Federal law (Inflation Reduction Act) caps your cost-sharing for this insulin product at $35 per 30-day supply (scaled for 60/90-day fills), with no deductible ever applying — this estimate reflects that cap directly from CMS's insulin-specific pricing file. That file also publishes a coinsurance-style field for this plan/tier, but it does not reliably match plans' real coinsurance rates, so it was not used to compute this figure; the copay-based amount shown is the authoritative one.",
]);

function hasActionableCaveats(caveats) {
  return (caveats || []).some((c) => !ROUTINE_CAVEAT_TEXTS.has(c));
}

function caveatListHtml(caveats) {
  if (!caveats?.length) return "";
  const items = caveats
    .map((c) => `<li${ROUTINE_CAVEAT_TEXTS.has(c) ? ' class="estimate-caveat--routine"' : ""}>${escapeHtml(c)}</li>`)
    .join("");
  return `<ul class="estimate-caveats">${items}</ul>`;
}

function estimateCardVariant(estimate) {
  if (estimate.quantity_limit_blocked || estimate.covered === false) {
    return "estimate-card--blocked";
  }
  if (hasActionableCaveats(estimate.caveats)) {
    return "estimate-card--warning";
  }
  return "";
}

const STATUS_ICONS = {
  warning: `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M10 3.5l7.5 13h-15l7.5-13z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M10 8.2v3.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="10" cy="14.3" r="0.9" fill="currentColor"/></svg>`,
  blocked: `<svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="10" cy="10" r="7.5" stroke="currentColor" stroke-width="1.8"/><path d="M6 6l8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
};

// No icon for the neutral/ok case — reserve the visual weight of a status badge for cards that
// actually need attention (blocked/not-covered or a genuine warning).
function estimateStatusIconHtml(variant) {
  const key = variant === "estimate-card--blocked" ? "blocked" : variant === "estimate-card--warning" ? "warning" : null;
  if (!key) return "";
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
    costHtml += `<p class="estimate-note estimate-note--blocked">This drug is not on this plan's formulary.</p>`;
  } else if (cost) {
    costHtml = `<div class="estimate-cost">${escapeHtml(cost)}</div>`;
  }

  const caveatHtml = caveatListHtml(estimate.caveats);

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

// Plan section: facts that describe the plan itself. Only rendered when at least one is known.
function renderPlanFactsHtml(data) {
  if (!data) return "";
  const plan = escapeHtml(
    data.plan_name && data.plan_key
      ? `${data.plan_name} (${data.plan_key})`
      : data.plan_key || data.plan_name || ""
  );
  const facts = [
    plan ? `<div><dt>${withFieldInfo("Plan", "plan")}</dt><dd>${plan}</dd></div>` : "",
    data.deductible != null
      ? `<div><dt>${withFieldInfo("Deductible", "deductible")}</dt><dd>${escapeHtml(formatCurrency(data.deductible))}</dd></div>`
      : "",
    data.annual_oop_cap != null
      ? `<div><dt>${withFieldInfo("OOP max", "annual_oop_cap")}</dt><dd>${escapeHtml(formatCurrency(data.annual_oop_cap))}</dd></div>`
      : "",
  ]
    .filter(Boolean)
    .join("");
  if (!facts) return "";
  return `
      <section class="estimate-section" aria-labelledby="estimate-plan-heading">
        <h4 class="estimate-section-title" id="estimate-plan-heading">Plan</h4>
        <dl class="estimate-facts">${facts}</dl>
      </section>`;
}

// Drug section: facts that describe the drug/fill within the plan's formulary. Only rendered
// facts with a known value — no "NA" rows.
function renderDrugFactsHtml(data) {
  if (!data) return "";
  const drug = escapeHtml(data.drug_name || "Drug");
  const covered = data.covered === true ? "Yes" : data.covered === false ? "No" : null;
  const benefitPhase = benefitPhaseLabel(data.benefit_phase);
  const effectivePhase = benefitPhaseLabel(data.effective_phase);
  const facts = [
    `<div><dt>${withFieldInfo("Drug", "drug")}</dt><dd>${drug}</dd></div>`,
    data.dosage
      ? `<div><dt>${withFieldInfo("Dosage", "dosage")}</dt><dd>${escapeHtml(data.dosage)}</dd></div>`
      : "",
    data.days_supply
      ? `<div><dt>${withFieldInfo("Fill size", "days_supply")}</dt><dd>${escapeHtml(`${data.days_supply}-day fill`)}</dd></div>`
      : "",
    covered
      ? `<div><dt>${withFieldInfo("Covered", "covered")}</dt><dd>${escapeHtml(covered)}</dd></div>`
      : "",
    data.tier != null
      ? `<div><dt>${withFieldInfo("Tier", "tier")}</dt><dd>${escapeHtml(`Tier ${data.tier}`)}</dd></div>`
      : "",
    benefitPhase
      ? `<div><dt>${withFieldInfo("Benefit phase", "benefit_phase")}</dt><dd>${escapeHtml(benefitPhase)}</dd></div>`
      : "",
    effectivePhase
      ? `<div><dt>${withFieldInfo("Effective phase", "effective_phase")}</dt><dd>${escapeHtml(effectivePhase)}</dd></div>`
      : "",
  ]
    .filter(Boolean)
    .join("");
  return `
      <section class="estimate-section" aria-labelledby="estimate-drug-heading">
        <h4 class="estimate-section-title" id="estimate-drug-heading">Drug</h4>
        <dl class="estimate-facts">${facts}</dl>
      </section>`;
}

// Cost summary: per-channel rate/estimated cost plus the account-level spend outlook. Channels
// always render (muted when empty) so the table shape stays consistent across cards; spend
// facts only render when known.
function renderCostSummaryHtml(data) {
  if (!data) return "";
  const channelRows = PHARMACY_CHANNEL_ROWS.map(([key, label]) => {
    const channel = data.channels?.[key];
    const mutedClass = channelHasData(channel) ? "" : " channel-row--muted";
    return `<tr class="${mutedClass.trim()}">
      <th scope="row">${withFieldInfo(label, key)}</th>
      <td>${channelRateHtml(channel)}</td>
      <td>${channelCostHtml(channel)}</td>
    </tr>`;
  }).join("");

  const spendFacts = [
    data.ytd_oop_spend != null
      ? `<div><dt>${withFieldInfo("YTD spend", "ytd_spend")}</dt><dd>${escapeHtml(formatCurrency(data.ytd_oop_spend))}</dd></div>`
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
    data.remaining_year_budget_cost_low != null
      ? `<div><dt>${withFieldInfo(
          `Rest-of-year OOP (${data.remaining_year_fills ?? "?"} fills, ${data.remaining_year_days ?? "?"} days left)`,
          "projected_remaining_year_oop"
        )}</dt><dd>${escapeHtml(
          data.remaining_year_budget_cost_low === data.remaining_year_budget_cost_high
            ? formatCurrency(data.remaining_year_budget_cost_low)
            : `${formatCurrency(data.remaining_year_budget_cost_low)}–${formatCurrency(data.remaining_year_budget_cost_high)}`
        )}</dd></div>`
      : "",
  ]
    .filter(Boolean)
    .join("");

  return `
      <section class="estimate-section" aria-labelledby="estimate-cost-heading">
        <h4 class="estimate-section-title" id="estimate-cost-heading">Cost summary</h4>
        <div class="channel-cost-table-wrap">
          <table class="channel-cost-table channel-cost-table--wide">
            <caption class="sr-only">Cost share and estimated cost by pharmacy channel</caption>
            <thead>
              <tr>
                <th scope="col">${withFieldInfo("Channel", "channel")}</th>
                <th scope="col">${withFieldInfo("Rate", "channel_rate")}</th>
                <th scope="col">${withFieldInfo("Est. cost", "est_cost")}</th>
              </tr>
            </thead>
            <tbody>${channelRows}</tbody>
          </table>
        </div>
        ${spendFacts ? `<dl class="estimate-facts estimate-facts--spend">${spendFacts}</dl>` : ""}
      </section>`;
}

function renderMultiChannelEstimateCardHtml(data, { compact = false, hidePlan = false, hideDrug = false } = {}) {
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

  const caveatHtml = caveatListHtml(data.caveats);

  const blockedHtml = data.quantity_limit_blocked
    ? `<p class="estimate-note estimate-note--blocked">Fill blocked${
        data.max_allowed_days_supply
          ? ` — max ${data.max_allowed_days_supply}-day supply`
          : ""
      }</p>`
    : "";

  const notCoveredHtml =
    data.covered === false
      ? `<p class="estimate-note estimate-note--blocked">This drug is not on this plan's formulary.</p>`
      : "";

  const compactClass = compact ? " estimate-card--compact" : "";
  const variant =
    data.quantity_limit_blocked || data.covered === false
      ? "estimate-card--blocked"
      : hasActionableCaveats(data.caveats)
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
      ${notCoveredHtml}
      ${hidePlan ? "" : renderPlanFactsHtml(data)}
      ${hideDrug ? "" : renderDrugFactsHtml(data)}
      ${renderCostSummaryHtml(data)}
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
    void singleDrugPicker.selectDrug(data.drug_name, data.dosage);
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
    cachedDisclaimerText = data.text;
    el("disclaimer-text").textContent = data.text;
  } catch {
    cachedDisclaimerText =
      "Disclaimer: This tool is for informational purposes only. The model can make mistakes. This is not medical advice.";
    el("disclaimer-text").textContent = cachedDisclaimerText;
  }
}

function initDisclaimerCollapse() {
  const banner = el("disclaimer-banner");
  if (!banner) return;
  banner.removeAttribute("open");
}

// ---- App menu (New chat / About / Disclaimer / Privacy) ----

function openMenu() {
  el("app-menu").classList.remove("hidden");
  el("menu-btn").setAttribute("aria-expanded", "true");
}

function closeMenu() {
  el("app-menu").classList.add("hidden");
  el("menu-btn").setAttribute("aria-expanded", "false");
}

function toggleMenu() {
  if (el("app-menu").classList.contains("hidden")) openMenu();
  else closeMenu();
}

// ---- Generic info modal (About / Disclaimer / Privacy content) ----

function openInfoModal(title, bodyHtml) {
  el("info-modal-title").textContent = title;
  el("info-modal-body").innerHTML = bodyHtml;
  el("info-modal").classList.remove("hidden");
  document.documentElement.classList.add("modal-open");
  document.body.classList.add("modal-open");
}

function closeInfoModal() {
  el("info-modal").classList.add("hidden");
  document.documentElement.classList.remove("modal-open");
  document.body.classList.remove("modal-open");
}

const ABOUT_APP_HTML = `
  <p>Estimates what a specific prescription drug will cost on a specific Medicare Part D or Medicare Advantage-with-Part-D plan, using CMS's own published formulary and pricing data — before you go to the pharmacy.</p>
  <p>Every dollar figure traces back to a specific CMS record; it isn't guessed by the AI model.</p>
  <p>Currently covers Arkansas and Texas plans, for a single drug on a plan's standard formulary (non-low-income-subsidy, pre-deductible/initial-coverage/catastrophic phase), across all four standard pharmacy channels — including insulin, priced via its separate $35-per-30-day-supply statutory cap. Other states and coinsurance-based plans aren't supported yet.</p>
  <p>This is not medical advice, financial advice, or Medicare enrollment guidance — confirm with your doctor, pharmacist, or plan before making decisions.</p>
  <p><em>Data source: CMS's public SPUF program.</em></p>
`;

function showAboutModal() {
  closeMenu();
  openInfoModal("About this app", ABOUT_APP_HTML);
}

function showDisclaimerModal() {
  closeMenu();
  const text = cachedDisclaimerText || el("disclaimer-text").textContent || "";
  openInfoModal("Disclaimer", `<p>${escapeHtml(text)}</p>`);
}

async function showPrivacyModal() {
  closeMenu();
  if (!cachedPrivacyText) {
    try {
      const res = await fetch(`${API}/api/privacy`);
      const data = await res.json();
      cachedPrivacyText = data.text;
    } catch {
      cachedPrivacyText = "Privacy policy could not be loaded right now. Please try again shortly.";
    }
  }
  openInfoModal("Privacy policy", `<p>${escapeHtml(cachedPrivacyText)}</p>`);
}

// ---- New chat ----

function resetGuidedFields() {
  singleDrugPicker.clear();
  compareDrugPicker.clear();
  el("filter-plan").value = "";
  el("filter-plan-input").value = "";
  el("filter-ytd").value = "";
  mdPlanCombobox.clear();
  showGuidedError(null);
  resetDrugRows();
  resetComparePlanRows();
  refreshSingleDrugPickers();
  refreshMultiDrugPickers();
  refreshCompareDrugPickers();
  updateGuidedSubmitButtonState();
}

function resetChat() {
  closeMenu();
  switchMode("chat");

  sessionId = null;
  turnCount = 0;
  resultsBaseline = null;
  resultsBatch = null;
  resultsComparison = null;
  sessionUsage = { inputTokens: 0, outputTokens: 0, costUsd: 0 };
  sessionMediatorUsage = { inputTokens: 0, outputTokens: 0, costUsd: 0 };

  el("turn-counter").textContent = "0/5 turns";
  updateSessionUsageDisplay();

  const messages = el("chat-messages");
  if (emptyStateHtml) messages.innerHTML = emptyStateHtml;
  messages.classList.remove("is-thread");

  el("results-content").innerHTML =
    `<p class="placeholder">Your cost estimate and sources will appear here after you get an estimate.</p>`;
  el("data-as-of").classList.add("hidden");

  el("chat-input").value = "";
  el("chat-input").focus();
  updateChatComposerHint();
}

function updateChatComposerHint() {
  const hint = el("chat-composer-hint");
  if (!hint) return;
  const hasThread = el("chat-messages")?.classList.contains("is-thread");
  hint.classList.toggle("hidden", !hasThread);
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

function filterPlans(query) {
  const q = query.trim().toLowerCase();
  if (!q) return allPlans;
  return allPlans.filter((p) => planSearchText(p).includes(q));
}

// Reusable plan combobox: the guided form needs one instance for the single-drug plan
// field, one for the multi-drug basket's single plan field, and up to 4 dynamic instances
// for compare-plans rows — all instances share this implementation and register themselves
// so populatePlanSelect() can refresh every instance's displayed label when plans (re)load.
// `getPlans` scopes the candidate list (defaults to the full unscoped allPlans list); the
// state-picker feature uses it to restrict a combobox to plans in the selected state and
// disables the input until a state is chosen (state/zip stay discovery-only — never sent to
// any estimate endpoint).
let planComboboxInstances = [];

function createPlanCombobox({
  inputId,
  hiddenId,
  listboxId,
  getPlans = () => allPlans,
  onSelect,
  onChange,
}) {
  let highlight = -1;
  const inputEl = () => el(inputId);
  const hiddenEl = () => el(hiddenId);
  const listboxEl = () => el(listboxId);

  function localFilterPlans(query) {
    const plans = getPlans();
    const q = query.trim().toLowerCase();
    if (!q) return plans;
    return plans.filter((p) => planSearchText(p).includes(q));
  }

  function clear() {
    const hadValue = Boolean(hiddenEl().value || inputEl().value);
    hiddenEl().value = "";
    inputEl().value = "";
    if (hadValue && onChange) onChange();
  }

  function setDisabled(disabled, placeholder) {
    inputEl().disabled = disabled;
    if (disabled) {
      clear();
      close();
    }
    if (placeholder) inputEl().placeholder = placeholder;
  }

  function onPlansRescoped() {
    const selected = hiddenEl().value;
    if (selected && !getPlans().some((p) => p.plan_key === selected)) {
      clear();
    }
  }

  function close() {
    listboxEl().classList.add("hidden");
    inputEl().setAttribute("aria-expanded", "false");
    inputEl().removeAttribute("aria-activedescendant");
    highlight = -1;
  }

  function open() {
    listboxEl().classList.remove("hidden");
    inputEl().setAttribute("aria-expanded", "true");
  }

  function selectPlan(plan) {
    const changed = hiddenEl().value !== plan.plan_key;
    hiddenEl().value = plan.plan_key;
    inputEl().value = formatPlanLabel(plan);
    close();
    if (onSelect) onSelect(plan);
    if (onChange && changed) onChange();
  }

  function render(plans) {
    const listbox = listboxEl();
    listbox.innerHTML = "";
    plans.forEach((p, i) => {
      const li = document.createElement("li");
      li.className = "plan-option";
      li.role = "option";
      li.id = `${listboxId}-option-${i}`;
      li.dataset.planKey = p.plan_key;
      li.textContent = formatPlanLabel(p);
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        selectPlan(p);
      });
      listbox.appendChild(li);
    });
  }

  function highlightOption(options) {
    options.forEach((opt, i) => {
      opt.classList.toggle("plan-option--active", i === highlight);
      if (i === highlight) {
        opt.scrollIntoView({ block: "nearest" });
        inputEl().setAttribute("aria-activedescendant", opt.id);
      }
    });
  }

  function refreshLabel() {
    const selected = hiddenEl().value;
    if (!selected) return;
    const plan = getPlans().find((p) => p.plan_key === selected);
    if (plan) {
      inputEl().value = formatPlanLabel(plan);
    } else {
      clear();
    }
  }

  function init() {
    inputEl().addEventListener("focus", () => {
      if (inputEl().disabled) return;
      render(localFilterPlans(inputEl().value));
      open();
    });
    inputEl().addEventListener("input", () => {
      const hadValue = Boolean(hiddenEl().value);
      hiddenEl().value = "";
      render(localFilterPlans(inputEl().value));
      highlight = -1;
      open();
      if (hadValue && onChange) onChange();
    });
    inputEl().addEventListener("blur", () => {
      setTimeout(() => {
        close();
        refreshLabel();
      }, 150);
    });
    inputEl().addEventListener("keydown", (e) => {
      const options = [...listboxEl().querySelectorAll(".plan-option")];
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (listboxEl().classList.contains("hidden")) {
          render(localFilterPlans(inputEl().value));
          open();
        }
        const visibleOptions = [...listboxEl().querySelectorAll(".plan-option")];
        if (!visibleOptions.length) return;
        highlight = Math.min(highlight + 1, visibleOptions.length - 1);
        highlightOption(visibleOptions);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        const visibleOptions = [...listboxEl().querySelectorAll(".plan-option")];
        if (!visibleOptions.length) return;
        highlight = Math.max(highlight - 1, 0);
        highlightOption(visibleOptions);
      } else if (e.key === "Enter" && highlight >= 0) {
        e.preventDefault();
        const opt = options[highlight];
        if (opt) {
          const plan = getPlans().find((p) => p.plan_key === opt.dataset.planKey);
          if (plan) selectPlan(plan);
        }
      } else if (e.key === "Escape") {
        close();
      }
    });
  }

  const instance = {
    init,
    render,
    refreshLabel,
    clear,
    selectPlan,
    setDisabled,
    onPlansRescoped,
    getValue: () => hiddenEl().value,
  };
  planComboboxInstances.push(instance);
  return instance;
}

function unregisterPlanCombobox(instance) {
  planComboboxInstances = planComboboxInstances.filter((inst) => inst !== instance);
}

// Guided form: a single shared State (required) scopes every plan combobox in all
// three submodes to `allPlans.filter(p => p.state === guidedState)` — no separate
// /api/plans?state= round trip needed since allPlans is already loaded in full.
let guidedState = "";
let guidedEstimateInFlight = false;

function guidedScopedPlans() {
  return guidedState ? allPlans.filter((p) => p.state === guidedState) : [];
}

function guidedPlanComboboxInstances() {
  return [primaryPlanCombobox, mdPlanCombobox, ...comparePlanRows.map((r) => r.combobox)];
}

function onGuidedStateChanged(state) {
  guidedState = state || "";
  guidedPlanComboboxInstances().forEach((inst) => {
    inst.setDisabled(
      !guidedState,
      guidedState ? "Type or scroll to select a plan" : "Select a state above first"
    );
    inst.onPlansRescoped();
  });
  refreshSingleDrugPickers();
  refreshMultiDrugPickers();
  refreshCompareDrugPickers();
  updateGuidedSubmitButtonState();
}

function refreshSingleDrugPickers() {
  const planId = el("filter-plan").value;
  singleDrugPicker.setDrugDisabled(
    !planId,
    planId ? "Click to select a drug" : "Select a plan first"
  );
  if (planId) void singleDrugPicker.refreshForPlanChange?.();
}

function refreshCompareDrugPickers() {
  const planIds = comparePlanRows.map(({ combobox }) => combobox.getValue()).filter(Boolean);
  const enabled = planIds.length > 0;
  compareDrugPicker.setDrugDisabled(
    !enabled,
    enabled ? "Click to select a drug" : "Select plans first"
  );
  if (enabled) void compareDrugPicker.refreshForPlanChange?.();
}

function refreshMultiDrugPickers() {
  const planId = el("md-plan").value;
  const drugPlaceholder = planId ? "Click to select a drug" : "Select a plan first";
  drugRows.forEach(({ picker }) => {
    picker.setDrugDisabled(!planId, drugPlaceholder);
    if (planId) void picker.refreshForPlanChange?.();
  });
}

function isGuidedSingleValid() {
  return Boolean(
    guidedState &&
      el("filter-drug").value.trim() &&
      el("filter-dosage").value.trim() &&
      el("filter-plan").value
  );
}

function isGuidedMultiDrugValid() {
  if (!guidedState || !el("md-plan").value) return false;
  const items = getDrugRowValues();
  if (!items.length) return false;
  return items.every((item) => item.dosage);
}

function isGuidedComparePlansValid() {
  return Boolean(
    guidedState &&
      el("cp-drug").value.trim() &&
      el("cp-dosage").value.trim() &&
      getComparePlanValues().length >= 2
  );
}

function updateGuidedSubmitButtonState() {
  const lock = guidedEstimateInFlight;
  el("guided-submit").disabled = lock || !isGuidedSingleValid();
  el("multidrug-submit").disabled = lock || !isGuidedMultiDrugValid();
  el("compareplans-submit").disabled = lock || !isGuidedComparePlansValid();
}

const primaryPlanCombobox = createPlanCombobox({
  inputId: "filter-plan-input",
  hiddenId: "filter-plan",
  listboxId: "filter-plan-listbox",
  getPlans: guidedScopedPlans,
  onSelect: () => refreshSingleDrugPickers(),
  onChange: () => {
    refreshSingleDrugPickers();
    updateGuidedSubmitButtonState();
  },
});

const mdPlanCombobox = createPlanCombobox({
  inputId: "md-plan-input",
  hiddenId: "md-plan",
  listboxId: "md-plan-listbox",
  getPlans: guidedScopedPlans,
  onSelect: () => refreshMultiDrugPickers(),
  onChange: () => {
    refreshMultiDrugPickers();
    updateGuidedSubmitButtonState();
  },
});

function clearPlanSelection() {
  primaryPlanCombobox.clear();
}

function selectPlan(plan) {
  primaryPlanCombobox.selectPlan(plan);
}

function populatePlanSelect(plans) {
  allPlans = sortPlans(plans);
  planComboboxInstances.forEach((inst) => inst.refreshLabel());
}

function initPlanCombobox() {
  primaryPlanCombobox.init();
  mdPlanCombobox.init();
  onGuidedStateChanged("");
}

async function loadPlans(contractYear = null) {
  const year = contractYear ?? currentDataRelease?.contract_year ?? null;
  const url = year ? `${API}/api/plans?year=${year}` : `${API}/api/plans`;
  const res = await fetch(url);
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

async function loadDataRelease() {
  const label = el("data-release-label");
  try {
    const res = await fetch(`${API}/api/data-release`);
    if (!res.ok) throw new Error(`data-release API ${res.status}`);
    const body = await res.json();
    currentDataRelease = body.release && typeof body.release === "object" ? body.release : null;
  } catch (e) {
    console.warn("Could not load data release", e);
    currentDataRelease = null;
  }

  if (!currentDataRelease) {
    label.textContent = "No data loaded";
    return;
  }
  label.textContent = currentDataRelease.label || currentDataRelease.id || "—";
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

// ── Location picker (State required, zip optional prefill) ──
// State is the only real filter — zip is a static USPS ZIP3->state lookup used purely to
// prefill/suggest State. Neither is ever sent to /api/estimate*, /api/estimate-batch, or
// /api/compare-plans, and neither affects any cost figure.

let availableStates = [];

async function loadStates() {
  try {
    const res = await fetch(`${API}/api/states`);
    if (!res.ok) throw new Error(`states API ${res.status}`);
    const data = await res.json();
    availableStates = Array.isArray(data.states) ? data.states : [];
  } catch (e) {
    console.warn("Could not load states", e);
    availableStates = [];
  }
  return availableStates;
}

async function lookupZipState(zip) {
  const res = await fetch(`${API}/api/zip-lookup?zip=${encodeURIComponent(zip)}`);
  if (!res.ok) throw new Error(`zip-lookup API ${res.status}`);
  const data = await res.json();
  return data.state || null;
}

function createStateCombobox({ inputId, hiddenId, listboxId, onSelect }) {
  let highlight = -1;
  const inputEl = () => el(inputId);
  const hiddenEl = () => el(hiddenId);
  const listboxEl = () => el(listboxId);

  function matchingStates(query) {
    const q = query.trim().toUpperCase();
    if (!q) return availableStates;
    return availableStates.filter((s) => s.includes(q));
  }

  function close() {
    listboxEl().classList.add("hidden");
    inputEl().setAttribute("aria-expanded", "false");
    inputEl().removeAttribute("aria-activedescendant");
    highlight = -1;
  }

  function open() {
    listboxEl().classList.remove("hidden");
    inputEl().setAttribute("aria-expanded", "true");
  }

  function clear() {
    hiddenEl().value = "";
    inputEl().value = "";
  }

  function selectState(state, { silent = false } = {}) {
    const changed = hiddenEl().value !== state;
    hiddenEl().value = state;
    inputEl().value = state;
    close();
    if (!silent && changed && onSelect) onSelect(state);
  }

  function render(states) {
    const listbox = listboxEl();
    listbox.innerHTML = "";
    states.forEach((s, i) => {
      const li = document.createElement("li");
      li.className = "plan-option";
      li.role = "option";
      li.id = `${listboxId}-option-${i}`;
      li.dataset.state = s;
      li.textContent = s;
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        selectState(s);
      });
      listbox.appendChild(li);
    });
  }

  function highlightOption(options) {
    options.forEach((opt, i) => {
      opt.classList.toggle("plan-option--active", i === highlight);
      if (i === highlight) {
        opt.scrollIntoView({ block: "nearest" });
        inputEl().setAttribute("aria-activedescendant", opt.id);
      }
    });
  }

  function init() {
    inputEl().addEventListener("focus", () => {
      render(matchingStates(inputEl().value));
      open();
    });
    inputEl().addEventListener("input", () => {
      hiddenEl().value = "";
      render(matchingStates(inputEl().value));
      highlight = -1;
      open();
    });
    inputEl().addEventListener("blur", () => {
      setTimeout(() => {
        close();
        const typed = inputEl().value.trim().toUpperCase();
        const match = availableStates.find((s) => s === typed);
        if (match) {
          selectState(match);
        } else if (hiddenEl().value) {
          inputEl().value = hiddenEl().value;
        } else {
          clear();
        }
      }, 150);
    });
    inputEl().addEventListener("keydown", (e) => {
      const options = [...listboxEl().querySelectorAll(".plan-option")];
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (listboxEl().classList.contains("hidden")) {
          render(matchingStates(inputEl().value));
          open();
        }
        const visible = [...listboxEl().querySelectorAll(".plan-option")];
        if (!visible.length) return;
        highlight = Math.min(highlight + 1, visible.length - 1);
        highlightOption(visible);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        const visible = [...listboxEl().querySelectorAll(".plan-option")];
        if (!visible.length) return;
        highlight = Math.max(highlight - 1, 0);
        highlightOption(visible);
      } else if (e.key === "Enter" && highlight >= 0) {
        e.preventDefault();
        const opt = options[highlight];
        if (opt) selectState(opt.dataset.state);
      } else if (e.key === "Escape") {
        close();
      }
    });
  }

  return { init, selectState, clear, getValue: () => hiddenEl().value };
}

// Wires an optional zip field to a state combobox: prefills an empty state, or shows a
// confirm-before-switch caution banner when zip and an already-picked state disagree
// (never silently overwrites, never silently ignores — the user always decides).
function wireZipPicker({ zipInputId, cautionId, stateCombobox, getCurrentState }) {
  const zipInputEl = () => el(zipInputId);
  const cautionEl = () => el(cautionId);
  let debounceTimer = null;

  function hideCaution() {
    cautionEl().classList.add("hidden");
    cautionEl().innerHTML = "";
    delete cautionEl().dataset.zipState;
  }

  function showMismatch(zipState, currentState) {
    cautionEl().innerHTML =
      `This zip looks like it's in ${zipState}, but ${currentState} is selected. ` +
      `<button type="button" class="link-btn" data-action="use-zip-state">Use ${zipState}</button> · ` +
      `<button type="button" class="link-btn" data-action="keep-state">Keep ${currentState}</button>`;
    cautionEl().dataset.zipState = zipState;
    cautionEl().classList.remove("hidden");
  }

  function showUnrecognized() {
    cautionEl().textContent = "Couldn't recognize that zip code.";
    cautionEl().classList.remove("hidden");
  }

  cautionEl().addEventListener("click", (e) => {
    const action = e.target.dataset.action;
    if (!action) return;
    if (action === "use-zip-state" && cautionEl().dataset.zipState) {
      stateCombobox.selectState(cautionEl().dataset.zipState);
    }
    hideCaution();
  });

  async function handleZip(zip) {
    let zipState = null;
    try {
      zipState = await lookupZipState(zip);
    } catch (e) {
      console.warn("zip lookup failed", e);
      return;
    }
    if (!zipState) {
      showUnrecognized();
      return;
    }
    const current = getCurrentState();
    if (!current) {
      stateCombobox.selectState(zipState);
      hideCaution();
    } else if (current !== zipState) {
      showMismatch(zipState, current);
    } else {
      hideCaution();
    }
  }

  zipInputEl().addEventListener("input", () => {
    hideCaution();
    clearTimeout(debounceTimer);
    const raw = zipInputEl().value.trim();
    if (raw.length !== 5) return;
    debounceTimer = setTimeout(() => handleZip(raw), 400);
  });
  zipInputEl().addEventListener("blur", () => {
    const raw = zipInputEl().value.trim();
    if (raw.length === 5) handleZip(raw);
  });
}

const guidedStateCombobox = createStateCombobox({
  inputId: "guided-state-input",
  hiddenId: "guided-state",
  listboxId: "guided-state-listbox",
  onSelect: onGuidedStateChanged,
});

// Chat mode: a lightweight, optional plan-picker widget. A picked plan only ever
// populates filters.plan_id on the /api/chat request (see getFilters()) — the user can
// still type/override plan info in the message text.
let chatState = "";

function chatScopedPlans() {
  return chatState ? allPlans.filter((p) => p.state === chatState) : [];
}

function onChatStateChanged(state) {
  chatState = state || "";
  chatPlanCombobox.setDisabled(
    !chatState,
    chatState ? "Type or scroll to select a plan (optional)" : "Select a state above first"
  );
  chatPlanCombobox.onPlansRescoped();
}

const chatStateCombobox = createStateCombobox({
  inputId: "chat-state-input",
  hiddenId: "chat-state",
  listboxId: "chat-state-listbox",
  onSelect: onChatStateChanged,
});

const chatPlanCombobox = createPlanCombobox({
  inputId: "chat-plan-input",
  hiddenId: "chat-plan",
  listboxId: "chat-plan-listbox",
  getPlans: chatScopedPlans,
});

function initLocationPickers() {
  guidedStateCombobox.init();
  chatStateCombobox.init();
  chatPlanCombobox.init();
  chatPlanCombobox.setDisabled(true, "Select a state above first");
  wireZipPicker({
    zipInputId: "guided-zip-input",
    cautionId: "guided-zip-caution",
    stateCombobox: guidedStateCombobox,
    getCurrentState: () => guidedState,
  });
  wireZipPicker({
    zipInputId: "chat-zip-input",
    cautionId: "chat-zip-caution",
    stateCombobox: chatStateCombobox,
    getCurrentState: () => chatState,
  });
}

// ── Drug + dosage pickers (select-only; list opens on click only) ──

const optionComboboxInstances = [];
const drugDosagePickerInstances = [];

async function fetchDrugs(query = "", planId = "") {
  try {
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (planId) params.set("plan_id", planId);
    const qs = params.toString();
    const res = await fetch(`${API}/api/drugs${qs ? `?${qs}` : ""}`);
    if (!res.ok) throw new Error(`drugs API ${res.status}`);
    const data = await res.json();
    const drugs = Array.isArray(data.drugs) ? data.drugs : [];
    if (!planId) return drugs;
    return drugs.map((item) => {
      if (typeof item === "string") {
        return { name: item, value: item, label: item };
      }
      return {
        name: item.name,
        value: item.name,
        label: item.name,
      };
    });
  } catch (e) {
    console.warn("Could not load drugs", e);
    return [];
  }
}

async function fetchDrugDosages(drug, planId = "") {
  if (!drug) return [];
  try {
    const params = new URLSearchParams({ drug });
    if (planId) params.set("plan_id", planId);
    const res = await fetch(`${API}/api/drug-dosages?${params.toString()}`);
    if (!res.ok) throw new Error(`drug-dosages API ${res.status}`);
    const data = await res.json();
    const dosages = Array.isArray(data.dosages) ? data.dosages : [];
    if (!planId) return dosages;
    return dosages.map((item) => {
      if (typeof item === "string") {
        return { dosage: item, value: item, label: item };
      }
      return {
        dosage: item.dosage,
        value: item.dosage,
        label: item.dosage,
      };
    });
  } catch (e) {
    console.warn("Could not load dosages", e);
    return [];
  }
}

function closeAllDrugPickers() {
  optionComboboxInstances.forEach((inst) => inst.close());
}

function normalizeComboboxOption(opt) {
  if (typeof opt === "string") {
    return { value: opt, label: opt, meta: null, metaClass: null };
  }
  const value = opt.value ?? opt.name ?? opt.dosage ?? "";
  const label = opt.label ?? opt.name ?? opt.dosage ?? value;
  const meta = opt.meta ?? null;
  const metaClass = opt.metaClass ?? null;
  return { value, label, meta, metaClass };
}

function comboboxOptionValue(opt) {
  return normalizeComboboxOption(opt).value;
}

function createOptionCombobox({
  inputId,
  hiddenId,
  listboxId,
  panelId = null,
  filterInputId = null,
  getOptions,
  onSelect,
  onChange,
  onSearch,
  onOpen,
  ariaLabel,
  selectionOnly = false,
  openOn = "focus",
}) {
  let highlight = -1;
  let searchTimer = null;
  let searchToken = 0;
  const inputEl = () => el(inputId);
  const hiddenEl = () => el(hiddenId);
  const listboxEl = () => el(listboxId);
  const panelEl = () => (panelId ? el(panelId) : null);
  const filterEl = () => (filterInputId ? el(filterInputId) : null);

  function matchingOptions(query) {
    const q = query.trim().toLowerCase();
    const options = getOptions();
    if (!q) return options;
    return options.filter((opt) => {
      const normalized = normalizeComboboxOption(opt);
      return (
        normalized.label.toLowerCase().includes(q) ||
        normalized.value.toLowerCase().includes(q)
      );
    });
  }

  function isDropdownFocusWithin() {
    const active = document.activeElement;
    if (!active) return false;
    if (active === inputEl() || active === filterEl()) return true;
    if (panelEl()?.contains(active)) return true;
    if (listboxEl().contains(active)) return true;
    return false;
  }

  async function refreshOptions(query) {
    let options;
    if (onSearch) {
      const token = ++searchToken;
      options = await onSearch(query);
      if (token !== searchToken) return options;
    } else {
      options = matchingOptions(query);
    }
    render(options);
    open();
    return options;
  }

  function close() {
    if (panelEl()) {
      panelEl().classList.add("hidden");
    } else {
      listboxEl().classList.add("hidden");
    }
    inputEl().setAttribute("aria-expanded", "false");
    inputEl().removeAttribute("aria-activedescendant");
    highlight = -1;
  }

  function open() {
    if (panelEl()) {
      panelEl().classList.remove("hidden");
    } else {
      listboxEl().classList.remove("hidden");
    }
    inputEl().setAttribute("aria-expanded", "true");
  }

  function clear() {
    const hadValue = Boolean(hiddenEl().value || inputEl().value);
    hiddenEl().value = "";
    inputEl().value = "";
    if (filterEl()) filterEl().value = "";
    if (hadValue && onChange) onChange();
  }

  function setDisabled(disabled, placeholder) {
    inputEl().disabled = disabled;
    if (disabled) {
      clear();
      close();
    }
    if (placeholder) inputEl().placeholder = placeholder;
  }

  function selectOption(value, { silent = false } = {}) {
    const normalized = normalizeComboboxOption(value);
    const changed = hiddenEl().value !== normalized.value;
    hiddenEl().value = normalized.value;
    inputEl().value = normalized.label;
    if (filterEl()) filterEl().value = "";
    close();
    if (!silent && changed && onSelect) onSelect(normalized.value);
    if (onChange && changed) onChange();
  }

  function render(options) {
    const listbox = listboxEl();
    listbox.innerHTML = "";
    options.forEach((opt, i) => {
      const normalized = normalizeComboboxOption(opt);
      const li = document.createElement("li");
      li.className = "plan-option";
      if (normalized.metaClass) li.classList.add(normalized.metaClass);
      li.role = "option";
      li.id = `${listboxId}-option-${i}`;
      li.dataset.value = normalized.value;
      const labelSpan = document.createElement("span");
      labelSpan.className = "picker-option-label";
      labelSpan.textContent = normalized.label;
      li.appendChild(labelSpan);
      if (normalized.meta) {
        const metaSpan = document.createElement("span");
        metaSpan.className = `picker-meta ${normalized.metaClass || ""}`.trim();
        metaSpan.textContent = normalized.meta;
        li.appendChild(metaSpan);
      }
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        selectOption(opt);
      });
      listbox.appendChild(li);
    });
  }

  function highlightOption(options) {
    options.forEach((opt, i) => {
      opt.classList.toggle("plan-option--active", i === highlight);
      if (i === highlight) {
        opt.scrollIntoView({ block: "nearest" });
        inputEl().setAttribute("aria-activedescendant", opt.id);
      }
    });
  }

  async function openDropdown(query = "") {
    if (inputEl().disabled) return;
    if (onOpen) onOpen();
    await refreshOptions(query);
    if (filterEl()) {
      filterEl().focus();
      filterEl().select();
    }
  }

  function init() {
    if (selectionOnly) {
      inputEl().readOnly = true;
    }

    if (openOn === "click") {
      inputEl().addEventListener("click", (e) => {
        if (inputEl().disabled) return;
        e.preventDefault();
        void openDropdown(filterEl()?.value || "");
      });
    } else {
      inputEl().addEventListener("focus", () => {
        if (inputEl().disabled) return;
        void openDropdown(inputEl().value);
      });
      inputEl().addEventListener("input", () => {
        const hadValue = Boolean(hiddenEl().value);
        hiddenEl().value = "";
        highlight = -1;
        clearTimeout(searchTimer);
        const query = inputEl().value;
        if (onSearch) {
          const delay = query.trim() ? 250 : 0;
          searchTimer = setTimeout(() => void refreshOptions(query), delay);
        } else {
          render(matchingOptions(query));
          open();
        }
        if (hadValue && onChange) onChange();
      });
    }

    if (selectionOnly) {
      inputEl().addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === "Escape") {
          return;
        }
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
          e.preventDefault();
        }
      });
    }

    if (filterEl()) {
      filterEl().addEventListener("input", () => {
        highlight = -1;
        clearTimeout(searchTimer);
        const query = filterEl().value;
        const delay = query.trim() ? 250 : 0;
        searchTimer = setTimeout(() => void refreshOptions(query), delay);
      });
      filterEl().addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          close();
          inputEl().focus();
        } else if (e.key === "ArrowDown") {
          e.preventDefault();
          const visible = [...listboxEl().querySelectorAll(".plan-option")];
          if (!visible.length) return;
          highlight = 0;
          highlightOption(visible);
        }
      });
    }

    if (panelEl()) {
      panelEl().addEventListener("mousedown", (e) => {
        e.preventDefault();
      });
    }

    inputEl().addEventListener("blur", () => {
      setTimeout(() => {
        if (isDropdownFocusWithin()) return;
        close();
        if (hiddenEl().value) {
          inputEl().value = hiddenEl().value;
        } else {
          clear();
        }
      }, 150);
    });

    if (filterEl()) {
      filterEl().addEventListener("blur", () => {
        setTimeout(() => {
          if (isDropdownFocusWithin()) return;
          close();
          if (hiddenEl().value) {
            inputEl().value = hiddenEl().value;
          } else {
            clear();
          }
        }, 150);
      });
    }

    inputEl().addEventListener("keydown", (e) => {
      const options = [...listboxEl().querySelectorAll(".plan-option")];
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (panelEl()?.classList.contains("hidden") && listboxEl().classList.contains("hidden")) {
          void openDropdown(filterEl()?.value || "");
        }
        const visible = [...listboxEl().querySelectorAll(".plan-option")];
        if (!visible.length) return;
        highlight = Math.min(highlight + 1, visible.length - 1);
        highlightOption(visible);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        const visible = [...listboxEl().querySelectorAll(".plan-option")];
        if (!visible.length) return;
        highlight = Math.max(highlight - 1, 0);
        highlightOption(visible);
      } else if (e.key === "Enter" && highlight >= 0) {
        e.preventDefault();
        const opt = options[highlight];
        if (opt) selectOption(opt.dataset.value);
      } else if (e.key === "Escape") {
        close();
      }
    });

    if (ariaLabel) listboxEl().setAttribute("aria-label", ariaLabel);
  }

  const instance = {
    init,
    selectOption,
    clear,
    setDisabled,
    render,
    close,
    openDropdown,
    getValue: () => hiddenEl().value,
  };
  optionComboboxInstances.push(instance);
  return instance;
}

function createDrugDosagePicker({
  drugInputId,
  drugHiddenId,
  drugListboxId,
  drugPanelId,
  drugFilterInputId,
  dosageInputId,
  dosageHiddenId,
  dosageListboxId,
  getPlanId = () => "",
  onChange,
}) {
  let pickerDrugs = [];
  let availableDosages = [];

  const dosageCombobox = createOptionCombobox({
    inputId: dosageInputId,
    hiddenId: dosageHiddenId,
    listboxId: dosageListboxId,
    getOptions: () => availableDosages,
    onChange,
    ariaLabel: "Dosages",
    selectionOnly: true,
    openOn: "click",
  });

  async function loadDosagesForDrug(drug) {
    const hasDrug = Boolean(drug);
    availableDosages = hasDrug ? await fetchDrugDosages(drug, getPlanId()) : [];
    const current = dosageCombobox.getValue();
    if (
      current &&
      !availableDosages.some((d) => comboboxOptionValue(d).toLowerCase() === current.toLowerCase())
    ) {
      dosageCombobox.clear();
    }
    dosageCombobox.setDisabled(
      !hasDrug,
      hasDrug ? "Click to select a dosage" : "Select a drug first"
    );
  }

  async function searchPickerDrugs(query) {
    pickerDrugs = await fetchDrugs(query, getPlanId());
    return pickerDrugs;
  }

  const drugCombobox = createOptionCombobox({
    inputId: drugInputId,
    hiddenId: drugHiddenId,
    listboxId: drugListboxId,
    panelId: drugPanelId,
    filterInputId: drugFilterInputId,
    getOptions: () => pickerDrugs,
    onSearch: searchPickerDrugs,
    onSelect: (drug) => {
      dosageCombobox.clear();
      void loadDosagesForDrug(drug);
    },
    onChange,
    ariaLabel: "Drugs",
    selectionOnly: true,
    openOn: "click",
  });

  function clear() {
    pickerDrugs = [];
    availableDosages = [];
    drugCombobox.clear();
    dosageCombobox.clear();
    dosageCombobox.setDisabled(true, "Select a drug first");
    if (onChange) onChange();
  }

  async function selectDrug(drug, dosage, { silent = false } = {}) {
    if (!drug) {
      clear();
      return;
    }
    drugCombobox.selectOption(drug, { silent: true });
    await loadDosagesForDrug(drug);
    if (dosage) {
      const match = availableDosages.find(
        (d) => comboboxOptionValue(d).toLowerCase() === String(dosage).toLowerCase()
      );
      if (match) dosageCombobox.selectOption(match, { silent });
    }
  }

  async function refreshForPlanChange() {
    const drug = drugCombobox.getValue();
    if (drug) {
      await loadDosagesForDrug(drug);
    }
    const panel = drugPanelId ? el(drugPanelId) : null;
    const listbox = el(drugListboxId);
    const isOpen = panel ? !panel.classList.contains("hidden") : !listbox.classList.contains("hidden");
    if (isOpen) {
      const query = drugFilterInputId ? el(drugFilterInputId).value : "";
      pickerDrugs = await fetchDrugs(query, getPlanId());
      drugCombobox.render(pickerDrugs);
    }
  }

  function setDrugDisabled(disabled, placeholder) {
    drugCombobox.setDisabled(disabled, placeholder);
  }

  function init() {
    drugCombobox.init();
    dosageCombobox.init();
    dosageCombobox.setDisabled(true, "Select a drug first");
  }

  const picker = {
    init,
    clear,
    selectDrug,
    refreshForPlanChange,
    setDrugDisabled,
    close: () => {
      drugCombobox.close();
      dosageCombobox.close();
    },
    getDrug: () => drugCombobox.getValue(),
    getDosage: () => dosageCombobox.getValue(),
  };
  drugDosagePickerInstances.push(picker);
  return picker;
}

const singleDrugPicker = createDrugDosagePicker({
  drugInputId: "filter-drug-input",
  drugHiddenId: "filter-drug",
  drugListboxId: "filter-drug-listbox",
  drugPanelId: "filter-drug-panel",
  drugFilterInputId: "filter-drug-filter",
  dosageInputId: "filter-dosage-input",
  dosageHiddenId: "filter-dosage",
  dosageListboxId: "filter-dosage-listbox",
  getPlanId: () => el("filter-plan").value,
  onChange: updateGuidedSubmitButtonState,
});

const compareDrugPicker = createDrugDosagePicker({
  drugInputId: "cp-drug-input",
  drugHiddenId: "cp-drug",
  drugListboxId: "cp-drug-listbox",
  drugPanelId: "cp-drug-panel",
  drugFilterInputId: "cp-drug-filter",
  dosageInputId: "cp-dosage-input",
  dosageHiddenId: "cp-dosage",
  dosageListboxId: "cp-dosage-listbox",
  getPlanId: () =>
    comparePlanRows.map(({ combobox }) => combobox.getValue()).filter(Boolean)[0] || "",
  onChange: updateGuidedSubmitButtonState,
});

function initDrugPickers() {
  singleDrugPicker.init();
  compareDrugPicker.init();
  refreshSingleDrugPickers();
  refreshCompareDrugPickers();
}

// ── Guided form: multi-drug basket rows (plain drug/dosage text pairs, cap 5) ──

const MAX_BATCH_DRUGS = 5;
const MAX_COMPARE_PLANS = 4;
const MAX_BATCH_DRUGS_LIMIT_MSG = `You can add up to ${MAX_BATCH_DRUGS} drugs per estimate.`;
const MAX_COMPARE_PLANS_LIMIT_MSG = `You can compare up to ${MAX_COMPARE_PLANS} plans at a time.`;

let drugRowCount = 0;
let drugRows = [];

function createDrugRowElement() {
  drugRowCount += 1;
  const idx = drugRowCount;
  const row = document.createElement("div");
  row.className = "repeatable-row";
  row.dataset.rowId = String(idx);
  row.innerHTML = `
    <div class="plan-combobox">
      <input
        type="text"
        id="md-drug-input-${idx}"
        class="plan-combobox-input"
        placeholder="Click to select a drug"
        autocomplete="off"
        role="combobox"
        aria-expanded="false"
        aria-controls="md-drug-listbox-${idx}"
        aria-autocomplete="list"
        readonly
      />
      <input type="hidden" id="md-drug-${idx}" value="" />
      <div id="md-drug-panel-${idx}" class="plan-dropdown-panel hidden" role="presentation">
        <input
          type="text"
          id="md-drug-filter-${idx}"
          class="combobox-filter"
          placeholder="Search drugs…"
          autocomplete="off"
          aria-label="Search drugs"
        />
        <ul id="md-drug-listbox-${idx}" class="plan-listbox plan-listbox--in-panel" role="listbox" aria-label="Drugs"></ul>
      </div>
    </div>
    <div class="plan-combobox">
      <input
        type="text"
        id="md-dosage-input-${idx}"
        class="plan-combobox-input"
        placeholder="Select a drug first"
        autocomplete="off"
        role="combobox"
        aria-expanded="false"
        aria-controls="md-dosage-listbox-${idx}"
        aria-autocomplete="list"
        readonly
        disabled
      />
      <input type="hidden" id="md-dosage-${idx}" value="" />
      <ul id="md-dosage-listbox-${idx}" class="plan-listbox hidden" role="listbox" aria-label="Dosages"></ul>
    </div>
    <button type="button" class="repeatable-row-remove" aria-label="Remove drug" title="Remove drug">&times;</button>
  `;
  el("multidrug-rows").appendChild(row);
  const picker = createDrugDosagePicker({
    drugInputId: `md-drug-input-${idx}`,
    drugHiddenId: `md-drug-${idx}`,
    drugListboxId: `md-drug-listbox-${idx}`,
    drugPanelId: `md-drug-panel-${idx}`,
    drugFilterInputId: `md-drug-filter-${idx}`,
    dosageInputId: `md-dosage-input-${idx}`,
    dosageHiddenId: `md-dosage-${idx}`,
    dosageListboxId: `md-dosage-listbox-${idx}`,
    getPlanId: () => el("md-plan").value,
    onChange: updateGuidedSubmitButtonState,
  });
  picker.init();
  const planId = el("md-plan").value;
  picker.setDrugDisabled(!planId, planId ? "Click to select a drug" : "Select a plan first");
  const entry = { row, picker };
  row.querySelector(".repeatable-row-remove").addEventListener("click", () => removeDrugRow(entry));
  return entry;
}

function updateDrugRowControls() {
  el("multidrug-add-row").disabled = drugRows.length >= MAX_BATCH_DRUGS;
  drugRows.forEach(({ row }) => {
    row.querySelector(".repeatable-row-remove").disabled = drugRows.length <= 1;
  });
}

function addDrugRow() {
  if (drugRows.length >= MAX_BATCH_DRUGS) return;
  drugRows.push(createDrugRowElement());
  updateDrugRowControls();
  updateGuidedSubmitButtonState();
}

function removeDrugRow(entry) {
  if (drugRows.length <= 1) return;
  drugRows = drugRows.filter((r) => r !== entry);
  entry.row.remove();
  updateDrugRowControls();
  updateGuidedSubmitButtonState();
  showGuidedError("");
}

function resetDrugRows() {
  el("multidrug-rows").innerHTML = "";
  drugRows = [];
  addDrugRow();
}

function getDrugRowValues() {
  return drugRows
    .map(({ picker }) => {
      const drug = picker.getDrug();
      const dosage = picker.getDosage();
      return drug ? { drug, dosage: dosage || undefined } : null;
    })
    .filter(Boolean);
}

// ── Guided form: compare-plans rows (repeatable plan combobox, cap 4) ──

let comparePlanRowCount = 0;
let comparePlanRows = [];

function createComparePlanRowEntry() {
  comparePlanRowCount += 1;
  const idx = comparePlanRowCount;
  const row = document.createElement("div");
  row.className = "repeatable-row";
  row.dataset.rowId = String(idx);
  row.innerHTML = `
    <div class="plan-combobox">
      <input
        type="text"
        id="cp-plan-input-${idx}"
        class="plan-combobox-input"
        placeholder="Type or scroll to select a plan"
        autocomplete="off"
        role="combobox"
        aria-expanded="false"
        aria-controls="cp-plan-listbox-${idx}"
        aria-autocomplete="list"
      />
      <input type="hidden" id="cp-plan-${idx}" value="" />
      <ul id="cp-plan-listbox-${idx}" class="plan-listbox hidden" role="listbox" aria-label="Medicare plans"></ul>
    </div>
    <button type="button" class="repeatable-row-remove" aria-label="Remove plan" title="Remove plan">&times;</button>
  `;
  el("compareplans-rows").appendChild(row);
  const combobox = createPlanCombobox({
    inputId: `cp-plan-input-${idx}`,
    hiddenId: `cp-plan-${idx}`,
    listboxId: `cp-plan-listbox-${idx}`,
    getPlans: guidedScopedPlans,
    onChange: () => {
      refreshCompareDrugPickers();
      updateGuidedSubmitButtonState();
    },
  });
  combobox.init();
  combobox.setDisabled(!guidedState, guidedState ? "Type or scroll to select a plan" : "Select a state above first");
  row.querySelector(".repeatable-row-remove").addEventListener("click", () => {
    removeComparePlanRow(entry);
  });
  const entry = { row, combobox };
  return entry;
}

function updateComparePlanRowControls() {
  el("compareplans-add-row").disabled = comparePlanRows.length >= MAX_COMPARE_PLANS;
  comparePlanRows.forEach(({ row }) => {
    row.querySelector(".repeatable-row-remove").disabled = comparePlanRows.length <= 2;
  });
}

function addComparePlanRow() {
  if (comparePlanRows.length >= MAX_COMPARE_PLANS) return;
  comparePlanRows.push(createComparePlanRowEntry());
  updateComparePlanRowControls();
  updateGuidedSubmitButtonState();
}

function removeComparePlanRow(entry) {
  if (comparePlanRows.length <= 2) return;
  comparePlanRows = comparePlanRows.filter((r) => r !== entry);
  unregisterPlanCombobox(entry.combobox);
  entry.row.remove();
  updateComparePlanRowControls();
  updateGuidedSubmitButtonState();
  showGuidedError("");
}

function resetComparePlanRows() {
  el("compareplans-rows").innerHTML = "";
  comparePlanRows.forEach(({ combobox }) => unregisterPlanCombobox(combobox));
  comparePlanRows = [];
  addComparePlanRow();
  addComparePlanRow();
}

function getComparePlanValues() {
  return comparePlanRows.map(({ combobox }) => combobox.getValue()).filter(Boolean);
}

// ── Guided form: sub-mode tabs (Single / Multiple drugs / Compare plans) ──

function switchGuidedSubmode(mode) {
  const submitByMode = {
    single: "guided-submit",
    multidrug: "multidrug-submit",
    compareplans: "compareplans-submit",
  };
  closeAllDrugPickers();
  ["single", "multidrug", "compareplans"].forEach((m) => {
    const isActive = m === mode;
    el(`guided-${m}`).classList.toggle("hidden", !isActive);
    el(`guided-mode-${m}`).classList.toggle("active", isActive);
    el(`guided-mode-${m}`).setAttribute("aria-selected", String(isActive));
    const submitBtn = el(submitByMode[m]);
    if (submitBtn) submitBtn.classList.toggle("hidden", !isActive);
  });
  showGuidedError("");
  updateGuidedSubmitButtonState();
}

// ── Render + submit: multi-drug basket and plan comparison ──

// Wraps a single Plan-facts or Drug-facts <section> in a lightweight card shell so it can be
// shown once above a stack of cards that all share that section's data.
function renderSharedSummaryCardHtml(sectionHtml) {
  if (!sectionHtml) return "";
  return `<div class="estimate-card estimate-card--shared estimate-card--compact" role="region" aria-label="Shared details">${sectionHtml}</div>`;
}

function renderBatchEstimateHtml(items, combinedTotal, caveat) {
  const totalText =
    combinedTotal.low != null
      ? formatCostRange(combinedTotal.low, combinedTotal.high) || formatCurrency(combinedTotal.low)
      : "Not available";
  const bannerParts = [`<span class="batch-total">Combined estimate: ${escapeHtml(totalText)}</span>`];
  if (caveat) bannerParts.push(`<span>${escapeHtml(caveat)}</span>`);
  const banner = `<div class="batch-summary-banner">${bannerParts.join("")}</div>`;

  // All items share one plan (Multi-drug form takes a single plan for every drug) — show the
  // Plan section once instead of repeating it in every drug card.
  const sharedPlan = renderSharedSummaryCardHtml(renderPlanFactsHtml(items.find((i) => i.data)?.data));

  const cards = items
    .map((item) => {
      const heading = `<div class="batch-item-heading">${escapeHtml(item.drug)}</div>`;
      if (item.data) {
        return heading + renderMultiChannelEstimateCardHtml(item.data, { compact: true, hidePlan: true });
      }
      return heading + `<p class="card-placeholder">${escapeHtml(item.message || "No estimate available.")}</p>`;
    })
    .join("");

  return banner + sharedPlan + cards;
}

function renderPlanComparisonHtml(items, disclaimer) {
  const banner = `<div class="comparison-disclaimer-banner">${escapeHtml(disclaimer)}</div>`;

  // All items share one drug (Compare-plans form takes a single drug across many plans) — show
  // the Drug section once instead of repeating it in every plan card.
  const sharedDrug = renderSharedSummaryCardHtml(renderDrugFactsHtml(items.find((i) => i.data)?.data));

  const cards = items
    .map((item) => {
      const label = item.data?.plan_name ? `${item.data.plan_name} (${item.plan_id})` : item.plan_id;
      const heading = `<div class="comparison-item-heading">${escapeHtml(label)}</div>`;
      if (item.data) {
        return heading + renderMultiChannelEstimateCardHtml(item.data, { compact: true, hideDrug: true });
      }
      return heading + `<p class="card-placeholder">${escapeHtml(item.message || "No estimate available.")}</p>`;
    })
    .join("");
  return banner + sharedDrug + cards;
}

function renderBatchEstimateResults(body) {
  resultsBatch = body;
  el("data-as-of").classList.add("hidden");
  el("results-content").innerHTML = renderBatchEstimateHtml(
    body.items,
    { low: body.combined_total_low, high: body.combined_total_high },
    body.caveat
  );
}

function renderPlanComparisonResults(body) {
  resultsComparison = body;
  el("data-as-of").classList.add("hidden");
  el("results-content").innerHTML = renderPlanComparisonHtml(body.items, body.disclaimer);
}

async function submitMultiDrugEstimate() {
  showGuidedError("");
  const planId = el("md-plan").value;
  const items = getDrugRowValues();
  if (!planId) {
    showGuidedError("Please select a plan.");
    return;
  }
  if (!items.length) {
    showGuidedError("Please select at least one drug.");
    return;
  }
  const missingDosage = items.find((item) => !item.dosage);
  if (missingDosage) {
    showGuidedError(`Please select a dosage for ${missingDosage.drug}.`);
    return;
  }
  const daysSupply = parseInt(el("md-days-supply").value, 10) || 30;
  const ytdRaw = el("md-ytd").value;
  const ytdNum = parseFloat(ytdRaw);
  const drugList = items
    .map((item) => (item.dosage ? `${item.drug} ${item.dosage}` : item.drug))
    .join(", ");
  const ytd = ytdRaw && !Number.isNaN(ytdNum) && ytdNum >= 0 ? ytdNum : 0;
  const message =
    `Estimate costs for ${drugList} on plan ${planId}. ` +
    `Use a ${daysSupply}-day supply and $${ytd} year-to-date out-of-pocket spending. ` +
    "Summarize each drug and the combined cost.";
  await sendGuidedInitial(message);
}

async function submitComparePlans() {
  showGuidedError("");
  const drug = el("cp-drug").value.trim();
  const dosage = el("cp-dosage").value.trim();
  const planIds = getComparePlanValues();
  if (!drug) {
    showGuidedError("Please select a drug.");
    return;
  }
  if (!dosage) {
    showGuidedError("Please select a dosage.");
    return;
  }
  if (planIds.length < 2) {
    showGuidedError("Please select at least 2 plans to compare.");
    return;
  }
  const daysSupply = parseInt(el("cp-days-supply").value, 10) || 30;
  const ytdRaw = el("cp-ytd").value;
  const ytdNum = parseFloat(ytdRaw);
  const ytd = ytdRaw && !Number.isNaN(ytdNum) && ytdNum >= 0 ? ytdNum : 0;
  const drugLabel = dosage ? `${drug} ${dosage}` : drug;
  const message =
    `Compare the cost of ${drugLabel} across these Medicare plans: ${planIds.join(", ")}. ` +
    `Use a ${daysSupply}-day supply and $${ytd} year-to-date out-of-pocket spending. ` +
    "Summarize the differences and identify the lowest estimated cost.";
  await sendGuidedInitial(message, {
    drug,
    dosage: dosage || undefined,
    days_supply: daysSupply,
    ytd_oop_spend: ytd,
  });
}

function getUserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Chicago";
  } catch {
    return "America/Chicago";
  }
}

function getFilters() {
  const filters = {};
  const drug = el("filter-drug").value.trim();
  const dosage = el("filter-dosage").value.trim();
  // Falls back to the Chat plan-picker widget's selection when the Guided form's own
  // plan field is empty (e.g. sending from the plain Chat tab) — plan_id is the only
  // field the chat picker ever contributes; state/zip themselves are never sent here.
  const plan = el("filter-plan").value || chatPlanCombobox.getValue();
  const daysSupply = el("filter-days-supply").value;
  const ytd = el("filter-ytd").value;
  if (drug) filters.drug = drug;
  if (dosage) filters.dosage = dosage;
  if (plan) filters.plan_id = plan;
  if (currentDataRelease?.contract_year) filters.contract_year = currentDataRelease.contract_year;
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

function renderMultiEstimatesStackHtml(estimates) {
  return estimates
    .map((data) => {
      const heading = `<div class="batch-item-heading">${escapeHtml(data.drug_name || "Drug")}</div>`;
      return heading + renderMultiChannelEstimateCardHtml(data, { compact: true });
    })
    .join("");
}

function renderMultiEstimatePanel(estimates, { citations, toolStatuses, dataAsOf } = {}) {
  const asOf = dataAsOf || {};
  const dates = Object.values(asOf).filter(Boolean);
  const badge = el("data-as-of");
  if (dates.length) {
    badge.textContent = `Data as of ${dates[0]}`;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
  const container = el("results-content");
  container.innerHTML = renderMultiEstimatesStackHtml(estimates) + renderCitationsCard(citations);
  if (toolStatuses && Object.keys(toolStatuses).length) {
    const statuses = Object.entries(toolStatuses)
      .map(([k, v]) => `${k}: ${v}`)
      .join(" · ");
    container.innerHTML += `<p style="font-size:0.75rem;color:var(--muted);margin-top:0.5rem">Tools: ${statuses}</p>`;
  }
}

function renderPanelFromChatResponse(resp, { citations, toolStatuses, dataAsOf } = {}) {
  if (resp.channel_estimates?.length > 1) {
    renderMultiEstimatePanel(resp.channel_estimates, { citations, toolStatuses, dataAsOf });
    return true;
  }
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

function appendMessage(role, text, source, citations, usage, containerId = "chat-messages") {
  const container = el(containerId);
  if (containerId === "chat-messages") {
    const empty = el("empty-state");
    if (empty) empty.remove();
    container.classList.add("is-thread");
    updateChatComposerHint();
  } else {
    el("guided-chat-placeholder")?.remove();
  }
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

  container.appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
    channel_estimates: resp.channel_estimates?.length ? resp.channel_estimates : null,
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
  if (resp.channel_estimates?.length) merged.channel_estimates = resp.channel_estimates;
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
    channel_estimates: baseline.channel_estimates || [],
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
  closeAllDrugPickers();
  el("mode-chat").classList.toggle("hidden", !isChat);
  el("mode-chat").hidden = !isChat;
  el("mode-guided").classList.toggle("hidden", isChat);
  el("mode-guided").hidden = isChat;
  el("mode-tab-chat").classList.toggle("active", isChat);
  el("mode-tab-chat").setAttribute("aria-selected", String(isChat));
  el("mode-tab-chat").tabIndex = isChat ? 0 : -1;
  el("mode-tab-guided").classList.toggle("active", !isChat);
  el("mode-tab-guided").setAttribute("aria-selected", String(!isChat));
  el("mode-tab-guided").tabIndex = isChat ? -1 : 0;
  el("turn-counter").classList.toggle("hidden", !isChat);
  if (isChat) {
    const hasThread = el("chat-messages")?.classList.contains("is-thread");
    if (!hasThread) {
      window.scrollTo(0, 0);
    }
  } else {
    el("chat-input")?.blur();
  }
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

function promptGuidedMandatoryFields() {
  showGuidedError("Please fill in all required fields above.");
}

function initGuidedSubmitWraps() {
  const row = document.querySelector(".guided-action-row");
  if (!row) return;
  row.addEventListener("click", (e) => {
    for (const id of ["guided-submit", "multidrug-submit", "compareplans-submit"]) {
      const btn = el(id);
      if (!btn || btn.classList.contains("hidden")) continue;
      const rect = btn.getBoundingClientRect();
      if (
        e.clientX < rect.left ||
        e.clientX > rect.right ||
        e.clientY < rect.top ||
        e.clientY > rect.bottom
      ) {
        continue;
      }
      if (btn.disabled && !guidedEstimateInFlight) {
        promptGuidedMandatoryFields();
      }
      break;
    }
  });
}

function initGuidedAddRowButtons() {
  const configs = [
    {
      btnId: "multidrug-add-row",
      isAtLimit: () => drugRows.length >= MAX_BATCH_DRUGS,
      message: MAX_BATCH_DRUGS_LIMIT_MSG,
    },
    {
      btnId: "compareplans-add-row",
      isAtLimit: () => comparePlanRows.length >= MAX_COMPARE_PLANS,
      message: MAX_COMPARE_PLANS_LIMIT_MSG,
    },
  ];

  for (const { btnId, isAtLimit, message } of configs) {
    const btn = el(btnId);
    if (!btn) continue;
    const parent = btn.parentElement;
    if (!parent) continue;
    parent.addEventListener("click", (e) => {
      const rect = btn.getBoundingClientRect();
      if (
        e.clientX < rect.left ||
        e.clientX > rect.right ||
        e.clientY < rect.top ||
        e.clientY > rect.bottom
      ) {
        return;
      }
      if (btn.disabled && isAtLimit()) {
        showGuidedError(message);
      }
    });
  }
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
  el("multidrug-submit").disabled = true;
  el("compareplans-submit").disabled = true;
  showLoading("Estimating cost…");
  if (!resultsBaseline) renderResultsSkeleton();

  try {
    const body = {
      message,
      session_id: sessionId,
      filters: getFilters(),
      model: getSelectedModel(),
      timezone: getUserTimezone(),
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
      { llm_usage: resp.llm_usage, mediator_llm_usage: resp.mediator_llm_usage, total_llm_usage: resp.total_llm_usage }
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
    accumulateMediatorUsage(resp.mediator_llm_usage);
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
    updateGuidedSubmitButtonState();
  }
}

function submitGuidedEstimate() {
  showGuidedError("");
  const drug = el("filter-drug").value.trim();
  const dosage = el("filter-dosage").value.trim();
  const plan = el("filter-plan").value;
  if (!drug) {
    showGuidedError("Please select a drug.");
    return;
  }
  if (!dosage) {
    showGuidedError("Please select a dosage.");
    return;
  }
  if (!plan) {
    showGuidedError("Please select a plan.");
    return;
  }
  void sendGuidedInitial(composeGuidedMessage(), getFilters());
}

function resetGuidedConversation() {
  guidedSessionId = null;
  guidedTurnCount = 0;
  el("guided-turn-counter").textContent = "0/5 turns";
  el("guided-chat-messages").innerHTML =
    `<p id="guided-chat-placeholder" class="placeholder">Your LLM summary and follow-up conversation will appear here.</p>`;
  el("guided-results-content").innerHTML =
    `<p class="placeholder">Detailed costs and sources will appear after you get an estimate.</p>`;
  el("guided-data-as-of").classList.add("hidden");
  el("guided-chat-input").value = "";
  el("guided-chat-input").disabled = true;
  el("guided-send-btn").disabled = true;
}

function renderGuidedResponse(resp) {
  const hasStructuredResult =
    resp.channel_estimates?.length || resp.channel_estimate || resp.estimate;
  if (!hasStructuredResult) return;

  const dates = Object.values(resp.data_as_of || {}).filter(Boolean);
  const badge = el("guided-data-as-of");
  if (dates.length) {
    badge.textContent = `Data as of ${dates[0]}`;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  let resultHtml = "";
  if (resp.channel_estimates?.length > 1) {
    resultHtml = renderMultiEstimatesStackHtml(resp.channel_estimates);
  } else if (resp.channel_estimate) {
    resultHtml = renderMultiChannelEstimateCardHtml(resp.channel_estimate, { compact: true });
  } else if (resp.estimate) {
    resultHtml = renderEstimateCardHtml(resp.estimate, { compact: true });
  }
  el("guided-results-content").innerHTML = resultHtml + renderCitationsCard(resp.citations);
}

function updateGuidedFollowupAvailability() {
  const available = Boolean(guidedSessionId) && guidedTurnCount < 5;
  el("guided-chat-input").disabled = !available;
  el("guided-send-btn").disabled = !available;
  el("guided-chat-input").placeholder = available
    ? `Ask a follow-up about this estimate (${5 - guidedTurnCount} remaining)`
    : guidedTurnCount >= 5
      ? "This guided conversation has reached 5 turns"
      : "Ask a follow-up about this estimate";
}

async function sendGuidedInitial(message, filters = null) {
  resetGuidedConversation();
  await sendGuidedMessage(message, { filters });
}

async function sendGuidedMessage(message, { filters = null } = {}) {
  if (!message.trim()) return;
  appendMessage("user", message, null, null, null, "guided-chat-messages");
  el("guided-chat-input").value = "";
  guidedEstimateInFlight = true;
  updateGuidedSubmitButtonState();
  el("guided-send-btn").disabled = true;
  el("guided-loading-text").textContent = guidedSessionId
    ? "Preparing follow-up…"
    : "Summarizing estimate…";
  el("guided-loading").classList.remove("hidden");
  showGuidedError("");

  try {
    const res = await fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        session_id: guidedSessionId,
        filters,
        model: getSelectedModel("guided-model-select"),
        timezone: getUserTimezone(),
      }),
    });
    const contentType = res.headers.get("content-type") || "";
    const body = contentType.includes("application/json") ? await res.json() : await res.text();
    if (!res.ok) {
      appendMessage(
        "assistant",
        `Sorry — ${chatErrorMessage(res, body)} Please try again.`,
        null,
        null,
        null,
        "guided-chat-messages"
      );
      return;
    }
    if (!body?.response) {
      appendMessage("assistant", "Sorry, something went wrong. Please try again.", null, null, null, "guided-chat-messages");
      return;
    }

    guidedSessionId = body.session_id;
    guidedTurnCount = body.turn_count;
    el("guided-turn-counter").textContent = `${guidedTurnCount}/5 turns`;
    const resp = body.response;
    const explanation = resp.explanation || resp.clarification_message || "No response.";
    appendMessage(
      "assistant",
      explanation,
      resp.channel_estimate || resp.channel_estimates?.length
        ? resp.response_source || "CMS data"
        : resp.response_source,
      resp.citations,
      { llm_usage: resp.llm_usage, mediator_llm_usage: resp.mediator_llm_usage, total_llm_usage: resp.total_llm_usage },
      "guided-chat-messages"
    );
    renderGuidedResponse(resp);
    accumulateSessionUsage(resp.llm_usage);
    accumulateMediatorUsage(resp.mediator_llm_usage);
  } catch (err) {
    appendMessage("assistant", "Sorry, something went wrong. Please try again.", null, null, null, "guided-chat-messages");
    console.error(err);
  } finally {
    el("guided-loading").classList.add("hidden");
    guidedEstimateInFlight = false;
    updateGuidedSubmitButtonState();
    updateGuidedFollowupAvailability();
  }
}

el("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(el("chat-input").value);
});

el("guided-chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (guidedTurnCount >= 5) return;
  sendGuidedMessage(el("guided-chat-input").value);
});

document.addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  sendMessage(chip.textContent.trim());
});

el("mode-tab-chat").addEventListener("click", () => switchMode("chat"));
el("mode-tab-guided").addEventListener("click", () => switchMode("guided"));
document.querySelector(".primary-mode-tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const mode = event.key === "ArrowLeft" || event.key === "Home" ? "chat" : "guided";
  switchMode(mode);
  el(`mode-tab-${mode}`).focus();
});
el("guided-submit").addEventListener("click", submitGuidedEstimate);

el("guided-mode-single").addEventListener("click", () => switchGuidedSubmode("single"));
el("guided-mode-multidrug").addEventListener("click", () => switchGuidedSubmode("multidrug"));
el("guided-mode-compareplans").addEventListener("click", () => switchGuidedSubmode("compareplans"));
el("multidrug-add-row").addEventListener("click", addDrugRow);
el("multidrug-submit").addEventListener("click", submitMultiDrugEstimate);
el("compareplans-add-row").addEventListener("click", addComparePlanRow);
el("compareplans-submit").addEventListener("click", submitComparePlans);

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
    await loadPlans(currentDataRelease?.contract_year ?? null);
  } catch (e) {
    console.warn("Could not load plans", e);
    updatePlanLoadHint(0, "Could not load plans — try again shortly");
  } finally {
    btn.disabled = false;
  }
});

el("menu-btn").addEventListener("click", (event) => {
  event.stopPropagation();
  toggleMenu();
});
el("menu-new-chat").addEventListener("click", resetChat);
el("menu-about").addEventListener("click", showAboutModal);
el("menu-disclaimer").addEventListener("click", showDisclaimerModal);
el("menu-privacy").addEventListener("click", showPrivacyModal);
el("info-modal-close").addEventListener("click", closeInfoModal);
el("info-modal").addEventListener("click", (event) => {
  if (event.target.dataset.action === "close-info-modal") closeInfoModal();
});

document.addEventListener("click", (event) => {
  const menu = el("app-menu");
  if (menu.classList.contains("hidden")) return;
  if (menu.contains(event.target) || event.target.closest("#menu-btn")) return;
  closeMenu();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!el("info-modal").classList.contains("hidden")) {
    closeInfoModal();
    return;
  }
  if (!el("app-menu").classList.contains("hidden")) {
    closeMenu();
  }
});

emptyStateHtml = el("empty-state").outerHTML;
updateChatComposerHint();
loadDisclaimer();
initDisclaimerCollapse();
initFieldInfoTooltips();
initPlanCombobox();
initLocationPickers();
initDrugPickers();
resetDrugRows();
resetComparePlanRows();
refreshMultiDrugPickers();
refreshCompareDrugPickers();
initGuidedSubmitWraps();
initGuidedAddRowButtons();
populateModelSelect();
populateModelSelect("guided-model-select");
updateSessionUsageDisplay();
updateGuidedSubmitButtonState();
loadStates();
async function initDataAndPlans() {
  await loadDataRelease();
  await pollPlansUntilLoaded();
}
void initDataAndPlans();
resetGuidedConversation();
switchMode("chat");
switchGuidedSubmode("single");
