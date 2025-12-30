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

    def extract_key_metrics(self, stats, debug=False):
        """
        Extracts key metrics from a single day's stats for display.
        Returns a dict with cleaned values.
        
        Args:
            stats: Daily stats from Garmin
            debug: If True, prints debug info about available data (use sparingly)
        
        FIXES:
        - Issue #4: HRV now shows today's value (lastNightAvg) instead of weeklyAvg
        - Issue #3: Body Battery now correctly extracts BOTH high and low values
        - Added null safety for ACWR data
        """
        if debug:
            print(f"\n{'='*60}")
            print(f"DEBUG - Extracting metrics for {stats.get('fetch_date')}")
            print(f"Available data types: {[k for k, v in stats.items() if v is not None and k != 'fetch_date']}")
            print(f"{'='*60}")
        
        metrics = {
            "date": stats.get("fetch_date"),
            "hrv_status": None,
            "hrv_value": None,
            "hrv_weekly_avg": None,
            "sleep_score": None,
            "body_battery_high": None,
            "body_battery_low": None,
            "training_status": None,
            "vo2_max": None,
            "acwr_ratio": None,
            "acwr_status": None,
            "acute_load": None,
            "chronic_load": None
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
                    # Filter out None values to prevent max/min errors
                    battery_values = [reading[1] for reading in values_array 
                                     if len(reading) >= 2 and reading[1] is not None]
                    
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

        # Training Status - Extract BOTH Garmin status AND ACWR data
        if stats.get("training_status"):
            if debug:
                print(f"\n{'='*60}")
                print(f"DEBUG - Training Status for {stats.get('fetch_date')}")
                print(f"{'='*60}")
            
            ts_data = stats["training_status"]
            most_recent = ts_data.get("mostRecentTrainingStatus", {})
            latest_data = most_recent.get("latestTrainingStatusData", {})
            
            # Get first device's data (usually only one primary device)
            device_data = next(iter(latest_data.values()), {})
            
            if device_data:
                if debug:
                    print(f"DEBUG: Available device_data keys: {list(device_data.keys())}")
                
                # Extract Garmin's training status phrase
                status_phrase = device_data.get("trainingStatusFeedbackPhrase", "")
                metrics["training_status"] = status_phrase
                
                # Extract VO2 Max from mostRecentVO2Max
                # Structure: training_status -> mostRecentVO2Max -> generic/cycling -> vo2MaxPreciseValue
                vo2_max_data = ts_data.get("mostRecentVO2Max", {})
                if vo2_max_data:
                    # Prioritize running (generic) over cycling
                    if vo2_max_data.get("generic") and vo2_max_data["generic"].get("vo2MaxPreciseValue"):
                        metrics["vo2_max"] = vo2_max_data["generic"]["vo2MaxPreciseValue"]
                        if debug:
                            print(f"DEBUG: VO2 Max (Running): {metrics['vo2_max']}")
                    elif vo2_max_data.get("cycling") and vo2_max_data["cycling"].get("vo2MaxPreciseValue"):
                        metrics["vo2_max"] = vo2_max_data["cycling"]["vo2MaxPreciseValue"]
                        if debug:
                            print(f"DEBUG: VO2 Max (Cycling): {metrics['vo2_max']}")
                
                # Extract ACWR data (the GOLD for AI coaching!)
                # FIXED: Handle None values properly
                acwr_data = device_data.get("acuteTrainingLoadDTO")
                
                if acwr_data and isinstance(acwr_data, dict):
                    if debug:
                        print(f"DEBUG: ACWR data available, keys: {list(acwr_data.keys())}")
                    metrics["acwr_ratio"] = acwr_data.get("dailyAcuteChronicWorkloadRatio")
                    metrics["acwr_status"] = acwr_data.get("acwrStatus")  # OPTIMAL, LOW, HIGH
                    metrics["acute_load"] = acwr_data.get("dailyTrainingLoadAcute")
                    metrics["chronic_load"] = acwr_data.get("dailyTrainingLoadChronic")
                    
                    if debug:
                        print(f"Garmin Status: {status_phrase}")
                        print(f"ACWR Ratio: {metrics['acwr_ratio']} ({metrics['acwr_status']})")
                        print(f"Acute Load (7d): {metrics['acute_load']}")
                        print(f"Chronic Load (28d): {metrics['chronic_load']}")
                else:
                    if debug:
                        print(f"DEBUG: ACWR data NOT available (acwr_data={acwr_data})")
                        print(f"DEBUG: This device may not support Training Load metrics")
                    metrics["acwr_ratio"] = None
                    metrics["acwr_status"] = None
                    metrics["acute_load"] = None
                    metrics["chronic_load"] = None
                    if debug:
                        print(f"Garmin Status: {status_phrase}")
                        print(f"ACWR: Not available on this device")
            else:
                if debug:
                    print(f"DEBUG: No device_data found in training_status")
            
            if debug:
                print(f"{'='*60}\n")

        return metrics
    
    def calculate_readiness_score(self, metrics_timeline):
        """
        Calculate readiness score based on recovery metrics.
        Readiness = how prepared you are for an intense training session TODAY.
        
        Weighted scoring:
        - Sleep Quality: 30% (direct recovery indicator)
        - HRV Status: 30% (nervous system recovery, compared to 14-day baseline)
        - Body Battery HIGH: 25% (morning energy after overnight recovery)
        - Training Status: 15% (current training load balance)
        
        Returns dict with score and breakdown.
        """
        if not metrics_timeline or len(metrics_timeline) == 0:
            return None
        
        latest = metrics_timeline[-1]
        
        print(f"\n=== Readiness Calculation for {latest.get('date', 'unknown')} ===")
        
        weighted_score = 0
        total_weight = 0
        metrics_used = []
        
        # === Sleep Quality (30% weight) ===
        if latest.get('sleep_score') is not None:
            sleep_score = latest['sleep_score']
            sleep_contribution = (sleep_score / 100) * 30
            weighted_score += sleep_contribution
            total_weight += 30
            metrics_used.append('sleep')
            print(f"  Sleep: {sleep_score}/100 → {sleep_contribution:.1f} points (30% weight)")
        
        # === HRV Status (30% weight) - Deviation from 14-day baseline ===
        hrv_status = latest.get('hrv_status')
        if hrv_status and latest.get('hrv_value') is not None:
            # Calculate 14-day HRV baseline
            hrv_values = [day.get('hrv_value') for day in metrics_timeline if day.get('hrv_value')]
            if len(hrv_values) >= 3:  # Need at least 3 days for baseline
                baseline_hrv = sum(hrv_values) / len(hrv_values)
                current_hrv = latest['hrv_value']
                deviation_pct = ((current_hrv - baseline_hrv) / baseline_hrv) * 100
                
                # Scoring logic:
                # +5% to +15% above baseline = optimal (100% of 30 points)
                # -5% to +5% = neutral/at baseline (70% of 30 points)
                # -5% to -15% below baseline = suboptimal (40% of 30 points)
                # More than ±15% = unbalanced, needs recovery (20% of 30 points)
                
                if hrv_status == 'UNBALANCED':
                    # Unbalanced = needs recovery, regardless of direction
                    hrv_contribution = 6  # 20% of 30
                    status_text = "UNBALANCED (needs recovery)"
                elif 5 <= deviation_pct <= 15:
                    # Elevated but balanced = excellent readiness
                    hrv_contribution = 30  # 100% of 30
                    status_text = f"ELEVATED (+{deviation_pct:.1f}% vs baseline)"
                elif -5 <= deviation_pct <= 5:
                    # At baseline = good readiness
                    hrv_contribution = 21  # 70% of 30
                    status_text = f"BASELINE ({deviation_pct:+.1f}% vs baseline)"
                elif -15 <= deviation_pct < -5:
                    # Below baseline but balanced = moderate readiness
                    hrv_contribution = 12  # 40% of 30
                    status_text = f"BELOW BASELINE ({deviation_pct:.1f}% vs baseline)"
                else:
                    # Way off baseline = poor readiness
                    hrv_contribution = 6  # 20% of 30
                    status_text = f"FAR FROM BASELINE ({deviation_pct:+.1f}% vs baseline)"
                
                weighted_score += hrv_contribution
                total_weight += 30
                metrics_used.append('hrv')
                print(f"  HRV Status: {status_text} → {hrv_contribution:.1f} points (30% weight)")
                print(f"    Today: {current_hrv}ms | 14-day baseline: {baseline_hrv:.1f}ms")
            else:
                # Not enough data for baseline, use simple balanced/unbalanced
                if hrv_status == 'BALANCED':
                    hrv_contribution = 30
                    weighted_score += hrv_contribution
                    total_weight += 30
                    metrics_used.append('hrv')
                    print(f"  HRV Status: BALANCED → {hrv_contribution} points (30% weight)")
                    print(f"    Today: {latest.get('hrv_value')}ms (insufficient data for baseline)")
        
        # === Body Battery HIGH (25% weight) - Morning recovery level ===
        # HIGH = peak after overnight recovery (what matters for readiness)
        # LOW = bedtime exhaustion (NOT used for readiness)
        if latest.get('body_battery_high') is not None:
            bb_high = latest['body_battery_high']
            bb_contribution = (bb_high / 100) * 25
            weighted_score += bb_contribution
            total_weight += 25
            metrics_used.append('body_battery')
            print(f"  Body Battery High: {bb_high}/100 → {bb_contribution:.1f} points (25% weight)")
            print(f"    (Morning recovery level, not bedtime low)")
        
        # === Training Status (15% weight) ===
        # Readiness perspective: RECOVERY = ready for hard work, PRODUCTIVE = fatigued
        training_status = latest.get('training_status')
        acwr_ratio = latest.get('acwr_ratio')
        acwr_status = latest.get('acwr_status')
        
        if training_status:
            # Extract base status from Garmin's phrase (e.g., "MAINTAINING_4" -> "MAINTAINING")
            base_status = training_status.split('_')[0]
            
            # Readiness-focused mapping:
            # RECOVERY = well-rested, ready for intense session (HIGH)
            # MAINTAINING = steady state, moderate readiness (MEDIUM-HIGH)
            # PRODUCTIVE = building fitness, fatigued (MEDIUM)
            # UNPRODUCTIVE/DETRAINING = over-recovered, low fitness (MEDIUM with caution)
            # OVERREACHING = needs recovery (LOW)
            status_map = {
                'RECOVERY': 15,        # 100% - Well-rested, ready to go hard
                'MAINTAINING': 12,     # 80% - Steady, good for moderate work
                'PRODUCTIVE': 9,       # 60% - Building fitness but fatigued
                'UNPRODUCTIVE': 9,     # 60% - Over-recovered, needs stimulus
                'DETRAINING': 6,       # 40% - Low fitness, caution needed
                'OVERREACHING': 3      # 20% - Needs recovery badly
            }
            ts_contribution = status_map.get(base_status, 7.5)
            weighted_score += ts_contribution
            total_weight += 15
            metrics_used.append('training_status')
            print(f"  Training Status: {base_status} → {ts_contribution} points (15% weight)")
            if acwr_ratio is not None and acwr_status:
                print(f"    ACWR: {acwr_ratio:.2f} ({acwr_status}) - Acute: {latest.get('acute_load')}, Chronic: {latest.get('chronic_load')}")
        
        # === Calculate final score ===
        if len(metrics_used) < 2:
            print(f"  ⚠️  Insufficient data: Only {len(metrics_used)} metric(s) available")
            print(f"  Minimum 2 metrics required for reliable readiness score")
            print("=" * 50)
            return None
        
        if total_weight > 0:
            # Normalize to 100-point scale
            final_score = round((weighted_score / total_weight) * 100)
            
            print(f"  Metrics used: {', '.join(metrics_used)}")
            print(f"  Final Readiness: {final_score}/100 (from {total_weight} points of data)")
            print("=" * 50)
            
            return {
                'score': final_score,
                'metrics_used': metrics_used,
                'data_quality': 'excellent' if len(metrics_used) >= 3 else 'moderate'
            }
        
        print(f"  Insufficient data for readiness calculation")
        print("=" * 50)
        return None