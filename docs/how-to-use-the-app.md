# How to Use the Medicare Drug Cost Navigator

This guide explains how to use the web app from a user's perspective — what each screen does, how to get a cost estimate, and how to read the results.

---

## What this app does

The **Drug Cost Navigator** estimates what a specific prescription drug will cost on a specific **Medicare Part D** or **Medicare Advantage with Part D (MA-PD)** plan.

- Dollar amounts come from **official CMS plan data**, not from guesses by the AI.
- The AI explains results in plain English and answers follow-up questions.
- Estimates are **informational only** — not medical advice, financial advice, or enrollment guidance. Always confirm costs with your doctor, pharmacist, or plan before making decisions.

**Current data coverage:** Arkansas and Texas plans (stand-alone PDP and local MA-PD plans). Other states may be added over time.

---



## Opening the app

1. Open the app URL in your browser (for local development: `http://localhost:8000`).
2. You can also add it to your home screen on a phone — the app supports install as a lightweight web app.

When the page loads, you will see:

- A **top bar** with the app title (tap to open About), **New chat**, **Feedback**, session usage, and turn counter
- An **Important notice** banner (tap to expand the full disclaimer)
- Two main tabs: **Chat** and **Guided form**

---



## Top bar and menu

### App title

Tap **Drug Cost Navigator** in the center of the top bar to open the **About app** modal.

### New chat

The **New chat** button in the top bar clears the Chat tab conversation, results, and turn counter — the same as **New chat** in the menu.

### Feedback

Use **Feedback** in the top bar (or **Send feedback** below the chat after you receive an answer) to tell us what worked, what didn't, or what you'd like to see.

The form asks for:

1. **Message** (required) — up to 2,000 characters
2. **State** (optional) — pre-filled when you've already picked a state in chat or the guided form
3. **ZIP code** (optional) — pre-filled from your chat or guided-form ZIP when available

After you send, you'll see a short confirmation. Feedback is stored on the server so the team can review it later; it is not used to change your current estimate.

### Turn counter

The header shows how many conversation turns you have used in the current session, for example `2/5 turns`. Each question or follow-up counts as one turn. The limit is **5 turns per session**.

### Session usage

Next to the turn counter you may see token and cost totals (for example `1,240 tokens · $0.01`). This is informational — it tracks AI usage for your current browser session.

### Menu (☰ Menu)

Open the menu from the top-left corner:


| Menu item          | What it does                                                                         |
| ------------------ | ------------------------------------------------------------------------------------ |
| **New chat**       | Clears the Chat tab conversation, results, and turn counter. Starts a fresh session. |
| **About app**      | Short description of what the app does and its current limitations.                  |
| **Disclaimer**     | Full legal/informational disclaimer.                                                 |
| **Privacy policy** | How your data is handled during a session.                                           |


---



## Two ways to get an estimate

You can use either mode — both reach the same underlying cost engine.


| Mode            | Best for                                                                                   |
| --------------- | ------------------------------------------------------------------------------------------ |
| **Chat**        | Natural-language questions; quick one-off lookups; exploring with example prompts          |
| **Guided form** | Step-by-step forms when you know drug, dosage, and plan; comparing multiple drugs or plans |


Switch between them using the **Chat** and **Guided form** tabs below the disclaimer banner.

---



## Chat tab



### Getting started

When Chat is empty, you will see:

- A short welcome message
- **Example prompt chips** — tap any chip to send a pre-written question immediately

Example questions the app understands well:

- *"How much will lovastatin 40mg cost on Medicare plan S5921-400? I have not spent anything out of pocket yet this year."*
- *"How much will lovastatin 40mg cost on plan S5921-400 for a 90-day supply?"*
- *"What's the cost for lovastatin 40mg on plan S5921-400 if I've already spent $800 this year?"*



### What to include in your question

For the best result, mention:

1. **Drug name** (for example `metformin`, `lovastatin`)
2. **Dosage** (for example `500mg`, `40mg`)
3. **Plan ID** (for example `S5921-400` or `S9999-001`)
4. **Optional:** days supply (30, 60, or 90), and year-to-date (YTD) out-of-pocket spending

You can type your own question in the text box at the bottom and click **Send**.

### Plan lookup helper (optional)

Above the chat input, expand **Plan lookup helper** if you do not know a plan ID:

1. **State** — type or select a state (required to filter plans)
2. **Zip code** (optional) — entering a 5-digit zip can pre-fill the state
3. **Plan** — pick a plan from the filtered list

This helper does not replace your question — it helps you find a plan ID to mention in chat. You still send a message describing what you want estimated.

### Model selector

Next to **Send**, you can choose which AI model summarizes the answer (for example GPT-5.4 Nano or Claude Haiku). The **cost numbers themselves always come from CMS data**, regardless of which model you pick.

### Chat results layout

After you send a message:

