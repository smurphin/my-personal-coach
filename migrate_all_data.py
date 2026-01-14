#!/usr/bin/env python3
"""
COMPREHENSIVE MIGRATION SCRIPT - Handles ALL schema updates

Migrates:
1. Training metrics (old schema → v2.0 schema)
2. Markdown plans → structured plan_v2 
3. Preserves existing strength sessions (doesn't migrate to S&C library)

Usage:
    python migrate_all_data.py --env local          # Migrate users_data.json
    python migrate_all_data.py --env staging        # Migrate staging DynamoDB
    python migrate_all_data.py --env production     # Migrate production DynamoDB
"""

import argparse
import json
import os
import re
import boto3
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any, List, Optional


# ============================================================================
# PART 1: TRAINING METRICS MIGRATION
# ============================================================================

def migrate_metric_value(old_data: Dict[str, Any]) -> Dict[str, Any]:
    """Migrate a single MetricValue from old schema to v2.0 schema."""
    new_data = {}
    
    # Required field: value
    new_data['value'] = old_data['value']
    
    # Migrate date_set → detected_at
    if 'date_set' in old_data:
        date_str = old_data['date_set']
        try:
            if 'T' in date_str:
                new_data['detected_at'] = date_str
            else:
                new_data['detected_at'] = f"{date_str}T12:00:00Z"
        except:
            new_data['detected_at'] = datetime.now().isoformat()
    elif 'detected_at' in old_data:
        new_data['detected_at'] = old_data['detected_at']
    else:
        new_data['detected_at'] = datetime.now().isoformat()
    
    # Migrate source → detected_from
    if 'source' in old_data:
        source = old_data['source']
        if isinstance(source, dict):
            new_data['detected_from'] = source
        else:
            new_data['detected_from'] = {
                'activity_id': 0,
                'activity_name': str(source),
                'detection_method': 'migrated_from_old_schema'
            }
    elif 'detected_from' in old_data:
        new_data['detected_from'] = old_data['detected_from']
    else:
        new_data['detected_from'] = {
            'activity_id': 0,
            'activity_name': 'Unknown',
            'detection_method': 'migrated_from_old_schema'
        }
    
    # Migrate pending_confirmation → user_confirmed (inverted)
    if 'pending_confirmation' in old_data:
        new_data['user_confirmed'] = not old_data['pending_confirmation']
    elif 'user_confirmed' in old_data:
        new_data['user_confirmed'] = old_data['user_confirmed']
    else:
        new_data['user_confirmed'] = False
    
    # New fields with defaults
    new_data['user_modified'] = old_data.get('user_modified', False)
    new_data['history'] = old_data.get('history', [])
    
    return new_data


def migrate_training_metrics(metrics_data: Dict[str, Any]) -> Dict[str, Any]:
    """Migrate training_metrics for a single user."""
    if not metrics_data:
        return metrics_data
    
    # Check if already migrated
    if 'version' in metrics_data and metrics_data['version'] == '2.0':
        print("  ✓ Training metrics already v2.0")
        return metrics_data
    
    print("  → Migrating training metrics to v2.0...")
    migrated = {'version': '2.0'}
    
    # Migrate each metric with detailed output
    metrics_migrated = []
    for metric_name in ['lthr', 'ftp', 'vdot']:
        if metric_name in metrics_data and metrics_data[metric_name]:
            old_metric = metrics_data[metric_name]
            print(f"    • {metric_name.upper()}: {old_metric.get('value')} ", end='')
            
            # Show what fields are being migrated
            old_fields = []
            if 'date_set' in old_metric:
                old_fields.append('date_set')
            if 'source' in old_metric:
                old_fields.append('source')
            if 'pending_confirmation' in old_metric:
                old_fields.append('pending_confirmation')
            
            if old_fields:
                print(f"(migrating: {', '.join(old_fields)})")
            else:
                print("(already new format)")
            
            migrated[metric_name] = migrate_metric_value(old_metric)
            metrics_migrated.append(metric_name)
    
    # Copy zones unchanged
    if 'zones' in metrics_data:
        migrated['zones'] = metrics_data['zones']
    
    if metrics_migrated:
        print(f"  ✅ Migrated: {', '.join([m.upper() for m in metrics_migrated])}")
    
    return migrated


# ============================================================================
# PART 2: PLAN STRUCTURE MIGRATION (markdown → plan_v2)
# ============================================================================

