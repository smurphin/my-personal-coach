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

    def get_health_stats_range(self, days=14):
        """
        Fetches health stats for the last N days.
        Returns a list of daily stats, ordered from oldest to newest.
        """
        today = date.today()
        stats_range = []
        
        for i in range(days - 1, -1, -1):  # Countdown from days-1 to 0
            target_date = today - timedelta(days=i)
            target_date_iso = target_date.isoformat()
            
            daily_stats = self.get_health_stats(target_date_iso)
            if daily_stats:
                stats_range.append(daily_stats)
        
        return stats_range

    def extract_key_metrics(self, stats):
        """
        Extracts key metrics from a single day's stats for display.
        Returns a dict with cleaned values.
        """
        metrics = {
            "date": stats.get("fetch_date"),
            "hrv_status": None,
            "hrv_value": None,
            "sleep_score": None,
            "body_battery_high": None,
            "body_battery_low": None,
            "training_status": None
        }

        # HRV
        if stats.get("hrv"):
            hrv_data = stats["hrv"]
            metrics["hrv_status"] = hrv_data.get("hrvSummary", {}).get("status")
            # Try to get the weekly average as the "value"
            metrics["hrv_value"] = hrv_data.get("hrvSummary", {}).get("weeklyAvg")

        # Sleep Score
        if stats.get("sleep"):
            sleep_data = stats["sleep"]
            metrics["sleep_score"] = sleep_data.get("dailySleepDTO", {}).get("sleepScores", {}).get("overall", {}).get("value")

        # Body Battery - get the HIGH and LOW values for the day
        if stats.get("body_battery") and isinstance(stats["body_battery"], list):
            battery_values = [reading.get("charged", 0) for reading in stats["body_battery"] if reading.get("charged") is not None]
            if battery_values:
                metrics["body_battery_high"] = max(battery_values)
                metrics["body_battery_low"] = min(battery_values)

        # Training Status
        if stats.get("training_status"):
            metrics["training_status"] = stats["training_status"].get("trainingStatus")

        return metrics
    
    def calculate_readiness_score(self, metrics_timeline):
        """
        Calculates a readiness-to-perform score (0-100) based on recent health metrics.
        
        Algorithm considers:
        - Recent sleep quality (last 3 nights, weighted toward most recent)
        - HRV status and trend
        - Body battery recovery pattern
        - Training status
        
        Returns: int (0-100) or None if insufficient data
        """
        if not metrics_timeline or len(metrics_timeline) < 3:
            return None
        
        # Get last 3 days of data
        recent_days = metrics_timeline[-3:]
        today = recent_days[-1]
        
        score = 0
        factors_count = 0
        
        # Sleep Quality (0-30 points) - weighted average of last 3 nights
        sleep_scores = [d.get('sleep_score') for d in recent_days if d.get('sleep_score')]
        if sleep_scores:
            # Weight: today=50%, yesterday=30%, day before=20%
            weights = [0.2, 0.3, 0.5] if len(sleep_scores) == 3 else [0.4, 0.6] if len(sleep_scores) == 2 else [1.0]
            weighted_sleep = sum(s * w for s, w in zip(sleep_scores[-len(weights):], weights))
            score += (weighted_sleep / 100) * 30
            factors_count += 1
        
        # HRV Status (0-25 points)
        hrv_status = today.get('hrv_status')
        if hrv_status:
            hrv_map = {
                'BALANCED': 25,
                'NORMAL': 20,
                'LOW': 10,
                'UNBALANCED': 5,
                'POOR': 0
            }
            score += hrv_map.get(hrv_status, 15)
            factors_count += 1
        
        # HRV Trend (0-15 points) - is it improving or declining?
        hrv_values = [d.get('hrv_value') for d in recent_days if d.get('hrv_value')]
        if len(hrv_values) >= 2:
            hrv_trend = hrv_values[-1] - hrv_values[0]
            if hrv_trend > 2:
                score += 15  # Improving
            elif hrv_trend > -2:
                score += 10  # Stable
            else:
                score += 5   # Declining
            factors_count += 1
        
        # Body Battery Recovery (0-20 points) - how well are they recovering?
        battery_high_values = [d.get('body_battery_high') for d in recent_days if d.get('body_battery_high')]
        if battery_high_values:
            avg_peak = sum(battery_high_values) / len(battery_high_values)
            score += (avg_peak / 100) * 20
            factors_count += 1
        
        # Training Status (0-10 points)
        training_status = today.get('training_status')
        if training_status:
            status_map = {
                'PRODUCTIVE': 10,
                'MAINTAINING': 8,
                'RECOVERY': 5,
                'UNPRODUCTIVE': 3,
                'DETRAINING': 2,
                'OVERREACHING': 0
            }
            score += status_map.get(training_status, 5)
            factors_count += 1
        
        # Normalize to 0-100 scale
        if factors_count > 0:
            max_possible = 30 + 25 + 15 + 20 + 10
            return int((score / max_possible) * 100)
        
        return None