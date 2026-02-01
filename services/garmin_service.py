from garmin_manager import GarminManager
from crypto_manager import encrypt, decrypt
from datetime import date, timedelta

class GarminService:
    """Service for Garmin Connect integration"""
    
    def authenticate_and_fetch(self, email, encrypted_password, target_date_iso, encrypted_tokenstore=None):
        """
        Authenticate with Garmin and fetch health stats for a specific date.
        Prefers tokenstore (saved session) when present so 2FA users don't re-enter OTP.
        Returns health stats dict or None on failure.
        """
        try:
            tokenstore = None
            if encrypted_tokenstore:
                tokenstore = decrypt(encrypted_tokenstore)
            if not tokenstore:
                password = decrypt(encrypted_password)
                if not password:
                    print("Could not decrypt Garmin password. Aborting fetch.")
                    return None
            else:
                password = ""  # tokenstore login doesn't use password

            garmin_manager = GarminManager(email, password)
            if garmin_manager.login(tokenstore=tokenstore):
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
        
        creds = user_data['garmin_credentials']
        return self.authenticate_and_fetch(
            creds['email'],
            creds['password'],
            yesterday_iso,
            encrypted_tokenstore=creds.get('tokenstore'),
        )
    
    def fetch_date_range(self, email, encrypted_password, days=14, encrypted_tokenstore=None):
        """
        Fetch health stats for a range of days.
        Prefers tokenstore when present (2FA users). Returns list of daily stats or None on failure.
        """
        try:
            tokenstore = None
            if encrypted_tokenstore:
                tokenstore = decrypt(encrypted_tokenstore)
            if not tokenstore:
                password = decrypt(encrypted_password)
                if not password:
                    return None
            else:
                password = ""

            garmin_manager = GarminManager(email, password)
            if not garmin_manager.login(tokenstore=tokenstore):
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
        Logs debug info only for the first day to avoid log spam.
        """
        if not stats_range:
            return []
        
        garmin_manager = GarminManager("dummy", "dummy")  # Just for method access
        
        # Extract metrics, with debug logging only on first day
        metrics = []
        for i, day in enumerate(stats_range):
            debug_mode = (i == 0)  # Only debug the first day
            metrics.append(garmin_manager.extract_key_metrics(day, debug=debug_mode))
        
        return metrics
    
    def calculate_readiness(self, metrics_timeline):
        """Calculate readiness score from metrics timeline"""
        if not metrics_timeline:
            return None
        
        garmin_manager = GarminManager("dummy", "dummy")
        return garmin_manager.calculate_readiness_score(metrics_timeline)
    
    def calculate_vo2_max_changes(self, metrics_timeline):
        """
        Calculate VO2 max changes: previous day and 14-day average.
        Returns dict with change_1d and change_14d_avg or None if insufficient data.
        """
        if not metrics_timeline or len(metrics_timeline) < 2:
            return None
        
        # Get today's VO2 max (last item in timeline)
        today_vo2 = metrics_timeline[-1].get('vo2_max')
        if today_vo2 is None:
            return None
        
        # Previous day change
        yesterday_vo2 = metrics_timeline[-2].get('vo2_max') if len(metrics_timeline) >= 2 else None
        change_1d = (today_vo2 - yesterday_vo2) if yesterday_vo2 is not None else None
        
        # 14-day average change
        # Get all VO2 max values from last 14 days (excluding today)
        vo2_values = []
        for i in range(max(0, len(metrics_timeline) - 15), len(metrics_timeline) - 1):
            vo2 = metrics_timeline[i].get('vo2_max')
            if vo2 is not None:
                vo2_values.append(vo2)
        
        if vo2_values:
            avg_14d = sum(vo2_values) / len(vo2_values)
            change_14d_avg = today_vo2 - avg_14d
        else:
            change_14d_avg = None
        
        return {
            'vo2_max': today_vo2,
            'change_1d': change_1d,
            'change_14d_avg': change_14d_avg
        }
    
    def store_credentials(self, email, password, tokenstore=None):
        """Encrypt and prepare credentials for storage. tokenstore (if provided) is used for 2FA users so they don't re-enter OTP on each fetch."""
        creds = {
            'email': email,
            'password': encrypt(password)
        }
        if tokenstore:
            creds['tokenstore'] = encrypt(tokenstore)
        return creds

# Create singleton instance
garmin_service = GarminService()