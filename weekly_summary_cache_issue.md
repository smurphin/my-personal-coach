# Weekly Summary Cache Optimization

## 📋 Overview

Implement smarter caching for weekly summary generation that reduces unnecessary AI API calls while ensuring summaries use the latest Garmin health data. Current implementation refreshes too frequently (every 6 hours) even when no new data is available.

**Primary Goal:** Only regenerate weekly summary when NEW Garmin data is received or cache is stale (>12 hours), not just because time has passed.

## 🎯 Problem Statement

**Current Behavior (Quick Fix):**
- Weekly summary regenerates every 6 hours regardless of new data
- No tracking of when Garmin data was last fetched
- Results in unnecessary AI API calls when user loads dashboard multiple times per day
- Cache invalidation based on time only, not data freshness

**Issues:**
1. **Over-generation:** User loads dashboard 3x in a day → 3 AI calls (wasteful)
2. **No data awareness:** Summary regenerates at 6-hour mark even if Garmin data hasn't updated
3. **Suboptimal caching:** Time-based only, doesn't consider data dependencies

**Example Scenario:**
```
08:00 - User loads dashboard
  → Garmin data fetched (cached from 00:00)
  → Weekly summary generated

10:00 - User loads dashboard again
  → Garmin data from cache (still from 00:00)
  → Weekly summary from cache (age: 2h) ✓ GOOD

14:30 - User loads dashboard again
  → Garmin data from cache (still from 00:00)
  → Weekly summary regenerated (age: 6.5h) ✗ WASTEFUL - same data!

20:00 - User loads dashboard again
  → Garmin data fetched fresh (new data from Garmin API)
  → Weekly summary from cache (age: 12h) ✗ WRONG - should regenerate!
```

## 🎯 Desired Behavior

**Smart Cache Logic:**
```
Regenerate summary ONLY when:
1. No cached summary exists
2. Cache is >12 hours old (absolute maximum)
3. New Garmin data fetched AFTER last summary generation
4. Manual force refresh requested
5. Training plan updated
6. New feedback/chat added

Otherwise: Use cached summary
```

## 🏗️ Technical Approach

### Core Concept: Data Version Tracking

Track WHEN data sources were last updated, not just when summary was generated.

```python
# DynamoDB User Item Schema
{
    'weekly_summary': {
        'content': 'Your weekly summary text...',
        'generated_at': '2025-12-14T08:00:00Z',
        
        # NEW: Track data versions used in this summary
        'data_versions': {
            'garmin_fetched_at': '2025-12-14T08:00:00Z',
            'plan_hash': 'abc123...',
            'last_feedback_id': 'activity_789',
            'last_chat_timestamp': '2025-12-13T18:30:00Z'
        }
    },
    
    'garmin_cache': {
        'data': {...},
        'fetched_at': '2025-12-14T08:00:00Z',  # When API was called
        'cached_at': '2025-12-14T08:00:00Z'     # When cache was stored
    }
}
```

### Decision Logic

```python
def should_regenerate_summary(user_data, force=False):
    if force:
        return True, "Manual force refresh"
    
    summary = user_data.get('weekly_summary', {})
    if not summary.get('content'):
        return True, "No existing summary"
    
    # Check age
    generated_at = datetime.fromisoformat(summary['generated_at'])
    age_hours = (datetime.utcnow() - generated_at).total_seconds() / 3600
    
    if age_hours > 12:
        return True, f"Summary is {age_hours:.1f} hours old (>12h threshold)"
    
    # Check if Garmin data is newer than summary
    garmin_cache = user_data.get('garmin_cache', {})
    garmin_fetched_at = garmin_cache.get('fetched_at')
    
    if garmin_fetched_at:
        summary_garmin_version = summary.get('data_versions', {}).get('garmin_fetched_at')
        
        if not summary_garmin_version:
            return True, "Summary missing Garmin data version tracking"
        
        garmin_time = datetime.fromisoformat(garmin_fetched_at)
        summary_garmin_time = datetime.fromisoformat(summary_garmin_version)
        
        if garmin_time > summary_garmin_time:
            return True, f"New Garmin data available (fetched {garmin_fetched_at})"
    
    return False, f"Using cache (age: {age_hours:.1f}h, all data current)"
```

## 📊 Implementation Phases

### Phase 1: Add Data Version Tracking (Backend)
**Goal:** Track when each data source was last updated

**Tasks:**
- [ ] Update `garmin_service.py` to track `fetched_at` timestamp
- [ ] Modify `weekly_summary_api()` to store `data_versions` with summary
- [ ] Add helper function `get_data_versions()` to collect all timestamps
- [ ] Update DynamoDB schema documentation

**Code Changes:**
```python
# In garmin_service.py
def fetch_and_cache_data(user_data):
    stats = fetch_from_garmin()
    
    cache_data = {
        'data': stats,
        'fetched_at': datetime.utcnow().isoformat() + 'Z',  # NEW
        'cached_at': datetime.utcnow().isoformat() + 'Z'
    }
    
    user_data['garmin_cache'] = cache_data
    save_user_data(user_data)

# In api_routes.py - weekly_summary_api()
def get_current_data_versions(user_data):
    """Collect timestamps of all data sources"""
    return {
        'garmin_fetched_at': user_data.get('garmin_cache', {}).get('fetched_at'),
        'plan_hash': hashlib.sha256(user_data['plan'].encode()).hexdigest(),
        'last_feedback_id': feedback_log[0]['activity_id'] if feedback_log else None,
        'last_chat_timestamp': chat_log[-1]['timestamp'] if chat_log else None
    }

# When saving summary:
user_data['weekly_summary'] = {
    'content': new_summary,
    'generated_at': now.isoformat() + 'Z',
    'data_versions': get_current_data_versions(user_data)  # NEW
}
```

