#!/usr/bin/env python3
"""
Migration script to convert existing plans to structured plan_v2 format.

Supports both DynamoDB and local JSON files.

Usage:
    # Local dev (JSON file)
    python migrate_existing_users.py --local --athlete-id 2117356 --dry-run
    python migrate_existing_users.py --local --athlete-id 2117356 --execute
    
    # AWS environments (DynamoDB)
    export DYNAMODB_TABLE=kAIzen-Coach-dev
    python migrate_existing_users.py --athlete-id 2117356 --dry-run
    python migrate_existing_users.py --athlete-id 2117356 --execute
"""
import argparse
import json
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Try to import data_manager, but provide fallback for local files
try:
    from data_manager import data_manager
    HAS_DATA_MANAGER = True
except ImportError:
    HAS_DATA_MANAGER = False
    print("⚠️  data_manager not available, using local file mode only")

from models.training_plan import TrainingPlan, TrainingMetrics
from utils.migration import migrate_plan_to_v2, validate_plan_structure, backfill_completions_from_strava

try:
    from services.strava_service import strava_service
    HAS_STRAVA = True
except ImportError:
    HAS_STRAVA = False
    print("⚠️  strava_service not available, skipping completion backfill")


def load_local_json(athlete_id: str) -> Optional[Dict[str, Any]]:
    """Load user data from local JSON file"""
    json_path = Path(f'users_data.json')
    
    if not json_path.exists():
        print(f"❌ Local file not found: {json_path}")
        return None
    
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        print(f"✓ Loaded local JSON: {json_path}")
        return data.get(athlete_id)  # Return just this athlete's data
    except Exception as e:
        print(f"❌ Failed to load local JSON: {e}")
        return None


