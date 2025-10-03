# garmin_manager.py
from garminconnect import (
    Garmin,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarminConnectAuthenticationError,
)
from datetime import date, timedelta

class GarminManager:
    def __init__(self, email, password):
        self.garmin = Garmin(email, password)

    def login(self):
        try:
            self.garmin.login()
            return True
        except (GarminConnectConnectionError, GarminConnectTooManyRequestsError, GarminConnectAuthenticationError) as e:
            print(f"Error logging into Garmin: {e}")
            return False

    def get_health_stats(self, target_date_iso):
        """
        Fetches health stats for a specific date, handling errors for each metric individually.
        """
        stats = {
            "hrv": None,
            "sleep": None,
            "body_battery": None,
            "training_status": None,
            "fetch_date": target_date_iso,
        }

        try:
            stats["hrv"] = self.garmin.get_hrv_data(target_date_iso)
        except Exception as e:
            print(f"Could not fetch HRV data for {target_date_iso}: {e}")

        try:
            stats["sleep"] = self.garmin.get_sleep_data(target_date_iso)
        except Exception as e:
            print(f"Could not fetch sleep data for {target_date_iso}: {e}")

        try:
            stats["body_battery"] = self.garmin.get_body_battery(target_date_iso)
        except Exception as e:
            print(f"Could not fetch Body Battery data for {target_date_iso}: {e}")

        try:
            stats["training_status"] = self.garmin.get_training_status(target_date_iso)
        except Exception as e:
            print(f"Could not fetch Training Status data for {target_date_iso}: {e}")

        # Only return a complete failure if ALL data points are missing.
        if all(value is None for key, value in stats.items() if key != "fetch_date"):
            print(f"All Garmin health stat fetches failed for {target_date_iso}.")
            return None

        return stats