### Phase 2: Implement Smart Cache Logic (Backend)
**Goal:** Only regenerate when data changes or cache is stale (>12h)

**Tasks:**
- [ ] Create `should_regenerate_summary()` function
- [ ] Update cache duration from 6h to 12h
- [ ] Implement data version comparison logic
- [ ] Add detailed logging for cache decisions
- [ ] Update API response to include regeneration reason

### Phase 3: Frontend Cache Status Display (Optional)
**Goal:** Show users why summary is cached or regenerated

**Tasks:**
- [ ] Add cache status badge to weekly summary widget
- [ ] Show "Last updated: X hours ago (fresh data)" or "(cached)"
- [ ] Display data freshness indicator
- [ ] Add tooltip explaining cache behavior

### Phase 4: Monitoring & Optimization
**Goal:** Track cache effectiveness and optimize

**Tasks:**
- [ ] Add metrics logging (cache hits/misses, regeneration reasons)
- [ ] Track average summary age at regeneration
- [ ] Monitor AI API call frequency per user
- [ ] Add CloudWatch dashboard for cache metrics

## 📊 Expected Impact

### Metrics Comparison

**Before Optimization (Current Quick Fix):**
- Cache duration: 6 hours
- AI calls/day/user: ~4-6 (every 6h)
- Cache hit rate: ~40%
- Over-generation: High (time-based only)

**After Full Implementation:**
- Cache duration: 12 hours (maximum)
- AI calls/day/user: ~2-3 (data-driven)
- Cache hit rate: ~70%
- Over-generation: Low (data-aware)

### Cost Savings

Assuming 100 active users:
- Current: 100 users × 4 calls/day = 400 AI calls/day
- Optimized: 100 users × 2.5 calls/day = 250 AI calls/day
- **Savings: 37.5% reduction in AI API costs**

### Storage Impact

- Storage increase per user: ~200 bytes (negligible)
- DynamoDB writes: 50% REDUCTION (fewer regenerations)
- Net cost: Small savings due to fewer write units

## 🧪 Testing Strategy

### Unit Tests

```python
def test_should_regenerate_no_summary():
    """Test regeneration when no summary exists"""
    user_data = {'plan': 'test'}
    assert should_regenerate_summary(user_data) == (True, "No existing summary")

def test_should_use_cache_no_new_data():
    """Test cache usage when no new Garmin data"""
    base_time = datetime.utcnow() - timedelta(hours=8)
    
    user_data = {
        'weekly_summary': {
            'content': 'summary',
            'generated_at': base_time.isoformat(),
            'data_versions': {
                'garmin_fetched_at': base_time.isoformat()
            }
        },
        'garmin_cache': {
            'fetched_at': base_time.isoformat()  # Same time = no new data
        }
    }
    
    result, reason = should_regenerate_summary(user_data)
    assert result == False
    assert "cache" in reason.lower()

def test_should_regenerate_new_garmin_data():
    """Test regeneration when new Garmin data available"""
    old_time = datetime.utcnow() - timedelta(hours=4)
    new_time = datetime.utcnow()
    
    user_data = {
        'weekly_summary': {
            'content': 'summary',
            'generated_at': old_time.isoformat(),
            'data_versions': {
                'garmin_fetched_at': old_time.isoformat()
            }
        },
        'garmin_cache': {
            'fetched_at': new_time.isoformat()  # NEW data available
        }
    }
    
    result, reason = should_regenerate_summary(user_data)
    assert result == True
    assert "New Garmin data" in reason
```

### Integration Tests

1. **Fresh Garmin Data Triggers Regeneration:**
   - Load dashboard at 08:00 → Summary generated
   - Manually refresh Garmin at 10:00 (force fetch)
   - Load dashboard at 10:05 → Summary regenerates (new Garmin data)

2. **No New Data = Cache:**
   - Load dashboard at 08:00 → Summary generated
   - Load dashboard at 14:00 → Uses cache (no new Garmin data)
   - Load dashboard at 19:00 → Uses cache (still no new data, 11h passed)

3. **12-Hour Maximum:**
   - Load dashboard at 08:00 → Summary generated
   - Wait 13 hours
   - Load dashboard at 21:00 → Regenerates (>12h threshold)

## 🚀 Success Criteria

✅ **Cache hit rate >70%** (users loading dashboard multiple times/day)  
✅ **AI calls reduced by 50%+** (from 4-6/day to 2-3/day per user)  
✅ **Zero stale summaries** (always use latest Garmin data when available)  
✅ **Fast page loads** (cached summaries instant)  
✅ **Clear logging** (every cache decision logged with reason)  

## 🔗 Related Issues

- #TBD - Garmin cache optimization
- #TBD - AI API cost reduction strategies
- #TBD - Dashboard performance monitoring

---

**Priority:** High (cost savings + better UX)  
**Complexity:** Medium (requires careful timestamp tracking)  
**Estimated Effort:** 1-2 days for full implementation  
**Dependencies:** None  
**Labels:** `enhancement`, `performance`, `caching`, `cost-optimization`