def save_local_json(athlete_id: str, user_data: Dict[str, Any]) -> bool:
    """Save user data to local JSON file"""
    json_path = Path(f'users_data.json')
    
    try:
        # Load entire file first
        all_data = {}
        if json_path.exists():
            with open(json_path, 'r') as f:
                all_data = json.load(f)
            
            # Create backup
            backup_path = Path(f'users_data_BACKUP_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
            import shutil
            shutil.copy(json_path, backup_path)
            print(f"✓ Created backup: {backup_path}")
        
        # Update just this athlete
        all_data[athlete_id] = user_data
        
        # Write entire file back
        with open(json_path, 'w') as f:
            json.dump(all_data, f, indent=2)
        print(f"✓ Saved to local JSON: {json_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to save local JSON: {e}")
        return False


def load_user_data(athlete_id: str, use_local: bool = False) -> Optional[Dict[str, Any]]:
    """Load user data from either local JSON or DynamoDB"""
    if use_local:
        return load_local_json(athlete_id)
    
    if not HAS_DATA_MANAGER:
        print("❌ data_manager not available and --local not specified")
        return None
    
    return data_manager.load_user_data(athlete_id)


def save_user_data(athlete_id: str, user_data: Dict[str, Any], use_local: bool = False) -> bool:
    """Save user data to either local JSON or DynamoDB"""
    if use_local:
        return save_local_json(athlete_id, user_data)
    
    if not HAS_DATA_MANAGER:
        print("❌ data_manager not available and --local not specified")
        return False
    
    try:
        data_manager.save_user_data(athlete_id, user_data)
        return True
    except Exception as e:
        print(f"❌ Failed to save to DynamoDB: {e}")
        return False


def migrate_user(athlete_id: str, dry_run: bool = True, use_local: bool = False) -> dict:
    """
    Migrate a single user's data to plan_v2 format.
    
    Args:
        athlete_id: Athlete ID to migrate
        dry_run: If True, don't save changes
        use_local: If True, use local JSON file instead of DynamoDB
    
    Returns:
        Dict with migration results
    """
    result = {
        'athlete_id': athlete_id,
        'success': False,
        'issues': [],
        'changes': []
    }
    
    try:
        # Load user data
        print(f"\n{'='*60}")
        print(f"Processing athlete_id: {athlete_id}")
        print(f"Mode: {'Local JSON' if use_local else 'DynamoDB'}")
        print(f"{'='*60}")
        
        user_data = load_user_data(athlete_id, use_local)
        
        if not user_data:
            result['issues'].append("❌ No user data found")
            return result
        
        # Check if already migrated
        if 'plan_v2' in user_data:
            print("ℹ️  User already has plan_v2")
            result['issues'].append("ℹ️  Already migrated")
            result['success'] = True
            return result
        
        # Check if user has a plan to migrate
        if 'plan' not in user_data or not user_data['plan']:
            result['issues'].append("⚠️  No plan to migrate")
            result['success'] = True  # Not an error, just nothing to do
            return result
        
        print(f"✓ Found plan: {len(user_data['plan'])} chars")
        
        # Gather inputs needed for migration
        user_inputs = {
            'goal': user_data.get('goal', 'Unknown goal'),
            'goal_date': None,  # Will extract from plan_data if available
            'goal_distance': None,
            'plan_start_date': None
        }
        
        # Try to get metadata from plan_data
        plan_data = user_data.get('plan_data')
        if plan_data:
            user_inputs['goal_date'] = plan_data.get('goal_date')
            user_inputs['plan_start_date'] = plan_data.get('plan_start_date')
            user_inputs['goal_distance'] = plan_data.get('goal_distance')
            print(f"✓ Found plan_data with metadata")
        
        # Migrate plan to v2
        print(f"\n📝 Migrating plan to structured format...")
        plan_v2 = migrate_plan_to_v2(
            plan_markdown=user_data['plan'],
            plan_data=plan_data,
            athlete_id=athlete_id,
            user_inputs=user_inputs
        )
        
        print(f"✓ Created structured plan with {len(plan_v2.weeks)} weeks")
        
        # Count sessions
        total_sessions = sum(len(w.sessions) for w in plan_v2.weeks)
        print(f"✓ Parsed {total_sessions} sessions")
        
        # Validate structure
        print(f"\n🔍 Validating plan structure...")
        validation = validate_plan_structure(plan_v2)
        for msg in validation:
            print(f"  {msg}")
            if msg.startswith('❌'):
                result['issues'].append(msg)
        
        # Try to backfill completions from Strava
        print(f"\n🔗 Backfilling session completions from Strava...")
        if HAS_STRAVA:
            try:
                access_token = user_data.get('token', {}).get('access_token')
                if access_token:
                    # Get recent activities (last 30 days)
                    from datetime import datetime, timedelta
                    after_timestamp = int((datetime.now() - timedelta(days=30)).timestamp())
                    recent_activities = strava_service.get_recent_activities(
                        access_token, 
                        after=after_timestamp, 
                        per_page=200
                    )
                    
                    matched = backfill_completions_from_strava(plan_v2, recent_activities)
                    print(f"✓ Matched {matched} activities to sessions")
                    result['changes'].append(f"Matched {matched} activities")
                else:
                    print("⚠️  No access token - skipping activity backfill")
            except Exception as e:
                print(f"⚠️  Could not backfill activities: {e}")
        else:
            print("⚠️  Strava service not available - skipping activity backfill")
        
        # === Preserve/Initialize training metrics ===
        print(f"\n📊 Processing training metrics...")
        
        if 'training_metrics' in user_data:
            print(f"✅ training_metrics already exists - PRESERVING")
            # Don't overwrite - user may have manually set values
        else:
            print(f"ℹ️  No training_metrics found - checking plan_data...")
            metrics = TrainingMetrics(version=1)
            metrics_found = False
            
            # Extract from plan_data if available
            if plan_data:
                # Extract LTHR from friel_hr_zones (Zone 4 max)
                if 'friel_hr_zones' in plan_data and 'zones' in plan_data['friel_hr_zones']:
                    zones = plan_data['friel_hr_zones']['zones']
                    if len(zones) >= 4 and 'max' in zones[3]:
                        lthr = zones[3]['max']
                        if lthr > 0:  # Valid LTHR
                            metrics.update_lthr(
                                value=lthr,
                                activity_id=0,
                                activity_name='Extracted from plan_data',
                                detection_method='user_provided'
                            )
                            print(f"  ✅ Extracted LTHR: {lthr} bpm from plan_data")
                            result['changes'].append(f"Extracted LTHR: {lthr}")
                            metrics_found = True
                
                # Extract FTP from friel_power_zones (parse from calculation_method)
                if 'friel_power_zones' in plan_data:
                    calc_method = plan_data['friel_power_zones'].get('calculation_method', '')
                    # Parse "Joe Friel (Estimated FTP: 210 W)"
                    import re
                    ftp_match = re.search(r'FTP:\s*(\d+)\s*W', calc_method)
                    if ftp_match:
                        ftp = int(ftp_match.group(1))
                        metrics.update_ftp(
                            value=ftp,
                            activity_id=0,
                            activity_name='Extracted from plan_data',
                            detection_method='user_provided'
                        )
                        print(f"  ✅ Extracted FTP: {ftp} W from plan_data")
                        result['changes'].append(f"Extracted FTP: {ftp}")
                        metrics_found = True
                
                # Check for VDOT data
                if 'vdot_data' in plan_data and 'status' in plan_data['vdot_data']:
                    status = plan_data['vdot_data']['status']
                    if 'VDOT Ready' in status or 'current_vdot' in plan_data['vdot_data']:
                        vdot_value = plan_data['vdot_data'].get('current_vdot')
                        if vdot_value:
                            from models.training_plan import MetricValue
                            metrics.vdot = MetricValue(
                                value=vdot_value,
                                source='EXTRACTED_FROM_PLAN_DATA',
                                date_set=datetime.now().isoformat()
                            )
                            print(f"  ✅ Extracted VDOT: {vdot_value} from plan_data")
                            result['changes'].append(f"Extracted VDOT: {vdot_value}")
                            metrics_found = True
                        else:
                            print(f"  ℹ️  VDOT status found but no current_vdot value")
            
            # Also check old-style root-level fields as fallback
            if not metrics_found or (not metrics.lthr and 'lthr' in user_data):
                if 'lthr' in user_data and user_data['lthr']:
                    metrics.update_lthr(
                        value=user_data['lthr'],
                        activity_id=0,
                        activity_name='Migrated from root field',
                        detection_method='user_provided'
                    )
                    print(f"  ✅ Migrated LTHR: {user_data['lthr']} bpm from root field")
                    result['changes'].append(f"Migrated LTHR: {user_data['lthr']}")
                    metrics_found = True
            
            if not metrics_found or (not metrics.ftp and 'ftp' in user_data):
                if 'ftp' in user_data and user_data['ftp']:
                    metrics.update_ftp(
                        value=user_data['ftp'],
                        activity_id=0,
                        activity_name='Migrated from root field',
                        detection_method='user_provided'
                    )
                    print(f"  ✅ Migrated FTP: {user_data['ftp']} W from root field")
                    result['changes'].append(f"Migrated FTP: {user_data['ftp']}")
                    metrics_found = True
            
            if not metrics_found or (not metrics.vdot and 'vdot' in user_data):
                if 'vdot' in user_data and user_data['vdot']:
                    from models.training_plan import MetricValue
                    from utils.vdot_calculator import VDOTCalculator
                    
                    vdot_value = user_data['vdot']
                    
                    # Calculate paces from VDOT
                    calc = VDOTCalculator()
                    paces = calc.get_training_paces(int(vdot_value))
                    
                    # Create VDOT metric with paces
                    metrics.vdot = MetricValue(
                        value=vdot_value,
                        source='MIGRATED',
                        date_set=datetime.now().isoformat()
                    )
                    
                    # Store paces in the metrics dict directly (will be converted to dict)
                    # Since MetricValue might not have paces field, we'll add it after to_dict()
                    print(f"  ✅ Migrated VDOT: {vdot_value} from root field")
                    print(f"     Calculated {len(paces)} training paces from Jack Daniels' tables")
                    result['changes'].append(f"Migrated VDOT: {vdot_value} with paces")
                    metrics_found = True
                    
                    # Store paces separately to add after to_dict()
                    vdot_paces = paces
                else:
                    vdot_paces = None
            else:
                vdot_paces = None
            
            if metrics_found:
                user_data['training_metrics'] = metrics.to_dict()
                
                # Add paces to VDOT if we migrated it
                if vdot_paces and 'vdot' in user_data['training_metrics']:
                    if isinstance(user_data['training_metrics']['vdot'], dict):
                        user_data['training_metrics']['vdot']['paces'] = vdot_paces
                        print(f"  ✅ Added {len(vdot_paces)} paces to VDOT")
                
                print(f"  ✅ Created training_metrics")
            else:
                print(f"  ℹ️  No metrics found - user will need to set manually")
        
        # === Preserve/Initialize lifestyle context ===
        print(f"\n👤 Processing lifestyle context...")
        
        if 'lifestyle' in user_data:
            print(f"✅ lifestyle already exists - PRESERVING")
            # Don't overwrite - user may have manually entered
        else:
            print(f"ℹ️  No lifestyle dict found - extracting from plan_data...")
            
            lifestyle = {}
            
            # Extract from plan_data if available
            if plan_data:
                if 'lifestyle_context' in plan_data and plan_data['lifestyle_context']:
                    lifestyle['training_constraints'] = plan_data['lifestyle_context']
                    print(f"  ✅ Extracted lifestyle_context ({len(plan_data['lifestyle_context'])} chars)")
                
                if 'athlete_type' in plan_data and plan_data['athlete_type']:
                    # Map old format to new format
                    athlete_type_raw = plan_data['athlete_type']
                    athlete_type_mapping = {
                        'The Improviser': 'IMPROVISER',
                        'The Disciplinarian': 'DISCIPLINARIAN',
                        'The Minimalist': 'MINIMALIST',
                        # Also handle if they're already in new format
                        'IMPROVISER': 'IMPROVISER',
                        'DISCIPLINARIAN': 'DISCIPLINARIAN',
                        'MINIMALIST': 'MINIMALIST'
                    }
                    lifestyle['athlete_type'] = athlete_type_mapping.get(athlete_type_raw, athlete_type_raw)
                    print(f"  ✅ Extracted athlete_type: {athlete_type_raw} → {lifestyle['athlete_type']}")
            
            # Also check for individual fields at root level
            for field in ['work_pattern', 'family_commitments', 'training_constraints', 'athlete_type']:
                if field in user_data and user_data[field] and field not in lifestyle:
                    lifestyle[field] = user_data[field]
                    print(f"  ✅ Found {field} at root level")
            
            if lifestyle:
                user_data['lifestyle'] = lifestyle
                print(f"  ✅ Created lifestyle dict with {len(lifestyle)} fields")
                result['changes'].append(f"Created lifestyle with: {', '.join(lifestyle.keys())}")
            else:
                print(f"  ℹ️  No lifestyle data found - user will need to set manually")
        
        # Save plan_v2 to user_data
        user_data['plan_v2'] = plan_v2.to_dict()
        result['changes'].append(f"Added plan_v2 with {len(plan_v2.weeks)} weeks")
        
        # Keep original plan for backward compatibility
        # Don't delete user_data['plan']
        
        if dry_run:
            print(f"\n🔍 DRY RUN - No changes saved")
            print(f"\nWould save:")
            print(f"  - plan_v2: {len(plan_v2.weeks)} weeks, {total_sessions} sessions")
            print(f"  - training_metrics: {bool(user_data.get('training_metrics'))}")
        else:
            print(f"\n💾 Saving changes...")
            success = save_user_data(athlete_id, user_data, use_local)
            if success:
                print(f"✓ Saved successfully")
            else:
                print(f"❌ Save failed")
                result['success'] = False
                result['issues'].append("Failed to save data")
                return result
        
        result['success'] = True
        
        # Print summary
        print(f"\n{'='*60}")
        print(f"Migration Summary for {athlete_id}")
        print(f"{'='*60}")
        print(f"Status: {'✅ SUCCESS' if result['success'] else '❌ FAILED'}")
        print(f"Changes: {len(result['changes'])}")
        for change in result['changes']:
            print(f"  - {change}")
        if result['issues']:
            print(f"Issues: {len(result['issues'])}")
            for issue in result['issues']:
                print(f"  - {issue}")
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        result['issues'].append(f"Exception: {str(e)}")
    
    return result


def main():
    parser = argparse.ArgumentParser(description='Migrate user training plans to structured format')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without saving')
    parser.add_argument('--execute', action='store_true', help='Execute migration and save changes')
    parser.add_argument('--athlete-id', type=str, help='Migrate specific athlete by ID')
    parser.add_argument('--all-users', action='store_true', help='Migrate all users')
    parser.add_argument('--local', action='store_true', help='Use local JSON file instead of DynamoDB')
    
    args = parser.parse_args()
    
    if not args.dry_run and not args.execute:
        print("❌ Must specify either --dry-run or --execute")
        parser.print_help()
        sys.exit(1)
    
    dry_run = not args.execute
    use_local = args.local
    
    if dry_run:
        print("🔍 DRY RUN MODE - No changes will be saved")
    else:
        mode_str = "local JSON file" if use_local else "DynamoDB"
        print(f"⚠️  EXECUTE MODE - Changes will be saved to {mode_str}")
        response = input("Are you sure you want to proceed? (yes/no): ")
        if response.lower() != 'yes':
            print("Aborted")
            sys.exit(0)
    
    # Determine which users to migrate
    athlete_ids = []
    
    if args.athlete_id:
        athlete_ids = [args.athlete_id]
    elif args.all_users:
        # For file backend, we can load the file
        # For DynamoDB, we'd need to scan (expensive!)
        print("\n⚠️  --all-users requires manual athlete_id list")
        print("Please specify athlete IDs:")
        print("  Known users: 2117356 (darren), <shane_id>, <mark_id>")
        athlete_ids_input = input("Enter comma-separated athlete IDs: ")
        athlete_ids = [aid.strip() for aid in athlete_ids_input.split(',') if aid.strip()]
    else:
        print("❌ Must specify --athlete-id or --all-users")
        parser.print_help()
        sys.exit(1)
    
    if not athlete_ids:
        print("❌ No athlete IDs provided")
        sys.exit(1)
    
    # Migrate each user
    results = []
    for athlete_id in athlete_ids:
        result = migrate_user(athlete_id, dry_run=dry_run, use_local=use_local)
        results.append(result)
    
    # Print overall summary
    print(f"\n{'='*60}")
    print(f"MIGRATION COMPLETE")
    print(f"{'='*60}")
    print(f"Total users processed: {len(results)}")
    print(f"Successful: {sum(1 for r in results if r['success'])}")
    print(f"Failed: {sum(1 for r in results if not r['success'])}")
    
    if not dry_run:
        storage = "local JSON files" if use_local else "DynamoDB"
        print(f"\n✅ All changes saved to {storage}")
    
    # Exit with error code if any failed
    if any(not r['success'] for r in results):
        sys.exit(1)


if __name__ == '__main__':
    main()