1. **Conversation** — your message and the assistant's plain-English reply appear in the main chat area.
2. **Estimate panel** — below the chat input, the **Estimate** section shows structured cost details and source citations.

While the app is working, you will see a loading spinner and **Estimating cost…**. The Send button is disabled until the response returns.

### Follow-ups in Chat

After the first answer, type a follow-up in the same chat box (for example *"What if I use mail-order instead?"* or *"Compare that to a 90-day supply"*). Each send uses one turn until you reach **5/5 turns**.

**Send feedback** appears below the chat once you've received at least one assistant reply — use it to report issues or suggestions without leaving the conversation.

### Shareable links

When you send a chat message, the browser address bar updates with your question (and any active filters) as query parameters. Opening that link in a new tab replays the same question automatically — useful for bookmarking or sharing a specific lookup.

Use **Menu → New chat** to start over.

### Finding a nearby pharmacy

You can also ask about pharmacies without asking for a cost estimate, for example:

- *"What pharmacies are near ZIP code 72712?"*
- *"Which preferred pharmacies does plan S5921-400 have near me?"*
- *"What will lovastatin cost at my preferred pharmacy near 72712?"*

The app matches CMS's published pharmacy-network data (optionally scoped to a plan you name) and reports straight-line distance from your ZIP code within a fixed 25-mile search — not driving distance, real-time hours, or in-stock information. If you ask for cost "at my preferred pharmacy," the app finds the nearest preferred-retail pharmacy first, then prices the drug at that channel (CMS prices apply per channel, not per individual pharmacy address).

---



## Guided form tab

The guided form walks you through fields step by step, then shows both a conversation summary and detailed estimate cards.

### Step 1: Pick a state

At the top of the guided form:

- **State** (required) — filters which plans appear in plan pickers below
- **Zip code** (optional) — can pre-fill the state; if zip and state disagree, the app warns you before switching



### Step 2: Choose a sub-mode

Three sub-tabs are available:


| Sub-mode           | Purpose                                               |
| ------------------ | ----------------------------------------------------- |
| **Single**         | One drug on one plan                                  |
| **Multiple drugs** | Up to 5 drugs on the same plan, with a combined total |
| **Compare plans**  | One drug compared across 2–4 plans                    |


---



### Single — one drug, one plan

Fill in:


| Field                     | Required | Notes                                                     |
| ------------------------- | -------- | --------------------------------------------------------- |
| **Drug**                  | Yes      | Click to open the drug picker; search by name             |
| **Dosage**                | Yes      | Enabled after you select a drug                           |
| **Plan**                  | Yes      | Filtered by the state you selected; type to search        |
| **Contract year**         | No       | 2025 or 2026                                              |
| **Days supply**           | No       | 30 (default), 60, or 90                                   |
| **YTD out-of-pocket ($)** | No       | How much you have already spent on Part D drugs this year |


Click **Get estimate**.

If required fields are missing, a red error message appears at the top of the form.

**Refresh** next to the plan field reloads the plan list from the database (useful if plans were still loading).

---



### Multiple drugs — several drugs on one plan

1. Select a **Plan** (state must be set first).
2. Fill **Drug** and **Dosage** for the first row.
3. Click **+ Add drug** to add more rows (up to **5 drugs** total).
4. Set **Days supply** and optional **YTD out-of-pocket**.
5. Click **Get combined estimate**.

The app estimates each drug and shows a combined cost summary.

---



### Compare plans — one drug across plans

1. Select **Drug** and **Dosage**.
2. Pick at least **two plans** (default is two rows; use **+ Add plan** for up to **4 plans** total).
3. Set **Days supply** and optional **YTD out-of-pocket**.
4. Click **Compare plans**.

The results highlight differences and which plan has the lowest estimated cost for that fill.

---



### Guided form results

After you submit a guided estimate:

1. **Estimate conversation** — the assistant summarizes the result; you can ask follow-ups here (up to 5 turns total for this guided session).
2. **Estimate details** — structured cards with costs, benefit context, pharmacy channels, and **Sources**.

Each new **Get estimate** / **Get combined estimate** / **Compare plans** click starts a **new guided conversation** (previous guided chat is cleared).

The guided form has its own **model selector** next to the submit button, independent from the Chat tab.

---



## Understanding your estimate



### Summary card

The estimate card shows:

- **Drug** and **plan** name (with plan ID)
- **Estimated cost** — a dollar amount or range for your fill
- **Badges** such as formulary tier, days supply, and benefit phase (pre-deductible, initial coverage, insulin-cap, or catastrophic)



### Status indicators


| Appearance        | Meaning                                                                                  |
| ----------------- | ---------------------------------------------------------------------------------------- |
| Normal cost shown | Drug is on the formulary and a cost could be estimated                                   |
| **Not covered**   | Drug is not on the plan's formulary — no cost estimate                                   |
| **Fill blocked**  | Plan quantity limits prevent the requested days supply (max allowed supply may be shown) |
| Warning styling   | Estimate returned with important **caveats** — read the bullet list carefully            |




