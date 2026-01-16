#!/usr/bin/env python3
"""
DynamoDB Migration Script

Migrates markdown plans to structured plan_v2 format.

Usage:
    # Dry run (see what would be migrated)
    python migrate_dynamodb.py --env staging --athlete-id 196048876 --dry-run

    # Execute migration
    python migrate_dynamodb.py --env production --athlete-id 2117356 --execute
"""
import argparse
import json
import os
import re
import boto3
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple




# ============================================================================
# DynamoDB Helpers
# ============================================================================

def convert_decimals(obj):
    """Convert DynamoDB Decimals to int/float."""
    if isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    elif isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def convert_to_decimals(obj):
    """Convert int/float to DynamoDB Decimals."""
    if isinstance(obj, dict):
        return {k: convert_to_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_decimals(i) for i in obj]
    elif isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, int):
        return Decimal(obj)
    return obj


# ============================================================================
# Date Parsing
# ============================================================================

def convert_date_to_iso(date_str: str, year: int = None) -> str:
    """Convert date strings like 'Jan 6th' or 'December 15th' to ISO format."""
    if year is None:
        year = datetime.now().year
    
    # Remove ordinal suffixes (st, nd, rd, th)
    match = re.match(r'([A-Za-z]+)\s+(\d+)(?:st|nd|rd|th)?', date_str.strip())
    if not match:
        return date_str
    
    month_str = match.group(1)
    day = int(match.group(2))
    
    # Try short month name first (%b), then full (%B)
    for fmt in ["%b %d %Y", "%B %d %Y"]:
        try:
            date_obj = datetime.strptime(f"{month_str} {day} {year}", fmt)
            return date_obj.strftime('%Y-%m-%d')
        except:
            continue
    
    return date_str


def parse_week_header(line: str) -> Optional[Dict[str, Any]]:
    """Parse week header line to extract week number and dates."""
    # Format: ### Week 1: December 15th - December 21st
    # Also handles: **Week 1:** without dates
    pattern = r'(?:###\s+)?(?:\*\*)?Week\s+(\d+)(?:\*\*)?:\s+(.+?)\s+-\s+(.+?)(?:\s+\(|$|\*\*)'
    match = re.search(pattern, line, re.IGNORECASE)
    
    if match:
        week_num = int(match.group(1))
        start_date_str = match.group(2).strip().rstrip('*')
        end_date_str = match.group(3).strip().rstrip('*')
        
        current_year = datetime.now().year
        
        result = {
            'week_number': week_num,
            'start_date': convert_date_to_iso(start_date_str, current_year),
            'end_date': convert_date_to_iso(end_date_str, current_year)
        }
        
        return result
    
    return None


# ============================================================================
# Session Parsing with Debug
# ============================================================================

