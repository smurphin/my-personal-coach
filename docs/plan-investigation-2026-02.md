# Plan investigation (Feb 2026) – what changed and how to restore

## From chat and feedback logs (dump 2026-02-05)

**Chat (last 20 messages, newest first):**

- **Model:** "I'm adjusting the remaining S&C session for this week to make it more achievable" with a "Plan Update Summary". **This is the update that overwrote the plan without archiving** (dashboard chat does not archive). The **intended change** was only: adjust the remaining S&C session for the current week to be more achievable (shorter/easier).
- User: S&C full body is 30 mins as prescribed; did 2 sets instead of 3 (8 mins per set).
- User: Unhappy with feedback (fatigue vs longer strength); didn’t have time for full 45 mins.
- Model: XC race marked as not done / rest day (son ill).
- Other: power zones, semantics, etc. – no plan structure change.

**Feedback (last 20):** All are post-activity feedback entries (activity_id, date, name). None in this window indicate “replace whole plan”; they’re session-level feedback. The 28 Jan archive entry (`regenerated_via_feedback_json`) is from an earlier feedback that did archive correctly.

## What actually got changed (vs what should have)

- **Intended:** One small change – “adjust the remaining S&C session for this week to be more achievable.”
- **What happened:** The model returned a full `plan_v2` (likely truncated to 6 weeks with fewer sessions in weeks 1–2). The dashboard applied it without archiving, so the previous good 10-week plan was lost.

We don’t have the exact plan_v2 the AI returned, so we can’t programmatically merge “good snapshot + that one S&C tweak” in a reliable way.

## Merge / restore strategy

1. **Restore the good snapshot**  
   Use archive **index 0** (2026-01-28, 10 weeks, 8 sessions in week 1, 7 in week 2). That’s the only full plan we have.

2. **Re-apply the intended change**  
   After restore, in chat ask once: e.g. “Can you make the remaining S&C session this week more achievable?” With dashboard chat now archiving before apply, that update will be safe and you’ll have a new archive entry if anything goes wrong.

3. **No automated merge**  
   Doing an automated merge (good snapshot + “S&C more achievable”) would require either the exact AI response or a patch format; we don’t have the former and the latter isn’t implemented yet. So restore + one re-request is the reliable path.

## Fix applied

- **Archive before plan update in dashboard chat** (JSON and markdown paths) so every plan replace is archived first. Same pattern as feedback and api_routes.
