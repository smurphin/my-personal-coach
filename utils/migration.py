"""
Migration utilities for converting existing markdown plans to structured plan_v2 format.
TESTED with real data: 62 sessions parsed successfully.
"""
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from models.training_plan import TrainingPlan, Week, Session


def migrate_plan_to_v2(plan_markdown: str, plan_data: Optional[Dict[str, Any]], 
                       athlete_id: str, user_inputs: Dict[str, Any]) -> TrainingPlan:
    """Convert existing markdown plan to structured TrainingPlan v2
    
    Args:
        plan_markdown: The markdown text of the training plan
        plan_data: Optional dict with 'weeks' metadata (dates, etc.)
        athlete_id: Athlete identifier
        user_inputs: Dict with goal, goal_date, plan_start_date, goal_distance
    
    Returns:
        TrainingPlan object with structured weeks and sessions
    """
    
    plan = TrainingPlan(
        version=2,
        athlete_id=athlete_id,
        athlete_goal=user_inputs.get('goal', ''),
        goal_date=user_inputs.get('goal_date'),
        goal_distance=user_inputs.get('goal_distance'),
        plan_start_date=user_inputs.get('plan_start_date')
    )
    
    # Get week dates from plan_structure JSON if available
    week_dates = {}
    if plan_data and 'weeks' in plan_data:
        for week_info in plan_data['weeks']:
            wn = week_info.get('week_number')
            if wn is not None and 'start_date' in week_info and 'end_date' in week_info:
                week_dates[wn] = (week_info['start_date'], week_info['end_date'])
        if week_dates:
            print(f"✓ Using plan_structure JSON for {len(week_dates)} weeks")
    
    # Fallback: parse week dates from markdown (content-based, ignores markdown decoration)
    if not week_dates:
        # Match content regardless of markdown decoration (###, **, or nothing)
        # This works for: ### Week 0: ..., **Week 0: ...**, Week 0: ..., etc.
        pattern = r'Week\s+(\d+):\s+(\w+\s+\d+(?:st|nd|rd|th)?)\s*-\s*(\w+\s+\d+(?:st|nd|rd|th)?)'
        for match in re.finditer(pattern, plan_markdown):
            week_num = int(match.group(1))
            try:
                current_year = datetime.now().year
                start_clean = re.sub(r'(\d+)(?:st|nd|rd|th)', r'\1', match.group(2))
                end_clean = re.sub(r'(\d+)(?:st|nd|rd|th)', r'\1', match.group(3))
                
                # Try full month name first (January), then abbreviated (Jan)
                try:
                    start_date = datetime.strptime(f"{start_clean} {current_year}", "%B %d %Y").date().isoformat()
                except ValueError:
                    start_date = datetime.strptime(f"{start_clean} {current_year}", "%b %d %Y").date().isoformat()
                
                try:
                    end_date = datetime.strptime(f"{end_clean} {current_year}", "%B %d %Y").date().isoformat()
                except ValueError:
                    end_date = datetime.strptime(f"{end_clean} {current_year}", "%b %d %Y").date().isoformat()
                
                week_dates[week_num] = (start_date, end_date)
            except Exception as e:
                print(f"⚠️  Could not parse dates for Week {week_num}: {e}")
        
        if week_dates:
            print(f"✓ Parsed {len(week_dates)} week dates from markdown")
    
    # Parse weeks and sessions
    # Content-based pattern: match "Week N:" regardless of markdown decoration
    # This works for: ### Week 0:, **Week 0:**, Week 0:, ## Week 0:, etc.
    week_pattern = r'Week\s+(\d+):'
    week_splits = list(re.finditer(week_pattern, plan_markdown))
    
    if not week_splits:
        print("❌ No weeks found - AI didn't use 'Week N:' format at all")
        return plan
    
    print(f"✓ Found {len(week_splits)} weeks in markdown")
    
    for idx, match in enumerate(week_splits):
        week_num = int(match.group(1))
        week_start_pos = match.end()
        week_end_pos = week_splits[idx + 1].start() if idx + 1 < len(week_splits) else len(plan_markdown)
        week_text = plan_markdown[week_start_pos:week_end_pos]
        
        # Get dates for this week
        start_date, end_date = week_dates.get(week_num, (None, None))
        
        # FALLBACK: Calculate dates from plan_start_date if missing
        if start_date is None or end_date is None:
            if plan.plan_start_date:
                try:
                    # Parse plan_start_date
                    if isinstance(plan.plan_start_date, str):
                        base_date = datetime.fromisoformat(plan.plan_start_date).date()
                    else:
                        base_date = plan.plan_start_date
                    
                    # Calculate week dates (assuming weeks start on Monday)
                    # Week 0 might be partial, but we still calculate from start date
                    days_offset = week_num * 7
                    calc_start = base_date + timedelta(days=days_offset)
                    calc_end = calc_start + timedelta(days=6)  # Week is 7 days (Mon-Sun)
                    
                    start_date = calc_start.isoformat()
                    end_date = calc_end.isoformat()
                    
                    if week_num == 0:
                        print(f"   Week {week_num}: ⚠️  Dates calculated from plan_start_date (may be partial week)")
                    else:
                        print(f"   Week {week_num}: ⚠️  Dates calculated from plan_start_date")
                except Exception as e:
                    print(f"   Week {week_num}: ⚠️  Could not calculate dates: {e}")
                    # Keep None values - dashboard will handle gracefully
        
        # === SESSION PARSING ===
        
        # NEW format: Multi-line with nested bullets
        # *   **Run 1: Hill Repeats** [KEY]
        #     *   Duration: ...
        #     *   Description: ...
        session_pattern_new = r'^\*\s+\*\*([A-Za-z&\s\d]+):\s+([^\*]+)\*\*\s+\[([^\]]+)\]'
        matches_new = list(re.finditer(session_pattern_new, week_text, re.MULTILINE))
        
        # Current AI format: **Type: Description** [PRIORITY] (single line)
        # Bullet point is optional (AI sometimes forgets it)
        # Matches both: *   **Run: ...** [KEY] and **Run: ...** [KEY]
        # Also handles day prefixes like: **Tue Jan 20:** **Run: ...** [KEY] or **Mon:** **Run: ...** [KEY]
        # Pattern allows optional day prefix (e.g., **Mon:** or **Tue Jan 20:**) before the session pattern
        # The pattern doesn't require line start, allowing it to match anywhere in the line
        session_pattern_current = r'(?:\*\s+)?(?:\*\*[^:]+:\*\*\s+)?\*\*([A-Za-z&\s]+):\s+([^\*]+)\*\*\s+\[([^\]]+)\]'
        matches_current = list(re.finditer(session_pattern_current, week_text, re.MULTILINE))
        
        # Legacy formats (for backward compatibility with existing DB plans)
        session_pattern_1 = r'^\*\s+\*\*([A-Za-z&\s]+?)\s*(\d+)?\s*\[([^\]]+)\]:\s*([^\*\n]+)'  # Staging format
        matches_1 = list(re.finditer(session_pattern_1, week_text, re.MULTILINE))
        
        session_pattern_2 = r'^\*\s+\*\*\[([^\]]+)\]\s+([A-Za-z]+):\s*([^\*\n]+)'  # Production format with day
        matches_2 = list(re.finditer(session_pattern_2, week_text, re.MULTILINE))
        
        session_pattern_3 = r'^-\s+\*\*Session\s+(\d+)\s+\[([^\]]+)\]:\*\*\s+(.+)'  # Old migrated plans
        matches_3 = list(re.finditer(session_pattern_3, week_text, re.MULTILINE))
        
        session_pattern_4 = r'^\*\s+\*\*\[([^\]]+)\]\s+([^:]+):\*\*\s+(.+)'  # Priority before type
        matches_4 = list(re.finditer(session_pattern_4, week_text, re.MULTILINE))
        
        sessions = []
        session_counter = 0
        
        if matches_new:
            # Use new multi-line format with nested bullets
            print(f"   Week {week_num}: Using new multi-line format (**Type: Name** [PRIORITY] with nested bullets)")
            lines = week_text.split('\n')
            
            for match in matches_new:
                session_counter += 1
                activity_type_raw = match.group(1).strip()
                session_name = match.group(2).strip()
                priority = match.group(3).strip().upper()
                
                # Find the line number of this match
                match_line_num = week_text[:match.start()].count('\n')
                
                # Look ahead for nested bullets (Duration and Description)
                duration_text = ""
                description_text = ""
                
                # Look at lines after the header (up to next session or end of week)
                for i in range(match_line_num + 1, len(lines)):
                    line = lines[i].strip()
                    
                    # Stop if we hit another session header
                    if re.match(r'^\*\s+\*\*[A-Za-z]', line):
                        break
                    
                    # Stop if we hit another week
                    if re.match(r'^###?\s+Week\s+\d+', line):
                        break
                    
                    # Stop if we hit a blank line followed by another session (likely next session)
                    if i > match_line_num + 1 and not line and i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        if re.match(r'^\*\s+\*\*[A-Za-z]', next_line):
                            break
                    
                    # Extract Duration
                    if re.match(r'^\*\s+\*\*?Duration:', line, re.IGNORECASE):
                        duration_text = re.sub(r'^\*\s+\*\*?Duration:\s*', '', line, flags=re.IGNORECASE).strip()
                        # Duration might continue on next lines if they're indented
                        j = i + 1
                        while j < len(lines):
                            next_line = lines[j].strip()
                            # Stop if we hit another bullet or header
                            if (re.match(r'^\*\s+\*\*?', next_line) or 
                                re.match(r'^###?\s+Week', next_line) or
                                not next_line):
                                break
                            # If it's indented (starts with spaces), it's continuation
                            if lines[j].startswith('    ') or lines[j].startswith('\t'):
                                duration_text += " " + next_line
                                j += 1
                            else:
                                break
                    
                    # Extract Description
                    elif re.match(r'^\*\s+\*\*?Description:', line, re.IGNORECASE):
                        description_text = re.sub(r'^\*\s+\*\*?Description:\s*', '', line, flags=re.IGNORECASE).strip()
                        # Description might continue on next lines if they're indented
                        j = i + 1
                        while j < len(lines):
                            next_line = lines[j].strip()
                            # Stop if we hit another bullet or header
                            if (re.match(r'^\*\s+\*\*?', next_line) or 
                                re.match(r'^###?\s+Week', next_line) or
                                not next_line):
                                break
                            # If it's indented (starts with spaces), it's continuation
                            if lines[j].startswith('    ') or lines[j].startswith('\t'):
                                description_text += " " + next_line
                                j += 1
                            else:
                                break
                
                # Combine session name, duration, and description for full context
                # Include duration_text in description as it contains important workout structure
                description_parts = []
                if duration_text:
                    description_parts.append(f"Duration: {duration_text}")
                if description_text:
                    description_parts.append(description_text)
                
                if description_parts:
                    full_description = f"{session_name}. {' '.join(description_parts)}"
                else:
                    full_description = session_name
                
                # Determine session type
                combined_text = (activity_type_raw + " " + full_description).lower()
                
                if any(x in combined_text for x in ['run', 'jog', 'parkrun', 'xc', 'cross country', 'track']):
                    session_type = 'RUN'
                elif any(x in combined_text for x in ['bike', 'cycling', 'cycle', 'ride', 'turbo', 'spin', 'trainer']):
                    session_type = 'BIKE'
                elif any(x in combined_text for x in ['swim', 'pool', 'lake', 'dip']):
                    session_type = 'SWIM'
                elif any(x in combined_text for x in ['s&c', 'strength', 'routine', 'gym', 'mobility', 'cross-training']):
                    session_type = 'STRENGTH'
                elif any(x in combined_text for x in ['cross-train', 'cross train']):
                    session_type = 'CROSS_TRAIN'
                else:
                    session_type = 'OTHER'
                
                # Extract duration from duration_text or description (for duration_minutes field)
                duration_minutes = None
                if duration_text:
                    # Try to find total duration (sum of all minutes mentioned)
                    duration_matches = re.findall(r'(\d+)\s*(?:min|mins|minutes)', duration_text, re.IGNORECASE)
                    if duration_matches:
                        # Sum all durations found (for intervals, use the main duration)
                        # For structured workouts, use the first/main duration
                        duration_minutes = int(duration_matches[0])
                else:
                    # Fallback: try to extract from description
                    duration_match = re.search(r'(\d+)\s*(?:min|mins|minutes)', full_description, re.IGNORECASE)
                    if duration_match:
                        duration_minutes = int(duration_match.group(1))
                
                # Extract zones from both duration and description
                zones = {}
                # Search in combined text (duration + description) for zones
                search_text = f"{duration_text} {description_text}" if duration_text and description_text else (duration_text or description_text or "")
                zone_match = re.search(r'[Zz]one\s*(\d+)(?:\s*[/-]\s*(\d+))?', search_text)
                if zone_match:
                    if zone_match.group(2):
                        zones['hr'] = f"{zone_match.group(1)}-{zone_match.group(2)}"
                    else:
                        zones['hr'] = zone_match.group(1)
                
                # Extract all pace information from full_description (includes both duration and description)
                # Look for labeled paces first (e.g., "Easy pace: 06:01/km", "Interval pace: 04:36/km")
                pace_matches = re.findall(r'(?:Easy|Marathon|Threshold|Interval|Repetition)\s+pace:\s*~?(\d+):(\d+)/km', full_description, re.IGNORECASE)
                if pace_matches:
                    # Store all paces found
                    pace_list = [f"{m[0]}:{m[1]}/km" for m in pace_matches]
                    zones['pace'] = ", ".join(pace_list) if len(pace_list) > 1 else pace_list[0]
                else:
                    # Fallback: extract any pace mentioned (e.g., "at ~6:01/km pace")
                    pace_match = re.search(r'~?(\d+):(\d+)/km', full_description)
                    if pace_match:
                        zones['pace'] = f"{pace_match.group(1)}:{pace_match.group(2)}/km"
                
                # Extract S&C routine name for linking
                s_and_c_routine = None
                if session_type == 'STRENGTH':
                    # Look for "Routine N" or routine name
                    routine_match = re.search(r'[Rr]outine\s+(\d+)', full_description)
                    if routine_match:
                        s_and_c_routine = f"routine_{routine_match.group(1)}"
                    else:
                        # Try to extract routine name from description
                        routine_match = re.search(r'S&C[:\s]+([^,]+)', full_description, re.IGNORECASE)
                        if routine_match:
                            routine_name = routine_match.group(1).strip()
                            # Map common names to routine IDs
                            if 'foundation' in routine_name.lower() or 'routine 1' in routine_name.lower():
                                s_and_c_routine = "routine_1_core"
                            elif 'core' in routine_name.lower() and 'posterior' in routine_name.lower() or 'routine 2' in routine_name.lower():
                                s_and_c_routine = "routine_2_core"
                            elif 'dynamic' in routine_name.lower() or 'routine 3' in routine_name.lower():
                                s_and_c_routine = "routine_3_dynamic"
                            elif 'full body' in routine_name.lower() or 'circuit' in routine_name.lower() or 'routine 4' in routine_name.lower():
                                s_and_c_routine = "routine_4_circuit"
                
                session = Session(
                    id=f"w{week_num}-s{session_counter}",
                    day="Anytime",
                    type=session_type,
                    date=start_date,
                    priority=priority,
                    duration_minutes=duration_minutes,
                    description=full_description,
                    zones=zones,
                    s_and_c_routine=s_and_c_routine,
                    scheduled=False,
                    completed=False
                )
                sessions.append(session)
        
        elif matches_current:
            # Use current format
            print(f"   Week {week_num}: Using current format (**Type: Description** [PRIORITY])")
            for match in matches_current:
                session_counter += 1
                activity_type_raw = match.group(1).strip()
                description_without_type = match.group(2).strip()
                priority = match.group(3).strip().upper()
                
                # Determine session type from activity_type_raw AND description
                combined_text = (activity_type_raw + " " + description_without_type).lower()
                
                if any(x in combined_text for x in ['run', 'jog', 'parkrun', 'xc', 'cross country', 'track']):
                    session_type = 'RUN'
                elif any(x in combined_text for x in ['bike', 'cycling', 'cycle', 'ride', 'turbo', 'spin', 'trainer']):
                    session_type = 'BIKE'
                elif any(x in combined_text for x in ['swim', 'pool', 'lake', 'dip']):
                    session_type = 'SWIM'
                elif any(x in combined_text for x in ['s&c', 'strength', 'routine', 'gym', 'mobility']):
                    session_type = 'STRENGTH'
                else:
                    session_type = 'OTHER'
                
                # For STRENGTH sessions, preserve "S&C:" prefix so s_and_c_utils can match it
                if session_type == 'STRENGTH':
                    description = f"{activity_type_raw}: {description_without_type}"
                else:
                    description = description_without_type
                
                # Extract duration
                duration_match = re.search(r'(\d+)\s*(?:min|mins|minutes)', description, re.IGNORECASE)
                duration_minutes = int(duration_match.group(1)) if duration_match else None
                
                # Extract zones
                zones = {}
                zone_match = re.search(r'[Zz]one\s*(\d+)(?:\s*-\s*(\d+))?', description)
                if zone_match:
                    if zone_match.group(2):
                        zones['hr'] = f"{zone_match.group(1)}-{zone_match.group(2)}"
                    else:
                        zones['hr'] = zone_match.group(1)
                
                # Extract S&C routine name for linking
                s_and_c_routine = None
                if session_type == 'STRENGTH':
                    # Description format: "S&C: Core Focus, 20 mins"
                    # Extract "Core Focus" part
                    routine_match = re.search(r'S&C:\s*([^,]+)', description, re.IGNORECASE)
                    if routine_match:
                        s_and_c_routine = routine_match.group(1).strip()
                
                session = Session(
                    id=f"w{week_num}-s{session_counter}",
                    day="Anytime",
                    type=session_type,
                    date=start_date,
                    priority=priority,
                    duration_minutes=duration_minutes,
                    description=description,
                    zones=zones,
                    s_and_c_routine=s_and_c_routine,  # Add routine name for linking
                    scheduled=False,
                    completed=False
                )
                sessions.append(session)
        
        elif matches_1:
            # Use Format 1 (staging)
            print(f"   Week {week_num}: Using Format 1 (staging format)")
            for match in matches_1:
                session_counter += 1
                session_prefix = match.group(1).strip()
                session_num_str = match.group(2)
                session_num = int(session_num_str) if session_num_str else session_counter
                priority_raw = match.group(3).strip()
                session_title = match.group(4).strip()
                
                priority = priority_raw.split()[0] if priority_raw else "NORMAL"
                full_text = f"{session_prefix}: {session_title}"
                
                if 'run' in session_prefix.lower() or 'xc' in session_title.lower():
                    session_type = 'RUN'
                elif 'bike' in session_prefix.lower() or 'cycle' in session_prefix.lower() or 'ride' in session_title.lower():
                    session_type = 'BIKE'
                elif 'swim' in session_prefix.lower():
                    session_type = 'SWIM'
                elif 's&c' in session_prefix.lower() or 'strength' in session_title.lower():
                    session_type = 'STRENGTH'
                    full_text = f"{session_prefix}: {session_title}"
                else:
                    session_type = 'OTHER'
                
                duration_match = re.search(r'(\d+)\s*(?:min|mins|minutes)', session_title, re.IGNORECASE)
                duration_minutes = int(duration_match.group(1)) if duration_match else None
                
                zones = {}
                zone_match = re.search(r'[Zz]one\s*(\d+)(?:\s*-\s*(\d+))?', session_title)
                if zone_match:
                    if zone_match.group(2):
                        zones['hr'] = f"{zone_match.group(1)}-{zone_match.group(2)}"
                    else:
                        zones['hr'] = zone_match.group(1)
                
                # Extract S&C routine for linking
                s_and_c_routine = None
                if session_type == 'STRENGTH':
                    routine_match = re.search(r'S&C:\s*([^,]+)', full_text, re.IGNORECASE)
                    if routine_match:
                        s_and_c_routine = routine_match.group(1).strip()
                
                session = Session(
                    id=f"w{week_num}-s{session_counter}",
                    day="Anytime",
                    type=session_type,
                    date=start_date,
                    priority=priority,
                    duration_minutes=duration_minutes,
                    description=full_text,
                    zones=zones,
                    s_and_c_routine=s_and_c_routine,
                    scheduled=False,
                    completed=False
                )
                sessions.append(session)
        
        elif matches_2:
            # Use Format 2 (production)
            print(f"   Week {week_num}: Using Format 2 (production format)")
            for match in matches_2:
                session_counter += 1
                priority = match.group(1).strip()
                day_of_week = match.group(2).strip()
                description = match.group(3).strip()
                
                if any(x in description.lower() for x in ['run', 'jog', 'parkrun', 'xc', 'track']):
                    session_type = 'RUN'
                elif any(x in description.lower() for x in ['bike', 'ride', 'cycle', 'turbo']):
                    session_type = 'BIKE'
                elif 'swim' in description.lower():
                    session_type = 'SWIM'
                elif 's&c' in description.lower() or 'strength' in description.lower():
                    session_type = 'STRENGTH'
                else:
                    session_type = 'OTHER'
                
                duration_match = re.search(r'(\d+)\s*(?:min|mins|minutes|km)', description, re.IGNORECASE)
                duration_minutes = int(duration_match.group(1)) if duration_match and 'min' in duration_match.group(0).lower() else None
                
                zones = {}
                zone_match = re.search(r'[Zz]one\s*(\d+)(?:\s*-\s*(\d+))?', description)
                if zone_match:
                    if zone_match.group(2):
                        zones['hr'] = f"{zone_match.group(1)}-{zone_match.group(2)}"
                    else:
                        zones['hr'] = zone_match.group(1)
                
                # Extract S&C routine for linking
                s_and_c_routine = None
                if session_type == 'STRENGTH':
                    routine_match = re.search(r'S&C:\s*([^,]+)', description, re.IGNORECASE)
                    if routine_match:
                        s_and_c_routine = routine_match.group(1).strip()
                
                session = Session(
                    id=f"w{week_num}-s{session_counter}",
                    day=day_of_week,
                    type=session_type,
                    date=start_date,
                    priority=priority,
                    duration_minutes=duration_minutes,
                    description=description,
                    zones=zones,
                    s_and_c_routine=s_and_c_routine,
                    scheduled=False,
                    completed=False
                )
                sessions.append(session)
        
        elif matches_3:
            # Use Format 3 (old migrated)
            print(f"   Week {week_num}: Using Format 3 (old migrated format)")
            for match in matches_3:
                session_counter += 1
                session_num = int(match.group(1))
                priority = match.group(2).strip()
                description = match.group(3).strip()
                
                if any(x in description.lower() for x in ['run', 'jog', 'parkrun']):
                    session_type = 'RUN'
                elif any(x in description.lower() for x in ['bike', 'ride', 'cycle']):
                    session_type = 'BIKE'
                elif 'swim' in description.lower():
                    session_type = 'SWIM'
                elif 's&c' in description.lower() or 'strength' in description.lower():
                    session_type = 'STRENGTH'
                else:
                    session_type = 'OTHER'
                
                duration_match = re.search(r'(\d+)\s*(?:min|mins|minutes)', description, re.IGNORECASE)
                duration_minutes = int(duration_match.group(1)) if duration_match else None
                
                zones = {}
                zone_match = re.search(r'[Zz]one\s*(\d+)(?:\s*-\s*(\d+))?', description)
                if zone_match:
                    if zone_match.group(2):
                        zones['hr'] = f"{zone_match.group(1)}-{zone_match.group(2)}"
                    else:
                        zones['hr'] = zone_match.group(1)
                
                # Extract S&C routine for linking
                s_and_c_routine = None
                if session_type == 'STRENGTH':
                    routine_match = re.search(r'S&C:\s*([^,]+)', description, re.IGNORECASE)
                    if routine_match:
                        s_and_c_routine = routine_match.group(1).strip()
                
                session = Session(
                    id=f"w{week_num}-s{session_counter}",
                    day="Anytime",
                    type=session_type,
                    date=start_date,
                    priority=priority,
                    duration_minutes=duration_minutes,
                    description=description,
                    zones=zones,
                    s_and_c_routine=s_and_c_routine,
                    scheduled=False,
                    completed=False
                )
                sessions.append(session)
        
        elif matches_4:
            # Use Format 4
            print(f"   Week {week_num}: Using Format 4 (priority before type)")
            for match in matches_4:
                session_counter += 1
                priority = match.group(1).strip()
                activity_and_details = match.group(2).strip()
                description = match.group(3).strip()
                
                full_description = f"{activity_and_details}: {description}"
                
                if any(x in full_description.lower() for x in ['run', 'jog', 'parkrun']):
                    session_type = 'RUN'
                elif any(x in full_description.lower() for x in ['bike', 'ride', 'cycle']):
                    session_type = 'BIKE'
                elif 'swim' in full_description.lower():
                    session_type = 'SWIM'
                elif 's&c' in full_description.lower() or 'strength' in full_description.lower():
                    session_type = 'STRENGTH'
                else:
                    session_type = 'OTHER'
                
                duration_match = re.search(r'(\d+)\s*(?:min|mins|minutes)', full_description, re.IGNORECASE)
                duration_minutes = int(duration_match.group(1)) if duration_match else None
                
                zones = {}
                zone_match = re.search(r'[Zz]one\s*(\d+)(?:\s*-\s*(\d+))?', full_description)
                if zone_match:
                    if zone_match.group(2):
                        zones['hr'] = f"{zone_match.group(1)}-{zone_match.group(2)}"
                    else:
                        zones['hr'] = zone_match.group(1)
                
                # Extract S&C routine for linking
                s_and_c_routine = None
                if session_type == 'STRENGTH':
                    routine_match = re.search(r'S&C:\s*([^,]+)', full_description, re.IGNORECASE)
                    if routine_match:
                        s_and_c_routine = routine_match.group(1).strip()
                
                session = Session(
                    id=f"w{week_num}-s{session_counter}",
                    day="Anytime",
                    type=session_type,
                    date=start_date,
                    priority=priority,
                    duration_minutes=duration_minutes,
                    description=full_description,
                    zones=zones,
                    s_and_c_routine=s_and_c_routine,
                    scheduled=False,
                    completed=False
                )
                sessions.append(session)
        
        else:
            print(f"   Week {week_num}: ⚠️  No sessions matched any format")
        
        # Create week object
        week = Week(
            week_number=week_num,
            start_date=start_date,
            end_date=end_date,
            sessions=sessions
        )
        plan.weeks.append(week)
    
    total_sessions = sum(len(w.sessions) for w in plan.weeks)
    print(f"✓ Created structured plan with {len(plan.weeks)} weeks")
    print(f"✓ Parsed {total_sessions} sessions")
    
    return plan


def parse_ai_response_to_v2(ai_response: str, athlete_id: str, 
                            user_inputs: Dict[str, Any]) -> Tuple[TrainingPlan, str]:
    """Parse AI response into TrainingPlan v2 format
    
    Used by ai_service.py when generating new plans from AI.
    
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