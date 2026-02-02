#!/usr/bin/env python3
"""
Measure DynamoDB user item size and show which keys use the most space.

DynamoDB item limit is 400 KB. This script loads an athlete's user_data (from
DynamoDB or local file), serializes it like DynamoDB/store, and reports:
- Total item size (bytes and KB)
- Per-top-level-key size, sorted descending
- Whether the item would exceed the limit

Usage:
  # From repo root, with .env or AWS credentials for the target env
  python check_dynamodb_item_size.py --athlete-id 5258947 --env mark

  # Use a specific table name
  python check_dynamodb_item_size.py --athlete-id 5258947 --table mark-kaizencoach-users

  # Local file backend (users_data.json)
  python check_dynamodb_item_size.py --athlete-id 5258947 --local
"""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

# Project root (for --local users_data.json path)
_SCRIPT_DIR = Path(__file__).resolve().parent

DYNAMODB_MAX_ITEM_BYTES = 400 * 1024  # 400 KB


def get_approx_size_bytes(obj):
    """Approximate serialized size (UTF-8 JSON). DynamoDB uses similar encoding."""
    return len(json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8"))


def _convert_decimals(obj):
    """Convert DynamoDB Decimals to int/float so we can measure JSON size. No app imports."""
    if isinstance(obj, dict):
        return {k: _convert_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_decimals(x) for x in obj]
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def load_user_data_dynamodb(athlete_id: str, table_name: str, region: str = "eu-west-1"):
    """Load one user's item from DynamoDB. Returns Python dict (not DynamoDB item)."""
    try:
        import boto3
    except ImportError:
        print("boto3 is required for DynamoDB. Install it with: pip install boto3")
        print("Or activate the project venv that has requirements.txt installed.")
        sys.exit(2)

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)
    response = table.get_item(Key={"athlete_id": str(athlete_id)})
    if "Item" not in response:
        return None
    return _convert_decimals(response["Item"])


def load_user_data_local(athlete_id: str, path: str = None):
    """Load one user's data from local JSON file."""
    p = Path(path or _SCRIPT_DIR / "users_data.json")
    if not p.exists():
        return None
    with open(p, "r") as f:
        all_data = json.load(f)
    return all_data.get(str(athlete_id))


def main():
    parser = argparse.ArgumentParser(
        description="Measure DynamoDB user item size and per-key breakdown"
    )
    parser.add_argument(
        "--athlete-id",
        required=True,
        help="Athlete ID (e.g. 5258947)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--env",
        choices=["staging", "production", "mark-prod", "shane-prod", "mark", "shane"],
        help="Environment: table chosen by env (mark/mark-prod -> mark-kaizencoach-users)",
    )
    group.add_argument(
        "--table",
        help="DynamoDB table name (e.g. mark-kaizencoach-users)",
    )
    group.add_argument(
        "--local",
        action="store_true",
        help="Load from local users_data.json",
    )
    parser.add_argument(
        "--region",
        default="eu-west-1",
        help="AWS region for DynamoDB (default: eu-west-1)",
    )
    args = parser.parse_args()

    athlete_id = args.athlete_id

    if args.local:
        user_data = load_user_data_local(athlete_id)
        source = "local (users_data.json)"
    else:
        table_map = {
            "staging": "staging-kaizencoach-users",
            "production": "my-personal-coach-users",
            "mark-prod": "mark-kaizencoach-users",
            "mark": "mark-kaizencoach-users",
            "shane-prod": "shane-kaizencoach-users",
            "shane": "shane-kaizencoach-users",
        }
        table_name = args.table or table_map.get(args.env)
        if not table_name:
            print(f"Unknown env: {args.env}. Use --table or a known --env.")
            sys.exit(1)
        user_data = load_user_data_dynamodb(athlete_id, table_name, args.region)
        source = f"DynamoDB table {table_name}"

    if user_data is None:
        print(f"Athlete {athlete_id} not found ({source}).")
        sys.exit(1)

    # Total size (same serialization as we'd store)
    total_bytes = get_approx_size_bytes(user_data)
    total_kb = total_bytes / 1024
    over_limit = total_bytes > DYNAMODB_MAX_ITEM_BYTES

    # Per-key sizes (only top-level)
    key_sizes = []
    for key in user_data:
        val = user_data[key]
        size = get_approx_size_bytes(val)
        key_sizes.append((key, size))

    key_sizes.sort(key=lambda x: -x[1])

    # Report
    print()
    print("=" * 60)
    print("DYNAMODB USER ITEM SIZE")
    print("=" * 60)
    print(f"Athlete ID:    {athlete_id}")
    print(f"Source:        {source}")
    print(f"Total size:   {total_bytes:,} bytes  ({total_kb:.2f} KB)")
    print(f"Limit:        {DYNAMODB_MAX_ITEM_BYTES:,} bytes  (400 KB)")
    if over_limit:
        print(f"Status:       OVER LIMIT by {total_bytes - DYNAMODB_MAX_ITEM_BYTES:,} bytes")
    else:
        print(f"Status:       OK  (headroom: {DYNAMODB_MAX_ITEM_BYTES - total_bytes:,} bytes)")
    print()
    print("Top-level keys by size (largest first):")
    print("-" * 60)
    for key, size in key_sizes:
        pct = (100.0 * size / total_bytes) if total_bytes else 0
        bar = "#" * min(40, int(pct / 2.5)) + " " * (40 - min(40, int(pct / 2.5)))
        print(f"  {key:30} {size:>10,} bytes  ({pct:5.1f}%)  |{bar}|")
    print("-" * 60)
    print()
    return 0 if not over_limit else 1


if __name__ == "__main__":
    sys.exit(main())
