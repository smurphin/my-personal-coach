# Splits vs Laps Logic for Interval Detection

## Overview

This document explains the logic for detecting interval sessions and choosing between **laps** (manual lap button presses/workout-defined intervals) vs **splits** (auto-laps at fixed distances like 1km/1mile) for activity analysis.

## The Problem

For interval sessions like "6 x 3min interval pace, 3min easy":
- **Laps** = 18 segments (warmup + 6 intervals + 6 recoveries + cooldown) - **CORRECT for analysis**
- **Splits** = 12 segments (1km auto-laps) - **WRONG**, splits cut across intervals and give meaningless data

If we analyze splits for an interval session, we get incorrect results (e.g., analyzing a 1km split that contains 0.3km of interval + 0.7km of recovery).

## Data Flow

### 1. Fetching Activity Data

**Location:** `routes/feedback_routes.py` and `routes/api_routes.py`

```python
# Step 1: Get activity detail (includes splits, may include laps)
activity = strava_service.get_activity_detail(access_token, activity_summary['id'])

# Step 2: Check if activity detail has laps
activity_laps_from_detail = activity.get('laps') or []

# Step 3: If activity detail has 0 or 1 lap, try dedicated endpoint
if len(activity_laps_from_detail) <= 1:
    activity_laps = strava_service.get_activity_laps(access_token, activity['id'])
    if activity_laps and len(activity_laps) > len(activity_laps_from_detail):
        activity['laps'] = activity_laps  # Override with data from dedicated endpoint
```

**Why:** The activity detail endpoint usually includes laps, but sometimes doesn't. The dedicated `/activities/{id}/laps` endpoint is more reliable as a fallback.

### 2. Processing Activity Data

**Location:** `services/training_service.py` - `analyze_activity()` method

```python
# Extract raw data from Strava response
splits_metric = activity.get("splits_metric") or []      # 1km auto-laps
splits_standard = activity.get("splits_standard") or []  # 1 mile auto-laps
laps = activity.get("laps") or []                        # Manual laps/workout intervals

# Create summaries (strips down to essential fields for AI prompt)
analyzed["splits_metric_summary"] = self._summarize_segments(splits_metric, kind="splits_metric")
analyzed["splits_standard_summary"] = self._summarize_segments(splits_standard, kind="splits_standard")
analyzed["laps_summary"] = self._summarize_segments(laps, kind="laps")
```

**What `_summarize_segments()` does:**
- Takes raw Strava lap/split objects
- Extracts: distance (m, km, miles), time (s), pace, HR, pace_zone
- Formats distances to 2 decimal places (0.41 km, 0.56 miles)
- Caps at 60 segments to avoid huge prompts
- Returns: `{kind, count, truncated, segments: [...]}`

### 3. Interval Detection Logic

**Location:** `services/training_service.py` - lines 269-360

The detection compares **laps vs splits** to determine if it's an interval session:

#### Detection Method 1: Count Mismatch (Strongest Signal)

```python
if len(laps_segments) != len(splits_segments):
    is_interval_session = True
    detection_method = "laps_vs_splits_count_mismatch"
```

**Example:** 18 laps vs 12 splits = interval session

#### Detection Method 2: Time Consistency (Time-Based Intervals)

```python
# Check if lap times are more consistent than split times
lap_time_std = self._calculate_std(lap_times)
split_time_std = self._calculate_std(split_times)

# If lap times are much more consistent (lower std dev), likely time-based intervals
if lap_time_std < split_time_std * 0.7:
    is_interval_session = True
    detection_method = "laps_vs_splits_time_consistency"
```

**Why:** For "6 x 3min intervals", laps will have consistent ~180s times, while splits will vary (some fast, some slow as they cut across intervals).

#### Detection Method 3: Distance Mismatch

```python
# Compare distances - if they differ significantly, likely intervals
for lap, split in zip(laps_segments[:10], splits_segments[:10]):
    if abs(lap_dist - split_dist) / max(lap_dist, split_dist) > 0.10:
        is_interval_session = True
        detection_method = "laps_vs_splits_distance_mismatch"
        break
```

**Why:** For time-based intervals, lap distances vary (e.g., 700m interval, 400m recovery) while splits are consistent (~1000m).

#### Detection Method 4: Pattern Detection (Fallback)

If no splits available, check if lap times suggest intervals:

```python
# Check if lap times are relatively consistent (within 20% of median)
# This suggests time-based intervals rather than distance-based
median_time = sorted(lap_times)[len(lap_times) // 2]
consistent_count = sum(1 for t in lap_times if abs(t - median_time) / median_time < 0.20)
if consistent_count >= len(lap_times) * 0.6:  # 60% of laps within 20% of median
    is_interval_session = True
    detection_method = "pattern_detection_fallback"
```

### 4. Setting Preferred Segment Summary

**Location:** `services/training_service.py` - lines 361-385

