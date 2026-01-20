"""
FTP Detection and Validation Service

Determines when a cycling activity qualifies for FTP calculation.
FTP can be calculated from:
1. Marked FTP test sessions (20 min, 8 min, 5 min, ramp tests)
2. Hard training sessions or races with sustained power efforts
3. Ramp tests (incremental power increases until failure)
"""
from typing import Optional, Dict, Any, Tuple


class FTPDetectionService:
    """
    Service to detect valid FTP-worthy activities and calculate FTP.
    
    An activity qualifies for FTP calculation if:
    1. Marked as FTP test in name/description, OR
    2. Hard effort with sustained power in appropriate zones:
       - 20 min effort: 95% of average power
       - 8 min effort: 90% of average power
       - 5 min effort: 85% of average power
       - Ramp test: 75% of peak 1-minute power
       - Or sustained effort in Zone 4+ (90-105% FTP) for 20+ minutes
    """
    
    # FTP test duration ranges (in seconds) with tolerance
    FTP_TEST_DURATIONS = {
        '20MIN': (1140, 1260),   # 19-21 minutes (±1 min tolerance)
        '8MIN': (420, 540),      # 7-9 minutes
        '5MIN': (270, 330),      # 4.5-5.5 minutes
        'RAMP': (300, 1200),     # 5-20 minutes (ramp tests vary in length)
    }
    
    def __init__(self):
        pass
    
    def is_ftp_test_marked(self, activity: Dict[str, Any]) -> bool:
        """
        Check if activity is marked as an FTP test in name/description.
        
        Args:
            activity: Strava activity dict
            
        Returns:
            True if marked as FTP test
        """
        name = activity.get('name') or ''
        description = activity.get('description') or ''
        # Handle None values - convert to empty string
        name = str(name).lower() if name else ''
        description = str(description).lower() if description else ''
        combined_text = name + ' ' + description
        
        ftp_keywords = [
            'ftp test', 'ftp', 'functional threshold', '20 min test', 
            '20min test', '8 min test', '8min test', '5 min test', '5min test',
            'threshold test', 'power test', 'ramp test', 'ramp', 'incremental test'
        ]
        
        for keyword in ftp_keywords:
            if keyword in combined_text:
                return True
        
        return False
    
    def get_test_duration_category(self, duration_seconds: int) -> Optional[str]:
        """
        Determine which FTP test duration this activity matches.
        
        Args:
            duration_seconds: Duration in seconds
            
        Returns:
            Test category ('20MIN', '8MIN', '5MIN') or None
        """
        for category, (min_dur, max_dur) in self.FTP_TEST_DURATIONS.items():
            if min_dur <= duration_seconds <= max_dur:
                return category
        
        return None
    
    def calculate_ftp_from_power(self, avg_power: float, test_duration: str, 
                                 peak_1min_power: Optional[float] = None) -> Optional[float]:
        """
        Calculate FTP from average power based on test duration.
        
        Standard conversions:
        - 20 min: FTP = 95% of average power
        - 8 min: FTP = 90% of average power
        - 5 min: FTP = 85% of average power
        - Ramp: FTP = 75% of peak 1-minute power (or 75% of max power if peak not available)
        
        Args:
            avg_power: Average power in watts
            test_duration: Test category ('20MIN', '8MIN', '5MIN', 'RAMP')
            peak_1min_power: Peak 1-minute power for ramp tests (optional)
            
        Returns:
            Calculated FTP in watts or None
        """
        if not avg_power or avg_power <= 0:
            return None
        
        conversion_factors = {
            '20MIN': 0.95,  # 20 min test: FTP = 95% of avg power
            '8MIN': 0.90,   # 8 min test: FTP = 90% of avg power
            '5MIN': 0.85,   # 5 min test: FTP = 85% of avg power
            'RAMP': 0.75,   # Ramp test: FTP = 75% of peak 1-min power
        }
        
        factor = conversion_factors.get(test_duration)
        if not factor:
            return None
        
        # For ramp tests, use peak 1-minute power if available, otherwise use max power
        if test_duration == 'RAMP':
            if peak_1min_power and peak_1min_power > 0:
                ftp = peak_1min_power * factor
            else:
                # Fallback: use average power (less accurate for ramp tests)
                # But this shouldn't happen if we detect ramp properly
                ftp = avg_power * factor
        else:
            ftp = avg_power * factor
        
        return round(ftp)
    
    def detect_ramp_test_pattern(self, power_data: list, time_data: list) -> Tuple[bool, Optional[float]]:
        """
        Detect if power data shows a ramp test pattern (increasing power over time).
        
        Ramp tests typically show:
        - Steady increase in power over time
        - Peak power near the end
        - Shorter duration (5-20 minutes)
        
        Args:
            power_data: List of power values
            time_data: List of time values (seconds)
            
        Returns:
            Tuple of (is_ramp_test, peak_1min_power)
        """
        if not power_data or len(power_data) < 60:  # Need at least 1 minute of data
            return False, None
        
        # Filter out zeros/nulls
        valid_indices = [(i, p) for i, p in enumerate(power_data) if p and p > 0]
        if len(valid_indices) < 60:
            return False, None
        
        # Calculate rolling averages to smooth out noise
        # Check if power generally increases over time
        # Split into thirds and compare average power
        third_size = len(valid_indices) // 3
        if third_size < 20:  # Need enough data points
            return False, None
        
        first_third_power = [p for _, p in valid_indices[:third_size]]
        middle_third_power = [p for _, p in valid_indices[third_size:2*third_size]]
        last_third_power = [p for _, p in valid_indices[2*third_size:]]
        
        avg_first = sum(first_third_power) / len(first_third_power) if first_third_power else 0
        avg_middle = sum(middle_third_power) / len(middle_third_power) if middle_third_power else 0
        avg_last = sum(last_third_power) / len(last_third_power) if last_third_power else 0
        
        # Ramp test should show increasing power: first < middle < last
        # Allow some tolerance (at least 10% increase from first to last)
        if avg_first > 0 and avg_last > avg_first * 1.10 and avg_middle > avg_first * 1.05:
            # Calculate peak 1-minute power (best 60-second average)
            peak_1min = 0
            window_size = min(60, len(valid_indices))  # 60 seconds or available data
            
            for i in range(len(valid_indices) - window_size + 1):
                window_power = [p for _, p in valid_indices[i:i+window_size]]
                if window_power:
                    window_avg = sum(window_power) / len(window_power)
                    peak_1min = max(peak_1min, window_avg)
            
            if peak_1min > 0:
                return True, peak_1min
        
        return False, None
    
    def analyze_power_zones(self, time_in_power_zones: Dict[str, int], 
                            total_time: int) -> Tuple[bool, str, Optional[str]]:
        """
        Analyze if the power zone distribution suggests an FTP-worthy effort.
        
        For FTP detection:
        - 20+ min sustained in Zone 4 (90-105% FTP) suggests threshold effort
        - High percentage in Zone 4+Z5 suggests hard effort
        - Very high Zone 5 suggests shorter test (8min or 5min)
        
        Args:
            time_in_power_zones: Dict of zone -> seconds (Zone 1, Zone 2, etc.)
            total_time: Total activity time in seconds
            
        Returns:
            Tuple of (qualifies, reason, suggested_duration)
        """
        if total_time == 0:
            return False, "No moving time", None
        
        # Convert zone keys to numbers for easier handling
        zone_times = {}
        for key, seconds in time_in_power_zones.items():
            if 'Zone' in key:
                zone_num = int(key.replace('Zone ', ''))
                zone_times[zone_num] = seconds
            else:
                # Try to parse as number
                try:
                    zone_num = int(key)
                    zone_times[zone_num] = seconds
                except (ValueError, TypeError):
                    continue
        
        # Calculate zone percentages
        z4_time = zone_times.get(4, 0)
        z5_time = zone_times.get(5, 0)
        z6_time = zone_times.get(6, 0)
        z7_time = zone_times.get(7, 0)
        
        z4_pct = (z4_time / total_time) * 100 if total_time > 0 else 0
        z5_pct = (z5_time / total_time) * 100 if total_time > 0 else 0
        z4_z5_pct = z4_pct + z5_pct
        z5_z6_z7_pct = (z5_time + z6_time + z7_time) / total_time * 100 if total_time > 0 else 0
        
        # Check for 20 min FTP test pattern
        # Should have significant time in Zone 4 (threshold zone)
        if z4_time >= 1140:  # At least 19 minutes in Zone 4
            if z4_pct >= 70:  # 70%+ in Zone 4
                return True, f"20+ min sustained in Zone 4 ({z4_pct:.0f}%)", '20MIN'
        
        # Check for 8 min test pattern (very high intensity)
        if total_time >= 420 and total_time <= 540:  # 7-9 minutes
            if z5_z6_z7_pct >= 60:  # 60%+ in Zone 5-7
                return True, f"8 min high-intensity effort ({z5_z6_z7_pct:.0f}% Z5-7)", '8MIN'
        
        # Check for 5 min test pattern (extremely high intensity)
        if total_time >= 270 and total_time <= 330:  # 4.5-5.5 minutes
            if z5_z6_z7_pct >= 70:  # 70%+ in Zone 5-7
                return True, f"5 min all-out effort ({z5_z6_z7_pct:.0f}% Z5-7)", '5MIN'
        
        # Check for hard training session (20+ min with high Zone 4+5)
        if total_time >= 1200:  # 20+ minutes
            if z4_z5_pct >= 50:  # 50%+ in Zone 4+5
                # Could be a hard training session or race
                # Use 20 min conversion as default
                return True, f"Hard effort: {z4_z5_pct:.0f}% in Zone 4+5 for 20+ min", '20MIN'
        
        return False, f"Insufficient intensity (Z4: {z4_pct:.0f}%, Z5: {z5_pct:.0f}%)", None
    
    def should_calculate_ftp(self, activity: Dict[str, Any], 
                             streams: Dict[str, Any],
                             time_in_power_zones: Dict[str, int],
                             time_in_hr_zones: Optional[Dict[str, int]] = None) -> Tuple[bool, str, Optional[str], Optional[float]]:
        """
        Determine if an activity qualifies for FTP calculation.
        
        Args:
            activity: Strava activity dict
            streams: Activity streams (must contain 'watts' and 'time')
            time_in_power_zones: Dict of zone -> seconds
            time_in_hr_zones: Dict of HR zone -> seconds (optional, for validation)
            
        Returns:
            Tuple of (should_calculate, reason, test_duration, calculated_ftp)
        """
        # Check 1: Is it a cycling activity?
        activity_type = activity.get('type', '')
        if activity_type not in ['Ride', 'VirtualRide']:
            return False, f"Not a cycling activity ({activity_type})", None, None
        
        # Check 2: Does it have power data?
        if not streams or 'watts' not in streams:
            return False, "No power data available", None, None
        
        power_data = streams['watts']['data']
        if not power_data or len(power_data) == 0:
            return False, "Empty power data", None, None
        
        # Get time data for ramp test detection
        time_data = streams.get('time', {}).get('data', [])
        
        # Check 3: Is it marked as FTP test?
        is_ftp_test = self.is_ftp_test_marked(activity)
        
        # Check 4: Get duration and check if it matches test duration
        moving_time = activity.get('moving_time', 0)
        if moving_time == 0:
            return False, "No moving time", None, None
        
        # Check 5: Validate HR Zone 4+ time (require >30 mins for FTP detection)
        if time_in_hr_zones:
            # Convert zone keys to numbers
            hr_zone_times = {}
            for key, seconds in time_in_hr_zones.items():
                if 'Zone' in key:
                    zone_num = int(key.replace('Zone ', ''))
                    hr_zone_times[zone_num] = seconds
                else:
                    try:
                        zone_num = int(key.replace('Z', ''))
                        hr_zone_times[zone_num] = seconds
                    except (ValueError, TypeError):
                        continue
            
            # Calculate total time in HR Zone 4+
            z4_hr_time = hr_zone_times.get(4, 0) + hr_zone_times.get(5, 0)
            z4_hr_minutes = z4_hr_time / 60
            
            # For FTP detection, require >30 minutes in HR Zone 4+
            if z4_hr_minutes < 30:
                if not is_ftp_test:
                    return False, f"Insufficient HR Zone 4+ time: {z4_hr_minutes:.1f} min (need >30 min for FTP detection)", None, None
                else:
                    # If marked as FTP test but doesn't meet HR criteria, warn but allow
                    print(f"   ⚠️  Marked as FTP test but only {z4_hr_minutes:.1f} min in HR Z4+ (recommended: >30 min)")
            else:
                print(f"   ✅ HR Zone 4+ validation passed: {z4_hr_minutes:.1f} min")
        else:
            # No HR data - if not marked as FTP test, we can't validate
            if not is_ftp_test:
                print(f"   ⚠️  No HR data available for validation (FTP detection recommended with HR data)")
        
        # Check 6: Analyze power zones
        qualifies, reason, suggested_duration = self.analyze_power_zones(
            time_in_power_zones, 
            moving_time
        )
        
        # Check if it's a ramp test (by name or pattern)
        name_raw = activity.get('name') or ''
        name = str(name_raw).lower() if name_raw else ''
        is_ramp_test = 'ramp' in name or 'incremental' in name
        
        # Detect ramp test pattern from power data
        # BUT: Only accept if it's marked as FTP test OR if intensity is high enough
        peak_1min_power = None
        if power_data and time_data:
            is_ramp_pattern, detected_peak = self.detect_ramp_test_pattern(power_data, time_data)
            if is_ramp_pattern:
                # Additional validation: ramp tests should have high final intensity
                # Check if the last portion (final 30%) is in Zone 4+
                z4_time = time_in_power_zones.get('Zone 4', 0)
                z5_time = time_in_power_zones.get('Zone 5', 0)
                z6_time = time_in_power_zones.get('Zone 6', 0)
                z7_time = time_in_power_zones.get('Zone 7', 0)
                
                total_z4_plus_time = (z4_time or 0) + (z5_time or 0) + (z6_time or 0) + (z7_time or 0)
                z4_plus_pct = (total_z4_plus_time / moving_time * 100) if moving_time > 0 else 0
                
                # Only accept ramp pattern if:
                # 1. Marked as FTP test, OR
                # 2. Duration is 10+ minutes AND has 30%+ in Zone 4+ (final intensity check)
                if is_ftp_test or (moving_time >= 600 and z4_plus_pct >= 30):
                    is_ramp_test = True
                    peak_1min_power = detected_peak
                    print(f"   🔍 Detected ramp test pattern (peak 1-min power: {peak_1min_power:.0f}W)")
                else:
                    print(f"   ⏭️  Ramp pattern detected but doesn't qualify (duration: {moving_time//60}min, Z4+: {z4_plus_pct:.0f}%) - likely warmup")
        
        # If marked as FTP test, use name/description to determine duration
        test_duration = None
        if is_ftp_test or is_ramp_test:
            # Check for ramp test first
            if is_ramp_test:
                test_duration = 'RAMP'
            # Try to extract duration from name
            elif '20' in name or 'twenty' in name:
                test_duration = '20MIN'
            elif '8' in name or 'eight' in name:
                test_duration = '8MIN'
            elif '5' in name or 'five' in name:
                test_duration = '5MIN'
            else:
                # Default to duration-based detection
                test_duration = self.get_test_duration_category(moving_time)
        
        # If not marked but qualifies, use suggested duration
        if not test_duration and qualifies:
            test_duration = suggested_duration
        
        # If still no duration, try to match by time
        if not test_duration:
            test_duration = self.get_test_duration_category(moving_time)
        
        if not test_duration and not is_ftp_test:
            return False, reason or "Does not match FTP test duration or intensity criteria", None, None
        
        # Check 7: Calculate FTP from power data
        # For FTP tests, use average power of the entire effort
        # For hard efforts, we might want to use best 20 min, but for now use average
        avg_power = activity.get('average_watts')
        if not avg_power or avg_power <= 0:
            # Try to calculate from streams
            if power_data:
                # Filter out zeros/nulls
                valid_power = [p for p in power_data if p and p > 0]
                if valid_power:
                    avg_power = sum(valid_power) / len(valid_power)
                else:
                    return False, "No valid power data in streams", None, None
            else:
                return False, "No average power available", None, None
        
        # Use suggested duration if we have one, otherwise default to 20MIN
        duration_for_calc = test_duration or '20MIN'
        
        # For ramp tests, we need peak 1-minute power
        if duration_for_calc == 'RAMP' and not peak_1min_power:
            # Try to calculate peak 1-minute power from streams
            if power_data and time_data and len(power_data) >= 60:
                # Find best 60-second rolling average
                window_size = min(60, len(power_data))
                peak_1min_power = 0
                
                for i in range(len(power_data) - window_size + 1):
                    window_power = [p for p in power_data[i:i+window_size] if p and p > 0]
                    if window_power:
                        window_avg = sum(window_power) / len(window_power)
                        peak_1min_power = max(peak_1min_power, window_avg)
                
                if peak_1min_power > 0:
                    print(f"   📊 Calculated peak 1-min power: {peak_1min_power:.0f}W")
        
        calculated_ftp = self.calculate_ftp_from_power(avg_power, duration_for_calc, peak_1min_power)
        
        if not calculated_ftp:
            return False, "Failed to calculate FTP from power", None, None
        
        # Decision logic
        if is_ramp_test:
            return True, f"Ramp test detected - {reason}", duration_for_calc, calculated_ftp
        
        if is_ftp_test:
            return True, f"Marked as FTP test - {reason}", duration_for_calc, calculated_ftp
        
        if qualifies:
            return True, f"Hard effort - {reason}", duration_for_calc, calculated_ftp
        
        return False, reason or "Does not meet FTP test criteria", None, None
    
    def calculate_ftp_from_activity(self, activity: Dict[str, Any], 
                                    streams: Dict[str, Any],
                                    time_in_power_zones: Dict[str, int],
                                    time_in_hr_zones: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Any]]:
        """
        Calculate FTP from an activity if it qualifies.
        
        Args:
            activity: Strava activity dict
            streams: Activity streams
            time_in_power_zones: Dict of zone -> seconds
            time_in_hr_zones: Dict of HR zone -> seconds (optional, for validation)
            
        Returns:
            Dict with FTP info or None if doesn't qualify
            {
                'ftp': float,
                'test_duration': str (e.g., '20MIN'),
                'average_power': float,
                'activity_id': int,
                'activity_name': str,
                'is_ftp_test': bool,
                'intensity_reason': str
            }
        """
        should_calc, reason, test_duration, calculated_ftp = self.should_calculate_ftp(
            activity,
            streams,
            time_in_power_zones,
            time_in_hr_zones
        )
        
        if not should_calc:
            print(f"   ℹ️  Not using for FTP: {reason}")
            return None
        
        is_ftp_test = self.is_ftp_test_marked(activity)
        avg_power = activity.get('average_watts', 0)
        
        result = {
            'ftp': calculated_ftp,
            'test_duration': test_duration,
            'average_power': avg_power,
            'activity_id': activity.get('id'),
            'activity_name': activity.get('name') or 'Unknown',
            'is_ftp_test': is_ftp_test,
            'intensity_reason': reason
        }
        
        print(f"   ✅ FTP {calculated_ftp}W from {test_duration} test - {reason}")
        
        return result


# Create singleton instance
ftp_detection_service = FTPDetectionService()