def convert_date_to_iso(date_str: str, year: int = 2026) -> str:
    """
    Convert date strings like 'Jan 6th' to ISO format 'YYYY-MM-DD'.
    Assumes current year if not specified.
    """
    import re
    
    # Remove ordinal suffixes (st, nd, rd, th)
    match = re.match(r'([A-Za-z]+)\s+(\d+)(?:st|nd|rd|th)?', date_str.strip())
    if not match:
        return date_str  # Return as-is if can't parse
    
    month_str = match.group(1)
    day = int(match.group(2))
    
    # Try short month name first (%b), then full (%B)
    for fmt in ["%b %d %Y", "%B %d %Y"]:
        try:
            date_obj = datetime.strptime(f"{month_str} {day} {year}", fmt)
            return date_obj.strftime('%Y-%m-%d')
        except:
            continue
    
    # Fallback: return original
    return date_str


def parse_week_header(line: str) -> Optional[Dict[str, Any]]:
    """Parse week header line to extract week number and dates."""
    # Format: ### Week 1: December 15th - December 21st
    # or: ### Week 0: Jan 6th - Jan 11th
    pattern = r'###\s+Week\s+(\d+):\s+(.+?)\s+-\s+(.+?)(?:\s+\(|$)'
    match = re.search(pattern, line)
    
    if match:
        start_date_str = match.group(2).strip()
        end_date_str = match.group(3).strip()
        
        return {
            'week_number': int(match.group(1)),
            'start_date': convert_date_to_iso(start_date_str),
            'end_date': convert_date_to_iso(end_date_str)
        }
    return None


def parse_session(lines: List[str], idx: int) -> Optional[Dict[str, Any]]:
    """Parse a single session starting at idx."""
    if idx >= len(lines):
        return None
    
    line = lines[idx].strip()
    
    # Actual format: - **Session 1 [IMPORTANT]:** Description
    # Pattern captures: Session number, priority, and title/description
    pattern = r'^-\s+\*\*Session\s+(\d+)\s+\[([^\]]+)\]:\*\*\s+(.+)'
    match = re.search(pattern, line)
    
    if not match:
        return None
    
    session_num = int(match.group(1))
    priority = match.group(2).strip()
    title = match.group(3).strip()
    
    # Collect description lines (lines that don't start with - or ###)
    description_lines = [title]
    for i in range(idx + 1, len(lines)):
        next_line = lines[i].strip()
        if not next_line:
            continue
        if next_line.startswith('-') or next_line.startswith('###'):
            break
        description_lines.append(next_line)
    
    full_description = ' '.join(description_lines)
    
    # Determine session type from description
    desc_lower = full_description.lower()
    if any(x in desc_lower for x in ['run', 'jog', 'parkrun', 'cross country', 'xc', 'track']):
        session_type = 'RUN'
    elif any(x in desc_lower for x in ['bike', 'cycling', 'cycle', 'ride', 'turbo', 'spin']):
        session_type = 'BIKE'
    elif any(x in desc_lower for x in ['swim', 'pool']):
        session_type = 'SWIM'
    elif any(x in desc_lower for x in ['s&c', 'strength', 'routine', 'gym']):
        session_type = 'STRENGTH'
    elif 'rest' in desc_lower:
        session_type = 'REST'
    else:
        session_type = 'OTHER'
    
    return {
        'id': f"session-{session_num}",  # Will be updated with proper week context
        'day': "Anytime",  # Old format doesn't have day info
        'type': session_type,
        'date': None,  # Not in old format
        'priority': priority,
        'duration_minutes': None,  # Could parse from description but skipping for now
        'description': full_description,
        'zones': {},
        'scheduled': True,
        'completed': False,
        'strava_activity_id': None,
        'completed_at': None,
        's_and_c_routine': None
    }


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
    lines = markdown.split('\n')
    
    weeks = []
    current_week = None
    
    for idx, line in enumerate(lines):
        # Check for week header
        week_info = parse_week_header(line)
        if week_info:
            if current_week:
                weeks.append(current_week)
            
            current_week = {
                'week_number': week_info['week_number'],
                'start_date': week_info['start_date'],
                'end_date': week_info['end_date'],
                'description': '',  # Not in old format
                'sessions': []
            }
            continue
        
        # Check for session (dash bullet with Session keyword)
        if current_week and line.startswith('- **Session'):
            session = parse_session(lines, idx)
            if session:
                # Update session ID with week context
                session_num = len(current_week['sessions']) + 1
                session['id'] = f"w{current_week['week_number']}-s{session_num}"
                current_week['sessions'].append(session)
    
    # Add last week
    if current_week:
        weeks.append(current_week)
    
    if not weeks:
        print("  ⚠ No weeks parsed from markdown")
        return None
    
    # Show detailed breakdown
    print(f"  📋 Discovered {len(weeks)} weeks:")
    total_sessions = 0
    session_types = {}
    
    for week in weeks:
        session_count = len(week['sessions'])
        total_sessions += session_count
        print(f"    • Week {week['week_number']}: {week['start_date']} - {week['end_date']} ({session_count} sessions)")
        
        # Count session types in this week
        for session in week['sessions']:
            session_type = session['type']
            session_types[session_type] = session_types.get(session_type, 0) + 1
            
            # Show session details
            priority_emoji = {
                'KEY': '🔑',
                'CRITICAL': '⚠️',
                'IMPORTANT': '📌',
                'OPTIONAL': '💡'
            }
            emoji = '  '
            for key, emj in priority_emoji.items():
                if key in session.get('priority', ''):
                    emoji = emj
                    break
            
            print(f"      {emoji} {session['type']}: {session['description'][:60]}{'...' if len(session['description']) > 60 else ''}")
    
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
        'goal_distance': None,  # Not stored in old format
        'plan_start_date': weeks[0]['start_date'] if weeks else None,
        'weeks': weeks,
        'libraries': {}
    }
    
    print(f"  ✅ Ready to migrate {len(weeks)} weeks with {total_sessions} sessions")
    
    return plan_v2


