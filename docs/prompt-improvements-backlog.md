# Prompt improvements – future task backlog

This doc is for **planning and prioritising** prompt/format improvements. Items here are **not to implement yet** unless explicitly scheduled.

---

## 1. Strategy: JSON-first, markdown as payload

**View to keep in mind when designing prompts and response formats:**

- **Prefer JSON as the primary contract** between app layers (planning, feedback, updates, session matching). Structured data is easier to reason about, validate, and evolve than free-form markdown.
- **Treat markdown as a payload field inside JSON**, not as the transport format. Example: `feedback_text` is a string whose *content* is markdown (headings, tables), but the *response* is always a JSON object.
- **Phase out markdown as the outer format** where possible. The user never sees the prompt directly, so optimise for machine readability and stable parsing.
- **Do not rely on the model to emit 100% valid JSON.** Long natural-language inside JSON strings will always be a weak point. Keep robust extraction/fallbacks (e.g. structure-based extraction for `feedback_text`) so that occasional malformed JSON does not lose data or break flows.
- **Summary:** JSON as the API shape; markdown only inside designated string fields; strong parsing and recovery so that perfect model output is aspirational, not required.

---

## 2. Backlog item: Feedback response schema refinement (Point 4 proposal)

**Goal:** Make the feedback JSON contract clearer and easier for the model to follow, and make plan-update behaviour more explicit, **without changing current behaviour or losing working plan-update functionality.**

### 2.1 Optional `mode` field (advisory at first)

- **Idea:** Encourage (do not yet require) the model to output a `mode` that declares intent:
  - `"NO_PLAN_CHANGE"` – feedback only; no structural plan change.
  - `"PLAN_UPDATE"` – feedback plus updated `plan_v2` and `change_summary_markdown`.
- **Example shape:**
  ```json
  {
    "mode": "NO_PLAN_CHANGE",
    "feedback_text": "...",
    "plan_v2": null,
    "change_summary_markdown": null
  }
  ```
  or
  ```json
  {
    "mode": "PLAN_UPDATE",
    "feedback_text": "...",
    "plan_v2": { ... },
    "change_summary_markdown": "Brief summary..."
  }
  ```
- **Benefits:** Removes ambiguity about whether `plan_v2` is present and authoritative; allows future extensions (e.g. `PLAN_SUGGESTION_ONLY`, `MINOR_TWEAK`) without changing core fields.
- **Implementation approach (low risk):**
  - **Prompt-only first:** Describe `mode` in the feedback prompt as recommended but optional; show both modes in examples.
  - **Runtime:** In extraction/generate_feedback, treat `mode` as advisory only:
    - If `mode` is missing → behave exactly as today.
    - If `mode` is `"NO_PLAN_CHANGE"` and `plan_v2` is present but malformed → ignore stray `plan_v2`, keep feedback.
    - If `mode` is `"PLAN_UPDATE"` but `plan_v2` fails validation → log clearly; do not apply plan update; still return feedback.
  - No change to existing validation or persistence logic; `mode` is extra signal when the model cooperates.

### 2.2 Tighten prompt-side `plan_v2` example (no schema change)

- **Idea:** Keep existing `plan_v2` validation and behaviour in code. In the feedback prompt only:
  - Shorten the example to a minimal valid object: one week, one session, required fields only.
  - Keep detailed constraints in a bullet list (session types, priorities, id format, day rules, etc.) so the model has one clear “shape” to copy and one clear set of rules.
- **Goal:** Same functionality, easier for the model to produce valid `plan_v2` without being overwhelmed by a long example.

### 2.3 Future (optional): diff-style plan updates

- **Idea (later):** Add an optional, separate field for incremental updates, e.g.:
  ```json
  {
    "mode": "PLAN_UPDATE",
    "feedback_text": "...",
    "plan_v2_diff": {
      "weeks_to_modify": [ ... ],
      "sessions_to_add": [ ... ],
      "sessions_to_cancel": [ ... ]
    },
    "change_summary_markdown": "..."
  }
  ```
  while still allowing full `plan_v2` for now.
- **When:** Only if full-plan rewrites prove fragile or expensive; not part of the first refinement pass.

### 2.4 Acceptance criteria (when implementing)

- [ ] Single canonical response format described in the prompt (“this format”, not “one of these formats”).
- [ ] Optional `mode` documented in prompt and optionally parsed; behaviour unchanged when `mode` is missing or invalid.
- [ ] Plan-update flow (validation, persistence, UI) unchanged; no regressions.
- [ ] Prompt example for `plan_v2` minimal and consistent with validator; rules remain in bullet form.
- [ ] CHANGELOG and version bump when shipping.

---

## 3. Related

- Feedback extraction robustness: structure-based extraction and removal of ```json from context are in place; see CHANGELOG and `utils/plan_validator.py`, `prompts/feedback_prompt.txt`.
- For other prompt ideas and backlog items, add them under new numbered sections in this file.
