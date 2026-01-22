# JSON-First Plan Generation Architecture

## Overview

This document describes the new JSON-first approach to plan generation and updates, designed to eliminate the brittleness of markdown parsing and make plan updates more reliable.

## Problem Statement

Previously, the app relied on:
1. AI generating markdown plans
2. Complex regex-based parsing to extract structured data
3. Frequent failures due to AI "creativity" in formatting

This led to:
- Missing sessions in `plan_v2`
- Lost session details
- Frequent re-parsing attempts
- Unreliable plan updates

## Solution: JSON-First Architecture

### Core Principle

**`plan_v2` is the single source of truth.** All plan updates should produce structured JSON directly, not markdown that needs parsing.

### Architecture Components

#### 1. JSON-First Plan Generation (`services/ai_service.py`)

The `generate_chat_response()` method now:
- Returns a tuple: `(response_text, plan_update_json, change_summary)`
- Extracts JSON from AI responses automatically
- Validates JSON before accepting it
- Falls back to markdown parsing only if JSON extraction fails

#### 2. JSON Validation (`utils/plan_validator.py`)

- `validate_plan_v2_json()`: Validates JSON structure
- `validate_and_load_plan_v2()`: Validates and loads into TrainingPlan object
- `extract_json_from_ai_response()`: Extracts JSON from AI responses (handles code blocks, extra text, etc.)

#### 3. Simplified Parser (`utils/simple_plan_parser.py`)

A format-agnostic fallback parser that:
- Strips markdown decoration
- Focuses on content, not formatting
- Extracts attributes from free text (duration, zones, priority)
- Is resilient to formatting variations

#### 4. Updated Parsing Strategy (`utils/migration.py`)

The `parse_ai_response_to_v2()` function now tries three strategies in order:

1. **JSON-first**: Extract `plan_v2` directly from JSON response
2. **Simple parser**: Use format-agnostic content extraction
3. **Complex parser**: Legacy regex-based markdown parsing (last resort)

### Updated Prompts

#### Chat Prompt (`prompts/chat_prompt.txt`)

Now includes:
- **JSON-first instructions** at the top (preferred method)
- Clear JSON schema with examples
- Fallback markdown instructions (for backward compatibility)
- Both `training_plan_json` and `training_plan` (markdown) in context

### Updated Routes

#### Chat Route (`routes/dashboard_routes.py`)

- Handles JSON plan updates first
- Preserves completed sessions via `archive_and_restore_past_weeks()`
- Stores change summary for display
- Falls back to markdown parsing if JSON not found

## Benefits

1. **Reliability**: JSON validation ensures plan structure is correct before saving
2. **Performance**: No need to parse markdown if JSON is available
3. **Resilience**: Three-tier fallback strategy (JSON → Simple → Complex)
4. **User Experience**: Shorter chat responses (just summary, not full plan)
5. **Maintainability**: Less dependency on markdown formatting rules

## Testing Strategy

### Test Case 1: JSON Plan Update via Chat

1. Send chat message: "I'm injured, need to reduce training"
2. **Expected**: AI returns JSON with updated plan
3. **Verify**: 
   - `plan_v2` updated in DynamoDB
   - Change summary displayed
   - Completed sessions preserved
   - No parsing errors in logs

### Test Case 2: Markdown Fallback

1. Send chat message that triggers plan update
2. **Expected**: If JSON fails, falls back to markdown parsing
3. **Verify**:
   - Plan still updates correctly
   - Logs show fallback strategy used
   - No data loss

### Test Case 3: Simple Parser Fallback

1. AI returns markdown but JSON extraction fails
2. **Expected**: Simple parser extracts sessions
3. **Verify**:
   - Sessions found even with non-standard formatting
   - Attributes (duration, zones) extracted from free text

## Migration Path

### Phase 1: Chat Updates (Current)
- ✅ Chat route uses JSON-first
- ✅ Fallback to markdown parsing

### Phase 2: Plan Generation (Future)
- Update `generate_training_plan()` to use JSON-first
- Update maintenance plan generation
- Update feedback-triggered plan updates

### Phase 3: Full Migration (Future)
- Remove markdown parsing entirely
- AI always returns JSON
- Markdown rendered from `plan_v2` for display only

## Monitoring

Watch for these log messages:

- `✅ Extracted valid plan_v2 update from chat response` - JSON success
- `⚠️  Extracted plan_v2 JSON but validation failed` - JSON invalid
- `✅ Parsed plan using simple format-agnostic parser` - Simple parser used
- `ℹ️  Using complex markdown parser (legacy method)` - Complex parser used

## Future Enhancements

1. **Plan Diffs**: Return only changes, not full plan
2. **Incremental Updates**: Update specific weeks/sessions only
3. **Validation Rules**: Add business logic validation (e.g., don't spike ACWR)
4. **Self-Check Prompts**: AI validates its own JSON before returning