# ============================================================================
# HELPERS
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
# LOCAL FILE MIGRATION
# ============================================================================

def migrate_local_file(data_file='users_data.json', dry_run=False):
    """Migrate local users_data.json file."""
    print(f"\n{'='*60}")
    print(f"📂 LOCAL FILE MIGRATION")
    print(f"{'='*60}")
    print(f"File: {data_file}")
    print(f"Mode: {'DRY-RUN (no changes)' if dry_run else 'LIVE MIGRATION'}")
    print(f"{'='*60}\n")
    
    if not os.path.exists(data_file):
        print(f"❌ File not found: {data_file}")
        return
    
    print(f"📂 Loading {data_file}...")
    with open(data_file, 'r') as f:
        data = json.load(f)
    
    print(f"✓ Found {len(data)} athlete(s) in file\n")
    
    if not dry_run:
        backup_file = f"{data_file}.backup"
        print(f"💾 Creating backup: {backup_file}")
        with open(backup_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    users_migrated = 0
    metrics_migrated = 0
    plans_migrated = 0
    
    for athlete_id, user_data in data.items():
        print(f"\n👤 Analyzing athlete {athlete_id}...")
        
        # Initialize migration flags
        needs_metrics_migration = False
        needs_plan_migration = False
        
        # Part 1: Training metrics
        if 'training_metrics' in user_data:
            needs_metrics_migration = (
                'version' not in user_data['training_metrics'] or 
                user_data['training_metrics'].get('version') != '2.0'
            )
            
            if needs_metrics_migration:
                print("  📊 Training metrics need migration")
                metrics_migrated += 1
                if not dry_run:
                    user_data['training_metrics'] = migrate_training_metrics(
                        user_data['training_metrics']
                    )
                else:
                    # Call function to show verbose output but don't save
                    migrate_training_metrics(user_data['training_metrics'])
            else:
                print("  ✓ Training metrics already v2.0")
        
        # Part 2: Plan structure
        has_plan = 'plan' in user_data and user_data['plan']
        needs_plan_migration = has_plan and 'plan_v2' not in user_data
        
        if needs_plan_migration:
            print("  📋 Plan structure needs migration")
            plans_migrated += 1
            if not dry_run:
                plan_v2 = migrate_plan_structure(user_data)
                if plan_v2:
                    user_data['plan_v2'] = plan_v2
            else:
                # Call function to show verbose output but don't save
                migrate_plan_structure(user_data)
        elif 'plan_v2' in user_data:
            print("  ✓ Plan already migrated to plan_v2")
        elif not has_plan:
            print("  ⚠ No plan to migrate")
        
        if needs_metrics_migration or needs_plan_migration:
            users_migrated += 1
    
    if dry_run:
        print(f"\n{'='*60}")
        print(f"🔍 DRY-RUN SUMMARY")
        print(f"{'='*60}")
        print(f"Environment: Local file (users_data.json)")
        print(f"Athletes analyzed: {len(data)}")
        print(f"\nChanges that would be applied:")
        print(f"  → {users_migrated} users would be migrated")
        print(f"  → {metrics_migrated} training metrics would be updated to v2.0")
        print(f"  → {plans_migrated} plans would be structured to plan_v2")
        print(f"\nNo changes made. Run without --dry-run to apply changes.")
        print(f"{'='*60}")
    else:
        print(f"\n💾 Saving migrated data to {data_file}...")
        with open(data_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        print(f"\n{'='*60}")
        print(f"✅ MIGRATION COMPLETE!")
        print(f"{'='*60}")
        print(f"  → {users_migrated} users migrated")
        print(f"  → {metrics_migrated} training metrics updated")
        print(f"  → {plans_migrated} plans structured")
        print(f"  → Backup saved to {data_file}.backup")
        print(f"{'='*60}")


# ============================================================================
# DYNAMODB MIGRATION
# ============================================================================

def migrate_dynamodb(table_name: str, athlete_id: str, dry_run=False):
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
    
    print(f"🔄 Analyzing migration requirements...\n")
    
    needs_metrics = False
    needs_plan = False
    
    # Check training metrics
    if 'training_metrics' in user_data:
        needs_metrics = (
            'version' not in user_data['training_metrics'] or 
            user_data['training_metrics'].get('version') != '2.0'
        )
        
        if needs_metrics:
            print("  📊 Training metrics need migration")
        else:
            print("  ✓ Training metrics already v2.0")
    
    # Check plan structure
    has_plan = 'plan' in user_data and user_data['plan']
    needs_plan = has_plan and 'plan_v2' not in user_data
    
    if needs_plan:
        print("  📋 Plan structure needs migration")
    elif 'plan_v2' in user_data:
        print("  ✓ Plan already migrated to plan_v2")
    elif not has_plan:
        print("  ⚠ No plan to migrate")
    
    if not needs_metrics and not needs_plan:
        print(f"\n✅ No migration needed for athlete {athlete_id}")
        return
    
    # Perform actual migration
    print(f"\n🔄 {'Analyzing' if dry_run else 'Migrating'} data...")
    
    # Part 1: Training metrics
    if needs_metrics:
        if not dry_run:
            user_data['training_metrics'] = migrate_training_metrics(
                user_data['training_metrics']
            )
        else:
            # Call function to show verbose output
            migrate_training_metrics(user_data['training_metrics'])
    
    # Part 2: Plan structure
    if needs_plan:
        if not dry_run:
            plan_v2 = migrate_plan_structure(user_data)
            if plan_v2:
                user_data['plan_v2'] = plan_v2
        else:
            # Call function to show verbose output
            migrate_plan_structure(user_data)
    
    if dry_run:
        # Summary after showing detailed output
        print(f"\n{'='*60}")
        print(f"🔍 DRY-RUN SUMMARY")
        print(f"{'='*60}")
        print(f"Environment: DynamoDB")
        print(f"Table: {table_name}")
        print(f"Athlete ID: {athlete_id}")
        print(f"\nChanges that would be applied:")
        if needs_metrics:
            print("   → Training metrics would be updated to v2.0")
        if needs_plan:
            print(f"   → Plan would be structured to plan_v2")
        print(f"\nNo changes made. Run without --dry-run to apply changes.")
        print(f"{'='*60}")
        return
    
    # Convert back to Decimals for DynamoDB
    user_data = convert_to_decimals(user_data)
    
    print(f"\n💾 Saving to DynamoDB table: {table_name}...")
    table.put_item(Item=user_data)
    
    print(f"\n{'='*60}")
    print(f"✅ MIGRATION COMPLETE!")
    print(f"{'='*60}")
    print(f"Table: {table_name}")
    print(f"Athlete ID: {athlete_id}")
    if needs_metrics:
        print("  ✓ Training metrics updated to v2.0")
    if needs_plan:
        print("  ✓ Plan structured to plan_v2")
    print(f"{'='*60}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Comprehensive data migration for kAIzen Coach'
    )
    parser.add_argument(
        '--env',
        choices=['local', 'staging', 'production', 'mark-prod', 'shane-prod'],
        required=True,
        help='Environment to migrate'
    )
    parser.add_argument(
        '--athlete-id',
        help='Specific athlete ID to migrate (for DynamoDB only)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be migrated without making changes'
    )
    
    args = parser.parse_args()
    
    if args.env == 'local':
        migrate_local_file(dry_run=args.dry_run)
    
    elif args.env == 'staging':
        athlete_id = args.athlete_id or '196048876'  # Shane's staging account
        migrate_dynamodb('staging-kaizencoach-users', athlete_id, dry_run=args.dry_run)
    
    elif args.env == 'production':
        athlete_id = args.athlete_id or '2117356'  # Your production account
        migrate_dynamodb('my-personal-coach-users', athlete_id, dry_run=args.dry_run)
    
    elif args.env == 'mark-prod':
        if not args.athlete_id:
            print("❌ --athlete-id required for mark-prod environment")
            return
        migrate_dynamodb('mark-kaizencoach-users', args.athlete_id, dry_run=args.dry_run)
    
    elif args.env == 'shane-prod':
        if not args.athlete_id:
            print("❌ --athlete-id required for shane-prod environment")
            return
        migrate_dynamodb('shane-kaizencoach-users', args.athlete_id, dry_run=args.dry_run)


if __name__ == '__main__':
    main()