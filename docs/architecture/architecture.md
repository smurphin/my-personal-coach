# My Personal Coach - Application Architecture

<!-- toc -->

- [Overview](#overview)
- [Core Flows](#core-flows)
  * [1. Authentication & Onboarding Flow](#1-authentication--onboarding-flow)
  * [2. Strava Webhook Processing](#2-strava-webhook-processing)
  * [3. Weekly Summary Generation](#3-weekly-summary-generation)
  * [7. Chat Interaction & Conversational Coaching](#7-chat-interaction--conversational-coaching)
  * [8. Dynamic Plan Adaptation](#8-dynamic-plan-adaptation)
  * [4. Dashboard Display Flow](#4-dashboard-display-flow)
  * [5. Training Plan Management](#5-training-plan-management)
  * [6. Plan Completion & Archival](#6-plan-completion--archival)
- [Critical Decision Points](#critical-decision-points)
  * [1. Data Storage Strategy](#1-data-storage-strategy)
  * [2. Garmin API Rate Management](#2-garmin-api-rate-management)
  * [3. AI Context Preparation](#3-ai-context-preparation)
  * [4. Recovery Concern Detection](#4-recovery-concern-detection)
- [External Integrations](#external-integrations)
  * [Strava Integration](#strava-integration)
  * [Garmin Connect](#garmin-connect)
  * [Google Gemini AI](#google-gemini-ai)
  * [AWS Services](#aws-services)
- [Data Flow Diagram Summary](#data-flow-diagram-summary)
- [Error Handling & Resilience](#error-handling--resilience)
  * [Storage Fallback](#storage-fallback)
  * [API Resilience](#api-resilience)
  * [Size Monitoring](#size-monitoring)
- [Performance Optimizations](#performance-optimizations)
- [Security Considerations](#security-considerations)
- [Future Enhancements](#future-enhancements)
  * [Planned Features](#planned-features)
  * [Architecture Improvements](#architecture-improvements)
- [Monitoring & Metrics](#monitoring--metrics)
  * [Key Metrics to Track](#key-metrics-to-track)
  * [Health Checks](#health-checks)

<!-- tocstop -->

## Overview
This document provides a comprehensive architectural overview of the my-personal-coach application, detailing data flows, decision points, and AI integration patterns.

## Core Flows

### 1. Authentication & Onboarding Flow
```
User Access → Check Authentication
├─ Not Authenticated → Strava OAuth → Store Token → Check First Time User
│   ├─ New User → Onboarding Flow
│   └─ Returning User → Dashboard
└─ Authenticated → Dashboard
```

**Onboarding Process:**
1. **Collect User Information**
   - Training goals (race targets, performance goals)
   - Experience level (beginner, intermediate, advanced)
   - Available training time per week
   - Key constraints (injuries, schedule limitations)

2. **Heart Rate Zone Setup**
   ```
   User has HR zones? 
   ├─ Yes → Manual input (Zones 1-5)
   └─ No → Estimate from recent Strava activities
       - Analyze max HR from recent efforts
       - Apply Joe Friel zone calculations
       - Calculate VDOT from recent race efforts
   ```

3. **Initial Plan Generation**
   - Fetch recent Strava activity history (4-8 weeks)
   - Assess baseline fitness:
     - Recent training volume
     - Intensity distribution
     - Recovery patterns from activity frequency
   - Prepare AI context with goals and constraints
   - Generate periodized training plan via Gemini AI
   - Store user profile and plan in DynamoDB

**Key Points:**
- OAuth tokens stored in DynamoDB
- Single sign-on via Strava
- Token refresh handled automatically
- New users get immediate training plan
- HR zones can be manually adjusted post-onboarding

---

### 2. Strava Webhook Processing

```
Webhook Event → Validate Subscription
├─ Verification Challenge → Return Challenge
└─ Activity Event → Process Event
    ├─ Create/Update → Fetch Full Activity → Extract Metrics → Store
    └─ Delete → Mark Deleted
```

**Decision Points:**
- **Event Type Check:** Determines whether to fetch, update, or delete
- **Data Size Check:** Routes to DynamoDB directly or via S3 archival

**Metrics Extracted:**
- Distance, duration, pace
- Heart rate zones and averages
- Elevation gain/loss
- Activity type and intensity

**Storage Logic:**
```
Data Size < 300KB → Store in DynamoDB
Data Size > 300KB → Trim data + Archive to S3 → Store reference in DynamoDB
```

---

### 3. Weekly Summary Generation

Triggered automatically at end of training week.

```
End of Week Detected
  ↓
Fetch Week's Activities from DynamoDB
  ↓
Check Garmin Connection
  ├─ Connected → Check Daily Cache
  │   ├─ Cached → Use Cached Data
  │   └─ Not Cached → Fetch from Garmin API → Cache → Use Data
  └─ Not Connected → Skip Garmin Data
  ↓
Prepare AI Context
```

**AI Context Assembly:**

1. **Baseline Training Philosophy**
   - Polarized training principles
   - Joe Friel's heart rate zones
   - Jack Daniels' VDOT pacing
   - User's specific training goals

2. **Historical Context** (NEW - Critical for continuity)
   ```
   Fetch from DynamoDB/S3:
   - Past 4 weekly summaries
   - Last plan completion report (if exists)
   - Long-term trend data
   - Historical performance metrics
   ```
   This provides AI with:
   - Training progression over time
   - Recurring patterns or issues
   - Past coaching recommendations and their outcomes
   - Context for current state vs historical baseline

3. **Weekly Activity Data**
   - All activities with full metrics
   - Total training volume (time/distance)
   - Training Stress Score (TSS) estimates
   - Heart rate distribution across zones
   - Zone 2 vs intensity balance

4. **Active Plan Context** (NEW - If plan exists)
   ```
   Include plan information:
   - Current training phase (base/build/peak/taper)
   - Planned vs actual comparison for the week
   - Session adherence rate
   - Upcoming key workouts
   - Phase-specific goals and progression
   ```
   This enables AI to:
   - Compare execution to plan
   - Identify systematic deviations
   - Adjust recommendations based on plan phase
   - Provide phase-appropriate guidance

5. **Recovery Metrics** (if Garmin connected)
   - Sleep quality and duration trends
   - HRV (Heart Rate Variability) patterns
   - Body Battery levels and recovery
   - Stress scores
   - Daily step counts

6. **Flagged Concerns** (automated detection)
   ```
   IF Sleep < 6.5 hours → Flag low sleep
   IF HRV declining 3+ days → Flag recovery concern
   IF High stress (>50) consistently → Flag stress
   IF Body Battery < 20 frequently → Flag fatigue
   ```

**Gemini AI Processing:**
- Analyzes training load and distribution
- Evaluates recovery state vs training intensity
- Assesses zone distribution adherence
- **Considers historical context and trends**
- **Evaluates plan adherence if active plan exists**
- Generates specific recommendations
- Proposes next week's training plan

**Output Storage:**
```
Summary Size < 300KB → DynamoDB
Summary Size > 300KB → S3 Archive + DynamoDB Reference
```

---

### 7. Chat Interaction & Conversational Coaching

Users can interact with the AI coach through a chat interface for questions, feedback, and dynamic guidance.

```
User Chat Request
  ↓
Load Chat Context
  ↓
Fetch User Profile & Current Plan
  ↓
Fetch Recent Training History (2 weeks)
  ↓
Fetch Historical Context
  ├─ Past 4 weekly summaries
  ├─ Recent plan modifications
  └─ Previous Q&A conversation history
  ↓
Assemble Full Context for AI
  ↓
Send to Gemini AI
  ↓
Generate Response
  ↓
Check if Plan Update Needed?
  ├─ Yes → Propose changes → User approval → Update plan
  └─ No → Return response to user
```

**Context Assembly for Chat:**
- **User Profile:** HR zones, VDOT, training philosophy, goals
- **Current Plan Status:** Active phase, adherence rate, upcoming sessions
- **Recent Training:** Last 2 weeks of activities with metrics
- **Recovery State:** Latest Garmin metrics (sleep, HRV, body battery)
- **Historical Summaries:** Past 4 weeks of AI coaching summaries
- **Conversation History:** Recent chat exchanges for continuity

**Chat Capabilities:**
- Answer training questions ("Should I run today with this fatigue?")
- Explain workout purposes ("Why am I doing threshold intervals?")
- Provide race strategy advice
- Suggest plan modifications based on circumstances
- Interpret recovery metrics
- Adjust training based on life constraints
- Track injury concerns and suggest modifications

**Plan Updates via Chat:**
```
IF AI suggests plan modification:
  Generate proposed changes
  Show user:
    - What will change
    - Why it's recommended
    - Impact on plan progression
  Wait for user approval
  IF approved:
    Update active plan
    Log change with reason & timestamp
    Notify user of updates
```

**Examples:**
- User: "I'm feeling really fatigued, should I skip my interval workout today?"
- AI: *Checks recent activities, sleep, HRV trends, plan phase*
- Response: "Your HRV is down 15% and you've had 3 days of high training load. I recommend swapping today's intervals for an easy 30-minute recovery run. This keeps you moving without digging deeper into fatigue."

---

### 8. Dynamic Plan Adaptation

Plans automatically adapt based on training execution and recovery patterns.

```
Weekly Summary Generated
  ↓
Evaluate Plan Adherence
  ├─ Sessions completed vs planned
  ├─ Volume achieved vs target
  └─ Intensity distribution
  ↓
Check for Significant Deviations?
  ├─ No → Continue current plan
  └─ Yes → Analyze patterns
      ↓
      Assess Recovery State
      ↓
      Prepare Adaptation Context
      ↓
      Gemini AI: Recommend Adaptations
      ↓
      Generate Adjustment Suggestions
      ↓
      Minor adjustments? 
      ├─ Yes → Auto-apply + notify user
      └─ No → Flag for user review
```

**Deviation Patterns Detected:**
- **Consistent under-training:** Missing 30%+ of planned volume
- **Over-reaching:** Exceeding planned volume/intensity consistently
- **Missed key workouts:** Skipping threshold/interval sessions
- **Recovery issues:** Poor sleep, declining HRV while maintaining volume

**Adaptation Types:**

**Minor (Auto-Applied):**
- Volume tweaks ±10% based on adherence
- Adding recovery days when HRV declining
- Intensity adjustments within same session type
- Moving rest days to accommodate missed sessions

**Major (Require User Approval):**
- Restructuring training phases
- Changing key workout types
- Shifting race date preparations
- Significant volume reductions

**Adaptation Context Includes:**
- Full plan structure and progression
- Adherence data (3-4 weeks)
- Recovery trends from Garmin
- Upcoming phase transitions
- Historical response to similar situations

**Example Scenarios:**

*Scenario 1: Persistent Under-training*
```
Pattern: User completing 60% of planned volume for 3 weeks
Recovery: Normal HRV and sleep
AI Action:
  - Reduce planned volume by 20%
  - Maintain intensity structure
  - Focus on consistency over volume
  - Notify: "I've adjusted your plan to match your current capacity"
```

*Scenario 2: Over-reaching with Recovery Issues*
```
Pattern: Exceeding volume, but HRV declining, sleep poor
AI Action:
  - Insert mandatory recovery day
  - Reduce next week volume 30%
  - Flag for user review
  - Explain: "Your body is showing signs of fatigue despite strong motivation"
```

*Scenario 3: Life Interference*
```
Pattern: Missing specific days (e.g., Tuesdays) consistently
AI Action:
  - Shift Tuesday workouts to available days
  - Maintain weekly structure
  - Auto-apply change
  - Note: "Adjusted schedule to fit your Tuesday commitments"
```

**Benefits:**
- Plans stay realistic and achievable
- Reduces injury risk from overtraining
- Maintains motivation through appropriate challenge
- Responds to life circumstances
- Learns from user patterns over time

---

### 4. Dashboard Display Flow

```
Dashboard Request
  ↓
Load User Profile
  ↓
Fetch Recent Activities
  ↓
Check for S3 Archived Data
  ├─ Yes → Fetch from S3 (with DynamoDB fallback)
  └─ No → Use DynamoDB Data
  ↓
Merge Data Sources
  ↓
Check Garmin Integration
  ├─ Enabled → Check Daily Cache
  │   ├─ Cached Today → Show Cached Metrics
  │   └─ Not Cached → Fetch Live → Update Cache → Show
  └─ Disabled → Show Strava Data Only
  ↓
Render Dashboard
```

**Dashboard Components:**
- Recent activities with key metrics
- Latest weekly AI summary
- Garmin health metrics (sleep, HRV, stress)
- Training trends and patterns
- Upcoming training recommendations

---

### 5. Training Plan Management

Users can request new training plans at any time through the dashboard interface.

```
Plan Request → Check for Active Plan
├─ Active Plan Exists → Confirm Replacement
│   ├─ Yes → Archive Current Plan → Start New Plan
│   └─ No → Cancel Request
└─ No Active Plan → Start New Plan
```

**Plan Generation Process:**

1. **Collect Plan Details**
   - Race date and distance (5K, 10K, half, full marathon, ultra)
   - Target finish time (if applicable)
   - Training days available per week
   - Key constraints (injuries, tapering preferences, etc.)

2. **Assess Current Fitness**
   ```
   Fetch Recent Training History (4-8 weeks)
   ├─ Analyze weekly volume trends
   ├─ Identify recent best performances
   ├─ Calculate current VDOT from race efforts
   └─ Assess recovery patterns and consistency
   ```

3. **AI-Powered Plan Creation**
   - Prepare comprehensive context:
     - User profile and HR zones
     - Current fitness assessment
     - Training history and patterns
     - Race goals and constraints
   - Send to Gemini AI for plan generation
   - AI creates periodized plan:
     - **Base Phase:** Aerobic development, high volume Z2
     - **Build Phase:** Progressive intensity, threshold work
     - **Peak Phase:** Race-specific workouts, volume maintenance
     - **Taper Phase:** Strategic recovery, race prep
   - Weekly progression with built-in recovery weeks
   - Session-level detail with pace/HR guidance

4. **Store and Display Plan**
   ```
   Plan Size Check
   ├─ < 300KB → Store in DynamoDB
   └─ > 300KB → Archive to S3 + Store reference in DynamoDB
   
   Display plan to user with:
   - Weekly breakdown
   - Session details
   - Progressive load chart
   - Key workout highlights
   ```

**Plan Structure:**
- Weeks organized by training phase
- Each session includes:
  - Workout type (easy, tempo, intervals, long run)
  - Target duration or distance
  - Pace/HR zone guidance
  - Recovery recommendations
- Integration with weekly AI summaries for plan adaptation

---

### 6. Plan Completion & Archival

Triggered automatically when a training plan reaches its end date.

```
Plan End Date Reached → Check Completion Status
├─ Fully Completed → Analyze Results
└─ Partially Completed → Flag Incomplete → Analyze Results
```

**Completion Analysis Process:**

1. **Compare Actual vs Planned**
   ```
   For each training week:
   - Planned sessions vs actual completed
   - Target volume vs actual volume
   - Key workout completion rate
   - Intensity distribution adherence
   - Overall plan completion percentage
   ```

2. **Fetch Final Performance Metrics**
   - VDOT progression (start vs end)
   - Volume progression over plan duration
   - Recovery trends (HRV, sleep quality improvements)
   - Performance gains in key distances
   - Race result analysis (if race occurred)

3. **AI-Powered Plan Review**
   - Send complete plan data to Gemini AI:
     - Original plan structure
     - Actual training completed
     - Performance outcomes
     - Recovery data trends
   - AI generates comprehensive review:
     - Success metrics and achievements
     - Areas of strong adherence
     - Challenges and lessons learned
     - Specific recommendations for future training
     - Suggested next goals based on progress

4. **Archive and Store**
   ```
   Archive Completed Plan
   ├─ Move full plan data to S3
   ├─ Add completion metadata:
   │   - Completion date
   │   - Completion percentage
   │   - Performance outcomes
   │   - AI review summary
   └─ Update DynamoDB with archive reference
   ```

5. **Prompt Next Steps**
   ```
   Ask User: Continue Training?
   ├─ Yes → Suggest Next Goals
   │   - Based on plan outcomes
   │   - Considering fitness progression
   │   - Aligned with user's broader goals
   │   - Offer to generate new plan
   └─ No → Mark user as inactive
       - Preserve all data
       - Reduce notification frequency
       - Allow easy reactivation
   ```

**Completion Report Contents:**
- Plan overview and original goals
- Completion statistics
- Performance metrics progression
- Key achievements
- Lessons learned
- AI-generated recommendations
- Suggested next training focus
- Historical trend charts

**Benefits:**
- Comprehensive training history maintained
- Data-driven insights for future planning
- Recognition of achievements
- Continuous improvement feedback loop
- Seamless transition to next training cycle

---

## Critical Decision Points

### 1. Data Storage Strategy
**Problem:** DynamoDB 400KB item limit  
**Solution:** Automatic trimming at 300KB threshold

```
For each data item:
  Measure size
  IF size > 300KB:
    Extract key metrics only
    Archive full data to S3 (gzipped)
    Store metrics + S3 reference in DynamoDB
  ELSE:
    Store full data in DynamoDB
```

### 2. Garmin API Rate Management
**Problem:** API rate limits can cause service degradation  
**Solution:** Daily caching strategy

```
For Garmin data requests:
  Check cache timestamp
  IF last_fetch < today:
    Fetch fresh data from Garmin API
    Update cache with timestamp
    Return fresh data
  ELSE:
    Return cached data
```

**Benefits:**
- Maximum 1 API call per user per day
- Instant response for cached data
- Graceful degradation if API unavailable

### 3. AI Context Preparation
**Decision:** What data to include in Gemini prompt

**Progressive Inclusion Strategy:**
```
1. Always Include: Training philosophy + user zones
2. Add: Week's activity data + metrics
3. If Available: Garmin recovery metrics
4. If Concerning: Flagged health/recovery patterns
```

**Context Size Management:**
- Prioritize recent data over historical
- Summarize older weeks vs full detail
- Include only relevant recovery trends
- Flag anomalies explicitly

### 4. Recovery Concern Detection
**Automated Pattern Recognition:**

```python
concerns = []

if avg_sleep < 6.5:
    concerns.append("Sleep below optimal range")
    
if hrv_trend == "declining" and days >= 3:
    concerns.append("HRV declining - recovery may be compromised")
    
if avg_stress > 50:
    concerns.append("Elevated stress levels")
    
if body_battery < 20 for multiple_days:
    concerns.append("Persistent low body battery")
```

**Integration with AI:**
- Concerns passed to Gemini with specific context
- AI provides targeted recommendations
- May suggest reducing training intensity
- Emphasizes recovery protocols

---

## External Integrations

### Strava Integration
**Authentication:** OAuth 2.0  
**Data Access:**
- Activity streams (heart rate, pace, cadence)
- Activity summaries
- Athlete profile

**Webhook Events:**
- Activity created
- Activity updated
- Activity deleted
- Requires subscription validation

### Garmin Connect
**Authentication:** Username/password via garminconnect library  
**Data Retrieved:**
- Sleep analysis (stages, duration, quality)
- HRV status and trends
- Body Battery (0-100 scale)
- Stress tracking
- Daily step counts

**Rate Limiting:** Daily cache prevents excessive calls

### Google Gemini AI
**Model:** gemini-2.5-flash (default)  
**Use Cases:**
- Weekly training analysis
- Recovery state assessment
- Training plan generation (periodized, goal-specific)
- Plan completion review and analysis
- Training plan adaptation
- Coaching recommendations

**Input:** Structured JSON with:
- User profile and zones
- Activity data
- Recovery metrics
- Flagged concerns
- Training goals and constraints (for plans)

**Output:** Natural language coaching summary with structured recommendations / Periodized training plans

### AWS Services
**DynamoDB:**
- User profiles and tokens
- Activity data (<300KB)
- Weekly summaries
- Garmin data cache

**S3 (kaizencoach-data bucket):**
- Large activity data (>300KB)
- Historical data archives
- Compressed with gzip
- Organized by user_id/date

**App Runner:**
- Container deployment
- Auto-scaling
- HTTPS endpoints
- Environment variable management

---

## Data Flow Diagram Summary

```
┌─────────────┐
│   New User  │──► OAuth ──► Onboarding ──► Profile Setup ──┐
└─────────────┘                                              │
                                                             ▼
┌─────────────┐                                    ┌──────────────┐
│   Strava    │──► Webhooks ──► Extract Metrics ───►│  DynamoDB    │
└─────────────┘                                    │   Storage    │
                                                   │  Decision    │
┌─────────────┐                                    │   < 300KB?   │
│   Garmin    │──► Daily Cache ────────────────────►│              │
└─────────────┘                                    └──────┬───────┘
                 ┌─────────────────────────────────────────┤
                 │                                         │
                 ▼                                         ▼
        ┌─────────────────┐                      ┌──────────────┐
        │    S3 Archive   │◄─────────────────────│ > 300KB Data │
        │  - Activities   │                      │  - Plans     │
        │  - Plans        │                      │  - Summaries │
        │  - Summaries    │                      └──────────────┘
        └─────────────────┘
                 │
                 ▼
        ┌─────────────────┐
        │  Weekly Summary │
        │    Trigger      │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │ AI Context Prep │
        │  - Philosophy   │
        │  - Activities   │
        │  - Recovery     │
        │  - Concerns     │
        │  - Plan Status  │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │   Gemini AI     │◄──── Plan Generation Request
        │   Analysis      │◄──── Plan Completion Review
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │   Dashboard     │
        │  - Activities   │
        │  - Summaries    │
        │  - Current Plan │
        │  - Health       │
        └─────────────────┘

Plan Lifecycle:
┌─────────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────────┐
│  Generate   │───►│    Active    │───►│  Complete   │───►│   Archive    │
│   Plan      │    │   Training   │    │   Review    │    │     S3       │
└─────────────┘    └──────────────┘    └─────────────┘    └──────────────┘
       │                                                            │
       └───────────────────► New Plan ◄────────────────────────────┘
```

---

## Error Handling & Resilience

### Storage Fallback
```
Try DynamoDB:
  Success → Return data
  Failure → Try S3 archive
    Success → Return archived data
    Failure → Return error with graceful degradation
```

### API Resilience
```
Garmin API Call:
  Try fetch
  IF timeout or error:
    Use cached data if available
    OR skip Garmin metrics for this cycle
    Continue with Strava data only
```

### Size Monitoring
```
Before DynamoDB write:
  Check item size
  IF approaching 400KB limit:
    Emergency trim operation
    Archive to S3
    Log warning for review
```

---

## Performance Optimizations

1. **Caching Strategy**
   - Daily Garmin data cache per user
   - Reduces API calls by ~95%
   - Faster dashboard load times

2. **Data Pruning**
   - Store only essential metrics in DynamoDB
   - Archive raw streams to S3
   - Compress S3 data with gzip

3. **Lazy Loading**
   - Load S3 data only when needed
   - DynamoDB for frequent access patterns
   - Progressive enhancement for Garmin metrics

4. **Webhook Optimization**
   - Async processing where possible
   - Quick ACK to Strava
   - Background data enrichment

---

## Security Considerations

1. **Token Management**
   - OAuth tokens encrypted at rest
   - Short-lived access tokens
   - Secure token refresh flow

2. **API Security**
   - Webhook signature verification
   - Rate limiting per user
   - Input validation and sanitization

3. **Data Privacy**
   - User data isolation
   - Secure S3 bucket policies
   - No public data exposure

---

## Future Enhancements

### Planned Features
- **Advanced Training Load:** CTL/ATL/TSB calculations for scientific load management
- **Predictive Modeling:** ML-based readiness predictions using recovery patterns
- **Race Taper:** Automated taper optimization based on individual response
- **Adaptive Plans:** Real-time plan adjustments based on recovery and performance
- **Plan Templates:** Pre-built plans for common race distances
- **Workout Library:** Searchable database of training sessions
- **Social Features:** Training groups, challenges, shared plans
- **Mobile App:** Native iOS/Android apps for on-the-go access
- **Integration Expansion:** TrainingPeaks, Wahoo, Polar devices

### Architecture Improvements
- Modular service refactoring (Issue #10) - IN PROGRESS
- Enhanced monitoring and logging
- Multi-model AI comparison (Gemini vs Claude vs GPT)
- Real-time notifications for recovery alerts
- Webhook retry logic with exponential backoff
- Plan version control and rollback capability
- A/B testing framework for AI prompt optimization

---

## Monitoring & Metrics

### Key Metrics to Track
- DynamoDB item sizes (alert at 350KB)
- S3 archival frequency
- API response times (Garmin, Strava, Gemini)
- Cache hit rates
- Weekly summary generation time
- User engagement (dashboard visits, summary reads)
- **Plan completion rates** (% of users completing plans)
- **Plan adherence** (actual vs planned session completion)
- **Onboarding conversion** (OAuth to first plan generation)
- **Plan generation time** (AI response latency)
- **Active plan count** (users with current plans)

### Health Checks
- OAuth token validity
- Webhook subscription status
- API connectivity (all services)
- Data staleness detection
- **Plan integrity** (valid dates, reasonable progression)
- **Archive accessibility** (S3 retrieval success rate)