def extract_workout_details(lines: List[str], session_line_idx: int) -> str:
    """
    Extract workout details from follow-up lines after a session header.
    Looks for lines like:
    *   **Workout:** ...
    *   **Purpose:** ...
    *   **Duration:** ...
    *   **Instruction:** ...
    
    Returns a consolidated string with all workout details.
    """
    details = []
    idx = session_line_idx + 1
    
    # Look ahead up to 10 lines for workout details
    while idx < len(lines) and idx < session_line_idx + 10:
        line = lines[idx]
        line_stripped = line.strip()
        
        # Stop if we hit another session header (starts with ** and contains [PRIORITY] or is a session type)
        if re.match(r'^\s*[\*\-]\s+\*\*.*\[(KEY|IMPORTANT|STRETCH)\]:', line_stripped, re.IGNORECASE):
            break
        if re.match(r'^\s*[\*\-]\s+\*\*(Run|Ride|S&C|Swim|Bike|Cycle|Strength)\s+\d+', line_stripped, re.IGNORECASE):
            break
        if re.match(r'^\s*[\*\-]\s+\*\*S&C', line_stripped, re.IGNORECASE):
            break
        
        # Stop if we hit a week header
        if re.match(r'^###\s+Week\s+\d+', line_stripped, re.IGNORECASE):
            break
        
        # Stop if we hit an empty line followed by a non-indented line (end of session block)
        if not line_stripped:
            # Check if next non-empty line is not indented (new session or week)
            next_idx = idx + 1
            while next_idx < len(lines) and not lines[next_idx].strip():
                next_idx += 1
            if next_idx < len(lines):
                next_line = lines[next_idx].strip()
                if not next_line.startswith('*') and not next_line.startswith('#'):
                    break
                if re.match(r'^\s*[\*\-]\s+\*\*', next_line):
                    break
        
        # Extract content from detail lines (indented with *)
        if line_stripped.startswith('*') and '**' in line_stripped:
            # Remove the leading bullet and extract the content
            # Format: *   **Workout:** content here
            # Format: *   **Purpose:** content here
            # Format: *   **Duration:** content here
            # Format: *   **Instruction:** content here
            match = re.match(r'^\*\s+\*\*([^:]+):\*\*\s*(.+)', line_stripped)
            if match:
                detail_content = match.group(2).strip()
                # Clean up the content (remove extra bold markers, etc.)
                detail_content = re.sub(r'\*\*([^*]+)\*\*', r'\1', detail_content)  # Remove nested **
                # Just add the content, not the label (e.g., "Workout:", "Purpose:")
                details.append(detail_content)
            else:
                # Just a continuation line with content (might be part of previous detail)
                content = re.sub(r'^\*\s+', '', line_stripped).strip()
                if content and not content.startswith('**'):
                    # Remove bold markers
                    content = re.sub(r'\*\*([^*]+)\*\*', r'\1', content)
                    if details:
                        # Append to last detail if it exists
                        details[-1] = f"{details[-1]} {content}"
                    else:
                        details.append(content)
        
        idx += 1
    
    # Join all details with spaces
    return ' '.join(details).strip()


