import os
import json
import boto3
from decimal import Decimal
from botocore.exceptions import ClientError
from config import Config

USERS_DATA_FILE = "users_data.json"

# --- HELPER FUNCTIONS FOR DYNAMODB ---

def json_to_dynamodb(data):
    """
    Recursively converts a Python dictionary with mixed types
    into a DynamoDB-compatible format.
    - Converts floats to strings to avoid precision issues.
    - Removes keys with None or empty string values.
    """
    if isinstance(data, dict):
        new_dict = {}
        for k, v in data.items():
            sanitized_value = json_to_dynamodb(v)
            if sanitized_value is not None:
                new_dict[k] = sanitized_value
        return new_dict
    elif isinstance(data, list):
        new_list = [json_to_dynamodb(item) for item in data]
        return [item for item in new_list if item is not None]
    elif isinstance(data, float):
        return str(data)
    elif data in [None, ""]:
        return None
    else:
        return data

def dynamodb_to_json(data):
    """
    Recursively converts a DynamoDB item (with Decimal types)
    into a standard Python dictionary.
    """
    if isinstance(data, dict):
        return {k: dynamodb_to_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [dynamodb_to_json(item) for item in data]
    elif isinstance(data, Decimal):
        # Convert Decimal to int if it's a whole number, otherwise float
        if data % 1 == 0:
            return int(data)
        else:
            return float(data)
    else:
        return data

# --- Backend Implementations ---

class FileBackend:
    """A data manager that uses a local JSON file for storage."""
    def _load_data(self):
        if not os.path.exists(USERS_DATA_FILE):
            return {}
        try:
            with open(USERS_DATA_FILE, 'r') as f:
                print(f"--- DM: Loading data from {USERS_DATA_FILE} ---")
                return json.load(f)
        except json.JSONDecodeError:
            return {}

    def _save_data(self, data):
        with open(USERS_DATA_FILE, 'w') as f:
            print(f"--- DM: Saving data to {USERS_DATA_FILE} ---")
            json.dump(data, f, indent=4)

    def load_user_data(self, athlete_id):
        all_data = self._load_data()
        return all_data.get(str(athlete_id), {})

    def save_user_data(self, athlete_id, user_data):
        all_data = self._load_data()
        all_data[str(athlete_id)] = user_data
        self._save_data(all_data)
        print(f"--- Saved data for user {athlete_id} to local file. ---")

    def delete_user_data(self, athlete_id):
        all_data = self._load_data()
        if str(athlete_id) in all_data:
            del all_data[str(athlete_id)]
            self._save_data(all_data)
            print(f"--- Deleted data for user {athlete_id} from local file. ---")

class DynamoDBBackend:
    """A data manager that uses AWS DynamoDB for storage."""
    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb', region_name=Config.AWS_REGION)
        # Use Config.DYNAMODB_TABLE instead of hardcoded name
        self.table = self.dynamodb.Table(Config.DYNAMODB_TABLE)
        print(f"--- DynamoDB Backend initialized with table: {Config.DYNAMODB_TABLE} ---")

    def load_user_data(self, athlete_id):
        try:
            response = self.table.get_item(Key={'athlete_id': str(athlete_id)})
            item = response.get('Item', {})
            return dynamodb_to_json(item)
        except Exception as e:
            print(f"Error loading data for user {athlete_id} from DynamoDB: {e}")
            return {}

    def save_user_data(self, athlete_id, user_data):
        try:
            user_data['athlete_id'] = str(athlete_id)
            item_to_save = json_to_dynamodb(user_data)
            self.table.put_item(Item=item_to_save)
            print(f"--- Saved data for user {athlete_id} to DynamoDB. ---")
        except Exception as e:
            print(f"Error saving data for user {athlete_id} to DynamoDB: {e}")
            raise e
        
    def delete_user_data(self, athlete_id):
        try:
            self.table.delete_item(Key={'athlete_id': str(athlete_id)})
            print(f"--- Deleted data for user {athlete_id} from DynamoDB. ---")
        except Exception as e:
            print(f"Error deleting data for user {athlete_id} from DynamoDB: {e}")

# --- Factory Function ---
def get_data_manager():
    """
    Factory function to return the correct data manager
    based on the environment.
    """
    if os.getenv('FLASK_ENV') == 'production':
        print("--- Using DynamoDB Backend ---")
        return DynamoDBBackend()
    else:
        print("--- Using Local File Backend ---")
        return FileBackend()

# Initialize a single instance of the data manager for the app to use
data_manager = get_data_manager()