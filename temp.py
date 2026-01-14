import boto3
import json
from decimal import Decimal

def convert_decimals(obj):
    if isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    elif isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj

dynamodb = boto3.resource('dynamodb', region_name='eu-west-1')
table = dynamodb.Table('staging-kaizencoach-users')
response = table.get_item(Key={'athlete_id': '196048876'})
user_data = convert_decimals(response['Item'])

print('Has plan_data:', 'plan_data' in user_data)
if 'plan_data' in user_data:
    print('plan_data keys:', list(user_data['plan_data'].keys()))
    if 'weeks' in user_data['plan_data']:
        weeks = user_data['plan_data']['weeks']
        print('Number of weeks in plan_data:', len(weeks))
        print('First 3 week numbers:', [w.get('week_number') for w in weeks[:3]])