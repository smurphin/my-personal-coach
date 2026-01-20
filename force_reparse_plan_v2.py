#!/usr/bin/env python3
"""
Force reparse plan_v2 from markdown and update DynamoDB.

This script:
1. Loads user data from DynamoDB
2. Reparses plan_v2 from the markdown plan using improved parsing logic
3. Preserves completed session status
4. Updates plan_v2 in DynamoDB

Usage:
    python force_reparse_plan_v2.py --env staging --athlete-id 196048876 [--dry-run]
"""

import argparse
import sys
import os

# Set environment variables BEFORE importing Config (Config reads them at import time)
def set_environment(env_name):
    """Set environment variables for Config based on env name"""
    if env_name in ['prod', 'shane-prod', 'mark-prod']:
        os.environ['FLASK_ENV'] = 'production'
    else:
        os.environ['FLASK_ENV'] = 'development'
    
    # Map env names to Config.ENVIRONMENT values
    env_map = {
        'staging': 'staging',
        'prod': 'prod',
        'shane-prod': 'shane',
        'mark-prod': 'mark'
    }
    if env_name in env_map:
        os.environ['ENVIRONMENT'] = env_map[env_name]

# Parse args first to set environment before importing Config
parser_pre = argparse.ArgumentParser(description='Force reparse plan_v2 from markdown', add_help=False)
parser_pre.add_argument('--env', choices=['staging', 'prod', 'shane-prod', 'mark-prod'])
parser_pre_args, _ = parser_pre.parse_known_args()

if parser_pre_args.env:
    set_environment(parser_pre_args.env)

# Add parent directory to path so we can import from app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from data_manager import DynamoDBBackend, FileBackend
from utils.migration import migrate_plan_to_v2
from models.training_plan import TrainingPlan


