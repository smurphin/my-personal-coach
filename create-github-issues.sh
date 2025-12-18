#!/bin/bash
# Create GitHub issue for Weekly Summary Cache Optimization

REPO="smurphin/my-personal-coach"
TITLE="Weekly Summary Cache Optimization"
LABELS="enhancement,infrastructure"

BODY=$(cat <<'EOF'
## 📋 Overview

Implement smarter caching for weekly summary generation that reduces unnecessary AI API calls while ensuring summaries use the latest Garmin health data.

**Problem:** Current implementation refreshes every 6 hours regardless of whether new data exists.

**Solution:** Only regenerate when NEW Garmin data is received or cache is >12 hours old.

## 🎯 Expected Impact

- Cache hit rate: 40% → 70%
- AI calls/day/user: 4-6 → 2-3
- Cost savings: 37.5% reduction

## 📊 Implementation

### Phase 1: Data Version Tracking (2-3h)

Track when data sources were last updated:

```python
'weekly_summary': {
    'content': '...',
    'generated_at': '2025-12-14T08:00:00Z',
    'data_versions': {
        'garmin_fetched_at': '2025-12-14T08:00:00Z',
        'plan_hash': 'abc123',
        'last_feedback_id': 'act_789'
    }
}
```

### Phase 2: Smart Cache Logic (3-4h)

```python
if garmin_fetched_at > summary_garmin_version:
    regenerate = True  # New data available
elif age_hours > 12:
    regenerate = True  # Too stale
else:
    use_cache = True  # No new data, still fresh
```

## ✅ Tasks

- [ ] Add `fetched_at` to garmin_cache
- [ ] Add `data_versions` to weekly_summary  
- [ ] Implement smart regeneration logic
- [ ] Update cache duration 6h → 12h
- [ ] Add cache decision logging
- [ ] Test: new Garmin data triggers regeneration
- [ ] Test: no new data uses cache

**Estimate:** 1-2 days  
**Priority:** High
EOF
)

echo "Creating issue in $REPO..."

gh issue create \
    --repo "$REPO" \
    --title "$TITLE" \
    --body "$BODY" \
    --label "$LABELS"

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Issue created successfully!"
else
    echo ""
    echo "❌ Failed to create issue"
    exit 1
fi