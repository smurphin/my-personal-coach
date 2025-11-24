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
        
        FIXES:
        - Issue #4: HRV now shows today's value (lastNightAvg) instead of weeklyAvg
        - Issue #3: Body Battery now correctly extracts BOTH high and low values
        """
        metrics = {
            "date": stats.get("fetch_date"),
            "hrv_status": None,
            "hrv_value": None,
            "hrv_weekly_avg": None,
            "sleep_score": None,
            "body_battery_high": None,
            "body_battery_low": None,
            "training_status": None
        }

        # === FIX #4: HRV - Show Today's Value ===
        if stats.get("hrv"):
            hrv_data = stats["hrv"]
            hrv_summary = hrv_data.get("hrvSummary", {})
            metrics["hrv_status"] = hrv_summary.get("status")
            
            # Priority order for today's HRV (not weekly average):
            # 1. lastNightAvg / lastNightAverage (most accurate for today)
            # 2. lastNightValue (fallback)
            # 3. weeklyAvg (last resort)
            metrics["hrv_value"] = (
                hrv_summary.get("lastNightAvg") or 
                hrv_summary.get("lastNightAverage") or
                hrv_summary.get("lastNightValue") or
                hrv_summary.get("weeklyAvg")
            )
            
            # Also store weekly average for trend analysis
            # (used for AI context and graph overlay)
            metrics["hrv_weekly_avg"] = hrv_summary.get("weeklyAvg")

        # Sleep Score
        if stats.get("sleep"):
            sleep_data = stats["sleep"]
            metrics["sleep_score"] = sleep_data.get("dailySleepDTO", {}).get("sleepScores", {}).get("overall", {}).get("value")

        # === Body Battery Extraction ===
        if stats.get("body_battery"):
            bb_data = stats["body_battery"]
            
            # Handle list format (array of day summaries)
            if isinstance(bb_data, list) and len(bb_data) > 0:
                day_data = bb_data[0]
                
                # The actual readings are in bodyBatteryValuesArray
                # Format: [[timestamp, level], [timestamp, level], ...]
                if 'bodyBatteryValuesArray' in day_data:
                    values_array = day_data['bodyBatteryValuesArray']
                    
                    # Extract the battery levels (second element of each pair)
                    battery_values = [reading[1] for reading in values_array if len(reading) >= 2]
                    
                    if battery_values:
                        metrics["body_battery_high"] = max(battery_values)
                        metrics["body_battery_low"] = min(battery_values)
                        print(f"  BB [{stats.get('fetch_date')}]: High {max(battery_values)}, Low {min(battery_values)}")
                
                # Fallback: use top-level charged if no array
                elif 'charged' in day_data:
                    metrics["body_battery_high"] = day_data.get('charged')
                    metrics["body_battery_low"] = day_data.get('charged')
            
            # Handle dict format (summary object - unlikely with this API)
            elif isinstance(bb_data, dict):
                metrics["body_battery_high"] = (
                    bb_data.get("highestValue") or
                    bb_data.get("maxValue") or
                    bb_data.get("charged")
                )
                metrics["body_battery_low"] = (
                    bb_data.get("lowestValue") or
                    bb_data.get("minValue") or
                    bb_data.get("drained")
                )

        # Training Status
        if stats.get("training_status"):
            metrics["training_status"] = stats["training_status"].get("trainingStatus")

        return metrics
    
    def calculate_readiness_score(self, metrics_timeline):
        """
        Calculates a readiness-to-perform score (0-100) based on recent health metrics.
        
        IMPROVED: More balanced weighting and clearer calculation
        
        Algorithm considers:
        - Recent sleep quality (40% weight)
        - HRV status (25% weight)
        - Body battery recovery (25% weight)
        - Training status (10% weight)
        
        Returns: int (0-100) or None if insufficient data
        """
        if not metrics_timeline or len(metrics_timeline) == 0:
            return None
        
        # Use yesterday's data (most recent complete day)
        latest = metrics_timeline[-1]
        
        score = 0
        weighted_score = 0
        total_weight = 0
        
        print(f"\n=== Readiness Calculation for {latest.get('date')} ===")
        
        # === Sleep Score (40% weight) ===
        if latest.get('sleep_score') is not None:
            sleep_score = latest['sleep_score']
            sleep_contribution = (sleep_score / 100) * 40
            weighted_score += sleep_contribution
            total_weight += 40
            print(f"  Sleep: {sleep_score}/100 → {sleep_contribution:.1f} points (40% weight)")
        
        # === HRV Status (25% weight) ===
        hrv_status = latest.get('hrv_status')
        if hrv_status:
            hrv_map = {
                'BALANCED': 25,
                'NORMAL': 20,
                'LOW': 12,
                'UNBALANCED': 8,
                'POOR': 3
            }
            hrv_contribution = hrv_map.get(hrv_status, 15)
            weighted_score += hrv_contribution
            total_weight += 25
            
            # Show both daily and rolling average for context
            hrv_daily = latest.get('hrv_value', 'N/A')
            hrv_garmin_avg = latest.get('hrv_weekly_avg', 'N/A')
            print(f"  HRV Status: {hrv_status} → {hrv_contribution} points (25% weight)")
            print(f"    Today: {hrv_daily}ms | Garmin 7-day avg: {hrv_garmin_avg}ms")
        
        # === Body Battery Recovery (25% weight) ===
        # Higher overnight low = better recovery
        if latest.get('body_battery_low') is not None:
            bb_low = latest['body_battery_low']
            bb_contribution = (bb_low / 100) * 25
            weighted_score += bb_contribution
            total_weight += 25
            print(f"  Body Battery Low: {bb_low}/100 → {bb_contribution:.1f} points (25% weight)")
        
        # === Training Status (10% weight) ===
        training_status = latest.get('training_status')
        if training_status:
            status_map = {
                'PRODUCTIVE': 10,
                'MAINTAINING': 8,
                'RECOVERY': 6,
                'UNPRODUCTIVE': 4,
                'DETRAINING': 2,
                'OVERREACHING': 0
            }
            ts_contribution = status_map.get(training_status, 5)
            weighted_score += ts_contribution
            total_weight += 10
            print(f"  Training Status: {training_status} → {ts_contribution} points (10% weight)")
        
        # Calculate final score
        if total_weight > 0:
            # Normalize to 100-point scale
            final_score = round((weighted_score / total_weight) * 100)
            print(f"  Final Readiness: {final_score}/100 (from {total_weight} points of data)")
            print("=" * 50)
            return final_score
        
        print(f"  Insufficient data for readiness calculation")
        print("=" * 50)
        return None