#!/usr/bin/env python3
"""
CLI script to trigger feedback generation for a specific athlete.
Can be run directly on the server without requiring web session.

Usage:
    python scripts/trigger_feedback.py --athlete-id 5258947 --env mark-prod
"""

import sys
import os
import argparse

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    parser = argparse.ArgumentParser(description='Trigger feedback generation for an athlete')
    parser.add_argument('--athlete-id', type=int, required=True, help='Athlete ID to process')
    parser.add_argument('--env', type=str, required=True, choices=['staging', 'prod', 'shane-prod', 'mark-prod'], 
                       help='Environment name')
    
    args = parser.parse_args()
    
    # Set environment variables before importing config (matching force_reparse_plan_v2.py pattern)
    if args.env in ['prod', 'shane-prod', 'mark-prod']:
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
    if args.env in env_map:
        os.environ['ENVIRONMENT'] = env_map[args.env]
    
    # Now import after environment is set
    from config import Config
    from data_manager import DynamoDBBackend, FileBackend, get_data_manager
    
    # Initialize data manager based on environment
    if Config.USE_DYNAMODB:
        backend = DynamoDBBackend()
        print(f"✅ Using DynamoDB backend (table: {Config.DYNAMODB_TABLE})")
    else:
        backend = FileBackend()
        print(f"✅ Using file backend")
    
    # Monkey-patch the data_manager in api_routes to use our backend
    import routes.api_routes
    routes.api_routes.data_manager = backend
    
    print(f"\n🔄 Triggering feedback generation for athlete {args.athlete_id}...")
    print(f"   Environment: {args.env}\n")
    
    try:
        # Call the webhook processing function directly
        from routes.api_routes import _trigger_webhook_processing
        _trigger_webhook_processing(args.athlete_id)
        print(f"\n✅ Feedback generation triggered successfully!")
    except Exception as e:
        print(f"\n❌ Error triggering feedback: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()

