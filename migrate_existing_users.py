#!/usr/bin/env python3
"""
Standalone migration script to convert existing plans to structured plan_v2 format.

Talks directly to DynamoDB via boto3 - no Flask app dependencies.

Usage:
    # Local dev (JSON file)
    python migrate_existing_users.py --local --athlete-id 2117356 --dry-run
    python migrate_existing_users.py --local --athlete-id 2117356 --execute
    
    # AWS environments (DynamoDB)
    export DYNAMODB_TABLE=staging-kaizencoach-users
    python migrate_existing_users.py --athlete-id 196048876 --dry-run
    python migrate_existing_users.py --athlete-id 196048876 --execute
"""
import argparse
import json
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from decimal import Decimal

# DynamoDB access
import boto3
from botocore.exceptions import ClientError

from models.training_plan import TrainingPlan, TrainingMetrics, MetricValue
from utils.migration import migrate_plan_to_v2, validate_plan_structure
from utils.vdot_calculator import VDOTCalculator


def convert_decimals(obj):
    """Convert DynamoDB Decimal types to native Python types"""
    if isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        # Convert to int if it's a whole number, otherwise float
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    else:
        return obj


def convert_to_dynamodb_format(obj):
    """Convert Python native types to DynamoDB format (replacing floats with Decimals)"""
    if isinstance(obj, list):
        return [convert_to_dynamodb_format(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_to_dynamodb_format(v) for k, v in obj.items()}
    elif isinstance(obj, float):
        return Decimal(str(obj))
    else:
        return obj


# ============================================================
# DynamoDB Direct Access
# ============================================================

def get_dynamodb_table():
    """Get DynamoDB table from environment variable"""
    table_name = os.environ.get('DYNAMODB_TABLE')
    if not table_name:
        print("❌ DYNAMODB_TABLE environment variable not set")
        return None
    
    # Get AWS region (default to eu-west-1)
    region = os.environ.get('AWS_REGION', os.environ.get('AWS_DEFAULT_REGION', 'eu-west-1'))
    
    print(f"--- Using DynamoDB Backend ---")
    print(f"Table: {table_name}")
    print(f"Region: {region}")
    
    try:
        # Check if AWS credentials are available
        import boto3
        session = boto3.Session()
        credentials = session.get_credentials()
        
        if credentials is None:
            print("❌ No AWS credentials found!")
            print("Configure credentials with:")
            print("  - aws configure")
            print("  - or set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
            return None
        
        dynamodb = boto3.resource('dynamodb', region_name=region)
        table = dynamodb.Table(table_name)
        
        # Test connection
        table.load()
        print(f"✓ Connected to DynamoDB table: {table_name}")
        
        return table
    except Exception as e:
        print(f"❌ Failed to connect to DynamoDB: {e}")
        print(f"Check that:")
        print(f"  - AWS credentials are configured")
        print(f"  - Table '{table_name}' exists in region '{region}'")
        print(f"  - You have permissions to access the table")
        return None


def load_dynamodb_user(athlete_id: str, table) -> Optional[Dict[str, Any]]:
    """Load user data directly from DynamoDB"""
    try:
        # athlete_id is stored as String in DynamoDB
        response = table.get_item(Key={'athlete_id': str(athlete_id)})
        if 'Item' not in response:
            print(f"❌ No data found for athlete_id: {athlete_id}")
            return None
        
        # Convert Decimals to native Python types
        user_data = convert_decimals(response['Item'])
        print(f"✓ Loaded data from DynamoDB for athlete {athlete_id}")
        return user_data
        
    except ClientError as e:
        print(f"❌ DynamoDB error loading athlete {athlete_id}: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error loading athlete {athlete_id}: {e}")
        return None


def save_dynamodb_user(athlete_id: str, user_data: Dict[str, Any], table) -> bool:
    """Save user data directly to DynamoDB"""
    try:
        # Convert to DynamoDB format (floats -> Decimals)
        dynamodb_data = convert_to_dynamodb_format(user_data)
        
        # Ensure athlete_id is set correctly as String
        dynamodb_data['athlete_id'] = str(athlete_id)
        
        table.put_item(Item=dynamodb_data)
        print(f"✓ Saved data to DynamoDB for athlete {athlete_id}")
        return True
        
    except ClientError as e:
        print(f"❌ DynamoDB error saving athlete {athlete_id}: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error saving athlete {athlete_id}: {e}")
        return False


# ============================================================
# Local JSON File Access
# ============================================================

def load_local_json(athlete_id: str) -> Optional[Dict[str, Any]]:
    """Load user data from local JSON file"""
    json_path = Path('users_data.json')
    
    if not json_path.exists():
        print(f"❌ Local file not found: {json_path}")
        return None
    
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        print(f"--- Using Local File Backend ---")
        print(f"✓ Loaded local JSON: {json_path}")
        return data.get(athlete_id)  # Return just this athlete's data
    except Exception as e:
        print(f"❌ Failed to load local JSON: {e}")
        return None


def save_local_json(athlete_id: str, user_data: Dict[str, Any]) -> bool:
    """Save user data to local JSON file"""
    json_path = Path('users_data.json')
    
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


# ============================================================
# Generic Load/Save (routes to correct backend)
# ============================================================

def load_user_data(athlete_id: str, use_local: bool = False, dynamodb_table=None) -> Optional[Dict[str, Any]]:
    """Load user data from either local JSON or DynamoDB"""
    if use_local:
        return load_local_json(athlete_id)
    
    if dynamodb_table is None:
        print("❌ No DynamoDB table available and --local not specified")
        return None
    
    return load_dynamodb_user(athlete_id, dynamodb_table)


def save_user_data(athlete_id: str, user_data: Dict[str, Any], use_local: bool = False, dynamodb_table=None) -> bool:
    """Save user data to either local JSON or DynamoDB"""
    if use_local:
        return save_local_json(athlete_id, user_data)
    
    if dynamodb_table is None:
        print("❌ No DynamoDB table available and --local not specified")
        return False
    
    return save_dynamodb_user(athlete_id, user_data, dynamodb_table)


# ============================================================
# Migration Logic
# ============================================================

def migrate_user(athlete_id: str, dry_run: bool = True, use_local: bool = False, dynamodb_table=None) -> dict:
    """
    Migrate a single user's data to plan_v2 format.
    
    Args:
        athlete_id: Athlete ID to migrate
        dry_run: If True, don't save changes
        use_local: If True, use local JSON file instead of DynamoDB
        dynamodb_table: DynamoDB table resource (if not using local)
    
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
        
        user_data = load_user_data(athlete_id, use_local, dynamodb_table)
        
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
            'goal_date': None,
            'goal_distance': None,
            'plan_start_date': None
        }
        
        # Get plan_structure for week dates
        plan_structure = user_data.get('plan_structure')
        if plan_structure and 'weeks' in plan_structure:
            print(f"✓ Found plan_structure with {len(plan_structure['weeks'])} weeks")
        
        # Migrate plan to v2
        print(f"\n📝 Migrating plan to structured format...")
        plan_v2 = migrate_plan_to_v2(
            plan_markdown=user_data['plan'],
            plan_data=plan_structure,  # Pass plan_structure, not plan_data
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
        
        # === Migrate training_metrics if they exist at root level ===
        print(f"\n📊 Processing training metrics...")
        
        if 'training_metrics' in user_data:
            print(f"✅ training_metrics already exists - PRESERVING")
            # Don't overwrite - it's already in new format
        else:
            print(f"ℹ️  No training_metrics dict found - checking for root-level metrics...")
            
            metrics = TrainingMetrics()
            metrics_found = False
            vdot_paces = None
            
            # Check for LTHR at root level
            if 'lthr' in user_data and user_data['lthr']:
                lthr_value = user_data['lthr']
                
                # Check if LTHR is user-entered or needs migration
                if isinstance(lthr_value, dict):
                    # Already in MetricValue format
                    metrics.lthr = MetricValue(**lthr_value)
                    print(f"  ✅ Migrated LTHR: {lthr_value.get('value')} (already structured)")
                else:
                    # Raw value - needs wrapping
                    metrics.lthr = MetricValue(
                        value=lthr_value,
                        source='MIGRATED',
                        date_set=datetime.now().isoformat()
                    )
                    print(f"  ✅ Migrated LTHR: {lthr_value} from root field")
                
                result['changes'].append(f"Migrated LTHR: {lthr_value}")
                metrics_found = True
            
            # Check for FTP at root level
            if 'ftp' in user_data and user_data['ftp']:
                ftp_value = user_data['ftp']
                
                if isinstance(ftp_value, dict):
                    metrics.ftp = MetricValue(**ftp_value)
                    print(f"  ✅ Migrated FTP: {ftp_value.get('value')} (already structured)")
                else:
                    metrics.ftp = MetricValue(
                        value=ftp_value,
                        source='MIGRATED',
                        date_set=datetime.now().isoformat()
                    )
                    print(f"  ✅ Migrated FTP: {ftp_value} from root field")
                
                result['changes'].append(f"Migrated FTP: {ftp_value}")
                metrics_found = True
            
            # Check for VDOT at root level
            if 'vdot' in user_data and user_data['vdot']:
                vdot_value = user_data['vdot']
                
                if isinstance(vdot_value, dict):
                    metrics.vdot = MetricValue(**vdot_value)
                    print(f"  ✅ Migrated VDOT: {vdot_value.get('value')} (already structured)")
                    vdot_paces = None  # Already has paces presumably
                else:
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
        
        # === Copy lifestyle data from plan_data ===
        print(f"\n👤 Processing lifestyle context...")
        
        plan_data = user_data.get('plan_data')  # Get plan_data for lifestyle info
        
        if 'lifestyle' in user_data:
            print(f"✅ lifestyle already exists - PRESERVING")
        elif plan_data:
            # Simple copy from plan_data
            lifestyle = {}
            
            if 'lifestyle_context' in plan_data and plan_data['lifestyle_context']:
                lifestyle['training_constraints'] = plan_data['lifestyle_context']
                print(f"  ✅ Copied lifestyle_context")
            
            if 'athlete_type' in plan_data and plan_data['athlete_type']:
                lifestyle['athlete_type'] = plan_data['athlete_type']
                print(f"  ✅ Copied athlete_type: {plan_data['athlete_type']}")
            
            if lifestyle:
                user_data['lifestyle'] = lifestyle
                print(f"  ✅ Created lifestyle with {len(lifestyle)} fields")
                result['changes'].append(f"Copied lifestyle from plan_data")
            else:
                print(f"  ℹ️  No lifestyle data in plan_data")
        else:
            print(f"  ℹ️  No plan_data found")
        
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
            success = save_user_data(athlete_id, user_data, use_local, dynamodb_table)
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
    
    # Get DynamoDB table if not using local
    dynamodb_table = None
    if not use_local:
        dynamodb_table = get_dynamodb_table()
        if dynamodb_table is None:
            sys.exit(1)
    
    # Determine which users to migrate
    athlete_ids = []
    
    if args.athlete_id:
        athlete_ids = [args.athlete_id]
    elif args.all_users:
        print("\n⚠️  --all-users requires manual athlete_id list")
        print("Please specify athlete IDs:")
        print("  Known users: 2117356 (darren), 196048876 (darren-staging), etc.")
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
        result = migrate_user(athlete_id, dry_run=dry_run, use_local=use_local, dynamodb_table=dynamodb_table)
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