### Detailed sections (multi-channel view)

When full detail is available, the estimate breaks down into:

**Plan & fill**

- Drug, dosage, plan, and days supply

**Benefit context**

- Whether the drug is covered
- Plan deductible and formulary tier
- Whether the tier counts toward the deductible
- Benefit phase and effective phase (based on your YTD spend)
- Annual out-of-pocket cap and remaining headroom
- Projected annual and rest-of-year costs (when applicable)

**This fill by channel**

A table with four standard pharmacy types:


| Channel              | Description                                     |
| -------------------- | ----------------------------------------------- |
| Preferred retail     | In-network retail with lowest cost share        |
| Standard retail      | Retail with standard (non-preferred) cost share |
| Preferred mail-order | Plan's preferred mail-order pharmacy            |
| Standard mail-order  | Mail-order with standard cost share             |


For each channel you may see plan copay, coinsurance, applied amounts, and **estimated cost** for that fill.

Hover or focus the **ⓘ** icons next to field labels for short explanations.

### Sources

The **Sources** section lists CMS records backing the estimate. Expand each item to see the claim, source name, data date, and a link to source documentation when available.

A **Data as of** badge shows when the underlying CMS data was published.

---



## Tips for better results

1. **Use the plan ID** from your Medicare card or plan documents (format like `S5921-400`).
2. **Include dosage** — `metformin 500mg` is much more reliable than `metformin` alone.
3. **Mention YTD spend** if you are past the deductible or approaching catastrophic coverage — it affects which benefit phase applies.
4. **Specify days supply** when you want 60- or 90-day fills; default is 30 days.
5. In the guided form, **select state first** so plan lists are manageable and relevant.
6. If the assistant asks for clarification, answer in a follow-up (while you still have turns left).

---



## What the app can and cannot do



### Supported today

- Medicare Part D and MA-PD plans in **Arkansas and Texas**
- Standard formulary drugs, plus **insulin** (priced via its separate $35-per-30-day IRA statutory cap, not the standard tiered/deductible pipeline)
- Benefit phases: pre-deductible, initial coverage, insulin-cap, and catastrophic
- All four standard CMS pharmacy channels
- Single-drug, multi-drug, and plan-comparison estimates, including baskets that mix insulin and oral drugs
- Nearby- and preferred-pharmacy lookup by ZIP code (see [Finding a nearby pharmacy](#finding-a-nearby-pharmacy) below)



### Not supported yet (the app may stop or warn)

- Plans or states outside the loaded dataset
- Certain specialty pricing rules beyond insulin's statutory cap
- **Low-income subsidy (LIS)** scenarios
- Some **coinsurance-only** plans where a fixed dollar estimate cannot be computed
- Real-time pharmacy pricing (estimates use quarterly CMS published data)
- Driving directions, real-time pharmacy hours/stock, or a wider search radius than the fixed 25-mile pharmacy lookup

When the app cannot produce a reliable number, it will say so explicitly rather than guessing.

---



## Privacy (summary)

- No account sign-up; no advertising cookies or analytics trackers in the app.
- Conversation content stays in your browser tab and short-lived server memory for your session (about 30 minutes of inactivity).
- Refreshing the page or starting a **New chat** clears the Chat session.
- Your messages are sent to an AI provider to generate explanations; cost math is done separately from CMS data.
- See **Menu → Privacy policy** for the full plain-language summary.

---



## Troubleshooting


| Problem                          | What to try                                                                                         |
| -------------------------------- | --------------------------------------------------------------------------------------------------- |
| Disclaimer stuck on "Loading…"   | Check your network connection; reload the page                                                      |
| No plans in picker               | Confirm a state is selected; click **Refresh**; data may still be loading                           |
| "Sorry — …" error from assistant | Server or AI may be unavailable; wait and retry                                                     |
| Send button disabled             | Wait for the current request to finish                                                              |
| Reached 5/5 turns                | Use **Menu → New chat** (Chat) or submit a new guided estimate (Guided form)                        |
| Plan ID not recognized           | Verify the ID from plan materials; use the plan lookup helper to pick from the list                 |
| Drug not covered                 | The drug may not be on that plan's formulary — try another plan or confirm the drug name and dosage |


---



## Quick reference

```
Chat flow:
  Type question (drug + dosage + plan) → Send → Read reply + Estimate panel → Follow up (≤5 turns)

Guided single:
  State → Drug → Dosage → Plan → Get estimate → Follow up in guided conversation

Guided multiple drugs:
  State → Plan → Add drugs (≤5) → Get combined estimate

Compare plans:
  State → Drug → Dosage → Pick 2–4 plans → Compare plans
```

---

*For technical setup, API details, and deployment, see the [Developer Guide](./developer-guide.md).*