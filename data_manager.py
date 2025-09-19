import os
import json
import boto3

USERS_DATA_FILE = "users_data.json"

# --- Backend Implementations ---

class FileBackend:
    """A data manager that uses a local JSON file for storage."""
    def _load_data(self):
        if not os.path.exists(USERS_DATA_FILE):
            return {}
        with open(USERS_DATA_FILE, 'r') as f:
            return json.load(f)

    def _save_data(self, data):
        with open(USERS_DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)

    def load_user_data(self, athlete_id):
        all_data = self._load_data()
        return all_data.get(str(athlete_id), {})

    def save_user_data(self, athlete_id, user_data):
        all_data = self._load_data()
        all_data[str(athlete_id)] = user_data
        self._save_data(all_data)


class DynamoDBBackend:
    """A data manager that uses AWS DynamoDB for storage."""
    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb', region_name='eu-west-1')
        self.table = self.dynamodb.Table('my-personal-coach-users')

    def load_user_data(self, athlete_id):
        try:
            response = self.table.get_item(Key={'athlete_id': str(athlete_id)})
            return response.get('Item', {})
        except Exception as e:
            print(f"Error loading data for user {athlete_id} from DynamoDB: {e}")
            return {}

    def save_user_data(self, athlete_id, user_data):
        try:
            user_data['athlete_id'] = str(athlete_id)
            self.table.put_item(Item=user_data)
        except Exception as e:
            print(f"Error saving data for user {athlete_id} to DynamoDB: {e}")


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