```python
if is_interval_session and has_laps:
    # For interval sessions, prioritize laps
    analyzed["preferred_segment_summary"] = "laps_summary"
    analyzed["preferred_segment_reason"] = "Interval session detected - laps differ from splits (manual lap button presses)"
elif has_laps and (has_splits_metric or has_splits_standard):
    # If laps exist but match splits, it's a standard run - use splits (more consistent)
    analyzed["preferred_segment_summary"] = "splits_metric_summary" if has_splits_metric else "splits_standard_summary"
    analyzed["preferred_segment_reason"] = "Standard run - laps match splits (auto-laps), using splits for consistency"
```

**Logic:**
- **Interval session** → Use `laps_summary`
- **Standard run** (laps = splits) → Use `splits_metric_summary` or `splits_standard_summary`
- **No laps** → Use splits

### 5. Unit Preference Detection

**Location:** `services/training_service.py` - `_detect_unit_preference()`

```python
# Check first few segments - if they're ~1600m, likely miles; if ~1000m, likely km
avg_distance = sum(sample_distances) / len(sample_distances)
# If average is closer to 1 mile (1609m) than 1km (1000m), prefer miles
return abs(avg_distance - 1609.34) < abs(avg_distance - 1000.0)
```

Sets `analyzed["distance_unit_preference"]` to "km" or "miles" for consistent formatting.

## AI Prompt Instructions

**Location:** `prompts/feedback_prompt.txt`

### Critical Instructions (Multiple Places)

#### 1. At Start of Session Analysis Section

```
**⚠️ BEFORE YOU START ANALYZING - CHECK THE DATA SOURCE:**
- If `intervals_detected.has_intervals` = true OR `preferred_segment_summary` = "laps_summary": Use `laps_summary` segments, NOT splits
- If `preferred_segment_summary` = "splits_metric_summary" or "splits_standard_summary": Use splits (standard run)
- **DO NOT analyze splits for interval sessions - you will get wrong results**
```

#### 2. Right After Completed Sessions JSON

```
**🚨🚨🚨 CRITICAL: BEFORE ANALYZING - READ THIS FIRST 🚨🚨🚨**

**STEP 1: Check if this is an interval session:**
- Look for `intervals_detected.has_intervals` = true
- Look for `preferred_segment_summary` = "laps_summary"  
- Look for session name containing "interval", "repeat", "x 3min", "6x", "3 min", etc.

**STEP 2: If it's an interval session, you MUST:**
- ✅ **FIRST:** Check `laps_summary.count` - this is the NUMBER OF LAPS available. Look at the actual number, don't assume.
- ✅ **IF `laps_summary.count > 1`:** You have multiple laps! Use `laps_summary.segments` array for your analysis.
- ✅ Create a table with header "Lap Analysis" or "Interval Analysis" showing ALL laps from `laps_summary.segments`
- ❌ DO NOT use `splits_metric_summary` or `splits_standard_summary` for interval sessions
- ❌ DO NOT say "single lap" if `laps_summary.count > 1` - check the actual count value!
```

#### 3. Detailed Instructions in Session Analysis

```
**🚨 CRITICAL - LAPS vs SPLITS for Interval Sessions - YOU MUST FOLLOW THIS:**

**STEP 1: Check for intervals:**
- Look at `intervals_detected.has_intervals` - if true, this is an interval session
- Look at `preferred_segment_summary` - if it says "laps_summary", use laps
- Look at the session name/description - if it mentions "interval", "repeat", "x 3min", etc.

**STEP 2: Choose the correct data source:**
- **IF intervals detected OR preferred_segment_summary = "laps_summary":**
  - ✅ USE `laps_summary.segments` for your analysis
  - ❌ DO NOT use `splits_metric_summary` or `splits_standard_summary`
  - ❌ DO NOT create tables showing "Split" - use "Lap" instead

**STEP 3: Understand the difference:**
- **Laps** = Manual lap button presses OR workout-defined intervals (e.g., 3-minute intervals)
- **Splits** = Auto-laps at fixed distances (e.g., every 1km or 1 mile)
- For interval sessions, laps represent the actual workout structure
- Splits will cut across intervals and recoveries, giving meaningless data

**EXAMPLE - "6 x 3min interval pace, 3min easy":**
- ✅ CORRECT: Use `laps_summary` which will have ~18 laps (warmup + 6 intervals + 6 recoveries + cooldown)
- ❌ WRONG: Using `splits_metric_summary` which will have ~12 splits (1km auto-laps that cut across intervals)
```

### Distance Formatting Instructions

```
**DISTANCE FORMATTING:** When displaying distances in tables or analysis:
- **Check `distance_unit_preference`** in the activity data - it will be "km" or "miles"
- **Choose ONE unit system per feedback** (either all km OR all miles) and use it consistently
- **Format partial distances as decimals:** 410.7m = 0.41 km (or 0.26 miles), 1000.5m = 1.00 km (or 0.62 miles)
- Always round to 2 decimal places
- Use the `distance_km` or `distance_miles` fields from segment data
```

## Data Structure Sent to AI

The `analyzed_sessions` JSON sent to the AI includes:

