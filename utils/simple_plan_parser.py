"""
Simplified, format-agnostic plan parser.

This parser focuses on CONTENT, not formatting. It:
- Strips all markdown decoration (**, #, *, etc.)
- Looks for week headers and session patterns
- Extracts attributes from free text (duration, zones, priority)
- Is resilient to formatting variations

This is used as a fallback when JSON-first generation fails.
"""
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from models.training_plan import TrainingPlan, Week, Session


def strip_markdown(text: str) -> str:
    """
    Remove markdown decoration from text, keeping only content.
    
    Examples:
    - "**Run: Easy**" -> "Run: Easy"
    - "### Week 1:" -> "Week 1:"
    - "*   **Session**" -> "Session"
    """
    # Remove markdown headers
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    # Remove bold/italic
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    # Remove bullet points
    text = re.sub(r'^\s*[\*\-]\s+', '', text, flags=re.MULTILINE)
    # Remove code blocks
    text = re.sub(r'```[^`]*```', '', text, flags=re.DOTALL)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text


def normalize_text(text: str) -> str:
    """
    Normalize text for pattern matching:
    - Convert fancy dashes to simple dashes
    - Normalize whitespace
    - Lowercase for type detection
    """
    text = text.replace('–', '-').replace('—', '-')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_week_info(line: str) -> Optional[Tuple[int, Optional[str], Optional[str]]]:
    """
    Extract week number and dates from a line.
    
    Handles formats like:
    - "Week 1: Jan 1st - Jan 7th"
    - "Week 1: January 1 - January 7"
    - "### Week 1: Jan 1 - Jan 7"
    
    Returns:
        Tuple of (week_number, start_date_str, end_date_str) or None
    """
    # Pattern: Week N: date - date
    pattern = r'Week\s+(\d+):\s*(.+?)\s*-\s*(.+?)(?:\s*\(|$)'
    match = re.search(pattern, line, re.IGNORECASE)
    
    if not match:
        return None
    
    week_num = int(match.group(1))
    start_str = match.group(2).strip()
    end_str = match.group(3).strip()
    
    # Try to parse dates (simplified - just extract, don't validate)
    return (week_num, start_str, end_str)


def detect_session_type(text: str) -> str:
    """
    Detect session type from text content.
    
    Returns one of: RUN, BIKE, SWIM, STRENGTH, OTHER, REST
    """
    text_lower = text.lower()
    
    if any(x in text_lower for x in ['run', 'jog', 'parkrun', 'xc', 'cross country', 'track', 'trail']):
        return 'RUN'
    elif any(x in text_lower for x in ['bike', 'cycling', 'cycle', 'ride', 'turbo', 'spin', 'trainer']):
        return 'BIKE'
    elif any(x in text_lower for x in ['swim', 'pool', 'lake', 'dip']):
        return 'SWIM'
    elif any(x in text_lower for x in ['s&c', 'strength', 'routine', 'gym', 'mobility', 'cross-training']):
        return 'STRENGTH'
    elif any(x in text_lower for x in ['rest', 'recovery day', 'off day']):
        return 'REST'
    else:
        return 'OTHER'


def extract_priority(text: str) -> Optional[str]:
    """
    Extract priority from text.
    
    Looks for: [KEY], [IMPORTANT], [STRETCH] or words "key", "important", "stretch"
    """
    # Check for bracket notation
    priority_match = re.search(r'\[(KEY|IMPORTANT|STRETCH)\]', text, re.IGNORECASE)
    if priority_match:
        return priority_match.group(1).upper()
    
    # Check for word mentions
    text_lower = text.lower()
    if 'key' in text_lower and 'important' not in text_lower:
        return 'KEY'
    elif 'important' in text_lower:
        return 'IMPORTANT'
    elif 'stretch' in text_lower:
        return 'STRETCH'
    
    return None


