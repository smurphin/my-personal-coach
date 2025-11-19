from garmin_manager import GarminManager
from crypto_manager import encrypt, decrypt
from datetime import date, timedelta

class GarminService:
    """Service for Garmin Connect integration"""
    
    def authenticate_and_fetch(self, email, encrypted_password, target_date_iso):
        """
        Authenticate with Garmin and fetch health stats for a specific date.
        Returns health stats dict or None on failure.
        """
        try:
            password = decrypt(encrypted_password)
            if not password:
                print("Could not decrypt Garmin password. Aborting fetch.")
                return None

            garmin_manager = GarminManager(email, password)
            if garmin_manager.login():
                health_stats = garmin_manager.get_health_stats(target_date_iso)
                if health_stats:
                    print(f"--- Successfully fetched Garmin data for {target_date_iso}. ---")
                    return health_stats
                else:
                    print(f"--- Failed to fetch Garmin data, but login was successful. ---")
                    return None
            else:
                print("--- Garmin login failed. ---")
                return None
        except Exception as e:
            print(f"Failed to fetch Garmin data: {e}")
            return None
    
    def fetch_yesterday_data(self, user_data):
        """
        Fetches yesterday's Garmin data.
        Returns the newly fetched data or None on failure.
        """
        if 'garmin_credentials' not in user_data:
            return None

        yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
        
        return self.authenticate_and_fetch(
            user_data['garmin_credentials']['email'],
            user_data['garmin_credentials']['password'],
            yesterday_iso
        )
    
    def fetch_date_range(self, email, encrypted_password, days=14):
        """
        Fetch health stats for a range of days.
        Returns list of daily stats or None on failure.
        """
        try:
            password = decrypt(encrypted_password)
            if not password:
                return None

            garmin_manager = GarminManager(email, password)
            if not garmin_manager.login():
                return None

            stats_range = garmin_manager.get_health_stats_range(days=days)
            return stats_range if stats_range else None
        except Exception as e:
            print(f"Error fetching Garmin date range: {e}")
            return None
    
    def extract_metrics_timeline(self, stats_range):
        """
        Extract key metrics from raw stats for display.
        Returns list of metric dicts.
        """
        if not stats_range:
            return []
        
        garmin_manager = GarminManager("dummy", "dummy")  # Just for method access
        return [garmin_manager.extract_key_metrics(day) for day in stats_range]
    
    def calculate_readiness(self, metrics_timeline):
        """Calculate readiness score from metrics timeline"""
        if not metrics_timeline:
            return None
        
        garmin_manager = GarminManager("dummy", "dummy")
        return garmin_manager.calculate_readiness_score(metrics_timeline)
    
    def store_credentials(self, email, password):
        """Encrypt and prepare credentials for storage"""
        return {
            'email': email,
            'password': encrypt(password)
        }

# Create singleton instance
garmin_service = GarminService()
