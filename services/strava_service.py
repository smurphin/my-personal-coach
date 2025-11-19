import requests
from config import Config
from utils.decorators import strava_api_call

class StravaService:
    """Service for interacting with Strava API"""
    
    def __init__(self):
        self.api_url = Config.STRAVA_API_URL
    
    @strava_api_call
    def get_api_data(self, access_token, endpoint, params=None):
        """Make a GET request to Strava API"""
        headers = {'Authorization': f'Bearer {access_token}'}
        response = requests.get(f"{self.api_url}/{endpoint}", headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    
    def get_activity_streams(self, access_token, activity_id):
        """Fetch streams for a single activity"""
        headers = {'Authorization': f'Bearer {access_token}'}
        params = {'keys': 'heartrate,time,watts,distance,altitude', 'key_by_type': True}
        response = requests.get(
            f"{self.api_url}/activities/{activity_id}/streams",
            headers=headers,
            params=params
        )
        return response.json() if response.status_code == 200 else None
    
    def get_athlete_stats(self, access_token, athlete_id):
        """Get athlete statistics"""
        return self.get_api_data(access_token, f"athletes/{athlete_id}/stats")
    
    def get_athlete_zones(self, access_token):
        """Get athlete's heart rate and power zones"""
        return self.get_api_data(access_token, "athlete/zones")
    
    def get_recent_activities(self, access_token, after_timestamp, per_page=100):
        """Get recent activities after a certain timestamp"""
        return self.get_api_data(
            access_token,
            "athlete/activities",
            params={'after': after_timestamp, 'per_page': per_page}
        )
    
    def get_activity_detail(self, access_token, activity_id):
        """Get detailed information about a specific activity"""
        return self.get_api_data(access_token, f"activities/{activity_id}")
    
    def deauthorize(self, access_token):
        """Deauthorize the app from Strava"""
        try:
            deauthorize_payload = {'access_token': access_token}
            requests.post("https://www.strava.com/oauth/deauthorize", data=deauthorize_payload)
        except Exception as e:
            print(f"Could not deauthorize from Strava: {e}")
    
    def exchange_token(self, auth_code):
        """Exchange authorization code for access token"""
        token_payload = {
            "client_id": Config.STRAVA_CLIENT_ID,
            "client_secret": Config.STRAVA_CLIENT_SECRET,
            "code": auth_code,
            "grant_type": "authorization_code"
        }
        response = requests.post("https://www.strava.com/oauth/token", data=token_payload)
        response.raise_for_status()
        return response.json()

# Create singleton instance
strava_service = StravaService()
