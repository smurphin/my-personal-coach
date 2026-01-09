"""
Migration utilities for converting existing markdown plans to structured plan_v2 format.
TESTED with real data: 62 sessions parsed successfully.
"""
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from models.training_plan import TrainingPlan, Week, Session
from utils.s_and_c_utils import load_default_s_and_c_library, process_s_and_c_session


def migrate_plan_to_v2(plan_markdown: str, plan_data: Optional[Dict[str, Any]], 
                       athlete_id: str, user_inputs: Dict[str, Any]) -> TrainingPlan:
    """Convert existing markdown plan to structured TrainingPlan v2"""
    
    plan = TrainingPlan(
        version=2,
        athlete_id=athlete_id,
        athlete_goal=user_inputs.get('goal', ''),
        goal_date=user_inputs.get('goal_date'),
        goal_distance=user_inputs.get('goal_distance'),
        plan_start_date=user_inputs.get('plan_start_date'),
        libraries={'s_and_c': load_default_s_and_c_library()}  # Add S&C library to all plans
    )
    
    # Get week dates from plan_structure['weeks']
    week_dates = {}
    if plan_data and isinstance(plan_data, dict) and 'weeks' in plan_data:
        # plan_data is plan_structure dict with 'weeks' list
        for week_info in plan_data['weeks']:
            wn = week_info.get('week_number')
            if wn is not None and 'start_date' in week_info and 'end_date' in week_info:
                week_dates[wn] = (week_info['start_date'], week_info['end_date'])
        if week_dates:
            print(f"✓ Using plan_structure for {len(week_dates)} weeks")
    
    # Fallback: parse markdown
    if not week_dates:
        pattern = r'###\s+Week\s+(\d+):\s+(\w+\s+\d+(?:st|nd|rd|th)?)\s*-\s*(\w+\s+\d+(?:st|nd|rd|th)?)'
        for match in re.finditer(pattern, plan_markdown):
            week_num = int(match.group(1))
            try:
                current_year = datetime.now().year
                start_clean = re.sub(r'(\d+)(?:st|nd|rd|th)', r'\1', match.group(2))
                end_clean = re.sub(r'(\d+)(?:st|nd|rd|th)', r'\1', match.group(3))
                start_obj = datetime.strptime(f"{start_clean} {current_year}", '%b %d %Y')
                end_obj = datetime.strptime(f"{end_clean} {current_year}", '%b %d %Y')
                if end_obj < start_obj:
                    end_obj = datetime.strptime(f"{end_clean} {current_year + 1}", '%b %d %Y')
                week_dates[week_num] = (start_obj.strftime('%Y-%m-%d'), end_obj.strftime('%Y-%m-%d'))
            except ValueError:
                continue
        if week_dates:
            print(f"✓ Parsed {len(week_dates)} weeks from markdown")
    
    # Parse weeks using regex to capture week numbers
    week_pattern = r'###?\s+Week\s+(\d+):'
    week_matches = list(re.finditer(week_pattern, plan_markdown))
    
    total_sessions = 0
    for idx, match in enumerate(week_matches):
        week_num = int(match.group(1))  # Actual week number from markdown
        
        if week_num not in week_dates:
            print(f"⚠️  Week {week_num} found in markdown but not in plan_structure")
            continue
        
        # Extract week text between this week and the next (or end)
        start_pos = match.end()
        end_pos = week_matches[idx + 1].start() if idx + 1 < len(week_matches) else len(plan_markdown)
        week_text = plan_markdown[start_pos:end_pos]
        
        start_date, end_date = week_dates[week_num]
        week_start = datetime.strptime(start_date, '%Y-%m-%d')
        
        # Extract description
        desc_match = re.search(r'\*\*Context:\*\*\s*([^\n]+)', week_text)
        description = desc_match.group(1).strip() if desc_match else ""
        
        # Parse sessions - support two formats:
        # Format 1: *   **Run 1 [KEY]: Title**
        # Format 2: *   **[KEY] Day: Title**
        
        # Try Format 1 first (staging format)
        session_pattern_1 = r'^\*\s+\*\*([A-Za-z&\s]+?)\s*(\d+)?\s*\[([^\]]+)\]:\s*([^\*\n]+)'
        matches_1 = list(re.finditer(session_pattern_1, week_text, re.MULTILINE))
        
        # Try Format 2 (production format)
        session_pattern_2 = r'^\*\s+\*\*\[([^\]]+)\]\s+([A-Za-z]+):\s*([^\*\n]+)'
        matches_2 = list(re.finditer(session_pattern_2, week_text, re.MULTILINE))
        
        sessions = []
        session_counter = 0
        
        if matches_1:
            # Use Format 1 (staging)
            for match in matches_1:
                session_counter += 1
                session_prefix = match.group(1).strip()  # "Run", "S&C", etc.
                session_num_str = match.group(2)
                session_num = int(session_num_str) if session_num_str else session_counter
                priority_raw = match.group(3).strip()
                session_title = match.group(4).strip()
                
                # Normalize priority
                priority = priority_raw.split()[0] if priority_raw else "NORMAL"
                full_text = f"{session_prefix}: {session_title}"
                
                # Type detection based on prefix
                prefix_lower = session_prefix.lower()
                if 'run' in prefix_lower or 'jog' in prefix_lower:
                    session_type = 'RUN'
                elif 'bike' in prefix_lower or 'spin' in prefix_lower or 'ride' in prefix_lower or 'cycle' in prefix_lower:
                    session_type = 'BIKE'
                elif 'swim' in prefix_lower:
                    session_type = 'SWIM'
                elif 's&c' in prefix_lower or 'strength' in prefix_lower:
                    session_type = 'STRENGTH'
                elif 'stretch' in prefix_lower or 'mobility' in prefix_lower or 'cross' in prefix_lower:
                    session_type = 'OTHER'
                elif 'rest' in prefix_lower:
                    session_type = 'REST'
                else:
                    session_type = 'OTHER'
                
                # Duration and zone extraction
                duration_minutes = None
                hour_match = re.search(r'(\d+)\s*(?:hour|hours|h(?:\s|:|$|\.|\,))', session_title, re.I)
                min_match = re.search(r'[~]?(\d+)\s*(?:min|mins|minutes)', session_title, re.I)
                if hour_match:
                    duration_minutes = int(hour_match.group(1)) * 60
                elif min_match:
                    duration_minutes = int(min_match.group(1))
                
                zones = {}
                zone_match = re.search(r'(?:Zone|Z)\s*([0-9][/-]?[0-9]?)', session_title, re.I)
                if zone_match:
                    zones['hr'] = zone_match.group(1)
                
                # Calculate date
                day_offset = min(session_num - 1, 6)
                session_date = (week_start + timedelta(days=day_offset)).strftime('%Y-%m-%d')
                
                session = Session(
                    id=f"w{week_num}-s{session_num}",
                    day=f"Day {session_num}",
                    type=session_type,
                    date=session_date,
                    priority=priority,
                    duration_minutes=duration_minutes,
                    description=full_text,
                    zones=zones,
                    scheduled=False,
                    completed=False
                )
                sessions.append(session)
        
        elif matches_2:
            # Use Format 2 (production)
            for match in matches_2:
                session_counter += 1
                priority_raw = match.group(1).strip()  # "KEY", "IMPORTANT", etc.
                day_of_week = match.group(2).strip()  # "Sun", "Tue", etc.
                session_title = match.group(3).strip()  # "Rivenhall XC (8km)"
                
                # Normalize priority
                priority = priority_raw.split()[0] if priority_raw else "NORMAL"
                full_text = f"{day_of_week}: {session_title}"
                
                # Type detection from session title
                title_lower = session_title.lower()
                if 'run' in title_lower or 'jog' in title_lower or 'xc' in title_lower or 'parkrun' in title_lower:
                    session_type = 'RUN'
                elif 'bike' in title_lower or 'ride' in title_lower or 'cycle' in title_lower or 'cycling' in title_lower or 'turbo' in title_lower or 'trainer' in title_lower:
                    session_type = 'BIKE'
                elif 'swim' in title_lower or 'pool' in title_lower or 'lake' in title_lower or 'dip' in title_lower:
                    session_type = 'SWIM'
                elif 's&c' in title_lower or 'routine' in title_lower or 'strength' in title_lower:
                    session_type = 'STRENGTH'
                elif 'walk' in title_lower:
                    session_type = 'OTHER'
                elif 'rest' in title_lower:
                    session_type = 'REST'
                else:
                    session_type = 'OTHER'
                
                # Duration extraction
                duration_minutes = None
                hour_match = re.search(r'(\d+)\s*(?:hour|hours|h(?:\s|:|$|\.|\,))', session_title, re.I)
                min_match = re.search(r'[~]?(\d+)\s*(?:min|mins|minutes)', session_title, re.I)
                if hour_match:
                    duration_minutes = int(hour_match.group(1)) * 60
                elif min_match:
                    duration_minutes = int(min_match.group(1))
                
                # Zone extraction
                zones = {}
                zone_match = re.search(r'(?:Zone|Z)\s*([0-9][/-]?[0-9]?)', session_title, re.I)
                if zone_match:
                    zones['hr'] = zone_match.group(1)
                
                # Map day to date
                day_map = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}
                day_offset = day_map.get(day_of_week[:3], session_counter - 1)
                session_date = (week_start + timedelta(days=day_offset)).strftime('%Y-%m-%d')
                
                session = Session(
                    id=f"w{week_num}-s{session_counter}",
                    day=day_of_week,
                    type=session_type,
                    date=session_date,
                    priority=priority,
                    duration_minutes=duration_minutes,
                    description=full_text,
                    zones=zones,
                    scheduled=False,
                    completed=False
                )
                sessions.append(session)
        
        total_sessions += len(sessions)
        
        week = Week(
            week_number=week_num,
            start_date=start_date,
            end_date=end_date,
            description=description,
            sessions=sessions
        )
        plan.weeks.append(week)
    
    print(f"✓ Created structured plan with {len(plan.weeks)} weeks")
    print(f"✓ Parsed {total_sessions} sessions")
    
    # Process all S&C sessions to add routine links
    s_and_c_count = 0
    for week in plan.weeks:
        for session in week.sessions:
            if session.type == 'STRENGTH':
                process_s_and_c_session(session)
                if session.s_and_c_routine:
                    s_and_c_count += 1
    
    if s_and_c_count > 0:
        print(f"✓ Linked {s_and_c_count} S&C sessions to library routines")
    
    return plan


