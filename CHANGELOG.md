# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.5] - 2026-02-05

### Added

- Plan archive UI at `/admin/plan_archive` to list and restore snapshots; visibility gated by `ADMIN_ATHLETE_IDS` when set.
- Plan archive API: `GET /admin/api/plan_archive` and `POST /admin/api/restore_plan_archive` for listing/restoring any tenant by `athlete_id`, protected by `FEEDBACK_TRIGGER_SECRET`.

### Changed

- Feedback prompt: plan_v2 JSON is the source of truth for comparison and plan updates; day/date only for Disciplinarian athletes, preserve "Anytime" for Minimalist and Improviser.

### Fixed

- Use structure-based extraction for `feedback_text` when AI returns malformed JSON so full feedback is kept; prompt updated to remind model to escape quotes in JSON strings.
- Archive current plan before applying chat (JSON or markdown) or reparse updates so the previous good state is never lost to overwrite.
- Webhook feedback uses plan_v2 and athlete_profile when available so the AI compares against the same source of truth as the feedback page.
- Plan merge when prepending archived past weeks: drop the first N weeks of the AI plan by count of past weeks (not max week number) so weeks 1–2 and Week 0 are not duplicated; preserve Week 0 when renumbering after merge.

## [0.1.1] - 2026-02-04

### Fixed

- Prevent JSON feedback extraction from truncating `feedback_text` when the AI returns malformed JSON (e.g. unescaped quotes inside code blocks).

## [0.1.0] - 2025-02-03

### Added

- Version tracking for deployments (VERSION file, Docker build-arg, `/version` endpoint)
- Deploy script with targets: staging, prod, beta, mark, shane, dom, all
- Changelog (this file)
- Runtime config via Secrets Manager: AI_MODEL, AI_TEMPERATURE, AI_MAX_OUTPUT_TOKENS, AI_THINKING_LEVEL (Gemini 3 only), WEBHOOK_DELAY_SECONDS—tweak per env without code deploy