def extract_duration(text: str) -> Optional[int]:
    """
    Extract duration in minutes from text.
    
    Handles:
    - "60 mins", "60 minutes", "1 hour", "2h15", "2 hours 15 minutes"
    """
    # Pattern for "X mins" or "X minutes"
    match = re.search(r'(\d+)\s*(?:min|mins|minutes)', text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    
    # Pattern for "X hour(s)" or "Xh"
    match = re.search(r'(\d+)\s*h(?:our|ours)?', text, re.IGNORECASE)
    if match:
        hours = int(match.group(1))
        # Check for additional minutes (e.g., "2h15")
        mins_match = re.search(r'(\d+)h\s*(\d+)', text, re.IGNORECASE)
        if mins_match:
            return hours * 60 + int(mins_match.group(2))
        return hours * 60
    
    return None


def extract_zones(text: str) -> Dict[str, Any]:
    """
    Extract zone information from text.
    
    Returns dict with 'hr', 'pace', 'power' keys as appropriate.
    """
    zones = {}
    
    # Heart rate zones: "Zone 2", "Z2", "Zone 3-4", "141-148 bpm"
    hr_match = re.search(r'[Zz]one\s*(\d+)(?:\s*[/-]\s*(\d+))?', text)
    if hr_match:
        if hr_match.group(2):
            zones['hr'] = f"{hr_match.group(1)}-{hr_match.group(2)}"
        else:
            zones['hr'] = hr_match.group(1)
    else:
        # Try bpm format: "141-148 bpm"
        bpm_match = re.search(r'(\d+)\s*-\s*(\d+)\s*bpm', text, re.IGNORECASE)
        if bpm_match:
            zones['hr'] = f"{bpm_match.group(1)}-{bpm_match.group(2)}"
    
    # Pace: "5:30/km", "9:47 - 10:24 min/mile"
    pace_match = re.search(r'(\d+):(\d+)\s*/km', text)
    if pace_match:
        zones['pace'] = f"{pace_match.group(1)}:{pace_match.group(2)}/km"
    else:
        pace_match = re.search(r'(\d+):(\d+)\s*(?:-|to)\s*(\d+):(\d+)\s*min/mile', text)
        if pace_match:
            zones['pace'] = f"{pace_match.group(1)}:{pace_match.group(2)}-{pace_match.group(3)}:{pace_match.group(4)}/mile"
    
    # Power: "250W", "Zone 4 power"
    power_match = re.search(r'(\d+)\s*W', text, re.IGNORECASE)
    if power_match:
        zones['power'] = power_match.group(1)
    
    return zones


def parse_plan_simple(plan_markdown: str, plan_data: Optional[Dict[str, Any]], 
                      athlete_id: str, user_inputs: Dict[str, Any]) -> TrainingPlan:
    """
    Parse a training plan from markdown using simplified, format-agnostic approach.
    
    This parser:
    1. Strips markdown decoration
    2. Finds week headers (any format)
    3. Finds session lines (any format that mentions a type)
    4. Extracts attributes from free text
    
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
    
    # Split into lines for processing
    lines = plan_markdown.split('\n')
    
    # Find all week headers
    week_headers = []
    for i, line in enumerate(lines):
        week_info = extract_week_info(line)
        if week_info:
            week_headers.append((i, week_info[0], week_info[1], week_info[2]))
    
    if not week_headers:
        print("⚠️  No week headers found in plan")
        return plan
    
    print(f"✓ Found {len(week_headers)} weeks using simple parser")
    
    # Process each week
    for week_idx, (line_num, week_num, start_date_str, end_date_str) in enumerate(week_headers):
        # Get week text (from this header to next header or end)
        week_start = line_num + 1
        week_end = week_headers[week_idx + 1][0] if week_idx + 1 < len(week_headers) else len(lines)
        week_lines = lines[week_start:week_end]
        week_text = '\n'.join(week_lines)
        
        # Get dates
        start_date, end_date = week_dates.get(week_num, (None, None))
        
        # Try to parse dates from strings if not in week_dates
        if not start_date and start_date_str:
            # Simplified date parsing (could be improved)
            try:
                current_year = datetime.now().year
                # Remove ordinal suffixes
                start_clean = re.sub(r'(\d+)(?:st|nd|rd|th)', r'\1', start_date_str)
                end_clean = re.sub(r'(\d+)(?:st|nd|rd|th)', r'\1', end_date_str)
                
                # Try parsing (simplified - may fail for some formats)
                for fmt in ["%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"]:
                    try:
                        start_date = datetime.strptime(f"{start_clean} {current_year}", fmt).date().isoformat()
                        end_date = datetime.strptime(f"{end_clean} {current_year}", fmt).date().isoformat()
                        break
                    except ValueError:
                        continue
            except Exception as e:
                print(f"   Week {week_num}: Could not parse dates: {e}")
        
        # Find sessions in this week
        sessions = []
        session_counter = 0
        
        # Look for lines that look like sessions
        # A session line typically:
        # - Contains a type word (Run, Bike, S&C, etc.)
        # - Has a colon (separating type from description)
        # - May have priority marker
        
        session_pattern = r'^(?:.*?)?(Run|Ride|Bike|S&C|Strength|Swim|Cycle|Rest|Recovery)[:\-]\s*(.+?)(?:\s*\[(KEY|IMPORTANT|STRETCH)\]|$)'
        
        for line in week_lines:
            line_normalized = normalize_text(line)
            
            # Check if this looks like a session header
            match = re.search(session_pattern, line_normalized, re.IGNORECASE)
            if match:
                session_counter += 1
                type_raw = match.group(1)
                description_raw = match.group(2).strip()
                priority_raw = match.group(3) if len(match.groups()) > 2 and match.group(3) else None
                
                # Get full description (may continue on next lines)
                full_description = description_raw
                line_idx = week_lines.index(line)
                
                # Look ahead for continuation lines (not starting with type words)
                for next_line in week_lines[line_idx + 1:]:
                    next_normalized = normalize_text(next_line)
                    # Stop if we hit another session
                    if re.search(session_pattern, next_normalized, re.IGNORECASE):
                        break
                    # Stop if we hit a week header
                    if extract_week_info(next_line):
                        break
                    # Add continuation to description
                    if next_normalized and not next_normalized.startswith(('week', '###')):
                        full_description += " " + next_normalized
                
                # Determine session type
                session_type = detect_session_type(type_raw + " " + full_description)
                
                # Extract priority
                priority = priority_raw.upper() if priority_raw else extract_priority(full_description)
                
                # Extract duration
                duration_minutes = extract_duration(full_description)
                
                # Extract zones
                zones = extract_zones(full_description)
                
                # Extract S&C routine if applicable
                s_and_c_routine = None
                if session_type == 'STRENGTH':
                    routine_match = re.search(r'S&C[:\s]+([^,]+)', full_description, re.IGNORECASE)
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
        
        week = Week(
            week_number=week_num,
            start_date=start_date,
            end_date=end_date,
            description="",
            sessions=sessions
        )
        plan.weeks.append(week)
        
        if sessions:
            print(f"   Week {week_num}: Found {len(sessions)} sessions")
        else:
            print(f"   Week {week_num}: ⚠️  No sessions found")
    
    total_sessions = sum(len(w.sessions) for w in plan.weeks)
    print(f"✓ Parsed {total_sessions} sessions using simple parser")
    
    return plan

