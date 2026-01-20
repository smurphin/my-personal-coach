"""
VDOT Context Preparation for AI Prompts

This module extracts VDOT data from training_metrics and formats it
for inclusion in AI prompts.

With DEBUG logging to verify what gets passed to AI.
"""

from typing import Dict, Any, Optional


def prepare_vdot_context(user_data: Dict[str, Any], debug: bool = True) -> Dict[str, Any]:
    """
    Prepare VDOT data for inclusion in AI prompt context.
    
    Args:
        user_data: User data dictionary from data_manager
        debug: Enable debug logging (default: True)
    
    Returns:
        Dictionary with VDOT data ready for prompt template
    """
    if debug:
        print("\n" + "-"*70)
        print("prepare_vdot_context() - DEBUG")
        print("-"*70)
    
    vdot_context = {
        'current_vdot': None,
        # Primary per-km paces (legacy fields, kept for backwards compatibility)
        'easy_pace': None,
        'marathon_pace': None,
        'threshold_pace': None,
        'interval_pace': None,      # Historically stored as per-mile interval pace
        'repetition_pace': None,    # Historically stored as per-km repetition pace
        # Explicit unit-specific paces
        'easy_pace_km': None,
        'easy_pace_mile': None,
        'marathon_pace_km': None,
        'marathon_pace_mile': None,
        'threshold_pace_km': None,
        'threshold_pace_mile': None,
        'interval_pace_km': None,
        'interval_pace_mile': None,
        'repetition_pace_km': None,
        'repetition_pace_mile': None,
        'source_activity': None,
        'recent_rejections': []  # List of recently rejected VDOTs
    }
    
    # Check if training_metrics exists
    if 'training_metrics' not in user_data:
        if debug:
            print("❌ No training_metrics found in user_data")
            print("-"*70 + "\n")
        return vdot_context
    
    try:
        metrics_dict = user_data['training_metrics']
        
        if debug:
            print(f"✅ Found training_metrics")
            print(f"   Keys: {list(metrics_dict.keys())}")
        
        if 'vdot' not in metrics_dict or not metrics_dict['vdot']:
            if debug:
                print("❌ No VDOT data in training_metrics")
                print("-"*70 + "\n")
            return vdot_context
        
        vdot_data = metrics_dict['vdot']
        
        if debug:
            print(f"\n📊 VDOT data structure:")
            if isinstance(vdot_data, dict):
                print(f"   Type: dict")
                print(f"   Keys: {list(vdot_data.keys())}")
            else:
                print(f"   Type: {type(vdot_data)}")
        
        # Handle both dict format and simple value
        if isinstance(vdot_data, dict):
            if 'value' not in vdot_data or not vdot_data['value']:
                if debug:
                    print("❌ No 'value' field in VDOT data")
                    print("-"*70 + "\n")
                return vdot_context
            vdot_value = vdot_data['value']
        else:
            vdot_value = vdot_data
        
        if debug:
            print(f"\n✅ VDOT value found: {vdot_value}")
        
        vdot_context['current_vdot'] = vdot_value
        
        # Get training paces
        if isinstance(vdot_data, dict) and 'paces' in vdot_data:
            paces = vdot_data['paces']
            
            if debug:
                print(f"\n📏 Paces stored in training_metrics:")
                print(f"   Number of pace entries: {len(paces) if paces else 0}")
            
            if paces:
                # Map pace keys to context keys
                # VDOTCalculator returns keys like "Easy/Long Pace per km" / "Marathon Pace per Mile"
                # We also support the shorter keys you've historically stored (e.g. "Easy Long per km").

                # --- Easy / Long ---
                easy_km = (
                    paces.get('Easy/Long Pace per km') or 
                    paces.get('Easy Long per km') or 
                    paces.get('Easy per km')
                )
                easy_mile = (
                    paces.get('Easy/Long Pace per Mile') or
                    paces.get('Easy Long per Mile') or
                    paces.get('Easy per Mile')
                )

                # --- Marathon ---
                mara_km = (
                    paces.get('Marathon Pace per km') or 
                    paces.get('Marathon per km') or 
                    paces.get('Marathon')
                )
                mara_mile = (
                    paces.get('Marathon Pace per Mile') or 
                    paces.get('Marathon per Mile')
                )

                # --- Threshold ---
                thresh_km = (
                    paces.get('Threshold Pace per km') or 
                    paces.get('Threshold per km') or 
                    paces.get('Threshold')
                )
                thresh_mile = (
                    paces.get('Threshold Pace per Mile') or 
                    paces.get('Threshold per Mile')
                )

                # --- Interval (VO2max) ---
                int_km = (
                    paces.get('Interval Pace per km') or
                    paces.get('Interval per km')
                )
                int_mile = (
                    paces.get('Interval Pace per Mile') or 
                    paces.get('Interval per Mile') or 
                    paces.get('Interval')
                )

                # --- Repetition ---
                rep_km = (
                    paces.get('Repetition Pace per km') or 
                    paces.get('Repetition per km')
                )
                rep_mile = (
                    paces.get('Repetition Pace per Mile') or
                    paces.get('Repetition per Mile')
                )

                # Populate explicit unit-specific fields
                vdot_context['easy_pace_km'] = easy_km or 'N/A'
                vdot_context['easy_pace_mile'] = easy_mile or 'N/A'
                vdot_context['marathon_pace_km'] = mara_km or 'N/A'
                vdot_context['marathon_pace_mile'] = mara_mile or 'N/A'
                vdot_context['threshold_pace_km'] = thresh_km or 'N/A'
                vdot_context['threshold_pace_mile'] = thresh_mile or 'N/A'
                vdot_context['interval_pace_km'] = int_km or 'N/A'
                vdot_context['interval_pace_mile'] = int_mile or 'N/A'
                vdot_context['repetition_pace_km'] = rep_km or 'N/A'
                vdot_context['repetition_pace_mile'] = rep_mile or 'N/A'

                # Maintain legacy top-level fields for compatibility
                vdot_context['easy_pace'] = easy_km or easy_mile or 'N/A'
                vdot_context['marathon_pace'] = mara_km or mara_mile or 'N/A'
                vdot_context['threshold_pace'] = thresh_km or thresh_mile or 'N/A'
                vdot_context['interval_pace'] = int_mile or int_km or 'N/A'
                vdot_context['repetition_pace'] = rep_km or rep_mile or 'N/A'
                
                if debug:
                    print(f"   Easy: {vdot_context['easy_pace']} (km: {vdot_context['easy_pace_km']}, mile: {vdot_context['easy_pace_mile']})")
                    print(f"   Marathon: {vdot_context['marathon_pace']} (km: {vdot_context['marathon_pace_km']}, mile: {vdot_context['marathon_pace_mile']})")
                    print(f"   Threshold: {vdot_context['threshold_pace']} (km: {vdot_context['threshold_pace_km']}, mile: {vdot_context['threshold_pace_mile']})")
                    print(f"   Interval: {vdot_context['interval_pace']} (km: {vdot_context['interval_pace_km']}, mile: {vdot_context['interval_pace_mile']})")
                    print(f"   Repetition: {vdot_context['repetition_pace']} (km: {vdot_context['repetition_pace_km']}, mile: {vdot_context['repetition_pace_mile']})")
        else:
            # Paces not stored - calculate on the fly
            if debug:
                print(f"\n⚠️  Paces not stored in training_metrics")
                print(f"   Will calculate from VDOT {int(vdot_value)}")
            
            try:
                from utils.vdot_calculator import VDOTCalculator
                calc = VDOTCalculator()
                paces = calc.get_training_paces(int(vdot_value))
                
                if paces:
                    # Re-use the same mapping logic as above for consistency
                    easy_km = paces.get('Easy/Long Pace per km')
                    easy_mile = paces.get('Easy/Long Pace per Mile')
                    mara_km = paces.get('Marathon Pace per km')
                    mara_mile = paces.get('Marathon Pace per Mile')
                    thresh_km = paces.get('Threshold Pace per km')
                    thresh_mile = paces.get('Threshold Pace per Mile')
                    int_km = paces.get('Interval Pace per km')
                    int_mile = paces.get('Interval Pace per Mile')
                    rep_km = paces.get('Repetition Pace per km')
                    rep_mile = paces.get('Repetition Pace per Mile')

                    vdot_context['easy_pace_km'] = easy_km or 'N/A'
                    vdot_context['easy_pace_mile'] = easy_mile or 'N/A'
                    vdot_context['marathon_pace_km'] = mara_km or 'N/A'
                    vdot_context['marathon_pace_mile'] = mara_mile or 'N/A'
                    vdot_context['threshold_pace_km'] = thresh_km or 'N/A'
                    vdot_context['threshold_pace_mile'] = thresh_mile or 'N/A'
                    vdot_context['interval_pace_km'] = int_km or 'N/A'
                    vdot_context['interval_pace_mile'] = int_mile or 'N/A'
                    vdot_context['repetition_pace_km'] = rep_km or 'N/A'
                    vdot_context['repetition_pace_mile'] = rep_mile or 'N/A'

                    vdot_context['easy_pace'] = easy_km or easy_mile or 'N/A'
                    vdot_context['marathon_pace'] = mara_km or mara_mile or 'N/A'
                    vdot_context['threshold_pace'] = thresh_km or thresh_mile or 'N/A'
                    vdot_context['interval_pace'] = int_mile or int_km or 'N/A'
                    vdot_context['repetition_pace'] = rep_km or rep_mile or 'N/A'
                    
                    if debug:
                        print(f"✅ Calculated paces on-the-fly")
                        print(f"   Easy: {vdot_context['easy_pace']} (km: {vdot_context['easy_pace_km']}, mile: {vdot_context['easy_pace_mile']})")
                        print(f"   Marathon: {vdot_context['marathon_pace']} (km: {vdot_context['marathon_pace_km']}, mile: {vdot_context['marathon_pace_mile']})")
            except Exception as e:
                if debug:
                    print(f"❌ Error calculating paces: {e}")
        
        # Get source activity info if available (from dict format)
        if isinstance(vdot_data, dict) and 'detected_from' in vdot_data:
            detected = vdot_data['detected_from']
            
            if debug:
                print(f"\n📍 Source activity info:")
                print(f"   Activity: {detected.get('activity_name', 'Unknown')}")
                print(f"   Distance: {detected.get('distance', 'Unknown')}")
            
            source_activity = {}
            
            # Get activity name
            if 'activity_name' in detected:
                source_activity['name'] = detected['activity_name']
            
            # Get distance
            if 'distance' in detected:
                source_activity['distance'] = detected['distance']
            
            # Get time (formatted)
            if 'time_seconds' in detected:
                time_seconds = detected['time_seconds']
                hours = int(time_seconds // 3600)
                minutes = int((time_seconds % 3600) // 60)
                seconds = int(time_seconds % 60)
                
                if hours > 0:
                    source_activity['time'] = f"{hours}:{minutes:02d}:{seconds:02d}"
                else:
                    source_activity['time'] = f"{minutes}:{seconds:02d}"
            
            # Get date
            if 'date' in detected:
                # Parse ISO date to friendly format
                try:
                    from datetime import datetime
                    date_obj = datetime.fromisoformat(detected['date'].replace('Z', ''))
                    source_activity['date'] = date_obj.strftime('%B %d, %Y')
                except:
                    source_activity['date'] = detected['date']
            
            if source_activity:
                vdot_context['source_activity'] = source_activity
                
                if debug:
                    print(f"   Date: {source_activity.get('date', 'Unknown')}")
                    print(f"   Time: {source_activity.get('time', 'Unknown')}")
        
        # Get recent VDOT rejections (last 3 rejections)
        if 'vdot_rejections' in metrics_dict and metrics_dict['vdot_rejections']:
            rejections = metrics_dict['vdot_rejections']
            # Get last 3 rejections (most recent first)
            recent_rejections = rejections[-3:] if len(rejections) <= 3 else rejections[-3:]
            
            vdot_context['recent_rejections'] = []
            for rejection in recent_rejections:
                rejection_info = {
                    'rejected_vdot': rejection.get('rejected_vdot'),
                    'rejected_at': rejection.get('rejected_at'),
                    'activity_name': rejection.get('detected_from', {}).get('activity_name', 'Unknown'),
                    'distance': rejection.get('detected_from', {}).get('distance'),
                    'time_seconds': rejection.get('detected_from', {}).get('time_seconds'),
                    'user_reason': rejection.get('user_reason')
                }
                vdot_context['recent_rejections'].append(rejection_info)
            
            if debug:
                print(f"\n❌ Recent VDOT rejections: {len(vdot_context['recent_rejections'])}")
                for i, rej in enumerate(vdot_context['recent_rejections'], 1):
                    print(f"   {i}. VDOT {rej['rejected_vdot']} from {rej['activity_name']}")
                    if rej.get('user_reason'):
                        print(f"      Reason: {rej['user_reason']}")
    
    except Exception as e:
        if debug:
            print(f"\n❌ ERROR in prepare_vdot_context: {e}")
            import traceback
            traceback.print_exc()
        
        # Return empty context on error
        return {
            'current_vdot': None,
            'easy_pace': None,
            'marathon_pace': None,
            'threshold_pace': None,
            'interval_pace': None,
            'repetition_pace': None,
            'source_activity': None,
            'recent_rejections': []
        }
    
    if debug:
        print(f"\n✅ Returning VDOT context:")
        print(f"   current_vdot: {vdot_context['current_vdot']}")
        print(f"   Has paces: {vdot_context['easy_pace'] != None}")
        print(f"   Has source: {vdot_context['source_activity'] != None}")
        print(f"   Recent rejections: {len(vdot_context.get('recent_rejections', []))}")
        print("-"*70 + "\n")
    
    return vdot_context