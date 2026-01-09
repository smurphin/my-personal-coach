#!/usr/bin/env python3
"""
DynamoDB Backup and Restore Utility

Usage:
    # Backup entire table
    python dynamodb_backup.py backup TABLE_NAME
    
    # Backup specific user
    python dynamodb_backup.py backup TABLE_NAME --athlete-id 2117356
    
    # Restore entire table
    python dynamodb_backup.py restore TABLE_NAME backup-file.json
    
    # Restore specific user
    python dynamodb_backup.py restore TABLE_NAME backup-file.json --athlete-id 2117356
    
    # List backups
    python dynamodb_backup.py list
"""

import boto3
import json
import argparse
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path

class DecimalEncoder(json.JSONEncoder):
    """Handle Decimal types from DynamoDB"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            # Convert to int if no decimal places, otherwise float
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def backup_table(table_name, output_file=None, athlete_id=None):
    """
    Backup DynamoDB table to JSON file
    
    Args:
        table_name: Name of DynamoDB table
        output_file: Output filename (optional, auto-generated if not provided)
        athlete_id: Backup specific athlete only (optional)
    """
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)
    
    # Generate filename if not provided
    if output_file is None:
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        if athlete_id:
            output_file = f"backup-{table_name}-user-{athlete_id}-{timestamp}.json"
        else:
            output_file = f"backup-{table_name}-{timestamp}.json"
    
    # Create backups directory if it doesn't exist
    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    output_path = backup_dir / output_file
    
    print(f"Starting backup of {table_name}...")
    if athlete_id:
        print(f"  Filtering for athlete_id: {athlete_id}")
    
    # Scan table (with filter if athlete_id provided)
    items = []
    scan_kwargs = {}
    
    if athlete_id:
        scan_kwargs['FilterExpression'] = 'athlete_id = :aid'
        scan_kwargs['ExpressionAttributeValues'] = {':aid': str(athlete_id)}
    
    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response['Items'])
        
        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        print(f"  Scanned {len(items)} items...")
    
    # Save to file
    backup_data = {
        'backup_time': datetime.now().isoformat(),
        'table_name': table_name,
        'athlete_id_filter': athlete_id,
        'item_count': len(items),
        'items': items
    }
    
    with open(output_path, 'w') as f:
        json.dump(backup_data, f, indent=2, cls=DecimalEncoder)
    
    print(f"✅ Backed up {len(items)} items to {output_path}")
    print(f"   File size: {output_path.stat().st_size / 1024 / 1024:.2f} MB")
    
    return str(output_path)

def restore_table(table_name, input_file, athlete_id=None, dry_run=False):
    """
    Restore DynamoDB table from JSON backup file
    
    Args:
        table_name: Name of DynamoDB table
        input_file: Input backup filename
        athlete_id: Restore specific athlete only (optional)
        dry_run: If True, don't actually write to DynamoDB
    """
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)
    
    print(f"{'DRY RUN: ' if dry_run else ''}Starting restore to {table_name}...")
    if athlete_id:
        print(f"  Filtering for athlete_id: {athlete_id}")
    
    # Load backup file
    with open(input_file, 'r') as f:
        backup_data = json.load(f)
    
    # Show backup info
    print(f"\nBackup Information:")
    print(f"  Created: {backup_data['backup_time']}")
    print(f"  Source table: {backup_data['table_name']}")
    print(f"  Total items: {backup_data['item_count']}")
    
    items = backup_data['items']
    
    # Filter for specific athlete if requested
    if athlete_id:
        items = [item for item in items if item.get('athlete_id') == str(athlete_id)]
        print(f"  Filtered to {len(items)} items for athlete {athlete_id}")
    
    if dry_run:
        print(f"\n🔍 DRY RUN: Would restore {len(items)} items")
        if items:
            print(f"\nSample item keys:")
            sample = items[0]
            for key in sample.keys():
                print(f"  - {key}: {type(sample[key]).__name__}")
        return len(items)
    
    # Restore items in batches
    restored_count = 0
    failed_items = []
    
    print(f"\nRestoring {len(items)} items...")
    
    # DynamoDB batch_writer handles batching automatically
    try:
        with table.batch_writer() as batch:
            for idx, item in enumerate(items, 1):
                try:
                    batch.put_item(Item=item)
                    restored_count += 1
                    
                    if idx % 100 == 0:
                        print(f"  Restored {idx}/{len(items)} items...")
                
                except Exception as e:
                    failed_items.append((item.get('athlete_id', 'unknown'), str(e)))
                    print(f"  ⚠️  Failed to restore item {idx}: {e}")
    
    except Exception as e:
        print(f"❌ Batch write error: {e}")
        return None
    
    print(f"\n✅ Restored {restored_count}/{len(items)} items to {table_name}")
    
    if failed_items:
        print(f"\n⚠️  Failed to restore {len(failed_items)} items:")
        for athlete_id, error in failed_items[:10]:  # Show first 10
            print(f"  - athlete_id {athlete_id}: {error}")
        if len(failed_items) > 10:
            print(f"  ... and {len(failed_items) - 10} more")
    
    return restored_count

def list_backups(backup_dir="backups"):
    """List available backup files"""
    backup_path = Path(backup_dir)
    
    if not backup_path.exists():
        print(f"No backups directory found at {backup_path}")
        return
    
    backup_files = sorted(backup_path.glob("backup-*.json"), reverse=True)
    
    if not backup_files:
        print(f"No backup files found in {backup_path}")
        return
    
    print(f"\n{'='*80}")
    print(f"Available Backups ({len(backup_files)} files)")
    print(f"{'='*80}\n")
    
    for backup_file in backup_files:
        try:
            with open(backup_file, 'r') as f:
                data = json.load(f)
            
            file_size = backup_file.stat().st_size / 1024 / 1024  # MB
            
            print(f"📄 {backup_file.name}")
            print(f"   Created: {data['backup_time']}")
            print(f"   Table: {data['table_name']}")
            print(f"   Items: {data['item_count']}")
            print(f"   Size: {file_size:.2f} MB")
            if data.get('athlete_id_filter'):
                print(f"   Athlete: {data['athlete_id_filter']}")
            print()
        
        except Exception as e:
            print(f"⚠️  {backup_file.name} - Error reading: {e}\n")

def main():
    parser = argparse.ArgumentParser(
        description='DynamoDB Backup and Restore Utility',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backup entire table
  python dynamodb_backup.py backup production-table
  
  # Backup specific user
  python dynamodb_backup.py backup production-table --athlete-id 2117356
  
  # Restore entire table
  python dynamodb_backup.py restore production-table backups/backup-file.json
  
  # Restore with dry-run
  python dynamodb_backup.py restore production-table backups/backup-file.json --dry-run
  
  # List available backups
  python dynamodb_backup.py list
        """
    )
    
    parser.add_argument('command', choices=['backup', 'restore', 'list'],
                       help='Command to execute')
    parser.add_argument('table_name', nargs='?',
                       help='DynamoDB table name')
    parser.add_argument('file', nargs='?',
                       help='Backup file (for restore command)')
    parser.add_argument('--athlete-id',
                       help='Filter for specific athlete ID')
    parser.add_argument('--dry-run', action='store_true',
                       help='Simulate restore without writing to DynamoDB')
    parser.add_argument('--output', '-o',
                       help='Output filename for backup')
    
    args = parser.parse_args()
    
    if args.command == 'list':
        list_backups()
    
    elif args.command == 'backup':
        if not args.table_name:
            parser.error('table_name is required for backup command')
        
        backup_file = backup_table(
            args.table_name,
            output_file=args.output,
            athlete_id=args.athlete_id
        )
        print(f"\n💾 Backup complete: {backup_file}")
    
    elif args.command == 'restore':
        if not args.table_name or not args.file:
            parser.error('table_name and file are required for restore command')
        
        if not os.path.exists(args.file):
            print(f"❌ Backup file not found: {args.file}")
            return
        
        # Confirm restore unless dry-run
        if not args.dry_run:
            print(f"\n⚠️  WARNING: This will overwrite data in {args.table_name}")
            response = input(f"Are you sure you want to restore? (yes/no): ")
            if response.lower() != 'yes':
                print("Restore cancelled")
                return
        
        restored = restore_table(
            args.table_name,
            args.file,
            athlete_id=args.athlete_id,
            dry_run=args.dry_run
        )
        
        if restored is not None and not args.dry_run:
            print(f"\n✅ Restore complete: {restored} items restored")

if __name__ == "__main__":
    main()