def parse_sessions_from_week_text(week_text: str, week_num: int) -> List[Dict[str, Any]]:
    """
    Parse sessions from a week's markdown text.
    Tries multiple patterns and extracts workout details from follow-up lines.
    """
    sessions = []
    lines = week_text.split('\n')
    
    # Pattern 1: Current AI format - **Type: Description** [PRIORITY]
    # Matches: *   **Run: Description here** [KEY]
    pattern1 = r'^(?:\*\s+)?\*\*([A-Za-z&\s]+):\s*([^\*]+)\*\*\s*\[([^\]]+)\]'
    
    # Pattern 2: Alternative with bullet - * **Type: Description** [PRIORITY]  
    pattern2 = r'^\s*[\*\-]\s+\*\*([A-Za-z&\s]+):\s*([^\*]+)\*\*\s*\[([^\]]+)\]'
    
    # Pattern 3: Priority first - * **[KEY] Run: Description**
    pattern3 = r'^\s*[\*\-]\s+\*\*\[([^\]]+)\]\s*([A-Za-z&\s]+):\s*([^\*]+)\*\*'
    
    # Pattern 4: Session numbered - **Session N [PRIORITY]:** Description
    pattern4 = r'^[\-\*]\s+\*\*Session\s+(\d+)\s*\[([^\]]+)\]:\*\*\s*(.+)'
    
    # Pattern 5: Very lenient - any line with ** and [PRIORITY]
    pattern5 = r'\*\*([^*]+)\*\*.*\[(KEY|IMPORTANT|STRETCH)\]'
    
    # Pattern 6: Type at start with colon - **Run:** or **S&C:**
    pattern6 = r'^\s*[\*\-]?\s*\*\*(Run|Ride|S&C|Swim|Bike|Cycle|Strength):\s*([^\*]+)\*\*\s*\[([^\]]+)\]'
    
    # Pattern 7: Type Number [PRIORITY]: Description - **Run 1 [IMPORTANT]: Description**
    pattern7 = r'^\s*[\*\-]?\s*\*\*([A-Za-z&\s]+)\s+(\d+)\s*\[([^\]]+)\]:\s*([^\*]+)\*\*'
    
    # Pattern 8: S&C without priority - **S&C:** Description (no closing **, no priority)
    # Matches: *   **S&C:** Routine 2 (Lower Body).
    # Simple pattern: bullet, spaces, **, S&C (or variations), :, space, description to end of line
    pattern8 = r'^\s*[\*\-]\s+\*\*(S\s*&\s*C|S&C|Strength)\s*:\s*(.+)$'
    
    patterns = [
        ("Current AI format", pattern1),
        ("Bullet variant", pattern2),
        ("Priority first", pattern3),
        ("Session numbered", pattern4),
        ("Lenient fallback", pattern5),
        ("Type at start", pattern6),
        ("Type Number Priority", pattern7),
        ("S&C without priority", pattern8),
    ]
    
    # Track workout details extraction
    sessions_with_details = 0
    sessions_without_details = 0
    
    # Try each pattern and collect all matches
    # We need to try multiple patterns because a week can have both numbered sessions (Pattern 7)
    # and S&C sessions (Pattern 8)
    for pattern_name, pattern in patterns:
        matches = list(re.finditer(pattern, week_text, re.MULTILINE | re.IGNORECASE))
        
        if matches:
            print(f"    ✓ Pattern '{pattern_name}' matched {len(matches)} sessions")
            
            for idx, match in enumerate(matches):
                # Find which line this match is on
                match_start = match.start()
                match_line_idx = week_text[:match_start].count('\n')
                
                # Extract groups based on pattern type
                if pattern_name == "Session numbered":
                    session_num = int(match.group(1))
                    priority = match.group(2).strip().upper()
                    description = match.group(3).strip()
                    activity_type = "OTHER"
                elif pattern_name == "Type Number Priority":
                    activity_type_raw = match.group(1).strip()
                    session_num = int(match.group(2))
                    priority = match.group(3).strip().upper()
                    description = match.group(4).strip()
                elif pattern_name == "S&C without priority":
                    activity_type_raw = match.group(1).strip()
                    description = match.group(2).strip()
                    priority = "STRETCH"  # Default priority for S&C
                elif pattern_name == "Priority first":
                    priority = match.group(1).strip().upper()
                    activity_type_raw = match.group(2).strip()
                    description = match.group(3).strip()
                elif pattern_name == "Lenient fallback":
                    # For lenient fallback, the description includes text after the closing **
                    # Pattern: **Description** – details here [PRIORITY]
                    # Example: **Run: The XC Sandwich** – 3 hours total... [KEY]
                    full_match = match.group(0)
                    session_name = match.group(1).strip()
                    
                    # Extract everything after the closing ** but before [PRIORITY]
                    # Find where the session name's closing ** ends
                    session_name_with_bold = f"**{session_name}**"
                    name_end_pos = full_match.find(session_name_with_bold)
                    if name_end_pos != -1:
                        after_bold_start = name_end_pos + len(session_name_with_bold)
                        # Find the [PRIORITY] tag
                        priority_start = full_match.rfind('[')
                        if priority_start > after_bold_start:
                            after_bold = full_match[after_bold_start:priority_start].strip()
                            # Clean up the text (remove extra ** markers, normalize whitespace)
                            after_bold = re.sub(r'\*\*([^*]+)\*\*', r'\1', after_bold)  # Remove nested **
                            after_bold = re.sub(r'\s+', ' ', after_bold).strip()  # Normalize whitespace
                            # Remove leading dashes/separators (em dash, en dash, regular dash)
                            after_bold = re.sub(r'^[–—\-]\s*', '', after_bold)
                            # Combine session name with details
                            if after_bold:
                                description = f"{session_name}. {after_bold}".strip()
                            else:
                                description = session_name
                        else:
                            description = session_name
                    else:
                        description = session_name
                    
                    priority = match.group(2).strip().upper()
                    activity_type_raw = "OTHER"
                else:
                    activity_type_raw = match.group(1).strip()
                    description = match.group(2).strip()
                    priority = match.group(3).strip().upper()
                
                # Determine session type
                combined_text = f"{activity_type_raw if 'activity_type_raw' in dir() else ''} {description}".lower()
                
                if any(x in combined_text for x in ['run', 'jog', 'parkrun', 'xc', 'cross country', 'track']):
                    session_type = 'RUN'
                elif any(x in combined_text for x in ['bike', 'cycling', 'cycle', 'ride', 'turbo', 'spin', 'trainer']):
                    session_type = 'BIKE'
                elif any(x in combined_text for x in ['swim', 'pool', 'lake']):
                    session_type = 'SWIM'
                elif any(x in combined_text for x in ['s&c', 'strength', 'routine', 'gym', 'mobility']):
                    session_type = 'STRENGTH'
                else:
                    session_type = 'OTHER'
                
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
                
                # Extract workout details from follow-up lines
                # Note: For "Lenient fallback" pattern, details are already in the description (same line)
                # So we only need to check follow-up lines for other patterns
                if pattern_name == "Lenient fallback":
                    # Details already extracted from same line, no need to check follow-up lines
                    full_description = description
                    sessions_with_details += 1
                    print(f"      📝 Session {idx+1}: Description from same line (Lenient fallback pattern)")
                    print(f"         Description: {description[:100]}{'...' if len(description) > 100 else ''}")
                else:
                    # For other patterns, check follow-up lines for workout details
                    workout_details = extract_workout_details(lines, match_line_idx)
                    
                    # Consolidate description: combine session title with workout details
                    if workout_details:
                        # Format: "Session Title. Workout details here"
                        # Remove any trailing period from description first
                        desc_clean = description.rstrip('.')
                        full_description = f"{desc_clean}. {workout_details}".strip()
                        sessions_with_details += 1
                        print(f"      📝 Session {idx+1}: Extracted workout details from follow-up lines")
                        print(f"         Original: {description[:60]}{'...' if len(description) > 60 else ''}")
                        print(f"         Details: {workout_details[:80]}{'...' if len(workout_details) > 80 else ''}")
                        print(f"         Final: {full_description[:100]}{'...' if len(full_description) > 100 else ''}")
                    else:
                        full_description = description
                        sessions_without_details += 1
                        print(f"      📝 Session {idx+1}: No workout details in follow-up lines")
                        print(f"         Description: {description[:80]}{'...' if len(description) > 80 else ''}")
                
                # Extract S&C routine name
                s_and_c_routine = None
                if session_type == 'STRENGTH':
                    # Try to extract routine name from description
                    routine_match = re.search(r'[Rr]outine\s+(\d+)', full_description, re.IGNORECASE)
                    if routine_match:
                        s_and_c_routine = f"Routine {routine_match.group(1)}"
                    else:
                        # Fallback: use the description itself if it mentions a routine
                        if 'routine' in full_description.lower():
                            s_and_c_routine = full_description.strip()
                
                # Re-extract duration from the full description (might be in workout details)
                if not duration_minutes:
                    duration_match = re.search(r'(\d+)\s*(?:min|mins|minutes)', full_description, re.IGNORECASE)
                    duration_minutes = int(duration_match.group(1)) if duration_match else None
                
                # Re-extract zones from the full description (might be in workout details)
                if not zones:
                    zone_match = re.search(r'[Zz]one\s*(\d+)(?:\s*-\s*(\d+))?', full_description)
                    if zone_match:
                        if zone_match.group(2):
                            zones['hr'] = f"{zone_match.group(1)}-{zone_match.group(2)}"
                        else:
                            zones['hr'] = zone_match.group(1)
                
                session = {
                    'id': f"w{week_num}-s{len(sessions)+1}",
                    'day': "Anytime",
                    'type': session_type,
                    'date': None,
                    'priority': priority,
                    'duration_minutes': duration_minutes,
                    'description': full_description,
                    'zones': zones,
                    'scheduled': True,
                    'completed': False,
                    'strava_activity_id': None,
                    'completed_at': None,
                    's_and_c_routine': s_and_c_routine
                }
                sessions.append(session)
    
    if not sessions:
        print(f"    ⚠️  No sessions matched any pattern")
        
        # Log first 5 lines with asterisks for manual inspection
        asterisk_lines = [l.strip() for l in lines if '*' in l][:5]
        if asterisk_lines:
            print(f"    Sample lines with asterisks:")
            for line in asterisk_lines:
                print(f"      → {line[:80]}{'...' if len(line) > 80 else ''}")
    else:
        # Summary of workout details extraction
        total = sessions_with_details + sessions_without_details
        if total > 0:
            print(f"    📊 Workout details: {sessions_with_details}/{total} sessions had details extracted")
    
    return sessions