def main():
    parser = argparse.ArgumentParser(description='Force reparse plan_v2 from markdown')
    parser.add_argument('--env', required=True, choices=['staging', 'prod', 'shane-prod', 'mark-prod'],
                        help='Environment name')
    parser.add_argument('--athlete-id', type=int, required=True,
                        help='Athlete ID')
    parser.add_argument('--dry-run', action='store_true',
                        help='Dry run mode (no changes)')
    
    args = parser.parse_args()
    
    # Set environment variables (needed for Config)
    set_environment(args.env)
    
    print("=" * 60)
    print("🔄 FORCE REPARSE PLAN_V2")
    print("=" * 60)
    print(f"Environment: {args.env}")
    print(f"Athlete ID: {args.athlete_id}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'UPDATE'}")
    print("=" * 60)
    
    # Initialize data manager - use DynamoDB for prod environments, FileBackend for staging/dev
    if args.env in ['prod', 'shane-prod', 'mark-prod']:
        print(f"📊 Using DynamoDB backend (table: {Config.DYNAMODB_TABLE})")
        data_manager = DynamoDBBackend()
    else:
        print(f"📂 Using local file backend")
        data_manager = FileBackend()
    
    # Load user data
    print(f"\n📥 Loading user data from DynamoDB...")
    user_data = data_manager.load_user_data(args.athlete_id)
    
    if not user_data:
        print(f"❌ User not found for athlete ID {args.athlete_id}")
        return 1
    
    # Check if plan exists
    if 'plan' not in user_data:
        print(f"❌ No plan found for athlete {args.athlete_id}")
        return 1
    
    plan_markdown = user_data['plan']
    plan_data = user_data.get('plan_data', {})
    
    # Get plan_v2 if it exists
    original_plan_v2 = None
    if 'plan_v2' in user_data:
        try:
            original_plan_v2 = TrainingPlan.from_dict(user_data['plan_v2'])
            print(f"✅ Found existing plan_v2 with {len(original_plan_v2.weeks)} weeks")
            original_total_sessions = sum(len(w.sessions) for w in original_plan_v2.weeks)
            original_weeks_with_sessions = sum(1 for w in original_plan_v2.weeks if len(w.sessions) > 0)
            print(f"   Original: {original_total_sessions} sessions across {original_weeks_with_sessions} weeks with sessions")
        except Exception as e:
            print(f"⚠️  Error loading plan_v2: {e}")
            print(f"   Will create new plan_v2")
    else:
        print(f"ℹ️  No existing plan_v2 found - will create new one")
    
    # Preserve completed sessions from original plan_v2
    existing_completed = {}
    if original_plan_v2:
        for week in original_plan_v2.weeks:
            for sess in week.sessions:
                if sess.completed:
                    existing_completed[sess.id] = {
                        'completed': True,
                        'strava_activity_id': sess.strava_activity_id if hasattr(sess, 'strava_activity_id') else None,
                        'completed_at': sess.completed_at if hasattr(sess, 'completed_at') else None
                    }
        if existing_completed:
            print(f"📋 Preserving {len(existing_completed)} completed sessions")
    
    # Prepare user_inputs for migration
    user_inputs = {
        'goal': plan_data.get('athlete_goal', ''),
        'goal_date': plan_data.get('goal_date'),
        'goal_distance': plan_data.get('goal_distance'),
        'plan_start_date': plan_data.get('plan_start_date')
    }
    
    # Reparse plan from markdown
    print(f"\n🔄 Reparsing plan from markdown...")
    print(f"   Markdown length: {len(plan_markdown)} chars")
    
    try:
        reparsed_plan = migrate_plan_to_v2(
            plan_markdown,
            plan_data,
            str(args.athlete_id),
            user_inputs
        )
        
        total_sessions = sum(len(w.sessions) for w in reparsed_plan.weeks)
        weeks_with_sessions = sum(1 for w in reparsed_plan.weeks if len(w.sessions) > 0)
        
        print(f"\n✅ Reparse successful!")
        print(f"   Parsed {len(reparsed_plan.weeks)} weeks")
        print(f"   Parsed {total_sessions} sessions")
        print(f"   Weeks with sessions: {weeks_with_sessions}")
        
        # Restore completed sessions
        restored_count = 0
        for week in reparsed_plan.weeks:
            for sess in week.sessions:
                if sess.id in existing_completed:
                    sess.completed = True
                    if existing_completed[sess.id]['strava_activity_id']:
                        sess.strava_activity_id = existing_completed[sess.id]['strava_activity_id']
                    if existing_completed[sess.id]['completed_at']:
                        sess.completed_at = existing_completed[sess.id]['completed_at']
                    restored_count += 1
        
        if restored_count > 0:
            print(f"   ✅ Restored {restored_count} completed sessions")
        
        # Show week-by-week breakdown
        print(f"\n📊 Week breakdown:")
        for week in reparsed_plan.weeks:
            session_count = len(week.sessions)
            completed_count = sum(1 for s in week.sessions if s.completed)
            print(f"   Week {week.week_number}: {session_count} sessions ({completed_count} completed)")
            if week.start_date and week.end_date:
                print(f"      Dates: {week.start_date} - {week.end_date}")
        
        if args.dry_run:
            print(f"\n🔍 DRY-RUN: Would update plan_v2")
            print(f"   Changes:")
            if original_plan_v2:
                print(f"   - Sessions: {original_total_sessions} → {total_sessions}")
                print(f"   - Weeks with sessions: {original_weeks_with_sessions} → {weeks_with_sessions}")
            else:
                print(f"   - Creating new plan_v2 with {total_sessions} sessions")
            print(f"\n   Run without --dry-run to apply changes")
            return 0
        
        # Update plan_v2 in user_data
        print(f"\n💾 Updating plan_v2 in DynamoDB...")
        user_data['plan_v2'] = reparsed_plan.to_dict()
        data_manager.save_user_data(args.athlete_id, user_data)
        
        print(f"✅ Successfully updated plan_v2!")
        print(f"   New plan has {total_sessions} sessions across {weeks_with_sessions} weeks with sessions")
        
        if original_plan_v2:
            print(f"\n📈 Comparison:")
            print(f"   Sessions: {original_total_sessions} → {total_sessions} ({total_sessions - original_total_sessions:+d})")
            print(f"   Weeks with sessions: {original_weeks_with_sessions} → {weeks_with_sessions} ({weeks_with_sessions - original_weeks_with_sessions:+d})")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error during reparse: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
