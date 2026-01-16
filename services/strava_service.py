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
    
    def get_activity_laps(self, access_token, activity_id):
        """
        Get laps for a specific activity using the dedicated laps endpoint.
        
        This endpoint returns all laps recorded by the device, which is more reliable
        than relying on the laps field in the activity detail response.
        
        Returns:
            List of lap objects, or empty list if no laps or error
        """
        try:
            return self.get_api_data(access_token, f"activities/{activity_id}/laps")
        except Exception as e:
            print(f"⚠️  Error fetching laps for activity {activity_id}: {e}")
            return []
    
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
    
    def refresh_access_token(self, refresh_token):
        """
        Refresh an expired Strava access token.
        
        Args:
            refresh_token: The refresh_token from user's stored token
            
        Returns:
            dict: New token data with access_token, refresh_token, expires_at
            None: If refresh fails
        """
        from datetime import datetime
        
        print(f"🔄 Refreshing Strava access token...")
        
        try:
            response = requests.post(
                'https://www.strava.com/oauth/token',
                data={
                    'client_id': Config.STRAVA_CLIENT_ID,
                    'client_secret': Config.STRAVA_CLIENT_SECRET,
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token
                },
                timeout=10
            )
            
            if response.status_code == 200:
                token_data = response.json()
                expires_at = token_data.get('expires_at')
                if expires_at:
                    expires_time = datetime.fromtimestamp(expires_at)
                    print(f"✅ Token refreshed successfully (expires at {expires_time})")
                return token_data
            else:
                print(f"❌ Token refresh failed: {response.status_code}")
                try:
                    error_data = response.json()
                    print(f"   Error details: {error_data}")
                except:
                    print(f"   Response: {response.text[:200]}")
                return None
                
        except Exception as e:
            print(f"❌ Token refresh exception: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def ensure_valid_token(self, athlete_id, user_data, data_manager):
        """
        Ensure the user has a valid access token, refreshing if necessary.
        
        Args:
            athlete_id: The athlete's ID
            user_data: The user's data dict (will be modified if token refreshed)
            data_manager: The data manager instance to save updates
            
        Returns:
            str: Valid access_token
            None: If token cannot be refreshed
        """
        from datetime import datetime
        
        token = user_data.get('token', {})
        
        # Check if token exists
        if not token or 'access_token' not in token:
            print(f"❌ No token found for athlete {athlete_id}")
            return None
        
        expires_at = token.get('expires_at', 0)
        current_time = datetime.now().timestamp()
        time_until_expiry = expires_at - current_time
        
        # Refresh if expired OR expiring within next 5 minutes (300 seconds)
        if time_until_expiry < 300:
            hours_ago = abs(time_until_expiry) / 3600
            
            if time_until_expiry < 0:
                print(f"⏰ Token EXPIRED {hours_ago:.1f}h ago for athlete {athlete_id} - refreshing...")
            else:
                print(f"⏰ Token expiring in {time_until_expiry/60:.0f}m for athlete {athlete_id} - refreshing...")
            
            refresh_token = token.get('refresh_token')
            if not refresh_token:
                print(f"❌ No refresh_token available for athlete {athlete_id}")
                return None
            
            new_token = self.refresh_access_token(refresh_token)
            if new_token:
                user_data['token'] = new_token
                data_manager.save_user_data(athlete_id, user_data)
                print(f"💾 Saved refreshed token for athlete {athlete_id}")
                return new_token['access_token']
            else:
                print(f"❌ Token refresh failed for athlete {athlete_id}")
                return None
        else:
            # Token still valid
            hours_remaining = time_until_expiry / 3600
            print(f"✅ Token valid for athlete {athlete_id} ({hours_remaining:.1f}h remaining)")
            return token['access_token']

# Create singleton instance
strava_service = StravaService()