import bisect
from datetime import datetime, timedelta
import re
from utils.formatters import format_seconds, map_race_distance

class TrainingService:
    """Service for training plan logic and activity analysis"""

    def _pace_seconds_per_km(self, distance_m, time_s):
        """Return pace as seconds/km, or None if cannot compute."""
        try:
            if not isinstance(distance_m, (int, float)) or distance_m <= 0:
                return None
            if not isinstance(time_s, (int, float)) or time_s <= 0:
                return None
            km = distance_m / 1000.0
            if km <= 0:
                return None
            return time_s / km
        except Exception:
            return None

    def _format_distance(self, distance_m: float, prefer_miles: bool = False):
        """
        Format distance consistently as km or miles with decimal for partial units.
        
        Args:
            distance_m: Distance in meters
            prefer_miles: If True, use miles; otherwise use km
            
        Returns:
            Formatted string like "1.0 km" or "0.6 miles" or "0.4 km"
        """
        if not isinstance(distance_m, (int, float)) or distance_m <= 0:
            return None
        
        if prefer_miles:
            miles = distance_m / 1609.34
            return f"{miles:.1f} miles"
        else:
            km = distance_m / 1000.0
            return f"{km:.1f} km"
    
    def _calculate_std(self, values):
        """Calculate standard deviation of a list of numbers"""
        if not values or len(values) < 2:
            return 0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5
    
    def _detect_unit_preference(self, segments_summary: dict):
        """
        Detect if splits/laps suggest metric (km) or imperial (miles) preference.
        
        Returns True if miles are preferred, False for km.
        """
        segments = segments_summary.get("segments", [])
        if not segments:
            return False
        
        # Check first few segments - if they're ~1600m, likely miles; if ~1000m, likely km
        sample_distances = [s.get("distance_m") for s in segments[:5] if isinstance(s.get("distance_m"), (int, float))]
        if not sample_distances:
            return False
        
        avg_distance = sum(sample_distances) / len(sample_distances)
        # If average is closer to 1 mile (1609m) than 1km (1000m), prefer miles
        return abs(avg_distance - 1609.34) < abs(avg_distance - 1000.0)

    def _summarize_segments(self, segments, kind: str, max_items: int = 60):
        """
        Strip Strava splits/laps down to a compact, JSON-friendly summary suitable for LLM prompts.

        We intentionally drop nested `activity` / `athlete` objects and cap list length to avoid
        blowing prompt size on long activities.
        """
        if not isinstance(segments, list) or not segments:
            return {"kind": kind, "count": 0, "truncated": False, "segments": []}

        segs = []
        for s in segments[:max_items]:
            distance_m = s.get("distance")
            moving_time_s = s.get("moving_time")
            elapsed_time_s = s.get("elapsed_time")
            avg_speed_mps = s.get("average_speed")
            avg_hr = s.get("average_heartrate")
            pace_zone = s.get("pace_zone")

            # Prefer elapsed_time when present; fallback to moving_time.
            time_s = elapsed_time_s if isinstance(elapsed_time_s, (int, float)) and elapsed_time_s > 0 else moving_time_s
            pace_s_per_km = self._pace_seconds_per_km(distance_m, time_s)

            segs.append({
                "index": s.get("split") or s.get("lap_index") or s.get("lap") or s.get("index"),
                "name": s.get("name"),
                "distance_m": round(distance_m, 2) if isinstance(distance_m, (int, float)) else None,
                "distance_km": round(distance_m / 1000.0, 2) if isinstance(distance_m, (int, float)) and distance_m > 0 else None,
                "distance_miles": round(distance_m / 1609.34, 2) if isinstance(distance_m, (int, float)) and distance_m > 0 else None,
                "elapsed_time_s": int(elapsed_time_s) if isinstance(elapsed_time_s, (int, float)) else None,
                "moving_time_s": int(moving_time_s) if isinstance(moving_time_s, (int, float)) else None,
                "average_speed_mps": round(avg_speed_mps, 3) if isinstance(avg_speed_mps, (int, float)) else None,
                "pace_s_per_km": round(pace_s_per_km, 1) if isinstance(pace_s_per_km, (int, float)) else None,
                "average_heartrate": round(avg_hr, 1) if isinstance(avg_hr, (int, float)) else avg_hr,
                "pace_zone": pace_zone,
            })

        return {
            "kind": kind,
            "count": len(segments),
            "truncated": len(segments) > max_items,
            "segments": segs
        }

    def _detect_interval_pattern(self, lap_summary: dict):
        """
        Best-effort interval detection from lap-like segments.

        Returns a compact structure the AI + session matcher can use. This is heuristic by design
        (different watch/Strava configs produce different lap structures).
        """
        segments = (lap_summary or {}).get("segments") or []
        speeds = [s.get("average_speed_mps") for s in segments if isinstance(s.get("average_speed_mps"), (int, float))]
        if len(speeds) < 6:
            return {"has_intervals": False, "reason": "insufficient_lap_speed_data"}

        speeds_sorted = sorted(speeds)
        median = speeds_sorted[len(speeds_sorted) // 2]
        if not median or median <= 0:
            return {"has_intervals": False, "reason": "invalid_median_speed"}

        work_threshold = median * 1.12
        recovery_threshold = median * 0.90

        labels = []
        for s in segments:
            spd = s.get("average_speed_mps")
            pace_zone = s.get("pace_zone")

            # Prefer Strava's pace_zone when available (more robust than speed thresholds).
            if isinstance(pace_zone, int):
                if pace_zone >= 4:
                    labels.append("work")
                    continue
                if pace_zone <= 2:
                    labels.append("recovery")
                    continue

            if not isinstance(spd, (int, float)):
                continue
            if spd >= work_threshold:
                labels.append("work")
            elif spd <= recovery_threshold:
                labels.append("recovery")
            else:
                labels.append("steady")

        transitions = 0
        last = None
        for lab in labels:
            if lab not in ("work", "recovery"):
                continue
            if last and lab != last:
                transitions += 1
            last = lab

        work_count = labels.count("work")
        recovery_count = labels.count("recovery")
        has_intervals = work_count >= 3 and recovery_count >= 2 and transitions >= 3

        return {
            "has_intervals": has_intervals,
            "work_count": work_count,
            "recovery_count": recovery_count,
            "transitions": transitions,
            "median_speed_mps": round(median, 3),
            "work_threshold_mps": round(work_threshold, 3),
            "recovery_threshold_mps": round(recovery_threshold, 3),
        }
    
    def calculate_friel_hr_zones(self, lthr):
        """Calculate heart rate zones using Joe Friel's method"""
        return {
            "zones": [
                {"min": 0, "max": int(lthr * 0.85)},
                {"min": int(lthr * 0.85), "max": int(lthr * 0.89)},
                {"min": int(lthr * 0.90), "max": int(lthr * 0.94)},
                {"min": int(lthr * 0.95), "max": int(lthr * 1.0)},
                {"min": int(lthr * 1.0), "max": -1}
            ],
            "calculation_method": f"Joe Friel (LTHR: {lthr} bpm)"
        }
    
    def calculate_friel_power_zones(self, ftp):
        """Calculate power zones using Joe Friel's method"""
        return {
            "zones": [
                {"min": 0, "max": int(ftp * 0.55)},
                {"min": int(ftp * 0.55), "max": int(ftp * 0.74)},
                {"min": int(ftp * 0.75), "max": int(ftp * 0.89)},
                {"min": int(ftp * 0.90), "max": int(ftp * 1.04)},
                {"min": int(ftp * 1.05), "max": int(ftp * 1.20)},
                {"min": int(ftp * 1.20), "max": int(ftp * 1.50)},
                {"min": int(ftp * 1.50), "max": -1}
            ],
            "calculation_method": f"Joe Friel (Estimated FTP: {ftp} W)"
        }
    
    def analyze_activity(self, activity, streams, zones):
        """Analyze a single activity and calculate time in zones"""
        analyzed = {
            "id": activity['id'],
            "name": activity['name'],
            "type": activity['type'],
            "start_date": activity['start_date_local'],
            "is_race": activity.get('workout_type') == 1,
            # Canonical Strava-ish keys (used by session matching and AI prompts)
            "distance": activity.get('distance', 0),  # meters
            "moving_time": activity.get('moving_time', 0),  # seconds
            "distance_km": round(activity.get('distance', 0) / 1000, 2),
            "moving_time_minutes": round(activity.get('moving_time', 0) / 60, 2),
            "total_elevation_gain_meters": activity.get('total_elevation_gain', 0),
            "average_speed_kph": round(activity.get('average_speed', 0) * 3.6, 2),
            "average_heartrate": activity.get('average_heartrate'),
            "max_heartrate": activity.get('max_heartrate'),
            "time_in_hr_zones": {f"Zone {i+1}": 0 for i in range(5)},
            "time_in_power_zones": {f"Zone {i+1}": 0 for i in range(7)},
            "private_note": activity.get('private_note', '')
        }

        # Add lap/split structure (if present) to improve interval analysis + matching.
        splits_metric = activity.get("splits_metric") or []
        splits_standard = activity.get("splits_standard") or []
        
        # Try to get laps from activity detail first (may be incomplete)
        laps_from_detail = activity.get("laps") or []
        
        # IMPORTANT: Also fetch laps from dedicated endpoint for complete data
        # The activity detail endpoint may not return all laps, especially for interval sessions
        # The /activities/{id}/laps endpoint is more reliable
        laps_from_endpoint = []
        if hasattr(self, '_strava_service') and self._strava_service:
            # If strava_service was passed, use it to fetch laps
            pass  # Will be handled by caller
        else:
            # If not available here, laps_from_endpoint will be empty and we'll use laps_from_detail
            pass
        
        # Use laps from endpoint if available, otherwise fall back to activity detail
        laps = laps_from_endpoint if laps_from_endpoint else laps_from_detail
        
        # Debug logging for interval sessions
        if len(laps) > 0 or len(laps_from_detail) > 0 or len(splits_metric) > 0 or len(splits_standard) > 0:
            print(f"📊 Activity {activity.get('id')} segment data:")
            print(f"   Laps from detail: {len(laps_from_detail)}")
            print(f"   Laps from endpoint: {len(laps_from_endpoint)}")
            print(f"   Laps to use: {len(laps)}")
            print(f"   Splits metric: {len(splits_metric)}")
            print(f"   Splits standard: {len(splits_standard)}")

        analyzed["splits_metric_summary"] = self._summarize_segments(splits_metric, kind="splits_metric")
        analyzed["splits_standard_summary"] = self._summarize_segments(splits_standard, kind="splits_standard")
        analyzed["laps_summary"] = self._summarize_segments(laps, kind="laps")
        
        # Additional debug for laps_summary
        if analyzed["laps_summary"].get("count", 0) > 0:
            print(f"   ✅ Created laps_summary with {analyzed['laps_summary']['count']} segments")
        
        # Safer interval detection: compare laps vs splits
        # If laps differ from splits, it's likely an interval session (manual lap button presses)
        # If they're the same, it's likely a standard run (auto-lap creates both)
        has_laps = analyzed["laps_summary"].get("count", 0) > 0
        has_splits_metric = analyzed["splits_metric_summary"].get("count", 0) > 0
        has_splits_standard = analyzed["splits_standard_summary"].get("count", 0) > 0
        
        is_interval_session = False
        detection_method = "none"
        
        if has_laps:
            # Compare laps to splits to detect intervals
            laps_segments = analyzed["laps_summary"].get("segments", [])
            
            if has_splits_metric:
                splits_segments = analyzed["splits_metric_summary"].get("segments", [])
                # Check if lap distances/times differ from split distances/times
                if len(laps_segments) != len(splits_segments):
                    is_interval_session = True
                    detection_method = "laps_vs_splits_count_mismatch"
                else:
                    # Compare both distances AND times - time-based intervals will have consistent lap times
                    # but varying distances, while splits will have consistent distances but varying times
                    lap_times = [lap.get("elapsed_time_s") or lap.get("moving_time_s") for lap in laps_segments[:10] if lap.get("elapsed_time_s") or lap.get("moving_time_s")]
                    split_times = [split.get("elapsed_time_s") or split.get("moving_time_s") for split in splits_segments[:10] if split.get("elapsed_time_s") or split.get("moving_time_s")]
                    
                    # Check if lap times are more consistent than split times (suggests time-based intervals)
                    if len(lap_times) >= 3 and len(split_times) >= 3:
                        lap_time_std = self._calculate_std(lap_times)
                        split_time_std = self._calculate_std(split_times)
                        # If lap times are much more consistent (lower std dev), likely time-based intervals
                        if lap_time_std > 0 and split_time_std > 0 and lap_time_std < split_time_std * 0.7:
                            is_interval_session = True
                            detection_method = "laps_vs_splits_time_consistency"
                    
                    # Also check distances - if they differ significantly, likely intervals
                    if not is_interval_session:
                        for lap, split in zip(laps_segments[:10], splits_segments[:10]):
                            lap_dist = lap.get("distance_m")
                            split_dist = split.get("distance_m")
                            if lap_dist and split_dist:
                                # If distance differs by more than 10%, likely an interval
                                if abs(lap_dist - split_dist) / max(lap_dist, split_dist) > 0.10:
                                    is_interval_session = True
                                    detection_method = "laps_vs_splits_distance_mismatch"
                                    break
            elif has_splits_standard:
                splits_segments = analyzed["splits_standard_summary"].get("segments", [])
                if len(laps_segments) != len(splits_segments):
                    is_interval_session = True
                    detection_method = "laps_vs_splits_count_mismatch"
                else:
                    # Same logic for standard splits
                    lap_times = [lap.get("elapsed_time_s") or lap.get("moving_time_s") for lap in laps_segments[:10] if lap.get("elapsed_time_s") or lap.get("moving_time_s")]
                    split_times = [split.get("elapsed_time_s") or split.get("moving_time_s") for split in splits_segments[:10] if split.get("elapsed_time_s") or split.get("moving_time_s")]
                    
                    if len(lap_times) >= 3 and len(split_times) >= 3:
                        lap_time_std = self._calculate_std(lap_times)
                        split_time_std = self._calculate_std(split_times)
                        if lap_time_std > 0 and split_time_std > 0 and lap_time_std < split_time_std * 0.7:
                            is_interval_session = True
                            detection_method = "laps_vs_splits_time_consistency"
                    
                    if not is_interval_session:
                        for lap, split in zip(laps_segments[:10], splits_segments[:10]):
                            lap_dist = lap.get("distance_m")
                            split_dist = split.get("distance_m")
                            if lap_dist and split_dist:
                                if abs(lap_dist - split_dist) / max(lap_dist, split_dist) > 0.10:
                                    is_interval_session = True
                                    detection_method = "laps_vs_splits_distance_mismatch"
                                    break
            else:
                # No splits available, but we have laps - check if lap times suggest intervals
                # Time-based intervals (e.g., 3min on/off) will have consistent lap times
                lap_times = [lap.get("elapsed_time_s") or lap.get("moving_time_s") for lap in laps_segments if lap.get("elapsed_time_s") or lap.get("moving_time_s")]
                if len(lap_times) >= 6:
                    # Check if lap times are relatively consistent (within 20% of median)
                    # This suggests time-based intervals rather than distance-based
                    median_time = sorted(lap_times)[len(lap_times) // 2]
                    consistent_count = sum(1 for t in lap_times if abs(t - median_time) / median_time < 0.20)
                    if consistent_count >= len(lap_times) * 0.6:  # 60% of laps within 20% of median
                        is_interval_session = True
                        detection_method = "laps_time_consistency"
                    else:
                        # Fallback to pattern detection
                        intervals_detected = self._detect_interval_pattern(analyzed["laps_summary"])
                        is_interval_session = intervals_detected.get("has_intervals", False)
                        detection_method = "pattern_detection_fallback"
                else:
                    # Not enough data, use pattern detection
                    intervals_detected = self._detect_interval_pattern(analyzed["laps_summary"])
                    is_interval_session = intervals_detected.get("has_intervals", False)
                    detection_method = "pattern_detection_fallback"
        
        analyzed["intervals_detected"] = {
            "has_intervals": is_interval_session,
            "detection_method": detection_method
        }
        
        # Additional check: if activity name/description suggests intervals and we have laps, prefer laps
        activity_name = activity.get('name') or ''
        activity_description = activity.get('description') or ''
        activity_name_lower = (activity_name + ' ' + activity_description).lower()
        mentions_intervals = any(keyword in activity_name_lower for keyword in ['interval', 'repeat', 'x ', 'x3', 'x4', 'x5', 'x6', 'x8', '3 min', '4 min', '5 min', '6 min', '8 min'])
        
        # Add guidance for AI: which summary to prioritize and unit preference
        if is_interval_session and has_laps:
            # For interval sessions, prioritize laps (manual lap button presses or workout-defined intervals)
            analyzed["preferred_segment_summary"] = "laps_summary"
            analyzed["preferred_segment_reason"] = "Interval session detected - laps differ from splits (manual lap button presses)"
        elif mentions_intervals and has_laps:
            # Session description mentions intervals and we have laps - strongly prefer laps
            analyzed["preferred_segment_summary"] = "laps_summary"
            analyzed["preferred_segment_reason"] = "Session description mentions intervals - using laps (time-based intervals don't align with distance splits)"
        elif has_laps and (has_splits_metric or has_splits_standard):
            # If laps exist but match splits, it's a standard run - use splits (more consistent)
            analyzed["preferred_segment_summary"] = "splits_metric_summary" if has_splits_metric else "splits_standard_summary"
            analyzed["preferred_segment_reason"] = "Standard run - laps match splits (auto-laps), using splits for consistency"
        elif has_laps:
            # Only laps available, no splits - use laps
            analyzed["preferred_segment_summary"] = "laps_summary"
            analyzed["preferred_segment_reason"] = "Laps available (no splits for comparison)"
        elif has_splits_metric:
            analyzed["preferred_segment_summary"] = "splits_metric_summary"
            analyzed["preferred_segment_reason"] = "Metric splits available (1km auto-laps)"
        elif has_splits_standard:
            analyzed["preferred_segment_summary"] = "splits_standard_summary"
            analyzed["preferred_segment_reason"] = "Standard splits available (1 mile auto-laps)"
        else:
            analyzed["preferred_segment_summary"] = None
            analyzed["preferred_segment_reason"] = "No segment data available"
        
        # Detect unit preference (km vs miles) from available splits/laps
        if has_splits_metric:
            prefer_miles = self._detect_unit_preference(analyzed["splits_metric_summary"])
        elif has_splits_standard:
            prefer_miles = True  # Standard splits are always miles
        elif has_laps:
            prefer_miles = self._detect_unit_preference(analyzed["laps_summary"])
        else:
            prefer_miles = False  # Default to km
        
        analyzed["distance_unit_preference"] = "miles" if prefer_miles else "km"
        
        if analyzed["is_race"]:
            analyzed["race_tag"] = map_race_distance(activity['distance'])
        
        if not streams:
            return analyzed
        
        time_data = streams.get('time', {}).get('data', [])
        if not time_data:
            return analyzed
        
        # Analyze heart rate zones
        if 'heartrate' in streams:
            hr_data = streams['heartrate']['data']
            hr_zones = zones.get('heart_rate', {}).get('zones', [])
            zone_mins = [z['min'] for z in hr_zones]
            
            for i in range(1, len(hr_data)):
                duration = time_data[i] - time_data[i-1]
                hr = hr_data[i-1]
                zone_index = bisect.bisect_right(zone_mins, hr) - 1
                analyzed["time_in_hr_zones"][f"Zone {zone_index + 1}"] += duration
        
        # Analyze power zones
        if 'watts' in streams:
            power_data = streams['watts']['data']
            power_zones = zones.get('power', {}).get('zones', [])
            
            for i in range(1, len(power_data)):
                duration = time_data[i] - time_data[i-1]
                power = power_data[i-1]
                
                current_zone_index = 0
                for zone_index, zone_data in enumerate(power_zones):
                    if power >= zone_data['min']:
                        current_zone_index = zone_index
                    else:
                        break
                
                analyzed["time_in_power_zones"][f"Zone {current_zone_index + 1}"] += duration
        
        return analyzed
    
    def find_valid_race_for_vdot(self, activities, access_token, friel_hr_zones, strava_service):
        """Find a valid race in the last 4 weeks for VDOT calculation"""
        four_weeks_ago = datetime.now() - timedelta(weeks=4)
        
        for activity in activities:
            activity_date_str = activity['start_date_local'].split('T')[0]
            activity_date = datetime.strptime(activity_date_str, '%Y-%m-%d')
            
            if activity.get('workout_type') == 1 and activity_date > four_weeks_ago:
                streams = strava_service.get_activity_streams(access_token, activity['id'])
                if streams and 'heartrate' in streams:
                    race_analysis = self.analyze_activity(
                        activity,
                        streams,
                        {"heart_rate": friel_hr_zones}
                    )
                    
                    total_time = sum(race_analysis['time_in_hr_zones'].values())
                    high_intensity_time = (
                        race_analysis['time_in_hr_zones']["Zone 4"] +
                        race_analysis['time_in_hr_zones']["Zone 5"]
                    )
                    
                    if total_time > 0 and (high_intensity_time / total_time) > 0.5:
                        return {
                            "status": "VDOT Ready",
                            "race_basis": f"{activity['name']} ({activity_date_str})"
                        }
        
        return {
            "status": "HR Training Recommended",
            "reason": "No recent, high-intensity race found."
        }
    
    def estimate_zones_from_activities(self, activities):
        """
        Estimate LTHR and FTP from recent activity data.
        Returns dict with 'lthr' and 'ftp' keys (or None if can't estimate).
        """
        estimates = {'lthr': None, 'ftp': None}
        
        if not activities:
            return estimates
        
        # Look for recent races or hard efforts (last 8 weeks)
        eight_weeks_ago = datetime.now() - timedelta(weeks=8)
        
        max_hr_values = []
        max_power_values = []
        avg_power_values = []
        
        for activity in activities:
            try:
                activity_date = datetime.strptime(
                    activity['start_date_local'].split('T')[0],
                    '%Y-%m-%d'
                )
                
                if activity_date < eight_weeks_ago:
                    continue
                
                # Collect heart rate data
                if activity.get('max_heartrate'):
                    max_hr_values.append(activity['max_heartrate'])
                
                # Collect power data (for cycling activities)
                if activity.get('type') in ['Ride', 'VirtualRide']:
                    if activity.get('average_watts') and activity.get('average_watts') > 0:
                        # Only use activities longer than 20 minutes for FTP estimation
                        if activity.get('moving_time', 0) >= 1200:  # 20 minutes in seconds
                            avg_power_values.append(activity['average_watts'])
                    
                    if activity.get('max_watts'):
                        max_power_values.append(activity['max_watts'])
                
            except (ValueError, KeyError):
                continue
        
        # Estimate LTHR from max HR (LTHR is typically 88% of max HR for trained athletes)
        # Use 88% as a reasonable estimate
        if max_hr_values:
            max_hr = max(max_hr_values)
            estimates['lthr'] = int(max_hr * 0.88)
            print(f"--- Found {len(max_hr_values)} activities with HR data ---")
            print(f"--- Max HR found: {max_hr} bpm, Estimated LTHR: {estimates['lthr']} bpm ---")
            # Show top 5 max HR values for debugging
            top_hrs = sorted(max_hr_values, reverse=True)[:5]
            print(f"--- Top 5 max HR values: {top_hrs} ---")
        
        # Estimate FTP from average power in longer efforts
        # Use the 90th percentile of average power values as a conservative FTP estimate
        if avg_power_values:
            avg_power_values.sort()
            # Take the top 10% of average power values
            top_efforts = avg_power_values[int(len(avg_power_values) * 0.9):]
            if top_efforts:
                estimates['ftp'] = int(sum(top_efforts) / len(top_efforts))
        
        return estimates
    
    def get_current_week_plan(self, plan_text, plan_structure=None):
        """
        Finds and returns the markdown for the current or closest upcoming week's plan.
        """
        today = datetime.now().date()

        # METHOD 1: Use structured JSON data if available
        if plan_structure and 'weeks' in plan_structure:
            print("--- Finding current week using structured JSON. ---")
            found_week_title = None
            closest_upcoming_title = None
            min_future_delta = timedelta(days=999)

            for week in plan_structure.get('weeks', []):
                try:
                    start_date = datetime.strptime(week['start_date'], '%Y-%m-%d').date()
                    end_date = datetime.strptime(week['end_date'], '%Y-%m-%d').date()

                    if start_date <= today <= end_date:
                        found_week_title = week['title']
                        break
                    elif start_date > today:
                        delta = start_date - today
                        if delta < min_future_delta:
                            min_future_delta = delta
                            closest_upcoming_title = week['title']
                except (ValueError, KeyError):
                    continue
            
            week_title_to_find = found_week_title or closest_upcoming_title
            
            if week_title_to_find:
                clean_title = re.escape(week_title_to_find.replace('*','').strip())
                sections = re.split(r'(?=###\s)', plan_text, flags=re.IGNORECASE)
                for section in sections:
                    if re.search(clean_title, section, re.IGNORECASE):
                        return section

        # METHOD 2: Fallback to regex parsing
        print("--- No structured JSON found. Falling back to legacy regex parsing. ---")
        from utils.formatters import extract_week_dates_from_plan
        
        all_weeks = extract_week_dates_from_plan(plan_text)
        
        current_week = None
        closest_upcoming_week = None
        min_future_delta = timedelta(days=999)

        for week in all_weeks:
            if week['start_date'] <= today <= week['end_date']:
                current_week = week
                break
            elif week['start_date'] > today:
                delta = week['start_date'] - today
                if delta < min_future_delta:
                    min_future_delta = delta
                    closest_upcoming_week = week

        week_to_display = current_week or closest_upcoming_week
        
        if week_to_display:
            lines = plan_text.splitlines()
            start_index = week_to_display['index']
            end_index = len(lines)
            
            for i in range(start_index + 1, len(lines)):
                if lines[i].strip().startswith('###') or lines[i].strip().startswith('**Week'):
                    end_index = i
                    break
            
            return "\n".join(lines[start_index:end_index])

        return "Could not determine the current or upcoming training week from your plan."
    
    def is_plan_finished(self, plan_text, plan_structure=None):
        """
        Check if the training plan has finished (today is past the last week's end_date).
        Returns tuple: (is_finished: bool, last_week_end_date: date or None)
        """
        today = datetime.now().date()
        
        # METHOD 1: Use structured JSON data if available
        if plan_structure and 'weeks' in plan_structure:
            weeks = plan_structure.get('weeks', [])
            if not weeks:
                return (False, None)
            
            # Find the last week's end date
            last_end_date = None
            for week in weeks:
                try:
                    end_date = datetime.strptime(week['end_date'], '%Y-%m-%d').date()
                    if last_end_date is None or end_date > last_end_date:
                        last_end_date = end_date
                except (ValueError, KeyError):
                    continue
            
            if last_end_date:
                return (today > last_end_date, last_end_date)
        
        # METHOD 2: Fallback to regex parsing
        from utils.formatters import extract_week_dates_from_plan
        
        all_weeks = extract_week_dates_from_plan(plan_text)
        if not all_weeks:
            return (False, None)
        
        # Find the latest end date
        last_end_date = max(week['end_date'] for week in all_weeks)
        
        return (today > last_end_date, last_end_date)

# Create singleton instance
training_service = TrainingService()
