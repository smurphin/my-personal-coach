"""
Intelligent session matching for different athlete types.

Disciplinarian: Sessions have fixed dates, match by date
Improviser/Minimalist: Sessions are flexible within week, match by characteristics

Shared helpers for AI-assisted matching (used by both feedback and webhook):
- get_candidate_sessions_text(): build candidate list for AI (same format in both flows)
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import re
from difflib import SequenceMatcher

# Strava activity type -> plan session type (used by feedback and webhook)
ACTIVITY_TYPE_MAP = {
    'Run': 'RUN', 'VirtualRun': 'RUN',
    'Ride': 'BIKE', 'VirtualRide': 'BIKE',
    'Swim': 'SWIM',
}


def get_candidate_sessions_text(plan_v2, activity_date_str: str, strava_activity_type: Optional[str] = None) -> Optional[str]:
    """
    Build the list of candidate sessions (incomplete, same week, same type) as text for AI matching.
    Used by both feedback and webhook so they use the same logic and format.

    Args:
        plan_v2: TrainingPlan object
        activity_date_str: ISO date string (YYYY-MM-DD)
        strava_activity_type: e.g. 'Run', 'Ride' - mapped to RUN, BIKE, etc.

    Returns:
        Text like "[w1-s1] RUN: Easy 45 min Zone 2" per line, or None if no candidates.
    """
    from models.training_plan import TrainingPlan
    expected_type = ACTIVITY_TYPE_MAP.get(strava_activity_type) if strava_activity_type else None
    target_week = None
    for week in plan_v2.weeks:
        if week.start_date and week.end_date and week.start_date <= activity_date_str <= week.end_date:
            target_week = week
            break
    if not target_week:
        return None
    candidate_sessions = [
        s for s in target_week.sessions
        if not s.completed and s.type != 'REST'
        and (not expected_type or s.type == expected_type)
    ]
    if not candidate_sessions:
        return None
    return "\n".join(
        f"[{s.id}] {s.type}: {s.description or 'No description'}"
        for s in candidate_sessions
    )


def _extract_target_distance_meters(session_desc: str) -> Optional[float]:
    """Extract target distance in meters from session description (e.g. '5 miles', '5k', '10k')."""
    if not session_desc:
        return None
    desc = session_desc.lower()
    # 5 miles
    m = re.search(r'5\s*miles?|5\s*mi\b', desc)
    if m:
        return 8047
    # 5k
    m = re.search(r'5\s*k\b|5k\b', desc)
    if m:
        return 5000
    # 10k
    m = re.search(r'10\s*k\b|10k\b', desc)
    if m:
        return 10000
    # half marathon
    if 'half marathon' in desc or ' hm ' in desc or '21.1' in desc:
        return 21100
    # marathon
    if 'marathon' in desc or '42.2' in desc or '26.2' in desc:
        return 42195
    return None


def _is_distance_based_session(session_desc: str) -> bool:
    """True if session is defined by distance (e.g. '5 mile race') rather than time ('35 minutes')."""
    if not session_desc:
        return False
    d = _extract_target_distance_meters(session_desc)
    return d is not None


def get_week_bounds(date_str: str) -> tuple[str, str]:
    """
    Get the Monday-Sunday bounds for a given date.
    
    Args:
        date_str: ISO format date string (YYYY-MM-DD)
        
    Returns:
        Tuple of (start_date, end_date) as ISO strings
    """
    date = datetime.fromisoformat(date_str).date()
    
    # Find Monday of this week (weekday 0 = Monday)
    days_since_monday = date.weekday()
    week_start = date - timedelta(days=days_since_monday)
    week_end = week_start + timedelta(days=6)
    
    return week_start.isoformat(), week_end.isoformat()


def similarity_score(text1: str, text2: str) -> float:
    """Calculate similarity between two text strings (0.0 to 1.0)"""
    if not text1 or not text2:
        return 0.0
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()


def match_session_to_activity(plan_v2, activity_data: Dict[str, Any], athlete_type: str) -> Optional[Any]:
    """
    Find the best matching session for a completed activity.
    
    Args:
        plan_v2: TrainingPlan object
        activity_data: Analyzed activity data with keys:
            - start_date: ISO timestamp
            - type: Activity type (Run, Ride, Swim, etc.)
            - name: Activity name/title
            - distance: Distance in meters
            - moving_time: Duration in seconds
        athlete_type: 'Disciplinarian', 'Improviser', or 'Minimalist'
        
    Returns:
        Matching TrainingSession object or None
    """
    from models.training_plan import TrainingPlan
    
    # Get activity date
    activity_datetime = datetime.fromisoformat(activity_data['start_date'].replace('Z', ''))
    activity_date = activity_datetime.date().isoformat()
    
    # Map Strava activity types to session types
    activity_type_map = {
        'Run': 'RUN',
        'Ride': 'BIKE',
        'Swim': 'SWIM',
        'VirtualRide': 'BIKE',
        'VirtualRun': 'RUN'
    }
    
    strava_type = activity_data.get('type', '')
    session_type = activity_type_map.get(strava_type, strava_type.upper())
    
    # DISCIPLINARIAN: Match by exact date if date is set
    if athlete_type == 'Disciplinarian':
        session = plan_v2.get_session_by_date(activity_date)
        if session:
            # Verify type matches if session has a type
            if session.type and session.type != 'REST':
                if session.type == session_type:
                    return session
                else:
                    print(f"⚠️  Date matched but type mismatch: session={session.type}, activity={session_type}")
            else:
                return session
    
    # IMPROVISER/MINIMALIST: Match by week + characteristics
    week_start, week_end = get_week_bounds(activity_date)
    
    # Find the week this activity falls into
    target_week = None
    for week in plan_v2.weeks:
        if week.start_date <= activity_date <= week.end_date:
            target_week = week
            break
    
    if not target_week:
        print(f"⚠️  Activity {activity_date} doesn't fall within any plan week")
        return None
    
    # Get all incomplete sessions in this week that match activity type
    candidate_sessions = [
        s for s in target_week.sessions
        if not s.completed
        and s.type == session_type
        and s.type != 'REST'
    ]
    
    if not candidate_sessions:
        print(f"ℹ️  No incomplete {session_type} sessions found in week {target_week.week_number}")
        return None
    
    # Score each candidate session
    def score_session(session) -> tuple[float, str]:
        """Return (score, reason) for a session match"""
        score = 0.0
        reasons = []
        
        # BASE SCORE: Type and week match (fundamental requirement)
        # This ensures we always have some score if type matches
        score += 1.0
        reasons.append("type + week match")
        
        activity_name = activity_data.get('name', '').lower()
        session_desc = (session.description or '').lower()
        
        # PRIMARY: Description similarity (most important for Improvisers)
        # Check for exact phrase matches first
        _match_text = f"{activity_name} {(activity_data.get('private_note') or '').lower()}".strip()
        desc_similarity = similarity_score(_match_text, session_desc)
        
        # Boost score significantly for good description matches
        if desc_similarity > 0.5:
            score += 10.0  # Strong match
            reasons.append(f"strong description match ({desc_similarity:.1%})")
        elif desc_similarity > 0.3:
            score += 5.0  # Moderate match
            reasons.append(f"description match ({desc_similarity:.1%})")
        elif desc_similarity > 0.1:
            score += 2.0  # Weak match
            reasons.append(f"weak description match ({desc_similarity:.1%})")
        
        # Check for specific keywords/phrases in both
        # Common run types
        run_type_matches = [
            (['race', 'league', 'championship', 'park run', '5 mile', '5 miles'], ['race', '5 mile', '10k', 'key effort']),
            (['club', 'social', 'group'], ['club', 'social', 'group']),
            (['long run', 'long'], ['long run', 'long']),
            (['tempo', 'threshold'], ['tempo', 'threshold']),
            (['interval', 'repeats'], ['interval', 'repeats']),
            (['easy', 'recovery'], ['easy', 'recovery']),
            (['fartlek'], ['fartlek']),
            (['hill'], ['hill'])
        ]
        
        # Cycling-specific matches
        bike_type_matches = [
            (['ftp', 'functional threshold', 'threshold test'], ['ftp', 'threshold', 'functional threshold']),
            (['ramp', 'ramp test', 'incremental'], ['ramp', 'incremental']),
            (['time trial', 'tt'], ['time trial', 'tt']),
            (['sweet spot'], ['sweet spot']),
            (['vo2', 'vo2max'], ['vo2', 'vo2max']),
            (['endurance', 'base'], ['endurance', 'base']),
        ]
        
        # Check cycling matches first (if it's a bike activity)
        if session_type == 'BIKE':
            for activity_keywords, session_keywords in bike_type_matches:
                activity_has = any(kw in _match_text for kw in activity_keywords)
                session_has = any(kw in session_desc for kw in session_keywords)
                if activity_has and session_has:
                    score += 10.0  # Strong boost for FTP/ramp test matches
                    reasons.append(f"cycling type match: {activity_keywords[0]}")
                    break
        
        # Then check run matches
        for activity_keywords, session_keywords in run_type_matches:
            activity_has = any(kw in _match_text for kw in activity_keywords)
            session_has = any(kw in session_desc for kw in session_keywords)
            if activity_has and session_has:
                score += 8.0
                reasons.append(f"specific type match: {activity_keywords[0]}")
                break

        # Use lap-derived interval structure when available (helps when activity title is generic
        # and HR doesn't neatly match prescribed zones).
        intervals = activity_data.get('intervals_detected') or {}
        if intervals.get('has_intervals'):
            # Session description indicates structured work.
            if any(kw in session_desc for kw in ['interval', 'repeats', 'vo2', 'track', ' i ', ' i-pace', 'rep']):
                score += 4.0
                reasons.append("interval structure match (laps)")
            # Activity name indicates intervals even if session description doesn't.
            elif any(kw in activity_name for kw in ['interval', 'repeats', 'vo2', 'track']):
                score += 2.0
                reasons.append("interval structure match (laps, name)")
        
        # SECONDARY: Intensity keywords
        intensity_keywords = {
            'easy': ['easy', 'recovery', 'z1', 'z2', 'zone 1', 'zone 2', 'conversational', 'social'],
            'tempo': ['tempo', 'threshold', 'z3', 'z4', 'zone 3', 'zone 4'],
            'hard': ['interval', 'vo2', 'z5', 'zone 5', 'hard', 'effort', 'fast']
        }
        
        for intensity, keywords in intensity_keywords.items():
            if any(kw in session_desc for kw in keywords):
                if any(kw in activity_name for kw in keywords):
                    score += 3.0
                    reasons.append(f"{intensity} intensity match")
                    break
        
        # RACE FLAG: When Strava marks activity as race, strongly favor race sessions
        if activity_data.get('is_race') and 'race' in session_desc:
            score += 8.0
            reasons.append("Strava race flag + session is race")
        
        # DISTANCE MATCHING: For running, match activity distance to session target (e.g. 5 miles, 5k)
        if session_type == 'RUN' and activity_data.get('distance'):
            target_m = _extract_target_distance_meters(session_desc)
            if target_m and target_m > 0:
                activity_m = activity_data['distance']
                ratio = min(activity_m, target_m) / max(activity_m, target_m)
                if ratio > 0.9:
                    score += 6.0
                    reasons.append(f"distance match ({ratio:.0%})")
                elif ratio > 0.85:
                    score += 4.0
                    reasons.append(f"distance match ({ratio:.0%})")
                elif ratio > 0.75:
                    score += 2.0
                    reasons.append(f"distance match ({ratio:.0%})")
        
        # TERTIARY: Duration matching - down-weight when session is distance-based (e.g. "5 mile race")
        is_dist_based = _is_distance_based_session(session_desc)
        if session.duration_minutes and activity_data.get('moving_time') and not is_dist_based:
            activity_duration_mins = activity_data['moving_time'] / 60
            session_duration = session.duration_minutes
            duration_ratio = min(activity_duration_mins, session_duration) / max(activity_duration_mins, session_duration)
            if duration_ratio > 0.8:
                score += 2.0
                reasons.append(f"duration match ({duration_ratio:.0%})")
            elif duration_ratio > 0.5:
                score += 1.0
        
        # Session priority - KEY sessions get stronger weight (races are usually KEY)
        if session.priority == 'KEY':
            score += 2.0
            reasons.append("KEY session")
        elif session.priority == 'IMPORTANT':
            score += 0.3
            reasons.append("IMPORTANT session")
        elif session.priority == 'STRETCH':
            score += 0.1
            reasons.append("STRETCH session")
        
        return score, " + ".join(reasons) if reasons else "type match only"
    
    # Score all candidates
    scored_sessions = [(session, *score_session(session)) for session in candidate_sessions]
    
    # Sort by score (highest first)
    scored_sessions.sort(key=lambda x: x[1], reverse=True)
    
    # Log matching results
    print(f"\n=== Session Matching for {strava_type} on {activity_date} ===")
    print(f"Week {target_week.week_number}: {week_start} to {week_end}")
    print(f"Found {len(candidate_sessions)} incomplete {session_type} sessions")
    for session, score, reason in scored_sessions[:3]:  # Show top 3
        print(f"  [{session.id}] Score: {score:.2f} - {reason}")
    
    # Return best match if score is reasonable
    best_session, best_score, best_reason = scored_sessions[0]
    
    # Determine confidence threshold based on context
    # If there's only one candidate, be more lenient (unique match)
    is_unique_match = len(candidate_sessions) == 1
    
    if is_unique_match:
        # For unique matches, lower threshold significantly
        # Type + week match + any description similarity is enough
        threshold = 2.0  # Much lower for unique matches
        print(f"   ℹ️  Unique match: Only 1 {session_type} session in this week")
        
        # Boost score for unique matches to account for high likelihood
        if best_score < threshold:
            # If we have type match + week match + any description similarity, boost it
            if best_score >= 1.0:  # Has at least some description match
                best_score = max(best_score, threshold)
                best_reason += " (unique match boost)"
                print(f"   📈 Boosting unique match score to {best_score:.2f}")
    else:
        # For multiple candidates, require higher confidence
        threshold = 5.0  # Moderate description match or strong keyword match
    
    # Special handling for STRETCH sessions - be more lenient
    if best_session.priority == 'STRETCH':
        # STRETCH sessions are optional, so if someone did one, it's likely intentional
        # Lower threshold further for STRETCH sessions
        if is_unique_match:
            threshold = 1.5  # Very lenient for unique STRETCH matches
        else:
            threshold = 3.0  # Lower than normal for STRETCH sessions
        print(f"   ℹ️  STRETCH session - using lower threshold ({threshold:.1f})")
    
    if best_score >= threshold:
        print(f"✅ Matched: {best_session.id} (score: {best_score:.2f}, {best_reason})")
        return best_session
    else:
        print(f"⚠️  No confident match (best score: {best_score:.2f}, need ≥{threshold:.1f})")
        return None


def match_sessions_batch(plan_v2, analyzed_sessions: List[Dict[str, Any]], athlete_type: str) -> List[tuple]:
    """
    Match multiple activities to their sessions.
    
    CRITICAL: Sessions are marked complete immediately after matching to prevent
    the same session from being matched multiple times in the same batch.
    
    Returns:
        List of (session, activity_data) tuples for matched pairs
    """
    matches = []
    
    for activity_data in analyzed_sessions:
        session = match_session_to_activity(plan_v2, activity_data, athlete_type)
        if session:
            # CRITICAL: Mark session complete immediately so it's excluded from future matches
            # This prevents multiple activities from matching the same session
            activity_id = activity_data.get('id')
            activity_start_date = activity_data.get('start_date')
            session.mark_complete(activity_id, activity_start_date)
            matches.append((session, activity_data))
    
    return matches