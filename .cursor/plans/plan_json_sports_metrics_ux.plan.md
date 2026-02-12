---
name: Plan JSON Sports Metrics UX
overview: Implement JSON-first plan generation (#107), sports selection at onboarding (#110), training metrics prefill and VDOT logic (#88), year rollover fix for Dec→Jan (#84), with UX improvements for long plan generation (#106) and reduced plan prompt token usage (#105). Issue
todos: []
isProject: false
---

# Plan: JSON-first plans, sports selection, metrics prefill, year fix, and UX/prompt optimizations

## Scope

- **#107** – Generate plan as native JSON (not markdown parsing).
- **#110** – Let athlete choose sports (Run, Bike, Swim, Strength, Other) during onboarding.
- **#88** – Pre-populate onboarding with LTHR/FTP; pass VDOT to plan prompt with "current if &lt; 4 weeks" logic and optional VDOT test in early weeks.
- **#84** – Fix week date year when plan spans December into January.
- **#106** – Improve "request takes too long" UX (consider timeouts / background job / polling).
- **#105** – Reduce plan prompt token usage (trim Strava/context).
- **#68** (context) – Plan continuation / plan structure mismatch; taken into account below.

---

## 0. Issue #68 – Plan continuation (context)

[Issue #68](https://github.com/smurphin/my-personal-coach/issues/68): Shane's plan showed 10 weeks to Seville marathon but `plan_structure` only had 6 weeks, triggering "plan finished" and unwanted onboarding. Fixes requested: (1) plan structure must match plan detail, (2) allow plan extension without full onboarding, (3) save lifestyle context so it doesn't need re-entry.

**How the current plan covers #68:**

- **Fix 1 – Plan structure matches plan detail:** With **#107** we compute the full week count from `plan_start_date` and `weeks_until_goal` and pass a fixed week calendar into the prompt; we overwrite each week's `start_date`/`end_date` from that calendar before saving. The stored `plan_v2` will therefore always have exactly the right number of weeks (no more "10 weeks in text, 6 in DB"). The new plan prompt plus JSON-first response handling directly addresses the mismatch.
- **Fix 2 – Extension without full onboarding:** The current "new plan" flow already reuses saved profile (goal date and goal text are the main new inputs). When implementing #107 and any "generate plan" or "generate maintenance plan" flows, keep in mind a possible **"continue plan"** or **"extend to next goal"** path: reuse `athlete_profile`, `training_metrics`, and (after #110) `sports`, and only ask for the new goal date and optional goal text—no full onboarding. This can be a small follow-up (e.g. a dedicated button/route "Extend my plan" that pre-fills from profile) rather than part of the initial scope; the current new-plan prompt and profile persistence make it feasible.
- **Fix 3 – Save lifestyle context:** Already done: `athlete_profile` (lifestyle_context, athlete_type) is saved in `generate_plan()` and used to prefill onboarding. With **#88** we add LTHR/FTP/VDOT prefill from `training_metrics`, and with **#110** we persist selected sports. Re-onboarding will therefore reuse lifestyle, type, metrics, and sports; user can still change any field. No extra work needed for Fix 3 beyond the planned prefill and sports persistence.

No separate #68 implementation section is needed; the above is covered by #107, #88, and #110. When touching the plan prompt or generate_plan flow, briefly verify that week count and stored weeks stay aligned (Fix 1).

---

## 1. Issue #107 – Plan creation as JSON

**Current state:** [services/ai_service.py](services/ai_service.py) calls `generate_content(prompt)` then [utils/migration.py](utils/migration.py) `parse_ai_response_to_v2()` which tries JSON extraction first, then simple/legacy markdown parsing. Plan prompt in [prompts/plan_prompt.txt](prompts/plan_prompt.txt) asks for markdown (e.g. `### Week N: Month Day - Month Day`). [JSON_FIRST_PLAN_GENERATION.md](JSON_FIRST_PLAN_GENERATION.md) describes Phase 2 as "Update generate_training_plan() to use JSON-first".

### 1.1 JSON-first architecture: Assessment → Plan

Instead of a single “do everything” plan call, plan generation becomes a **two-step flow**:

1. **Call A – Training assessment (JSON)**
  - **Inputs:** Strava history (short- and long-term), `training_metrics` (VDOT, LTHR, FTP + paces/zones), and **Garmin health stats** if available (HRV, resting HR, sleep, stress/body-battery aggregates).
  - **Output:** A structured `assessment` JSON stored in `user_data`, e.g.:
    - `short_term_state`: last 4–12 weeks (load, intensity distribution, adherence, recent changes).
    - `long_term_state`: background experience from lifetime Strava history (event types, typical volume, durability).
    - `current_fitness_summary`: concise 1–5 line summary (focused and decision-oriented, not a wall of text).
    - `current_fitness_snippet`: 1–2 line “at a glance” version (for weekly feedback / chat prompts).
    - `strengths`, `limiters`, `risk_flags` (e.g. overuse risk, under-recovery).
    - `garmin_summary` (if Garmin connected): pre-aggregated trends, e.g.:
      - HRV: 7d vs 30d average + simple “up/down/stable” trend.
      - Resting HR: 7d vs 30d average + trend.
      - VO2 max: latest value and 7d/30d trend if available (running/cycling).
      - Sleep: 7d average duration/quality.
      - Stress/body battery: 7d average or simple trend.
  - **Cadence:**
    - Always run **fresh** when the athlete generates a **new plan**.
    - Run a **weekly background refresh** for the short-term part and Garmin summary.
    - Refresh **long-term background** less frequently — e.g. at the **end of a training plan** or on a **6‑monthly** cadence.
  - **Usage across the app:** The **latest stored assessment** is always passed into:
    - Plan generation (Call B below).
    - Feedback / weekly summaries.
    - Chat prompts (so the model always has a current picture, even if it’s a few days old).
2. **Call B – Plan generation (JSON-first, plan_v2)**
  - **Inputs:** `assessment` JSON from Call A, `goal`, `goal_date`, `plan_start_date`, `weeks_until_goal`, partial-week info, selected `sports`, `athlete_type`, `lifestyle_context`, `training_metrics`, and the precomputed **week calendar**.
  - **Prompt goal:** “Given this assessment and these constraints, produce a `plan_v2` JSON (weeks + sessions).  also produce a short markdown preamble that explains how the plan reflects the assessment.”
  - The model focuses on **turning assessment → periodised plan**, not rediscovering the athlete from raw history on every call.

### 1.2 Week dates, realistic Week 0, and response handling

- **Week dates:** Do **not** rely on the model to output correct week dates across year boundary. **Compute week dates server-side** from `plan_start_date` and `weeks_until_goal` (and optional `has_partial_week` / `days_in_partial_week`) in [routes/plan_routes.py](routes/plan_routes.py) (reuse logic from `calculate_weeks_until_goal`). Pass into the prompt as a fixed **week calendar** (e.g. list of `{ week_number, start_date, end_date }`).
- **Realistic Week 0 (late-day onboarding):**
  - Use the athlete’s **local time** (from their timezone).
  - If onboarding happens **after 16:00 local time**, treat **“today” as unavailable** for training:
    - Either set `plan_start_date` to **tomorrow** (so Week 0 runs Thu–Sun instead of Wed–Sun in the example), or
    - Keep `plan_start_date` as today but set `days_in_partial_week` so the calendar only allocates days from **tomorrow onward**.
  - If onboarding happens **before 16:00 local time**, include **today** in Week 0, but in the plan prompt clearly instruct the model to:
    - Treat any **“today” session as STRETCH / optional**, not KEY.
    - Make it clear in the session description that it’s optional, depending on how much time/energy the athlete has left in the day.
- **Overwrite dates from calendar:** After parsing the JSON into a `TrainingPlan`, **overwrite each week’s `start_date`/`end_date` from the calendar** before saving. This guarantees:
  - The stored `plan_v2` has the exact planned duration (e.g. 10 weeks, ending on `goal_date`).
  - Week dates are chronologically correct even across Dec→Jan (fixes #84 in the JSON-first path).
- **Response handling:** In `generate_training_plan()`, after `generate_content()`:
  - Parse response: extract JSON (e.g. from fenced block or top-level object); validate with [utils/plan_validator.py](utils/plan_validator.py) `validate_and_load_plan_v2()`.
  - Apply the precomputed week calendar to each week’s dates as above.
  - Generate markdown for display from `plan_v2.to_markdown()` (already used elsewhere). No markdown parsing for plan structure.
- **Fallback:** If JSON extraction/validation fails, keep a single fallback path: try existing `parse_ai_response_to_v2()` and then **apply server-side week dates** over the parsed plan so week dates are always correct.

**Files:** [prompts/plan_prompt.txt](prompts/plan_prompt.txt) (updated to JSON-first and to consume `assessment`), [services/ai_service.py](services/ai_service.py) (new assessment call + JSON-first plan handling), [routes/plan_routes.py](routes/plan_routes.py) (week date computation + overwrite + wiring for both calls), [utils/plan_validator.py](utils/plan_validator.py) (ensure schema allows optional dates when overwritten), and a helper module for week dates (e.g. `utils/week_dates.py`) plus a small Garmin aggregation helper if needed.

---

## 2. Issue #84 – Year correct when plan spans Dec → Jan

**Current state:** Week dates come from (a) AI output parsed by [utils/migration.py](utils/migration.py) and [utils/simple_plan_parser.py](utils/simple_plan_parser.py), which use `current_year = datetime.now().year` for both start and end when parsing "Month Day" text, so "December 29 – January 4" becomes same year and ordering breaks. [utils/formatters.py](utils/formatters.py) already has correct logic: if `start_date.month > end_date.month`, then adjust year (start = year−1 or end = year+1).

**Approach:**

- **Primary fix:** With #107, week dates are computed server-side from `plan_start_date` and number of weeks; no "Month Day" parsing for structure, so Dec→Jan is correct by construction.
- **Secondary fix (for fallback and existing flows):** In [utils/migration.py](utils/migration.py) and [utils/simple_plan_parser.py](utils/simple_plan_parser.py), when parsing "Month Day – Month Day" into two dates with a single `current_year`, apply the same year-transition rule as in [utils/formatters.py](utils/formatters.py) (lines 76–81): if `start_date.month > end_date.month`, set `end_date = end_date.replace(year=current_year + 1)` (and optionally `start_date = start_date.replace(year=current_year - 1)` when appropriate for "Jan – Dec" in southern hemisphere if ever needed). This makes legacy/fallback parsing robust for Dec→Jan.

**Files:** [utils/migration.py](utils/migration.py), [utils/simple_plan_parser.py](utils/simple_plan_parser.py).

---

## 3. Issue #110 – Select desired sports during onboarding

**Current state:** Onboarding form in [templates/onboarding.html](templates/onboarding.html) has goal, goal date, sessions/hours, athlete type, LTHR/FTP, unit prefs; no sports selection. Plan prompt and AI logic do not explicitly restrict to "only these sports".

**Approach:**

- **Data model:** Add to `athlete_profile` (or a small `onboarding_plan_preferences` stored with the plan/user) a list of selected sports, e.g. `sports: ["Run", "Bike", "Swim", "Strength", "Other"]`. Persist in the same place as other onboarding choices (e.g. with `athlete_profile` in user_data).
- **Onboarding UI:** Add a "Sports to include in your plan" section: checkboxes (or multi-select) for Run, Bike, Swim, Strength, Other. At least one required. Default: e.g. Run + Bike checked if goal suggests multisport, else Run.
- **Backend:** In [routes/plan_routes.py](routes/plan_routes.py) `generate_plan()`, read selected sports from form, save into profile/preferences, and pass `sports` (or `included_sports`) into `user_inputs` and into `final_data_for_ai`.
- **Prompt:** In [prompts/plan_prompt.txt](prompts/plan_prompt.txt), add a line: "The athlete has selected these sports to include in their plan: {{ included_sports }}. Only prescribe sessions for these sports (and REST); do not prescribe sessions for sports they did not select."

**Files:** [templates/onboarding.html](templates/onboarding.html), [routes/plan_routes.py](routes/plan_routes.py), [prompts/plan_prompt.txt](prompts/plan_prompt.txt). Optional: prefill `sports` from `athlete_profile` when re-onboarding.

---

## 4. Issue #88 – Pass training metrics to onboarding and VDOT logic

**Current state:** Onboarding route in [routes/plan_routes.py](routes/plan_routes.py) passes only `athlete_profile` (lifestyle_context, athlete_type) to [templates/onboarding.html](templates/onboarding.html). LTHR/FTP inputs use `request.form.get('lthr', '')` so no prefill from DB. VDOT is prepared for plan prompt via [utils/vdot_context.py](utils/vdot_context.py) `prepare_vdot_context(user_data)` but there is no "VDOT current if &lt; 4 weeks" or "schedule VDOT test in early weeks" instruction in the plan prompt.

**Approach:**

- **Pre-populate LTHR/FTP/VDOT on form:** In the onboarding view, build a small dict from `user_data.get('training_metrics', {})`: `lthr_value`, `ftp_value`, `vdot_value` (and optionally `vdot_date` for display). Pass to template (e.g. as part of `athlete_profile` or a new `training_metrics_prefill`). In [templates/onboarding.html](templates/onboarding.html), set `value="{{ training_metrics_prefill.lthr_value or '' }}"` (and same for ftp, and optionally show "Current VDOT: X (from …)" if present).
- **VDOT in plan prompt:** Ensure `prepare_vdot_context()` exposes a "recent" flag (e.g. `vdot_is_recent`: true if `detected_at`/`date_set` is within 4 weeks). In [prompts/plan_prompt.txt](prompts/plan_prompt.txt): if VDOT is present and recent, state "Use this VDOT for pace prescription." If VDOT is older than 4 weeks (or missing after a gap), add: "Consider scheduling a VDOT test in the early weeks of the plan (e.g. 5K time trial or Parkrun) at an appropriate time—do not break the athlete in week 1." If the athlete just finished a plan with a race and is starting a new plan with no gap, prefer VDOT from that race (already in training_metrics if detection ran).
- **FTP/LTHR:** Already saved from form in `generate_plan()`; ensure when pre-populating we use the same field names and that form post still overwrites correctly.

**Files:** [routes/plan_routes.py](routes/plan_routes.py) (onboarding view + prefill, and any `prepare_vdot_context` call for plan), [templates/onboarding.html](templates/onboarding.html), [utils/vdot_context.py](utils/vdot_context.py) (optional `vdot_is_recent` / `vdot_age_days`), [prompts/plan_prompt.txt](prompts/plan_prompt.txt).

---

## 5. Issue #105 – Plan prompt efficiency (fewer tokens)

**Current state:** [routes/plan_routes.py](routes/plan_routes.py) builds `final_data_for_ai` with `analyzed_activities` (up to 200 activities, then detailed analysis for last week), full `athlete_stats`, `strava_zones`, and a large `json_data` dump. Plan prompt renders `json_data` and training history.

**Updated with assessment/plan split:**

- **Deep understanding of the last 6–8 weeks belongs in Call A (assessment), not in the plan prompt:**
  - For **Call A**, we deliberately send enough detail to let the model truly understand:
    - How well the athlete has trained over the last 6–8 weeks (volume, intensity distribution, adherence).
    - Whether they’ve been **consistently training** (so Week 1 doesn’t start too light), or are **undertrained / disrupted** (so the plan eases in).
  - This includes:
    - Detailed analyzed activities for the last 6–8 weeks.
    - Aggregated long-term stats.
    - Garmin trends (HRV, resting HR, VO2 max, sleep, stress/body battery).
  - The result is a structured `assessment` JSON (short_term_state, long_term_state, strengths/limiters, risk_flags, current_fitness_summary/snippet).
- **Call B (plan generation) should be much leaner:**
  - It consumes the **assessment JSON** plus goal, dates, sports, profile, and the week calendar.
  - It does **not** need the full raw activity list again.

**Approach (trim where it matters now):**

- **For Call A (assessment):** Keep:
  - **Full 6–8 weeks of detailed context** (this is where deep understanding lives).
  - Long-term aggregates and Garmin summary.
  - Avoid obviously redundant data (e.g. the same stats repeated in multiple shapes), but favour **richness over extreme trimming** for this call.
- **For Call B (plan generation):**
  - Pass the **assessment JSON** and a **compact** context object:
    - `goal`, `goal_date`, `plan_start_date`, `weeks_until_goal`, `has_partial_week`, `days_in_partial_week`.
    - `athlete_type`, `lifestyle_context`, selected `sports`, `training_metrics`.
    - Precomputed **week calendar** (week_number, start_date, end_date).
  - Optionally pass a **short “recent training snippet”** (e.g. 1–2 lines from `current_fitness_snippet`), but not full analyzed_activities.
  - Remove or heavily trim:
    - Large `analyzed_activities` arrays.
    - Verbose raw `athlete_stats` and `strava_zones` that are only needed to build the assessment.
- **Prompt text:** Shorten any long boilerplate in [prompts/plan_prompt.txt](prompts/plan_prompt.txt) that restates what’s already encoded in the assessment JSON, and clearly describe:
  - How to use `short_term_state` to decide starting volume/intensity (don’t start too light if they’ve trained well; ease in if they’re undertrained).
  - How to treat Week 0 sessions, especially “today” when onboarding is before 16:00 (today’s session = STRETCH/optional).

**Files:** [routes/plan_routes.py](routes/plan_routes.py) (separate payload building for Call A vs Call B, and trimmed plan payload), [prompts/plan_prompt.txt](prompts/plan_prompt.txt) (use assessment JSON, week calendar, and recent-training snippet instead of full raw history). [services/ai_service.py](services/ai_service.py) where the two calls and their payloads are wired together.

---

## 6. Issue #106 – "Request takes too long" UX

**Current state:** Plan generation is synchronous: browser POST to `/generate_plan`, backend runs Strava fetch + AI call + parse + save, then redirect. Long-running request can hit nginx/proxy timeouts (e.g. 60s) and user sees "takes too long" or connection drop.

**Approach (explicit choice: Options A & C only):**

- **Option A – Increase timeouts (minimal change):**
  - Document and, if you control infra, increase proxy/load balancer timeouts (e.g. `proxy_read_timeout`) for `/generate_plan` and `/generate_maintenance_plan` so that typical plan generation (e.g. 90–120s) succeeds.
  - No application-level queue or worker; just infra config + docs.
- **Option C – Keep connection but improve feedback (front-end UX):**
  - Keep the synchronous flow but ensure the loading state (e.g. tutorial carousel on onboarding) clearly communicates:
    - “Plan generation can take 1–2 minutes.”
    - What to do if it times out (e.g. “If this page errors, please retry – your data is safe.”).
  - Make sure any timeout/error page is friendly and re-usable (not a raw stack trace).
  - Optionally send periodic log output / progress markers server-side, but **do not** introduce Celery, queues, or background workers in this feature.

We explicitly **avoid Option B (background job + polling)** for now, to keep the system simpler until/unless real-world timeouts show that a queue/worker architecture is necessary.

**Files:** Infra config/docs for timeouts, [templates/onboarding.html](templates/onboarding.html) (copy and UX around “this may take a couple of minutes”), and possibly a small shared error/timeout template.

---

## Implementation approach: path to end goal

**Principle:** Consider **current state** and **end state** (all issues done), then implement in **batches that share the same code**, so we don’t repeatedly open the same files for “issue 1, then issue 2.” Still **incremental and testable** after each batch; release as **one feature** at the end.

**Why not strict issue-by-issue:** Many issues touch the same surfaces (e.g. `plan_routes.py` onboarding + `generate_plan`, `onboarding.html`, `plan_prompt.txt`). Doing “#88 only” then “#110 only” would mean editing the onboarding route and template twice. Batching by **code surface / flow** reduces churn and keeps each change coherent.

**Suggested path (by code surface):**


| Batch                                            | What we’re changing                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | Issues                                                        | Test after                                                                      |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| **1. Week dates**                                | Add `utils/week_dates.py` (or equivalent) to compute week ranges from `plan_start_date` + `weeks_until_goal` + partial week. Fix year rollover in [utils/migration.py](utils/migration.py) and [utils/simple_plan_parser.py](utils/simple_plan_parser.py) when parsing “Month Day – Month Day”.                                                                                                                                                                                                                                                                                                                   | #84 (fallback parsing), and the week-date logic #107 will use | Parsers and week-date helper; optional quick test with existing plan flow       |
| **2. Onboarding (one pass)**                     | [routes/plan_routes.py](routes/plan_routes.py) onboarding view: build `training_metrics_prefill` from `user_data`, pass to template; in `generate_plan()` read and persist `sports`, pass `included_sports` into `user_inputs` and `final_data_for_ai`. [templates/onboarding.html](templates/onboarding.html): prefill LTHR/FTP (and optional VDOT display), add sports checkboxes. [utils/vdot_context.py](utils/vdot_context.py): expose `vdot_is_recent` (or similar). [prompts/plan_prompt.txt](prompts/plan_prompt.txt): add `included_sports` line and VDOT recency / “schedule VDOT test if old” wording. | #88, #110                                                     | Onboarding form load + submit; new plan generation uses sports and VDOT wording |
| **3. Plan payload + prompt (trim and calendar)** | [routes/plan_routes.py](routes/plan_routes.py): build trimmed `final_data_for_ai` (summary of activities, not full list; minimal athlete_stats/strava_zones). Compute week calendar via batch 1 helper and add to payload. [prompts/plan_prompt.txt](prompts/plan_prompt.txt): use trimmed structure; refer to “recent training summary” and week calendar. Prompt still asks for **markdown** output for now.                                                                                                                                                                                                    | #105                                                          | Plan generation still works; smaller prompt, correct week dates in payload      |
| **4. JSON-first plan generation**                | [prompts/plan_prompt.txt](prompts/plan_prompt.txt): require **JSON** as primary output (plan_v2 shape), with precomputed week dates in the prompt. [services/ai_service.py](services/ai_service.py): parse JSON, validate, overwrite week dates from precomputed calendar, generate markdown from plan_v2; fallback to existing parser + apply week dates. [routes/plan_routes.py](routes/plan_routes.py): pass week calendar into AI, handle response (already uses ai_service).                                                                                                                                 | #107, #68 Fix 1                                               | New plan returns valid plan_v2 with correct week count and dates                |
| **5. Long-request UX**                           | Per plan: Option A (document/timeout) and/or Option C (loading copy). Option B (background job) only if needed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | #106                                                          | User sees clear messaging; timeouts reduced if infra updated                    |


**Incremental testing:** After batches 2, 3, and 4, run through “onboarding → generate plan → view plan” (and, where relevant, a plan that spans Dec→Jan or has multiple sports). Batch 1 can be tested via unit-style checks or by generating a plan and inspecting stored week dates.

**One feature release:** Single feature branch, one VERSION bump, one CHANGELOG release section listing all user-facing changes.

---

## Branch and versioning

Per [.cursor/rules/versioning.mdc](.cursor/rules/versioning.mdc): do all work on a feature branch (not `main`). Bump **VERSION** and add **CHANGELOG.md** under `[Unreleased]` for deployable changes (e.g. "Plan generation now outputs JSON with server-computed week dates", "Onboarding pre-fills LTHR/FTP/VDOT and allows sports selection", "Fixed week dates when plan spans December to January", "Reduced plan prompt token usage", "Improved handling of long plan generation requests").