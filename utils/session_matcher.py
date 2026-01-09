"""
Intelligent session matching for different athlete types.

Disciplinarian: Sessions have fixed dates, match by date
Improviser/Minimalist: Sessions are flexible within week, match by characteristics
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from difflib import SequenceMatcher


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
        
        activity_name = activity_data.get('name', '').lower()
        session_desc = (session.description or '').lower()
        
        # PRIMARY: Description similarity (most important for Improvisers)
        # Check for exact phrase matches first
        desc_similarity = similarity_score(activity_name, session_desc)
        
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
            (['club', 'social', 'group'], ['club', 'social', 'group']),
            (['long run', 'long'], ['long run', 'long']),
            (['tempo', 'threshold'], ['tempo', 'threshold']),
            (['interval', 'repeats'], ['interval', 'repeats']),
            (['easy', 'recovery'], ['easy', 'recovery']),
            (['fartlek'], ['fartlek']),
            (['hill'], ['hill'])
        ]
        
        for activity_keywords, session_keywords in run_type_matches:
            activity_has = any(kw in activity_name for kw in activity_keywords)
            session_has = any(kw in session_desc for kw in session_keywords)
            if activity_has and session_has:
                score += 8.0
                reasons.append(f"specific type match: {activity_keywords[0]}")
                break
        
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
        
        # TERTIARY: Duration matching (if available)
        if session.duration_minutes and activity_data.get('moving_time'):
            activity_duration_mins = activity_data['moving_time'] / 60
            session_duration = session.duration_minutes
            
            # Within 20% is a good match
            duration_ratio = min(activity_duration_mins, session_duration) / max(activity_duration_mins, session_duration)
            if duration_ratio > 0.8:
                score += 2.0
                reasons.append(f"duration match ({duration_ratio:.0%})")
            elif duration_ratio > 0.5:
                score += 1.0
        
        # LOWEST PRIORITY: Session priority (only used as tiebreaker)
        if session.priority == 'KEY':
            score += 0.5
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
    
    # Require at least moderate confidence (5.0 = moderate description match or strong keyword match)
    if best_score >= 5.0:
        print(f"✅ Matched: {best_session.id} (score: {best_score:.2f}, {best_reason})")
        return best_session
    else:
        print(f"⚠️  No confident match (best score: {best_score:.2f}, need ≥5.0)")
        return None


def match_sessions_batch(plan_v2, analyzed_sessions: List[Dict[str, Any]], athlete_type: str) -> List[tuple]:
    """
    Match multiple activities to their sessions.
    
    Returns:
        List of (session, activity_data) tuples for matched pairs
    """
    matches = []
    
    for activity_data in analyzed_sessions:
        session = match_session_to_activity(plan_v2, activity_data, athlete_type)
        if session:
            matches.append((session, activity_data))
    
    return matches