def generate_markdown_from_v2(plan: TrainingPlan) -> str:
    """Generate markdown from TrainingPlan for backward compatibility"""
    lines = []
    for week in plan.weeks:
        lines.append(f"### Week {week.week_number}: {week.start_date} to {week.end_date}")
        if week.description:
            lines.append(f"**Context:** {week.description}")
        lines.append("")
        for i, session in enumerate(week.sessions, 1):
            lines.append(f"- **Session {i} [{session.priority}]:** {session.description}")
        lines.append("")
    return "\n".join(lines)


def validate_plan_structure(plan: TrainingPlan) -> List[str]:
    """Validate plan structure and return issues"""
    issues = []
    if not plan.weeks:
        issues.append("Plan has no weeks")
        return issues
    
    week_numbers = [w.week_number for w in plan.weeks]
    
    # Check if weeks are continuous (allow both 0-based and 1-based)
    expected_zero_based = list(range(len(week_numbers)))
    expected_one_based = list(range(1, len(week_numbers) + 1))
    
    if week_numbers != expected_zero_based and week_numbers != expected_one_based:
        issues.append(f"Week numbers not continuous: {week_numbers}")
    
    for week in plan.weeks:
        if not week.sessions:
            issues.append(f"Week {week.week_number} has no sessions")
        
        try:
            start = datetime.strptime(week.start_date, '%Y-%m-%d')
            end = datetime.strptime(week.end_date, '%Y-%m-%d')
            days = (end - start).days + 1
            if days != 7:
                issues.append(f"Week {week.week_number} is not 7 days")
        except ValueError:
            issues.append(f"Week {week.week_number} has invalid dates")
    
    return issues