# ============================================================================
# Main Migration Logic
# ============================================================================

def migrate_plan_structure(user_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Migrate markdown plan to structured plan_v2."""
    
    if 'plan_v2' in user_data:
        print("  ✓ Plan already migrated to plan_v2")
        return None
    
    if 'plan' not in user_data or not user_data['plan']:
        print("  ⚠ No plan to migrate")
        return None
    
    print("  → Migrating plan structure to plan_v2...")
    
    markdown = user_data['plan']
    
    # Find all week headers
    week_pattern = r'(?:###\s+)?(?:\*\*)?Week\s+(\d+)(?:\*\*)?:'
    week_matches = list(re.finditer(week_pattern, markdown, re.IGNORECASE))
    
    if not week_matches:
        print("  ⚠ No weeks found in markdown")
        return None
    
    weeks = []
    
    for idx, match in enumerate(week_matches):
        week_num = int(match.group(1))
        week_start_pos = match.start()
        week_end_pos = week_matches[idx + 1].start() if idx + 1 < len(week_matches) else len(markdown)
        week_text = markdown[week_start_pos:week_end_pos]
        
        # Parse week header line for dates
        header_line = week_text.split('\n')[0]
        week_info = parse_week_header(header_line)
        
        start_date = week_info['start_date'] if week_info else None
        end_date = week_info['end_date'] if week_info else None
        
        # Parse sessions from this week's text
        print(f"  📅 Week {week_num}: {start_date or '?'} - {end_date or '?'}")
        sessions = parse_sessions_from_week_text(week_text, week_num)
        
        # Update session dates
        for session in sessions:
            session['date'] = start_date
        
        week = {
            'week_number': week_num,
            'start_date': start_date,
            'end_date': end_date,
            'description': '',
            'sessions': sessions
        }
        weeks.append(week)
    
    # Show summary
    print(f"\n  📋 Discovered {len(weeks)} weeks:")
    total_sessions = 0
    session_types = {}
    
    for week in weeks:
        session_count = len(week['sessions'])
        total_sessions += session_count
        print(f"    • Week {week['week_number']}: {week['start_date']} - {week['end_date']} ({session_count} sessions)")
        
        for session in week['sessions']:
            session_type = session['type']
            session_types[session_type] = session_types.get(session_type, 0) + 1
    
    print(f"\n  📊 Session breakdown:")
    for session_type, count in sorted(session_types.items()):
        print(f"    • {session_type}: {count} sessions")
    
    # Create plan_v2 structure
    plan_v2 = {
        'version': 2,
        'created_at': datetime.now().isoformat(),
        'athlete_id': user_data.get('athlete_id'),
        'athlete_goal': user_data.get('plan_data', {}).get('athlete_goal', ''),
        'goal_date': user_data.get('goal_date'),
        'goal_distance': None,
        'plan_start_date': weeks[0]['start_date'] if weeks else None,
        'weeks': weeks,
        'libraries': {}
    }
    
    print(f"  ✅ Ready to migrate {len(weeks)} weeks with {total_sessions} sessions")
    
    return plan_v2


def migrate_dynamodb(table_name: str, athlete_id: str, dry_run: bool = True):
    """Migrate specific athlete in DynamoDB table."""
    
    print(f"\n{'='*60}")
    print(f"📊 DYNAMODB MIGRATION")
    print(f"{'='*60}")
    print(f"Table: {table_name}")
    print(f"Athlete ID: {athlete_id}")
    print(f"Mode: {'DRY-RUN (no changes)' if dry_run else 'LIVE MIGRATION'}")
    print(f"Region: eu-west-1")
    print(f"{'='*60}\n")
    
    print(f"🔌 Connecting to DynamoDB...")
    dynamodb = boto3.resource('dynamodb', region_name='eu-west-1')
    table = dynamodb.Table(table_name)
    
    print(f"📥 Fetching athlete data from DynamoDB...")
    response = table.get_item(Key={'athlete_id': athlete_id})
    
    if 'Item' not in response:
        print(f"❌ Athlete {athlete_id} not found in {table_name}")
        return
    
    print(f"✓ Athlete data retrieved\n")
    user_data = convert_decimals(response['Item'])
    
    # Check for existing plan
    has_plan = 'plan' in user_data and user_data['plan']
    has_plan_v2 = 'plan_v2' in user_data
    
    if has_plan_v2:
        print("  ✓ Plan already migrated to plan_v2")
        return
    
    if not has_plan:
        print("  ⚠ No plan to migrate")
        return
    
    print(f"  📝 Found plan: {len(user_data['plan'])} chars")
    
    # Run migration
    plan_v2 = migrate_plan_structure(user_data)
    
    if not plan_v2:
        print("\n❌ Migration produced no plan_v2")
        return
    
    if dry_run:
        print(f"\n{'='*60}")
        print(f"🔍 DRY-RUN SUMMARY")
        print(f"{'='*60}")
        print(f"No changes made. Run with --execute to apply changes.")
        print(f"{'='*60}")
        return
    
    # Apply migration
    user_data['plan_v2'] = plan_v2
    
    # Use the same conversion function as the app (json_to_dynamodb) to ensure consistency
    # This preserves booleans correctly, unlike convert_to_decimals which is only for numbers
    from data_manager import json_to_dynamodb
    item_to_save = json_to_dynamodb(user_data)
    
    print(f"\n💾 Saving to DynamoDB table: {table_name}...")
    table.put_item(Item=item_to_save)
    
    print(f"\n{'='*60}")
    print(f"✅ MIGRATION COMPLETE!")
    print(f"{'='*60}")


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='Migrate DynamoDB plans to plan_v2')
    parser.add_argument('--env', choices=['staging', 'production', 'mark-prod', 'shane-prod'],
                        required=True, help='Environment to migrate')
    parser.add_argument('--athlete-id', help='Specific athlete ID to migrate')
    parser.add_argument('--dry-run', action='store_true', help='Preview without saving')
    parser.add_argument('--execute', action='store_true', help='Execute migration and save')
    
    args = parser.parse_args()
    
    if not args.dry_run and not args.execute:
        print("❌ Must specify either --dry-run or --execute")
        parser.print_help()
        return
    
    dry_run = not args.execute
    
    # Map env to table name
    table_map = {
        'staging': 'staging-kaizencoach-users',
        'production': 'my-personal-coach-users',
        'mark-prod': 'mark-kaizencoach-users',
        'shane-prod': 'shane-kaizencoach-users',
    }
    
    # Default athlete IDs
    default_athletes = {
        'staging': '196048876',
        'production': '2117356',
    }
    
    table_name = table_map[args.env]
    athlete_id = args.athlete_id or default_athletes.get(args.env)
    
    if not athlete_id:
        print(f"❌ --athlete-id required for {args.env} environment")
        return
    
    migrate_dynamodb(table_name, athlete_id, dry_run=dry_run)


if __name__ == '__main__':
    main()

