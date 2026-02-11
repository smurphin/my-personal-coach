# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Onboarding: choose which sports to include in your plan (Run, Bike, Swim, Strength, Other).
- Onboarding: LTHR, FTP, and VDOT are pre-filled from your saved metrics when you create another plan.
- Plan: if you have a recent VDOT (last 4 weeks) it is used for paces; otherwise the plan suggests a VDOT test in early weeks.
- Plan and chat: a short fitness assessment runs before plan generation and is used to shape the plan and coach replies.
- Chat: when Garmin is connected, recent HRV, sleep, and body battery trends can inform the coach’s answers.

### Changed

- Plan: S&C sessions only appear when you selected Strength at onboarding.
- Onboarding: sports choices sit between goal date and sessions per week.
- Chat: the coach only adds or changes sessions for the sports you selected (e.g. no bike on a run-only plan unless you ask).
- Feedback: suggested plan changes stay within your selected sports.
- Plan: Improviser plans now include at least one or two optional (STRETCH) sessions per week.
- Plan: the overview at the top can include the full zones and VDOT tables again instead of being cut off.
- Plan: a “zones differ” note under the HR table only appears when the difference is more than a couple of beats, not for small rounding or Zone 3.
- Dashboard: you see the dashboard when you have an active plan, not only when the legacy plan field is set.
- Maintenance plan: uses the same generation as the main plan.

### Fixed

- Week dates for plans that span December into January now show the correct year for each week.
- Plan view: the overview at the top no longer truncates the zones and VDOT tables.
- Plan: the first week of a plan now starts on the chosen start date and is no longer missing.
- Plan: the coach does not schedule sessions on dates you listed as no-training in upcoming commitments.
- Chat: Garmin data for context no longer errors when the coach replies.

## [0.1.7] - 2026-02-06

### Changed

- Feedback: coach response format was simplified so feedback text is returned reliably.

### Fixed

- Feedback: full coach feedback is kept even when the response format is slightly wrong.
- Strava: activities outside the recent week or missed by sync are now included when processing feedback.
- Feedback: quotes in coach feedback now display correctly in the UI.

## [0.1.6] - 2026-02-06

### Added

- Feedback: cycling analysis no longer treats HR vs power zone mismatch as an error.
- Feedback: when power is missing, cycling analysis uses HR only instead of suggesting a broken power meter.
- Docs: added guide on cycling zones (HR vs power) and when mismatch is expected.

### Changed

- Feedback: cycling feedback uses principle-based guidance and a coach-like tone.

### Fixed

- Config: thinking level (LOW/MEDIUM/HIGH) for Gemini is now applied correctly from environment settings.

## [0.1.5] - 2026-02-05

### Added

- Admin: plan archive page to list and restore plan snapshots, visible only to configured admin athletes.
- Admin: API to list and restore plan archives per athlete, protected by secret.

### Changed

- Feedback: coach uses the same plan source for comparison and updates as the feedback page.
- Feedback: Disciplinarian plans show day and date; Minimalist and Improviser keep "Anytime".

### Fixed

- Feedback: full coach feedback is kept when the response is malformed.
- Plan: current plan is archived before applying chat or updates so the previous version can be restored.
- Strava webhook: coach compares against the same plan and profile as the feedback page.
- Plan: when merging past weeks into a new plan, duplicate weeks and wrong Week 0 are no longer produced.

## [0.1.1] - 2026-02-04

### Fixed

- Feedback: coach feedback is no longer cut off when the response contains unescaped quotes.

## [0.1.0] - 2026-02-03

### Added

- Version tracking for deployments (VERSION file, Docker build-arg, `/version` endpoint)
- Deploy script with targets: staging, prod, beta, mark, shane, dom, all
- Changelog (this file)
- Runtime config via Secrets Manager: AI_MODEL, AI_TEMPERATURE, AI_MAX_OUTPUT_TOKENS, AI_THINKING_LEVEL (Gemini 3 only), WEBHOOK_DELAY_SECONDS—tweak per env without code deploy