def parse_ai_response_to_v2(ai_response: str, athlete_id: str, 
                           user_inputs: Dict[str, Any]) -> tuple:
    """
    Parse AI-generated training plan response into structured format.
    Used by ai_service.py when generating new plans.
    
    Args:
        ai_response: Raw AI response text
        athlete_id: Athlete ID
        user_inputs: User inputs (goal, sessions_per_week, etc.)
    
    Returns:
        Tuple of (TrainingPlan, markdown_text)
    """
    import json
    
    # Extract JSON metadata if present
    plan_data = None
    plan_markdown = ai_response
    
    json_match = re.search(r'```json\s*\n(.*?)\n```', ai_response, re.DOTALL)
    if json_match:
        try:
            plan_data = json.loads(json_match.group(1).strip())
            plan_markdown = ai_response[:json_match.start()].strip()
        except json.JSONDecodeError as e:
            print(f"⚠️  Failed to parse plan JSON: {e}")
    
    # Convert to structured format
    plan_v2 = migrate_plan_to_v2(
        plan_markdown=plan_markdown,
        plan_data={'weeks': plan_data['weeks']} if plan_data and 'weeks' in plan_data else None,
        athlete_id=athlete_id,
        user_inputs=user_inputs
    )
    
    return plan_v2, plan_markdown