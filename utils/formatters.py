from datetime import datetime, timedelta
import re

def format_seconds(seconds):
    """Format seconds into a human-readable string (e.g., '1h 30m 45s')"""
    seconds = int(seconds)
    if seconds == 0:
        return "0s"
    
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0:
        parts.append(f"{secs}s")
    
    return " ".join(parts)

def map_race_distance(distance_meters):
    """Map a distance in meters to a standard race name"""
    if 4875 <= distance_meters <= 5125:
        return "5k Race"
    if 9750 <= distance_meters <= 10250:
        return "10k Race"
    if 20570 <= distance_meters <= 21625:
        return "Half Marathon Race"
    if 41140 <= distance_meters <= 43250:
        return "Marathon Race"
    return "Race (Non-Standard Distance)"

def format_activity_date(raw_date):
    """
    Format activity date from ISO format to readable format.
    Example: '2025-10-04T09:36:15Z' -> '04-10-2025 09:36'
    """
    if not raw_date:
        return raw_date
    
    # Split by 'T' if present
    if 'T' in raw_date.rstrip('Z'):
        date_part, time_part = raw_date.rstrip('Z').split('T')
        # Reformat date from YYYY-MM-DD to DD-MM-YYYY
        date_parts = date_part.split('-')
        time_clean = time_part.split('.')[0]  # Remove milliseconds
        time_no_seconds = time_clean[:5]  # HH:MM only (remove :SS)
        return f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]} {time_no_seconds}"
    
    return raw_date

def extract_week_dates_from_plan(plan_text):
    """
    Extract week start and end dates from plan markdown.
    Returns list of tuples: [(week_num, start_date, end_date, title), ...]
    """
    lines = plan_text.splitlines()
    today = datetime.now().date()
    
    all_weeks = []
    for i, line in enumerate(lines):
        is_header = line.strip().startswith('###') or line.strip().startswith('**Week')
        if not is_header:
            continue

        date_range_match = re.search(r'(\w+\s\d{1,2})[a-z]{2}\s*-\s*(\w+\s\d{1,2})[a-z]{2}', line)
        if date_range_match:
            start_str, end_str = date_range_match.groups()
            for date_format in ["%B %d %Y", "%b %d %Y"]:
                try:
                    start_date = datetime.strptime(f"{start_str} {today.year}", date_format).date()
                    end_date = datetime.strptime(f"{end_str} {today.year}", date_format).date()
                    
                    # Handle year transitions
                    if start_date.month > end_date.month:
                        if today.month < start_date.month:
                            start_date = start_date.replace(year=today.year - 1)
                        else:
                            end_date = end_date.replace(year=today.year + 1)
                    
                    all_weeks.append({
                        'start_date': start_date,
                        'end_date': end_date,
                        'index': i,
                        'title': line.strip()
                    })
                    break
                except ValueError:
                    continue
    
    return all_weeks