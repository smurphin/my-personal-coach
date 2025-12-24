import bisect
from datetime import datetime, timedelta
import re
from utils.formatters import format_seconds, map_race_distance

class TrainingService:
    """Service for training plan logic and activity analysis"""
    
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
