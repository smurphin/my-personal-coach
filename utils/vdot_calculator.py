"""
VDOT (VO2max) calculation and lookup utilities.

This module provides VDOT calculations based on Jack Daniels' Running Formula.
It uses a CSV lookup table as the source of truth to avoid AI calculation errors.
"""
import csv
from typing import Optional, Dict, Tuple
from pathlib import Path


class VDOTCalculator:
    """
    VDOT calculator using Jack Daniels' lookup tables.
    
    Load VDOT values from CSV to ensure accuracy and eliminate AI hallucinations.
    """
    
    def __init__(self, csv_path: Optional[str] = None):
        """
        Initialize VDOT calculator with optional CSV path.
        
        Args:
            csv_path: Path to VDOT lookup CSV. If None, uses default location.
        """
        self.vdot_table = {}
        self.csv_path = csv_path or 'data/vdot_table.csv'
        self._load_table()
    
    def _load_table(self):
        """Load VDOT table from CSV"""
        csv_file = Path(self.csv_path)
        
        if not csv_file.exists():
            print(f"⚠️  VDOT CSV not found at {self.csv_path}")
            print(f"   Using fallback calculation (less accurate)")
            return
        
        try:
            with open(csv_file, 'r') as f:
                # Check if first line looks like a description (doesn't start with "VDOT")
                first_line = f.readline().strip()
                
                if first_line.startswith('VDOT'):
                    # First line is headers, reset to start
                    f.seek(0)
                else:
                    # First line is description, skip it (already read)
                    # Next line should be headers
                    print(f"   Skipping description row")
                
                reader = csv.DictReader(f)
                row_count = 0
                
                for row in reader:
                    # Handle empty first column (common in exports)
                    if '' in row:
                        del row['']
                    
                    # Expected columns: VDOT, Race_5k, Race_10k, Race_Half_Marathon, Race_Marathon, etc.
                    if 'VDOT' not in row:
                        # First data row might still be bad, skip
                        continue
                    
                    try:
                        vdot = float(row['VDOT'])
                        self.vdot_table[vdot] = row
                        row_count += 1
                    except (ValueError, KeyError) as e:
                        # Skip invalid rows silently (might be footer text)
                        continue
            
            if row_count > 0:
                print(f"✅ Loaded VDOT table with {len(self.vdot_table)} entries from {self.csv_path}")
            else:
                print(f"❌ No valid VDOT entries found in {self.csv_path}")
                print(f"   Check CSV format - expected 'VDOT' column")
        
        except Exception as e:
            print(f"❌ Failed to load VDOT CSV: {e}")
            import traceback
            traceback.print_exc()
            print(f"   Using fallback calculation")
    
    def get_vdot_from_race(self, distance: str, time_seconds: int) -> Optional[float]:
        """
        Get VDOT from race performance using CSV lookup.
        
        Args:
            distance: Race distance ('5K', '10K', 'HM', 'MARATHON', 'MILE', '1500M', '3K', etc.)
            time_seconds: Race time in seconds
        
        Returns:
            VDOT value or None if not found
        """
        if not self.vdot_table:
            # Fallback to formula if CSV not loaded
            return self._calculate_vdot_fallback(distance, time_seconds)
        
        # Map input distance to CSV column names
        distance_map = {
            '1500M': 'Race_1.5km',
            '1.5K': 'Race_1.5km',
            '1.5KM': 'Race_1.5km',
            'MILE': 'Race_Mile',
            '1MILE': 'Race_Mile',
            '3K': 'Race_3km',
            '3000M': 'Race_3km',
            '3KM': 'Race_3km',
            '2MILE': 'Race_2_mile',
            '2MILES': 'Race_2_mile',
            '5K': 'Race_5k',
            '5000M': 'Race_5k',
            '5KM': 'Race_5k',
            '10K': 'Race_10k',
            '10000M': 'Race_10k',
            '10KM': 'Race_10k',
            '15K': 'Race_15km',
            '15000M': 'Race_15km',
            '15KM': 'Race_15km',
            'HM': 'Race_Half_Marathon',
            'HALF': 'Race_Half_Marathon',
            'HALF_MARATHON': 'Race_Half_Marathon',
            'HALFMARATHON': 'Race_Half_Marathon',
            '21K': 'Race_Half_Marathon',
            'MARATHON': 'Race_Marathon',
            '42K': 'Race_Marathon',
            '42.2K': 'Race_Marathon',
            'FULL': 'Race_Marathon'
        }
        
        column_name = distance_map.get(distance.upper().replace(' ', ''), None)
        
        if not column_name:
            print(f"⚠️  Unknown distance: {distance}")
            return self._calculate_vdot_fallback(distance, time_seconds)
        
        # Find the highest VDOT where athlete's time meets or beats the standard
        # VDOT is a threshold - you only achieve it by running that fast or faster
        qualified_vdots = []
        
        for vdot, row in self.vdot_table.items():
            if column_name not in row or not row[column_name]:
                continue
            
            # Parse time from CSV
            table_time = self._parse_time(row[column_name])
            if table_time is None:
                continue
            
            # Athlete qualifies for this VDOT if their time is equal to or faster
            # (lower time = faster, so actual_time <= table_time)
            if time_seconds <= table_time:
                qualified_vdots.append((vdot, table_time))
        
        if not qualified_vdots:
            # Athlete didn't meet any VDOT standard in the table
            print(f"⚠️  Time {time_seconds}s for {distance} slower than lowest VDOT in table")
            return self._calculate_vdot_fallback(distance, time_seconds)
        
        # Take the highest VDOT the athlete qualified for
        # This automatically rounds down - you get credit for what you achieved
        best_vdot = max(qualified_vdots, key=lambda x: x[0])[0]
        
        print(f"✅ VDOT lookup: {distance} in {time_seconds}s → VDOT {best_vdot}")
        return best_vdot
    
    def _parse_time(self, time_str: str) -> Optional[int]:
        """
        Parse time string to seconds.
        
        Handles formats from Jack Daniels' table:
        - MM:SS (e.g., "18:30" = 18 minutes 30 seconds)
        - HH:MM:SS (e.g., "1:23:45" = 1 hour 23 minutes 45 seconds)
        - M:SS (e.g., "4:03" = 4 minutes 3 seconds)
        
        Special case: Times like "30:40:00" where first number > 59 are MM:SS:hundredths
        (the last two digits are not seconds, they're hundredths/ignored)
        
        Returns:
            Time in seconds or None if invalid
        """
        try:
            if not time_str or not isinstance(time_str, str):
                return None
            
            time_str = time_str.strip()
            
            # Try parsing as seconds first
            if time_str.isdigit():
                return int(time_str)
            
            # Parse as time format
            parts = time_str.split(':')
            
            if len(parts) == 2:
                # MM:SS or M:SS
                minutes, seconds = map(int, parts)
                return minutes * 60 + seconds
            
            elif len(parts) == 3:
                # Could be HH:MM:SS or MM:SS:hundredths
                first, second, third = map(int, parts)
                
                # If first part is > 59, treat as MM:SS:hundredths
                # (Jack Daniels' tables use this for race times under 1 hour)
                # e.g., "30:40:00" = 30 minutes 40 seconds (hundredths ignored)
                if first > 59:
                    return first * 60 + second
                
                # Otherwise treat as proper HH:MM:SS
                # e.g., "01:03:46" = 1 hour 3 minutes 46 seconds
                return first * 3600 + second * 60 + third
            
            return None
        
        except (ValueError, AttributeError):
            return None
    
    def _calculate_vdot_fallback(self, distance: str, time_seconds: int) -> float:
        """
        Fallback VDOT calculation using Jack Daniels' formula.
        
        This is less accurate than CSV lookup but works when CSV is unavailable.
        Formula: VDOT = (-4.60 + 0.182258 × v + 0.000104 × v²) / (0.8 + 0.1894393 × e^(-0.012778 × t) + 0.2989558 × e^(-0.1932605 × t))
        where v = velocity in meters/min, t = time in minutes
        
        Simplified approximation for common distances:
        """
        import math
        
        # Convert distance to meters
        distance_meters = {
            '5K': 5000,
            '10K': 10000,
            'HM': 21097.5,
            'MARATHON': 42195
        }.get(distance.upper(), 5000)
        
        # Calculate velocity in meters/minute
        time_minutes = time_seconds / 60.0
        velocity = distance_meters / time_minutes
        
        # Jack Daniels' VDOT formula (simplified)
        # VO2 = -4.60 + 0.182258 * v + 0.000104 * v^2
        vo2 = -4.60 + (0.182258 * velocity) + (0.000104 * velocity * velocity)
        
        # Percent adjustment based on time
        percent = 0.8 + (0.1894393 * math.exp(-0.012778 * time_minutes)) + \
                  (0.2989558 * math.exp(-0.1932605 * time_minutes))
        
        vdot = vo2 / percent
        
        print(f"⚠️  VDOT fallback calculation: {distance} in {time_seconds}s → VDOT {vdot:.1f}")
        print(f"   Consider loading CSV for more accurate results")
        
        return round(vdot, 1)
    
    def get_equivalent_times(self, vdot: float) -> Dict[str, str]:
        """
        Get equivalent race times for a given VDOT.
        
        Args:
            vdot: VDOT value
        
        Returns:
            Dict of distance: time_string pairs
        """
        if not self.vdot_table:
            return {}
        
        # Find closest VDOT in table
        closest_vdot = min(self.vdot_table.keys(), 
                          key=lambda x: abs(x - vdot))
        
        row = self.vdot_table[closest_vdot]
        
        # Return all race distance times
        equivalent_times = {}
        for key, value in row.items():
            if key.startswith('Race_') and value:
                # Clean up column name: Race_5k -> 5k, Race_Half_Marathon -> Half Marathon
                distance_name = key.replace('Race_', '').replace('_', ' ')
                equivalent_times[distance_name] = value
        
        return equivalent_times
    
    def get_training_paces(self, vdot: float) -> Dict[str, str]:
        """
        Get training paces directly from CSV for a given VDOT.
        
        Returns paces in the format they appear in Jack Daniels' tables.
        
        Args:
            vdot: VDOT value
        
        Returns:
            Dict of pace type: pace string (from CSV)
            
        Example:
            >>> paces = get_training_paces(52.0)
            >>> print(paces)
            {
                'Easy/Long Pace per Mile': '07:59',
                'Easy/Long Pace per km': '04:55',
                'Marathon Pace per Mile': '06:56',
                'Marathon Pace per km': '04:18',
                'Threshold Pace 400m': '01:37',
                'Threshold Pace per Mile': '06:27',
                'Threshold Pace per km': '04:04',
                'Interval Pace 400m': '01:30',
                'Interval Pace per Mile': '05:56',
                'Repetition Pace 400m': '01:25'
            }
        """
        if not self.vdot_table:
            return {}
        
        # Find closest VDOT in table
        closest_vdot = min(self.vdot_table.keys(), 
                          key=lambda x: abs(x - vdot))
        
        row = self.vdot_table[closest_vdot]
        
        # Extract training pace columns
        training_paces = {}
        pace_columns = [
            'Easy_Long_Pace_per_Mile',
            'Easy_Long_Pace_per_km',
            'Marathon_Pace_per_Mile',
            'Marathon_Pace_per_km',
            'Threshold_Pace_400m',
            'Threshold_Pace_per_Mile',
            'Threshold_Pace_per_km',
            'Interval_Pace_400m',
            # Support both the legacy mislabelled header and the corrected one.
            # - Legacy: Interval_Pace_1.2km  (you may later rename this in the CSV)
            # - Correct: Interval_Pace_per_km
            'Interval_Pace_1.2km',
            'Interval_Pace_per_Mile',
            'Interval_Pace_per_km',
            'Repetition_Pace_200m',
            'Repetition_Pace_400m',
            'Repetition_Pace_per_Mile'
        ]
        
        for col in pace_columns:
            if col in row and row[col]:
                # Clean up column name for display
                # Easy_Long_Pace_per_Mile -> Easy/Long per Mile
                display_name = col.replace('_', ' ').replace('Pace ', '')
                training_paces[display_name] = row[col]
        
        return training_paces
    
    def suggest_training_paces(self, vdot: float) -> Dict[str, str]:
        """
        Calculate training paces based on VDOT.
        
        If CSV is loaded, uses values from CSV (more accurate).
        Otherwise falls back to formula calculation.
        
        Returns:
            Dict of pace type: pace string (e.g., "5:30/km")
        """
        if self.vdot_table:
            # Use CSV data if available
            csv_paces = self.get_training_paces(vdot)
            
            # Convert to simplified format (just the main pace zones)
            simplified = {}
            for key, value in csv_paces.items():
                if 'per km' in key.lower():
                    if 'Easy' in key:
                        simplified['E'] = value + '/km'
                    elif 'Marathon' in key:
                        simplified['M'] = value + '/km'
                    elif 'Threshold' in key:
                        simplified['T'] = value + '/km'
                    elif 'Interval' in key:
                        simplified['I'] = value + '/km'
            
            # Repetition from 400m pace (double it for per km)
            if 'Repetition 400m' in csv_paces:
                rep_400m = csv_paces['Repetition 400m']
                # Convert MM:SS to seconds, multiply by 2.5 to get per km
                parts = rep_400m.split(':')
                if len(parts) == 2:
                    total_seconds = int(parts[0]) * 60 + int(parts[1])
                    per_km_seconds = total_seconds * 2.5
                    minutes = int(per_km_seconds // 60)
                    seconds = int(per_km_seconds % 60)
                    simplified['R'] = f"{minutes}:{seconds:02d}/km"
            
            return simplified
        
        # Fallback to formula if CSV not available
        # Jack Daniels' pace calculations as percentages of VDOT
        paces = {
            'E': 0.59,   # Easy - 59% of VO2max
            'M': 0.84,   # Marathon - 84% of VO2max
            'T': 0.88,   # Threshold - 88% of VO2max
            'I': 0.95,   # Interval - 95% of VO2max
            'R': 1.0     # Repetition - 100% of VO2max
        }
        
        training_paces = {}
        
        for pace_type, intensity in paces.items():
            # Calculate velocity at this intensity
            vo2_at_intensity = vdot * intensity
            
            # Convert VO2 to velocity (simplified formula)
            # This is an approximation - CSV lookup would be more accurate
            velocity_m_per_min = (vo2_at_intensity + 4.60) / 0.182258
            
            # Convert to pace per kilometer
            seconds_per_km = 1000 / (velocity_m_per_min / 60)
            
            minutes = int(seconds_per_km // 60)
            seconds = int(seconds_per_km % 60)
            
            training_paces[pace_type] = f"{minutes}:{seconds:02d}/km"
        
        return training_paces


# Create singleton instance
vdot_calculator = VDOTCalculator()


def get_vdot_from_race(distance: str, time_seconds: int) -> Optional[float]:
    """
    Convenience function to get VDOT from race performance.
    
    Args:
        distance: Race distance ('5K', '10K', 'HM', 'MARATHON')
        time_seconds: Race time in seconds
    
    Returns:
        VDOT value
    
    Example:
        >>> vdot = get_vdot_from_race('HM', 5520)  # 1:32:00 half marathon
        >>> print(f"VDOT: {vdot}")
        VDOT: 52.5
    """
    return vdot_calculator.get_vdot_from_race(distance, time_seconds)


def validate_ai_vdot(distance: str, time_seconds: int, ai_vdot: float, 
                    tolerance: float = 2.0) -> Tuple[bool, float]:
    """
    Validate AI-calculated VDOT against CSV lookup.
    
    Args:
        distance: Race distance
        time_seconds: Race time
        ai_vdot: VDOT value from AI
        tolerance: Acceptable difference (default 2.0 VDOT points)
    
    Returns:
        Tuple of (is_valid, correct_vdot)
    
    Example:
        >>> is_valid, correct = validate_ai_vdot('HM', 5520, 48.0)
        >>> if not is_valid:
        ...     print(f"AI was wrong! Correct VDOT: {correct}")
    """
    correct_vdot = get_vdot_from_race(distance, time_seconds)
    
    if correct_vdot is None:
        return True, ai_vdot  # Can't validate, assume AI is correct
    
    difference = abs(correct_vdot - ai_vdot)
    is_valid = difference <= tolerance
    
    if not is_valid:
        print(f"⚠️  AI VDOT validation failed:")
        print(f"   AI calculated: {ai_vdot}")
        print(f"   Correct VDOT: {correct_vdot}")
        print(f"   Difference: {difference:.1f} points")
    
    return is_valid, correct_vdot