```json
{
  "id": 16612116519,
  "name": "⬆️ - 6 x 3mins @ i -  3 mins E - ⬇️",
  "distance": 11148.6,
  "moving_time": 3861,
  "laps_summary": {
    "kind": "laps",
    "count": 18,
    "truncated": false,
    "segments": [
      {
        "index": 1,
        "name": "Lap 1",
        "distance_m": 1000.0,
        "distance_km": 1.00,
        "distance_miles": 0.62,
        "elapsed_time_s": 335,
        "average_speed_mps": 2.99,
        "pace_s_per_km": 335.0,
        "average_heartrate": 132.0,
        "pace_zone": 2
      },
      // ... 17 more laps
    ]
  },
  "splits_metric_summary": {
    "kind": "splits_metric",
    "count": 12,
    "segments": [...]
  },
  "intervals_detected": {
    "has_intervals": true,
    "detection_method": "laps_vs_splits_count_mismatch"
  },
  "preferred_segment_summary": "laps_summary",
  "preferred_segment_reason": "Interval session detected - laps differ from splits (manual lap button presses)",
  "distance_unit_preference": "km"
}
```

## Example: "6 x 3min interval pace, 3min easy"

### What the Data Looks Like

**Laps (18 total):**
- Lap 1: Warmup (1000m, 335s)
- Lap 2: Warmup (1000m, 357s)
- Lap 3: Warmup (536m, 209s)
- Lap 4: Interval 1 (709m, 180s) ← 3min interval
- Lap 5: Recovery 1 (447m, 180s) ← 3min easy
- Lap 6: Interval 2 (681m, 180s)
- Lap 7: Recovery 2 (387m, 180s)
- ... (continues for 6 intervals + 6 recoveries)
- Lap 18: Cooldown (306m, 109s)

**Splits (12 total - 1km auto-laps):**
- Split 1: 1000m, 335s (contains part of warmup)
- Split 2: 1000m, 357s (contains part of warmup)
- Split 3: 1002m, 327s (contains part of warmup + start of interval 1)
- Split 4: 998m, 326s (contains end of interval 1 + start of recovery 1)
- ... (splits cut across intervals and recoveries)

### Detection Result

```
intervals_detected.has_intervals = true
detection_method = "laps_vs_splits_count_mismatch"  # 18 laps ≠ 12 splits
preferred_segment_summary = "laps_summary"
```

### AI Should Do

1. Check `intervals_detected.has_intervals` = true ✅
2. Check `preferred_segment_summary` = "laps_summary" ✅
3. Check `laps_summary.count` = 18 ✅
4. Use `laps_summary.segments` (18 laps) for analysis ✅
5. Create table with header "Lap Analysis" ✅
6. Show each of the 18 laps with distance, time, pace, HR ✅
7. **DO NOT** use `splits_metric_summary` ❌

## Key Design Decisions

### 1. Why Compare Laps vs Splits?

**Safer than pattern detection alone:**
- Pattern detection (speed variations) can be fooled by hills, wind, etc.
- Comparing laps vs splits is more reliable: if they differ, it's likely intervals

### 2. Why Multiple Detection Methods?

**Handles different scenarios:**
- **Count mismatch:** Works for most interval sessions (18 laps vs 12 splits)
- **Time consistency:** Catches time-based intervals when count matches (rare edge case)
- **Distance mismatch:** Catches when intervals don't align with 1km splits
- **Pattern fallback:** Works when no splits available

### 3. Why Set `preferred_segment_summary`?

**Gives AI clear guidance:**
- AI doesn't have to figure out which to use
- Reduces chance of AI choosing wrong data source
- Makes debugging easier (can see why a choice was made)

### 4. Why Multiple Prompt Warnings?

**AI sometimes ignores instructions:**
- Multiple warnings at different points in prompt
- Explicit step-by-step instructions
- Clear examples of right vs wrong
- Emphasizes consequences of wrong choice

## Debugging

### Logs to Check

1. **Activity segment data:**
   ```
   📊 Activity {id} segment data:
      Laps from detail: 18
      Laps from endpoint: 0
      Laps to use: 18
      Splits metric: 12
   ```

2. **Interval detection:**
   ```
   🔍 INTERVAL SESSION DETECTED:
      Laps count: 18
      Splits metric count: 12
      Preferred segment: laps_summary
      Detection method: laps_vs_splits_count_mismatch
   ```

3. **Lap fetching:**
   ```
   ✅ Activity detail has 18 laps - using those
   ```
   OR
   ```
   ✅ Fetched 18 laps from /activities/{id}/laps endpoint (detail had 1)
   ```

### Common Issues

1. **AI says "single lap" when there are 18:**
   - Check if `laps_summary.count` is actually 18 in the JSON
   - AI might be misreading the data structure
   - Solution: More explicit instructions to check `laps_summary.count`

2. **AI uses splits instead of laps:**
   - Check if `preferred_segment_summary` is set correctly
   - Check if `intervals_detected.has_intervals` is true
   - Solution: More prominent warnings in prompt

3. **Detection doesn't work:**
   - Check if laps and splits both exist
   - Check if they have different counts
   - Check logs for detection method used

## Testing

See `test_splits_laps_analysis.py` for regression tests that verify:
- Laps/splits summaries are created correctly
- Interval detection works
- Distance formatting is correct

