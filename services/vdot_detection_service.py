"""
VDOT Detection and Validation Service

Determines when an activity qualifies for VDOT calculation.
Only races and all-out time trials should update VDOT values.
"""
from typing import Optional, Dict, Any, Tuple
from utils.vdot_calculator import get_vdot_from_race


class VDOTDetectionService:
    """
    Service to detect valid VDOT-worthy activities and calculate VDOT.
    
    An activity qualifies for VDOT calculation if:
    1. Marked as a race in Strava, OR
    2. Meets all-out time trial criteria:
       - Continuous effort (no significant recovery periods)
       - Appropriate distance (1500m - marathon)
       - High sustained intensity based on HR zones
    """
    
    # Distance ranges that are valid for VDOT (in meters)
    VALID_DISTANCES = {
        '1500M': (1400, 1600),
        'MILE': (1580, 1620),
        '3K': (2900, 3100),
        '5K': (4900, 5100),
        '10K': (9900, 10100),
        '15K': (14900, 15100),
        'HM': (21000, 21300),
        'MARATHON': (42000, 42500)
    }
    
    def __init__(self):
        pass
    
    def is_race_marked(self, activity: Dict[str, Any]) -> bool:
        """
        Check if activity is marked as a race in Strava.
        
        Args:
            activity: Strava activity dict
        
        Returns:
            True if marked as race
        """
        # Strava 'workout_type' field:
        # 0 = default run, 1 = race, 2 = long run, 3 = workout
        workout_type = activity.get('workout_type')
        
        if workout_type == 1:
            return True
        
        # Also check if "race" is in the name
        name = activity.get('name', '').lower()
        race_keywords = ['race', 'parkrun', 'marathon', 'half marathon', '10k race', '5k race']
        
        for keyword in race_keywords:
            if keyword in name:
                return True
        
        return False
    
    def get_distance_category(self, distance_meters: float) -> Optional[str]:
        """
        Determine which standard race distance this activity matches.
        
        Args:
            distance_meters: Distance in meters
        
        Returns:
            Distance category (e.g., '5K', 'HM') or None if not a standard distance
        """
        for category, (min_dist, max_dist) in self.VALID_DISTANCES.items():
            if min_dist <= distance_meters <= max_dist:
                return category
        
        return None
    
    def analyze_effort_intensity(self, time_in_zones: Dict[str, int], 
                                 total_time: int,
                                 distance_category: str) -> Tuple[bool, str]:
        """
        Analyze if the effort intensity qualifies as all-out.
        
        Different distances require different zone distributions:
        - 1500m-3K: 60%+ in Z5
        - 5K-10K: 50%+ in Z5 or 80%+ in Z4+Z5
        - 15K-HM: 70%+ in Z4+Z5
        - Marathon: 80%+ in Z3+Z4
        
        Args:
            time_in_zones: Dict of zone -> seconds
            total_time: Total activity time in seconds
            distance_category: Distance category (5K, HM, etc.)
        
        Returns:
            Tuple of (qualifies, reason)
        """
        if total_time == 0:
            return False, "No moving time"
        
        # Calculate zone percentages
        z1_pct = (time_in_zones.get('Z1', 0) / total_time) * 100
        z2_pct = (time_in_zones.get('Z2', 0) / total_time) * 100
        z3_pct = (time_in_zones.get('Z3', 0) / total_time) * 100
        z4_pct = (time_in_zones.get('Z4', 0) / total_time) * 100
        z5_pct = (time_in_zones.get('Z5', 0) / total_time) * 100
        
        z4_z5_pct = z4_pct + z5_pct
        z3_z4_pct = z3_pct + z4_pct
        z1_z2_pct = z1_pct + z2_pct
        
        # Short races (1500m-3K): Should be mostly Z5
        if distance_category in ['1500M', 'MILE', '3K']:
            if z5_pct >= 60:
                return True, f"60%+ in Z5 ({z5_pct:.0f}%)"
            else:
                return False, f"Only {z5_pct:.0f}% in Z5, need 60%+ for {distance_category}"
        
        # Medium races (5K-10K): High Z5 or combined Z4+Z5
        elif distance_category in ['5K', '10K']:
            if z5_pct >= 50:
                return True, f"50%+ in Z5 ({z5_pct:.0f}%)"
            elif z4_z5_pct >= 80:
                return True, f"80%+ in Z4+Z5 ({z4_z5_pct:.0f}%)"
            else:
                return False, f"Only {z5_pct:.0f}% Z5 and {z4_z5_pct:.0f}% Z4+Z5, need 50% Z5 or 80% Z4+Z5"
        
        # Long races (15K-HM): Mostly Z4+Z5
        elif distance_category in ['15K', 'HM']:
            if z4_z5_pct >= 70:
                return True, f"70%+ in Z4+Z5 ({z4_z5_pct:.0f}%)"
            else:
                return False, f"Only {z4_z5_pct:.0f}% in Z4+Z5, need 70%+ for {distance_category}"
        
        # Marathon: Mostly Z3+Z4
        elif distance_category == 'MARATHON':
            if z3_z4_pct >= 80:
                return True, f"80%+ in Z3+Z4 ({z3_z4_pct:.0f}%)"
            else:
                return False, f"Only {z3_z4_pct:.0f}% in Z3+Z4, need 80%+ for marathon"
        
        return False, f"Unknown distance category: {distance_category}"
    
    def has_recovery_intervals(self, time_in_zones: Dict[str, int], 
                               total_time: int) -> bool:
        """
        Detect if activity has significant recovery periods (interval workout).
        
        If >20% of time is in Z1-Z2, likely has recovery intervals.
        
        Args:
            time_in_zones: Dict of zone -> seconds
            total_time: Total activity time in seconds
        
        Returns:
            True if likely an interval workout
        """
        if total_time == 0:
            return True
        
        z1_time = time_in_zones.get('Z1', 0)
        z2_time = time_in_zones.get('Z2', 0)
        easy_pct = ((z1_time + z2_time) / total_time) * 100
        
        # More than 20% easy = likely intervals with recovery
        return easy_pct > 20
    
    def should_calculate_vdot(self, activity: Dict[str, Any], 
                             time_in_zones: Dict[str, int]) -> Tuple[bool, str, Optional[str]]:
        """
        Determine if an activity qualifies for VDOT calculation.
        
        Args:
            activity: Strava activity dict
            time_in_zones: Dict of zone -> seconds
        
        Returns:
            Tuple of (should_calculate, reason, distance_category)
        """
        # Check 1: Is it marked as a race?
        is_race = self.is_race_marked(activity)
        
        # Check 2: Is it an appropriate distance?
        distance_meters = activity.get('distance', 0)
        distance_category = self.get_distance_category(distance_meters)
        
        if not distance_category:
            return False, f"Distance {distance_meters}m not a standard race distance", None
        
        # Check 3: Does it have HR data?
        total_zone_time = sum(time_in_zones.values())
        if total_zone_time == 0:
            return False, "No heart rate data available", None
        
        # Check 4: Is it a continuous effort (not intervals)?
        moving_time = activity.get('moving_time', 0)
        elapsed_time = activity.get('elapsed_time', 0)
        
        # If elapsed >> moving, lots of stops (not a race/TT)
        if elapsed_time > 0 and (moving_time / elapsed_time) < 0.9:
            return False, "Too many stops (not continuous effort)", None
        
        # Check for recovery intervals
        if self.has_recovery_intervals(time_in_zones, moving_time):
            return False, "Contains recovery intervals (not a continuous effort)", None
        
        # Check 5: Is the intensity appropriate?
        qualifies, intensity_reason = self.analyze_effort_intensity(
            time_in_zones, 
            moving_time, 
            distance_category
        )
        
        # Decision logic
        if is_race:
            # If marked as race, always calculate VDOT
            return True, f"Marked as race - {intensity_reason}", distance_category
        
        if qualifies:
            # If meets intensity criteria, treat as time trial
            return True, f"All-out time trial - {intensity_reason}", distance_category
        
        # Doesn't meet criteria
        return False, intensity_reason, None
    
    def calculate_vdot_from_activity(self, activity: Dict[str, Any], 
                                     time_in_zones: Dict[str, int]) -> Optional[Dict[str, Any]]:
        """
        Calculate VDOT from an activity if it qualifies.
        
        Args:
            activity: Strava activity dict
            time_in_zones: Dict of zone -> seconds
        
        Returns:
            Dict with VDOT info or None if doesn't qualify
            {
                'vdot': float,
                'distance': str (e.g., 'HM'),
                'distance_meters': float,
                'time_seconds': int,
                'activity_id': int,
                'activity_name': str,
                'is_race': bool,
                'intensity_reason': str
            }
        """
        should_calc, reason, distance_category = self.should_calculate_vdot(
            activity, 
            time_in_zones
        )
        
        if not should_calc:
            print(f"   ℹ️  Not using for VDOT: {reason}")
            return None
        
        # Calculate VDOT using CSV lookup
        distance_meters = activity.get('distance', 0)
        time_seconds = activity.get('moving_time', 0)
        
        vdot = get_vdot_from_race(distance_category, time_seconds)
        
        if not vdot:
            print(f"   ⚠️  Failed to calculate VDOT for {distance_category}")
            return None
        
        is_race = self.is_race_marked(activity)
        
        result = {
            'vdot': vdot,
            'distance': distance_category,
            'distance_meters': distance_meters,
            'time_seconds': time_seconds,
            'activity_id': activity.get('id'),
            'activity_name': activity.get('name', 'Unknown'),
            'is_race': is_race,
            'intensity_reason': reason
        }
        
        print(f"   ✅ VDOT {vdot} from {distance_category} - {reason}")
        
        return result


# Create singleton instance
vdot_detection_service = VDOTDetectionService()