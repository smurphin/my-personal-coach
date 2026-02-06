from flask import Blueprint, request, jsonify, session
from datetime import datetime, date, timedelta
import json
import time
import os
import jinja2
import re
import threading
from collections import defaultdict
from config import Config
from data_manager import data_manager
from services.strava_service import strava_service
from services.training_service import training_service
from services.ai_service import ai_service
from services.garmin_service import garmin_service
from utils.decorators import login_required
from utils.formatters import format_seconds, format_activity_date

# Import S3 manager
try:
    from s3_manager import s3_manager, S3_AVAILABLE
except ImportError:
    print("⚠️  s3_manager not available - S3 storage disabled")
    S3_AVAILABLE = False
    s3_manager = None

# IMPORTANT: Only use S3 in production
USE_S3 = S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production'

api_bp = Blueprint('api', __name__)


@api_bp.route('/version')
def version():
    """Return deployed version for ECR/App Runner traceability. No auth required."""
    return jsonify({
        'version': os.getenv('APP_VERSION', 'dev'),
        'environment': os.getenv('ENVIRONMENT', 'dev')
    })


# Webhook processing queue with delay
# Structure: {athlete_id: {'activity_ids': set(), 'activity_updates': {activity_id: count}, 'timer': Timer, 'last_update': timestamp}}
webhook_queue = {}
webhook_queue_lock = threading.Lock()

# Webhook delay comes from Config (env/Secrets: WEBHOOK_DELAY_SECONDS)
# Prod: 300, staging: 10-30 for quicker feedback

def process_queued_webhooks(athlete_id):
    """
    Process all queued webhook events for an athlete.
    This function is called after the delay period to batch process multiple activities.
    """
    with webhook_queue_lock:
        if athlete_id not in webhook_queue:
            return
        
        queue_entry = webhook_queue[athlete_id]
        activity_ids = list(queue_entry['activity_ids'])
        activity_updates = queue_entry.get('activity_updates', {})
        # Remove from queue before processing
        del webhook_queue[athlete_id]
    
    print(f"\n{'='*70}")
    print(f"PROCESSING QUEUED WEBHOOKS FOR ATHLETE {athlete_id}")
    print(f"{'='*70}")
    print(f"⏰ Processing queued activity updates after {Config.WEBHOOK_DELAY_SECONDS}s delay...")
    print(f"📋 Processing {len(activity_ids)} unique activities")
    
    # Log activities that were updated multiple times
    multiple_updates = {aid: count for aid, count in activity_updates.items() if count > 1}
    if multiple_updates:
        print(f"🔄 Activities updated multiple times (will process latest version):")
        for aid, count in multiple_updates.items():
            print(f"   - Activity {aid}: {count} updates")
    
    # Pass queued activity IDs so we always consider them (even if Strava list or feedback_log would skip them)
    _trigger_webhook_processing(athlete_id, queued_activity_ids=activity_ids)


def _trigger_webhook_processing(athlete_id, queued_activity_ids=None):
    """
    Trigger the normal webhook processing flow for an athlete.
    Finds new activities from Strava (last 7 days). If queued_activity_ids is provided (from webhook),
    any queued ID not already processed is also included so webhook-driven activities are never skipped.
    """
    user_data = data_manager.load_user_data(athlete_id)
    if not user_data or 'token' not in user_data:
        print(f"❌ Could not find user data for athlete {athlete_id}")
        return
    
    # Ensure token is valid
    access_token = strava_service.ensure_valid_token(athlete_id, user_data, data_manager)
    if not access_token:
        print(f"❌ Could not get valid token for athlete {athlete_id}")
        return
    
    training_plan = user_data.get('plan')
    if not training_plan:
        print(f"--- No training plan found for athlete {athlete_id}. Skipping. ---")
        return
    
    if 'feedback_log' not in user_data:
        user_data['feedback_log'] = []
    
    feedback_log = user_data['feedback_log']
    
    # Check for new activities
    processed_activity_ids = set()
    for entry in feedback_log:
        for act_id in entry.get('logged_activity_ids', [entry.get('activity_id')]):
            processed_activity_ids.add(str(act_id))
    
    seven_days_ago = datetime.now() - timedelta(days=7)
    last_fetch_timestamp = int(seven_days_ago.timestamp())
    
    recent_activities_summary = strava_service.get_recent_activities(
        access_token,
        last_fetch_timestamp,
        per_page=100
    )
    
    if not isinstance(recent_activities_summary, list):
        print(f"⚠️ Strava API call failed for athlete {athlete_id}, will still try queued activity IDs")
        recent_activities_summary = []
    
    new_activities_to_process = [
        act for act in recent_activities_summary
        if str(act['id']) not in processed_activity_ids
    ]
    existing_ids = {str(act['id']) for act in new_activities_to_process}

    # Include queued webhook activity IDs so we never skip activities the webhook told us about
    if queued_activity_ids:
        for qid in queued_activity_ids:
            qid_str = str(qid)
            if qid_str in processed_activity_ids or qid_str in existing_ids:
                continue
            detail = strava_service.get_activity_detail(access_token, qid)
            if detail and isinstance(detail, dict) and detail.get('id'):
                new_activities_to_process.append({'id': detail['id']})
                existing_ids.add(qid_str)
                print(f"📥 Including queued activity {qid} (not in recent Strava list)")
            else:
                print(f"⚠️ Queued activity {qid} could not be fetched from Strava, skipping")
    
    if not new_activities_to_process:
        print(f"--- No new activities to analyze for athlete {athlete_id}. ---")
        return
    
    new_activities_to_process.reverse()
    
    # Continue with existing processing logic (analyze, VDOT, feedback generation, etc.)
    # This is the same code that was in the webhook handler - we'll call it from there
    # For now, we'll duplicate the logic here, but ideally we'd extract it to a shared function
    _process_webhook_activities(athlete_id, user_data, access_token, new_activities_to_process)


def _process_webhook_activities(athlete_id, user_data, access_token, new_activities_to_process):
    """
    Process activities for webhook - extracted processing logic.
    This handles analysis, VDOT detection, feedback generation, and plan updates.
    """
    # Analyze new activities
    analyzed_sessions = []
    raw_activities = []
    
    # Load Friel zones from plan_data (same source used for plan generation)
    plan_data = user_data.get('plan_data', {}) or {}
    friel_hr_zones = plan_data.get('friel_hr_zones') or {}
    friel_power_zones = plan_data.get('friel_power_zones') or {}
    
    for activity_summary in new_activities_to_process:
        activity = strava_service.get_activity_detail(access_token, activity_summary['id'])
        if not activity:
            continue
        
        # Check if activity detail has laps, if not try dedicated endpoint
        # The activity detail endpoint usually includes laps, but the dedicated endpoint is more reliable
        activity_laps_from_detail = activity.get('laps') or []
        if len(activity_laps_from_detail) <= 1:
            # If activity detail has 0 or 1 lap, try dedicated endpoint (might have more)
            activity_laps = strava_service.get_activity_laps(access_token, activity['id'])
            if activity_laps and len(activity_laps) > len(activity_laps_from_detail):
                # Override laps in activity dict with data from dedicated endpoint
                activity['laps'] = activity_laps
                print(f"✅ Fetched {len(activity_laps)} laps from /activities/{activity['id']}/laps endpoint (detail had {len(activity_laps_from_detail)})")
            elif activity_laps_from_detail:
                print(f"ℹ️  Activity detail has {len(activity_laps_from_detail)} lap(s), dedicated endpoint returned {len(activity_laps) if activity_laps else 0}")
        else:
            print(f"✅ Activity detail has {len(activity_laps_from_detail)} laps - using those")
        
        streams = strava_service.get_activity_streams(access_token, activity['id'])
        
        # Build zones dict for analysis, including power zones when available
        zones_for_analysis = {}
        if friel_hr_zones:
            zones_for_analysis["heart_rate"] = friel_hr_zones
        if friel_power_zones:
            zones_for_analysis["power"] = friel_power_zones
        
        analyzed_session = training_service.analyze_activity(
            activity,
            streams,
            zones_for_analysis
        )
        
        raw_time_in_zones = analyzed_session["time_in_hr_zones"].copy()
        
        for key, seconds in analyzed_session["time_in_hr_zones"].items():
            analyzed_session["time_in_hr_zones"][key] = format_seconds(seconds)
        
        analyzed_sessions.append(analyzed_session)
        raw_activities.append({
            'activity': activity,
            'time_in_zones': raw_time_in_zones
        })
    
    if not analyzed_sessions:
        print("❌ Found new activities, but could not analyze their details.")
        return
    
    # Fetch Garmin data
    first_activity_date_iso = datetime.fromisoformat(
        analyzed_sessions[0]['start_date'].replace('Z', '')
    ).date().isoformat()
    
    garmin_data_for_activity = None
    if 'garmin_credentials' in user_data:
        creds = user_data['garmin_credentials']
        garmin_data_for_activity = garmin_service.authenticate_and_fetch(
            creds['email'],
            creds['password'],
            first_activity_date_iso,
            encrypted_tokenstore=creds.get('tokenstore'),
        )
    
    # VDOT DETECTION - Check ALL activities, but ONLY running activities (fix for issue #87)
    if raw_activities and analyzed_sessions:
        from services.vdot_detection_service import vdot_detection_service
        from utils.vdot_calculator import VDOTCalculator
        
        print("\n" + "="*70)
        print("VDOT DETECTION - DEBUG LOG (WEBHOOK - QUEUED)")
        print("="*70)
        
        print(f"📊 Processing {len(raw_activities)} activities for VDOT detection (running only)...")
        
        # Process ALL activities and find the best VDOT candidate
        # BUT: Only check running activities (fix for issue #87)
        vdot_result = None
        vdot_candidates = []
        
        for idx, raw_activity_data in enumerate(raw_activities):
            raw_activity = raw_activity_data['activity']
            activity_type = raw_activity.get('type', '')
            
            # Skip non-running activities (fix for issue #87)
            if activity_type not in ['Run', 'VirtualRun']:
                print(f"   ⏭️  Skipping activity {idx+1}/{len(raw_activities)}: {raw_activity.get('name', 'Unknown')} (Type: {activity_type} - not a running activity)")
                continue
            
            time_in_zones_raw = raw_activity_data['time_in_zones']
            
            time_in_zones = {}
            for zone_name, zone_time in time_in_zones_raw.items():
                if 'Zone' in zone_name:
                    zone_num = zone_name.replace('Zone ', '')
                    time_in_zones[f'Z{zone_num}'] = zone_time
                else:
                    time_in_zones[zone_name] = zone_time
            
            activity_id = raw_activity.get('id')
            activity_name = raw_activity.get('name', 'Unknown')
            is_race = raw_activity.get('workout_type') == 1
            
            print(f"   🔍 Checking activity {idx+1}/{len(raw_activities)}: {activity_name} (ID: {activity_id}, Race: {is_race})")
            
            result = vdot_detection_service.calculate_vdot_from_activity(
                raw_activity,
                time_in_zones
            )
            
            if result:
                # Prioritize: races first, then by intensity (Z4+Z5 %)
                distance_meters = raw_activity.get('distance', 0)
                moving_time = raw_activity.get('moving_time', 0)
                priority = 0
                
                if is_race:
                    priority = 1000  # Races get highest priority
                elif moving_time > 0:
                    z4_pct = (time_in_zones.get('Z4', 0) / moving_time) * 100
                    z5_pct = (time_in_zones.get('Z5', 0) / moving_time) * 100
                    priority = z4_pct + z5_pct  # Higher intensity = higher priority
                
                vdot_candidates.append((priority, result, raw_activity, time_in_zones))
                print(f"   ✅ Qualifies for VDOT: VDOT {result['vdot']}, Priority: {priority:.1f}")
            else:
                should_calc, reason, _ = vdot_detection_service.should_calculate_vdot(raw_activity, time_in_zones)
                print(f"   ❌ Does not qualify: {reason}")
        
        # Use the highest priority candidate (or first if multiple have same priority)
        if vdot_candidates:
            # Sort by priority (highest first), then by distance (for tie-breaking)
            vdot_candidates.sort(key=lambda x: (x[0], x[1]['distance_meters']), reverse=True)
            priority, vdot_result, _, _ = vdot_candidates[0]
            print(f"\n🎯 Selected highest priority VDOT candidate (priority: {priority:.1f})")
        
        if vdot_result:
            print(f"\n✅ VDOT DETECTION SUCCESSFUL!")
            print(f"   Distance: {vdot_result['distance']}")
            print(f"   Calculated VDOT: {vdot_result['vdot']}")
            
            current_vdot = None
            if 'training_metrics' in user_data and 'vdot' in user_data['training_metrics']:
                current_vdot_data = user_data['training_metrics']['vdot']
                if isinstance(current_vdot_data, dict):
                    current_vdot = current_vdot_data.get('value')
            
            new_vdot = int(vdot_result['vdot'])
            
            if current_vdot is not None and current_vdot == new_vdot:
                print(f"   ⏭️  Skipping update: VDOT value unchanged ({new_vdot})")
            else:
                print(f"\n🎯 UPDATING VDOT: {current_vdot} → {new_vdot}")
                
                calc = VDOTCalculator()
                paces = calc.get_training_paces(new_vdot)
                
                if 'training_metrics' not in user_data:
                    user_data['training_metrics'] = {'version': 1}
                
                previous_vdot = None
                if 'vdot' in user_data['training_metrics']:
                    previous_vdot = user_data['training_metrics']['vdot'].copy()
                
                user_data['training_metrics']['vdot'] = {
                    'value': new_vdot,
                    'source': 'RACE_DETECTION',
                    'date_set': datetime.now().isoformat(),
                    'user_confirmed': False,
                    'pending_confirmation': True,
                    'detected_from': {
                        'activity_id': vdot_result['activity_id'],
                        'activity_name': vdot_result['activity_name'],
                        'distance': vdot_result['distance'],
                        'distance_meters': vdot_result['distance_meters'],
                        'time_seconds': vdot_result['time_seconds'],
                        'is_race': vdot_result['is_race'],
                        'intensity_reason': vdot_result['intensity_reason']
                    },
                    'paces': paces,
                    'previous_value': previous_vdot
                }
                
                safe_save_user_data(athlete_id, user_data)
                user_data = data_manager.load_user_data(athlete_id)
    
    # FTP DETECTION - Check ALL activities, but ONLY cycling activities
    if raw_activities and analyzed_sessions:
        from services.ftp_detection_service import ftp_detection_service
        
        print("\n" + "="*70)
        print("FTP DETECTION - DEBUG LOG (WEBHOOK - QUEUED)")
        print("="*70)
        
        print(f"📊 Processing {len(raw_activities)} activities for FTP detection (cycling only)...")
        
        # Process ALL activities and find the best FTP candidate
        # BUT: Only check cycling activities
        ftp_result = None
        ftp_candidates = []
        
        for idx, raw_activity_data in enumerate(raw_activities):
            raw_activity = raw_activity_data['activity']
            activity_type = raw_activity.get('type', '')
            
            # Skip non-cycling activities
            if activity_type not in ['Ride', 'VirtualRide']:
                print(f"   ⏭️  Skipping activity {idx+1}/{len(raw_activities)}: {raw_activity.get('name', 'Unknown')} (Type: {activity_type} - not a cycling activity)")
                continue
            
            # Get power zones from analyzed session (match by activity ID)
            activity_id = raw_activity.get('id')
            analyzed_session = None
            for sess in analyzed_sessions:
                if sess.get('id') == activity_id:
                    analyzed_session = sess
                    break
            
            if not analyzed_session:
                print(f"   ⚠️  Could not find analyzed session for activity {activity_id}")
                continue
            
            time_in_power_zones = analyzed_session.get('time_in_power_zones', {})
            # Get HR zones for validation (convert formatted strings back to seconds)
            time_in_hr_zones_raw = analyzed_session.get('time_in_hr_zones', {})
            time_in_hr_zones = {}
            if time_in_hr_zones_raw:
                # Try to convert formatted strings back to seconds
                # Check if already in seconds (numeric) or formatted (MM:SS)
                for zone, value in time_in_hr_zones_raw.items():
                    if isinstance(value, (int, float)):
                        time_in_hr_zones[zone] = int(value)
                    elif isinstance(value, str) and ':' in value:
                        # Format: "MM:SS" - convert back to seconds
                        try:
                            parts = value.split(':')
                            if len(parts) == 2:
                                minutes, seconds = int(parts[0]), int(parts[1])
                                time_in_hr_zones[zone] = minutes * 60 + seconds
                        except (ValueError, IndexError):
                            continue
            
            # Get streams for power data (activity_id already set above)
            streams = strava_service.get_activity_streams(access_token, activity_id)
            
            activity_name = raw_activity.get('name', 'Unknown')
            is_ftp_test = 'ftp' in activity_name.lower() or 'test' in activity_name.lower()
            
            print(f"   🔍 Checking activity {idx+1}/{len(raw_activities)}: {activity_name} (ID: {activity_id}, FTP Test: {is_ftp_test})")
            
            result = ftp_detection_service.calculate_ftp_from_activity(
                raw_activity,
                streams,
                time_in_power_zones,
                time_in_hr_zones
            )
            
            if result:
                # Prioritize: marked FTP tests first, then by intensity
                priority = 0
                if result['is_ftp_test']:
                    priority = 1000  # Marked FTP tests get highest priority
                else:
                    # Use average power as priority (higher power = more likely valid FTP)
                    priority = result.get('average_power', 0)
                
                ftp_candidates.append((priority, result))
                print(f"   ✅ Qualifies for FTP: FTP {result['ftp']}W, Priority: {priority:.1f}")
            else:
                print(f"   ❌ Does not qualify for FTP calculation")
        
        # Use the highest priority candidate
        if ftp_candidates:
            ftp_candidates.sort(key=lambda x: x[0], reverse=True)
            priority, ftp_result = ftp_candidates[0]
            print(f"\n🎯 Selected highest priority FTP candidate (priority: {priority:.1f})")
        
        if ftp_result:
            print(f"\n✅ FTP DETECTION SUCCESSFUL!")
            print(f"   Test Duration: {ftp_result['test_duration']}")
            print(f"   Calculated FTP: {ftp_result['ftp']}W")
            
            current_ftp = None
            if 'training_metrics' in user_data and 'ftp' in user_data['training_metrics']:
                current_ftp_data = user_data['training_metrics']['ftp']
                if isinstance(current_ftp_data, dict):
                    current_ftp = current_ftp_data.get('value')
            
            new_ftp = int(ftp_result['ftp'])
            
            if current_ftp is not None and current_ftp == new_ftp:
                print(f"   ⏭️  Skipping update: FTP value unchanged ({new_ftp})")
            else:
                print(f"\n🎯 UPDATING FTP: {current_ftp} → {new_ftp}")
                
                if 'training_metrics' not in user_data:
                    user_data['training_metrics'] = {'version': 1}
                
                previous_ftp = None
                if 'ftp' in user_data['training_metrics']:
                    previous_ftp = user_data['training_metrics']['ftp'].copy()
                
                user_data['training_metrics']['ftp'] = {
                    'value': new_ftp,
                    'source': 'FTP_TEST_DETECTION',
                    'date_set': datetime.now().isoformat(),
                    'user_confirmed': False,
                    'pending_confirmation': True,
                    'detected_from': {
                        'activity_id': ftp_result['activity_id'],
                        'activity_name': ftp_result['activity_name'],
                        'test_duration': ftp_result['test_duration'],
                        'average_power': ftp_result['average_power'],
                        'is_ftp_test': ftp_result['is_ftp_test'],
                        'intensity_reason': ftp_result['intensity_reason']
                    },
                    'previous_value': previous_ftp
                }
                
                safe_save_user_data(athlete_id, user_data)
                user_data = data_manager.load_user_data(athlete_id)
    
    # Prepare VDOT context for AI
    from utils.vdot_context import prepare_vdot_context
    
    vdot_data = prepare_vdot_context(user_data)
    
    # Use plan_v2 as source of truth when available (same as feedback page)
    if 'plan_v2' in user_data and user_data['plan_v2']:
        from models.training_plan import TrainingPlan
        try:
            training_plan = TrainingPlan.from_dict(user_data['plan_v2'])
            print("✅ Using structured plan_v2 for feedback generation (webhook)")
        except Exception as e:
            print(f"⚠️  Failed to load plan_v2, falling back to markdown: {e}")
            training_plan = user_data.get('plan')
    else:
        training_plan = user_data.get('plan')
        print("ℹ️  Using markdown plan for feedback generation (plan_v2 not found)")
    
    # Athlete profile so AI respects type (Minimalist/Improviser/Disciplinarian) and day flexibility
    athlete_profile = user_data.get('athlete_profile', {})
    if not athlete_profile:
        plan_data = user_data.get('plan_data', {}) or {}
        athlete_profile = {
            'lifestyle_context': plan_data.get('lifestyle_context'),
            'athlete_type': plan_data.get('athlete_type'),
        }
    
    feedback_log = user_data.get('feedback_log', [])
    
    # Log all activities being passed to feedback generation
    print(f"\n📊 Preparing feedback generation for {len(analyzed_sessions)} activities:")
    for idx, sess in enumerate(analyzed_sessions, 1):
        print(f"   {idx}. {sess.get('name', 'Unknown')} (ID: {sess.get('id')}, Type: {sess.get('type', 'Unknown')}, Date: {sess.get('start_date', 'Unknown')[:10]})")
    
    # region agent log
    try:
        import json as _json
        import hashlib as _hashlib
        _log_entry = {
            "sessionId": "debug-session",
            "runId": "pre-fix",
            "hypothesisId": "H1",
            "location": "routes/api_routes.py:486",
            "message": "Before generate_feedback in _process_webhook_activities",
            "data": {
                "athlete_id": athlete_id,
                "has_training_plan": bool(training_plan),
                "feedback_log_len": len(feedback_log),
                "analyzed_sessions_len": len(analyzed_sessions),
                "has_garmin_data": bool(garmin_data_for_activity),
            },
            "timestamp": int(time.time() * 1000),
        }
        with open("/home/darren/git/.cursor/debug.log", "a") as _f:
            _f.write(_json.dumps(_log_entry) + "\n")
    except Exception:
        pass
    # endregion
    
    # Generate feedback (now returns tuple: feedback_text, plan_update_json, change_summary)
    try:
        feedback_text, plan_update_json, change_summary = ai_service.generate_feedback(
            training_plan,
            feedback_log,
            analyzed_sessions,
            user_data.get('training_history'),
            garmin_data_for_activity,
            incomplete_sessions=None,
            vdot_data=vdot_data,
            athlete_profile=athlete_profile,
        )
    except Exception as e:
        # region agent log
        try:
            import json as _json
            import time as _time
            _log_entry = {
                "sessionId": "debug-session",
                "runId": "pre-fix",
                "hypothesisId": "H2",
                "location": "routes/api_routes.py:492",
                "message": "Exception in generate_feedback in _process_webhook_activities",
                "data": {
                    "athlete_id": athlete_id,
                    "error": str(e),
                },
                "timestamp": int(_time.time() * 1000),
            }
            with open("/home/darren/git/.cursor/debug.log", "a") as _f:
                _f.write(_json.dumps(_log_entry) + "\n")
        except Exception:
            pass
        # endregion
        raise
    
    print(f"\n✅ AI feedback generated ({len(feedback_text)} characters)")
    
    # Create feedback log entry
    activity_names = [session['name'] for session in analyzed_sessions]
    if len(activity_names) == 1:
        descriptive_name = f"Feedback for: {activity_names[0]}"
    else:
        descriptive_name = ai_service.summarize_activities(activity_names)
        if not descriptive_name:
            descriptive_name = f"Feedback for activities: {', '.join(activity_names)}"
    
    all_activity_ids = [s['id'] for s in analyzed_sessions]
    
    new_log_entry = {
        "activity_id": int(analyzed_sessions[0]['id']),
        "activity_name": descriptive_name,
        "activity_date": format_activity_date(analyzed_sessions[0].get('start_date', '')),
        "feedback_markdown": feedback_text,  # Use feedback_text from tuple
        "logged_activity_ids": all_activity_ids
    }
    
    # region agent log
    try:
        import json as _json
        import hashlib as _hashlib
        import time as _time
        _log_entry = {
            "sessionId": "debug-session",
            "runId": "pre-fix",
            "hypothesisId": "H2",
            "location": "routes/api_routes.py:feedback_log_entry",
            "message": "New feedback_log entry before insert (webhook path)",
            "data": {
                "activity_id": int(analyzed_sessions[0]['id']),
                "feedback_text_length": len(feedback_text),
                "feedback_text_sha256": _hashlib.sha256(feedback_text.encode("utf-8")).hexdigest(),
                "entry_feedback_markdown_length": len(str(new_log_entry["feedback_markdown"])),
                "entry_feedback_markdown_sha256": _hashlib.sha256(str(new_log_entry["feedback_markdown"]).encode("utf-8")).hexdigest(),
            },
            "timestamp": int(_time.time() * 1000),
        }
        with open("/home/darren/git/.cursor/debug.log", "a") as _f:
            _f.write(_json.dumps(_log_entry) + "\n")
    except Exception:
        pass
    # endregion
    
    feedback_log.insert(0, new_log_entry)
    user_data['feedback_log'] = feedback_log
    print(f"📝 Added feedback entry for {len(analyzed_sessions)} activities to feedback_log")
    print(f"   📋 feedback_log now has {len(feedback_log)} entries")
    print(f"   🔍 New entry activity_id: {new_log_entry.get('activity_id')}, name: {new_log_entry.get('activity_name', '')[:50]}")
    
    # CRITICAL: Verify the entry is actually in user_data before proceeding
    if 'feedback_log' not in user_data or not user_data['feedback_log']:
        print(f"❌ CRITICAL: feedback_log missing or empty after adding entry!")
    elif user_data['feedback_log'][0].get('activity_id') != new_log_entry.get('activity_id'):
        print(f"❌ CRITICAL: feedback_log[0] activity_id mismatch! Expected {new_log_entry.get('activity_id')}, got {user_data['feedback_log'][0].get('activity_id')}")
    else:
        print(f"   ✅ Verified: feedback_log[0] has correct activity_id {user_data['feedback_log'][0].get('activity_id')}")
    
    # === SESSION MATCHING (AI-assisted, same as feedback flow) ===
    # Uses same helper and AI call as feedback so both routes match the same way
    if 'plan_v2' in user_data and user_data['plan_v2']:
        try:
            from models.training_plan import TrainingPlan
            from utils.session_matcher import get_candidate_sessions_text
            
            plan_v2 = TrainingPlan.from_dict(user_data['plan_v2'])
            
            print(f"\n{'='*70}")
            print(f"SESSION MATCHING - WEBHOOK (AI-assisted, same as feedback)")
            print(f"{'='*70}")
            print(f"Activities to match: {len(analyzed_sessions)}")
            
            matches = []
            for activity_data in analyzed_sessions:
                activity_date = datetime.fromisoformat(activity_data['start_date'].replace('Z', '')).date()
                activity_date_str = activity_date.isoformat()
                
                incomplete_sessions_text = get_candidate_sessions_text(
                    plan_v2, activity_date_str, activity_data.get('type')
                )
                if not incomplete_sessions_text:
                    print(f"   ℹ️  No candidate sessions for activity {activity_date_str}, skipping")
                    continue
                
                session_id = ai_service.match_activity_to_session(activity_data, incomplete_sessions_text)
                if not session_id:
                    continue
                
                session = plan_v2.get_session_by_id(session_id)
                if session:
                    activity_id = int(activity_data.get('id')) if activity_data.get('id') is not None else None
                    session.mark_complete(activity_id, activity_data.get('start_date'))
                    matches.append((session, activity_data))
                    print(f"   ✓ AI matched {session.id} ({session.type}) to activity {activity_id} on {activity_date_str}")
                else:
                    print(f"   ⚠️  AI returned session_id {session_id} but not found in plan")
            
            if matches:
                print(f"\n✅ Found {len(matches)} session matches (AI-assisted)")
                user_data['plan_v2'] = plan_v2.to_dict()
                print(f"\n💾 Saving plan_v2 with {len(matches)} newly completed sessions")
            else:
                print(f"\nℹ️  No sessions matched for {len(analyzed_sessions)} activities")
                print(f"   (This is normal if activities don't match any incomplete sessions)")
            
            print(f"{'='*70}\n")
            
        except Exception as e:
            print(f"⚠️  Error during session matching: {e}")
            import traceback
            traceback.print_exc()
    
    # NEW: Handle JSON-first plan updates (preferred method)
    if plan_update_json:
        print(f"✅ Found JSON plan update in feedback response!")
        print(f"   Plan has {len(plan_update_json.get('weeks', []))} weeks")
        
        # Get current plan_v2 as backup for archiving
        current_plan_v2_dict = user_data.get('plan_v2')
        
        # SAFEGUARD: Archive and restore past weeks
        from utils.plan_utils import archive_and_restore_past_weeks
        from models.training_plan import TrainingPlan
        
        try:
            new_plan_v2_obj = TrainingPlan.from_dict(plan_update_json)
            if current_plan_v2_dict:
                new_plan_v2_obj = archive_and_restore_past_weeks(current_plan_v2_dict, new_plan_v2_obj)
                
                # CRITICAL: Preserve completed sessions from current plan
                # Only preserve from past and current weeks (not future weeks)
                from datetime import date
                today = date.today()
                current_plan_v2_obj = TrainingPlan.from_dict(current_plan_v2_dict)
                existing_completed = {}
                
                for week in current_plan_v2_obj.weeks:
                    # Only preserve from weeks that have ended (past) or are current (includes today)
                    week_is_past_or_current = False
                    if week.end_date:
                        try:
                            week_end = datetime.strptime(week.end_date, '%Y-%m-%d').date()
                            week_is_past_or_current = week_end <= today  # Past or current week
                        except (ValueError, TypeError):
                            # If we can't parse the date, skip this week
                            continue
                    
                    # Only preserve completed sessions from past/current weeks
                    if week_is_past_or_current:
                        for sess in week.sessions:
                            if sess.completed:
                                existing_completed[sess.id] = {
                                    'completed': True,
                                    'strava_activity_id': sess.strava_activity_id,
                                    'completed_at': sess.completed_at
                                }
                
                # Restore completed sessions in new plan (match by session ID)
                restored_count = 0
                for week in new_plan_v2_obj.weeks:
                    for sess in week.sessions:
                        if sess.id in existing_completed:
                            sess.completed = True
                            sess.strava_activity_id = existing_completed[sess.id]['strava_activity_id']
                            sess.completed_at = existing_completed[sess.id]['completed_at']
                            restored_count += 1
                
                if restored_count > 0:
                    print(f"   ✅ Preserved {restored_count} completed sessions from past/current weeks")
            
            # CRITICAL: Archive old plan BEFORE overwriting
            if 'plan' in user_data and user_data.get('plan'):
                if 'archive' not in user_data:
                    user_data['archive'] = []
                
                user_data['archive'].insert(0, {
                    'plan': user_data['plan'],
                    'plan_v2': user_data.get('plan_v2'),
                    'completed_date': datetime.now().isoformat(),
                    'reason': 'regenerated_via_feedback_json'
                })
                print(f"📦 Archived old plan before JSON regeneration (archive now has {len(user_data['archive'])} entries)")
            
            # Update plan_v2
            user_data['plan_v2'] = new_plan_v2_obj.to_dict()
            
            # Also update markdown plan for backward compatibility
            user_data['plan'] = new_plan_v2_obj.to_markdown()
            
            # Store change summary for display
            if change_summary:
                user_data['last_plan_change_summary'] = change_summary
                print(f"   📋 Change summary: {change_summary[:100]}...")
            
            print(f"--- Plan updated via JSON! ---")
            print(f"--- New plan has {len(new_plan_v2_obj.weeks)} weeks with {sum(len(w.sessions) for w in new_plan_v2_obj.weeks)} sessions ---")
            
        except Exception as e:
            print(f"⚠️  Error processing JSON plan update: {e}")
            import traceback
            traceback.print_exc()
            # Fall through to markdown parsing as fallback
    
    # FALLBACK: Handle markdown plan updates (legacy support)
    elif '[PLAN_UPDATED]' in feedback_text:
        match = re.search(r"```markdown\n(.*?)```", feedback_text, re.DOTALL)
        if match:
            new_plan_markdown = match.group(1).strip()
            
            # CRITICAL: Archive old plan BEFORE overwriting
            if 'plan' in user_data and user_data.get('plan'):
                if 'archive' not in user_data:
                    user_data['archive'] = []
                
                # Archive current plan with timestamp
                user_data['archive'].insert(0, {
                    'plan': user_data['plan'],
                    'plan_v2': user_data.get('plan_v2'),  # Also archive plan_v2
                    'completed_date': datetime.now().isoformat(),
                    'reason': 'regenerated_via_feedback'
                })
                print(f"📦 Archived old plan before regeneration (archive now has {len(user_data['archive'])} entries)")
            
            user_data['plan'] = new_plan_markdown
            print(f"✅ Plan updated via queued webhook processing")
            
            # Update plan_v2
            try:
                current_plan_v2 = user_data.get('plan_v2')
                existing_completed = {}
                if current_plan_v2 and 'weeks' in current_plan_v2:
                    for week in current_plan_v2['weeks']:
                        for sess in week.get('sessions', []):
                            if sess.get('completed'):
                                existing_completed[sess['id']] = {
                                    'completed': True,
                                    'strava_activity_id': sess.get('strava_activity_id'),
                                    'completed_at': sess.get('completed_at')
                                }
                    print(f"   📋 Preserving {len(existing_completed)} completed sessions")
                
                from utils.migration import parse_ai_response_to_v2
                
                user_inputs = {
                    'goal': user_data.get('goal', ''),
                    'goal_date': user_data.get('goal_date'),
                    'plan_start_date': user_data.get('plan_start_date'),
                    'goal_distance': user_data.get('goal_distance')
                }
                
                plan_structure = user_data.get('plan_structure')
                if plan_structure and 'weeks' in plan_structure:
                    json_block = f"\n\n```json\n{json.dumps(plan_structure)}\n```"
                    ai_response_with_structure = new_plan_markdown + json_block
                    plan_v2, _ = parse_ai_response_to_v2(
                        ai_response_with_structure,
                        athlete_id,
                        user_inputs
                    )
                else:
                    plan_v2, _ = parse_ai_response_to_v2(
                        new_plan_markdown,
                        athlete_id,
                        user_inputs
                    )
                
                if plan_v2 and plan_v2.weeks:
                    total_sessions = sum(len(week.sessions) for week in plan_v2.weeks)
                    if total_sessions > 0:
                        # SAFEGUARD: Archive and restore past weeks
                        from utils.plan_utils import archive_and_restore_past_weeks
                        plan_v2 = archive_and_restore_past_weeks(current_plan_v2, plan_v2)
                        
                        restored_count = 0
                        for week in plan_v2.weeks:
                            for sess in week.sessions:
                                if sess.id in existing_completed:
                                    sess.completed = True
                                    sess.strava_activity_id = existing_completed[sess.id]['strava_activity_id']
                                    sess.completed_at = existing_completed[sess.id]['completed_at']
                                    restored_count += 1
                        
                        if restored_count > 0:
                            print(f"   ✅ Restored {restored_count} completed sessions")
                        
                        user_data['plan_v2'] = plan_v2.to_dict()
                        final_week_count = len(plan_v2.weeks)
                        print(f"   ✅ plan_v2 updated with {final_week_count} weeks ({total_sessions} sessions)")
            except Exception as e:
                print(f"   ⚠️  Error parsing plan_v2: {e}")
                import traceback
                traceback.print_exc()
    
    # CRITICAL: Double-check feedback_log entry is still present before saving
    first_entry_id = None
    if 'feedback_log' in user_data and user_data['feedback_log']:
        first_entry_id = user_data['feedback_log'][0].get('activity_id')
        print(f"🔍 Before final save: feedback_log[0] activity_id = {first_entry_id}")
    else:
        print(f"❌ CRITICAL: feedback_log missing or empty BEFORE final save!")
    
    safe_save_user_data(athlete_id, user_data)
    
    # CRITICAL: Verify entry was saved by reloading and checking
    if first_entry_id:
        try:
            verification_data = data_manager.load_user_data(athlete_id)
            if 'feedback_log' in verification_data and verification_data['feedback_log']:
                saved_entry_id = verification_data['feedback_log'][0].get('activity_id')
                print(f"✅ After save: Reloaded feedback_log[0] activity_id = {saved_entry_id}")
                if saved_entry_id != first_entry_id:
                    print(f"❌ CRITICAL: Entry not saved correctly! Expected {first_entry_id}, got {saved_entry_id}")
            else:
                print(f"❌ CRITICAL: feedback_log missing or empty AFTER save and reload!")
        except Exception as e:
            print(f"⚠️  Could not verify save: {e}")
    
    print(f"✅ Successfully processed queued webhooks for athlete {athlete_id}")


def safe_save_user_data(athlete_id, user_data):
    """
    Wrapper for data_manager.save_user_data that trims data to fit DynamoDB limits.
    Keeps only last 20 feedback entries and 30 chat messages.
    IMPORTANT: Trimmed feedback_log entries are saved to S3 for permanent storage.
    """
    # Trim feedback_log - but save trimmed entries to S3 first
    if 'feedback_log' in user_data and len(user_data['feedback_log']) > 20:
        # Debug: log what entries we're keeping vs trimming
        new_entry_activity_id = user_data['feedback_log'][0].get('activity_id') if user_data['feedback_log'] else None
        trimmed_entries = user_data['feedback_log'][20:]  # Entries beyond the first 20
        kept_entries = user_data['feedback_log'][:20]  # Entries we're keeping
        kept_activity_ids = [e.get('activity_id') for e in kept_entries]
        trimmed_activity_ids = [e.get('activity_id') for e in trimmed_entries]
        
        print(f"⚠️  Trimming feedback_log from {len(user_data['feedback_log'])} to 20 entries")
        print(f"   🔍 New entry activity_id {new_entry_activity_id} will be {'KEPT' if new_entry_activity_id in kept_activity_ids else 'TRIMMED'}")
        print(f"   📋 Keeping {len(kept_entries)} entries (activity_ids: {kept_activity_ids[:5]}...)")
        print(f"   ✂️  Trimming {len(trimmed_entries)} entries (activity_ids: {trimmed_activity_ids})")
        
        # Save trimmed entries to S3 for permanent storage
        try:
            if S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production':
                # Load existing S3 feedback_log and merge
                s3_key = f"athletes/{athlete_id}/feedback_log.json.gz"
                existing_s3_log = s3_manager.load_large_data(s3_key) or []
                
                # Merge: add trimmed entries to S3 log (avoid duplicates by activity_id)
                existing_activity_ids = {entry.get('activity_id') for entry in existing_s3_log}
                for entry in trimmed_entries:
                    activity_id = entry.get('activity_id')
                    if activity_id not in existing_activity_ids:
                        existing_s3_log.append(entry)
                        existing_activity_ids.add(activity_id)
                
                # Sort by activity_id (most recent first)
                existing_s3_log.sort(key=lambda x: x.get('activity_id', 0), reverse=True)
                
                # Save back to S3
                s3_manager.save_large_data(athlete_id, 'feedback_log', existing_s3_log)
                print(f"✅ Saved {len(trimmed_entries)} trimmed feedback_log entries to S3")
                
                # Store S3 key reference in user_data
                if 'feedback_log_s3_key' not in user_data:
                    user_data['feedback_log_s3_key'] = s3_key
        except Exception as e:
            print(f"⚠️  Error saving trimmed feedback_log to S3: {e}")
        
        # Now trim the in-memory version
        user_data['feedback_log'] = user_data['feedback_log'][:20]
        print(f"   ✅ Trimmed feedback_log to {len(user_data['feedback_log'])} entries in memory")
        print(f"   📋 Remaining entries activity_ids: {[e.get('activity_id') for e in user_data['feedback_log'][:5]]}")
    
    # Trim chat_log and archive older messages to S3 (so they can be loaded via "Load older")
    if 'chat_log' in user_data and len(user_data['chat_log']) > 30:
        chat_log = user_data['chat_log']
        keep_count = 30
        to_keep = chat_log[-keep_count:]
        trimmed_older = chat_log[:-keep_count]
        print(f"⚠️  Trimming chat_log from {len(chat_log)} to {keep_count} messages")
        try:
            if S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production':
                s3_key = f"athletes/{athlete_id}/chat_log.json.gz"
                existing_s3 = s3_manager.load_large_data(s3_key) or []
                # Older messages first: existing_s3 (oldest) + trimmed_older (newer)
                merged = list(existing_s3) + list(trimmed_older)
                s3_manager.save_large_data(athlete_id, 'chat_log', merged)
                user_data['chat_log_s3_key'] = s3_key
                print(f"✅ Archived {len(trimmed_older)} older chat messages to S3")
        except Exception as e:
            print(f"⚠️  Error archiving chat_log to S3: {e}")
        user_data['chat_log'] = to_keep
    
    # Remove analyzed_activities if present at root level
    if 'analyzed_activities' in user_data:
        print(f"⚠️  Removing analyzed_activities from DynamoDB (root-level)")
        del user_data['analyzed_activities']
    
    # Trim heavy fields inside plan_data (keep only metadata needed by the app)
    if 'plan_data' in user_data:
        plan_data = user_data['plan_data']
        if isinstance(plan_data, dict):
            allowed_keys = {
                'athlete_goal',
                'sessions_per_week',
                'hours_per_week',
                'lifestyle_context',
                'athlete_type',
                'friel_hr_zones',
                'friel_power_zones',
                'goal_includes_cycling',
                'weeks_until_goal',
                'goal_date',
                'plan_start_date',
                'has_partial_week',
                'days_in_partial_week',
                'goal_distance',
                'maintenance_weeks',
                'vdot_data',
                'weeks',
            }
            trimmed_plan_data = {k: v for k, v in plan_data.items() if k in allowed_keys}
            removed_keys = sorted(set(plan_data.keys()) - set(trimmed_plan_data.keys()))
            if removed_keys:
                print(f"⚠️  Trimming plan_data keys from DynamoDB (removed: {removed_keys})")
            user_data['plan_data'] = trimmed_plan_data
    
    # Remove duplicate garmin_history if metadata exists
    if 'garmin_history_metadata' in user_data and 'garmin_history' in user_data:
        print(f"⚠️  Removing duplicate garmin_history (already in S3)")
        del user_data['garmin_history']
    
    # Move all plan archive to S3 (used only for historical reference and rollback)
    if 'archive' in user_data and isinstance(user_data['archive'], list) and len(user_data['archive']) > 0:
        archive_entries = user_data['archive']
        try:
            from utils.archive_loader import save_user_archive_to_s3

            if S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production':
                # Load any existing archive from S3 (older entries)
                existing = []
                s3_key = user_data.get('archive_s3_key')
                if s3_key:
                    existing = s3_manager.load_large_data(s3_key) or []
                # Newest first: current in-memory first, then existing from S3
                merged = list(archive_entries) + existing
                result_key = save_user_archive_to_s3(athlete_id, merged)
                if result_key:
                    user_data['archive_s3_key'] = f"athletes/{athlete_id}/plan_archive.json.gz"
                    user_data['archive'] = []
                    print(f"✅ Archived all {len(archive_entries)} plan(s) to S3 (total in S3: {len(merged)})")
                else:
                    print("⚠️  save_user_archive_to_s3 returned None - archive not moved")
            else:
                print("ℹ️  S3 not available or not production - archive remains in DynamoDB (may hit size limit)")
        except Exception as e:
            print(f"⚠️  Error moving archive to S3: {e}")
    
    # Debug: log feedback_log state before saving
    if 'feedback_log' in user_data:
        print(f"💾 Saving feedback_log with {len(user_data['feedback_log'])} entries to DynamoDB")
        if user_data['feedback_log']:
            print(f"   📋 First entry activity_id: {user_data['feedback_log'][0].get('activity_id')}, name: {user_data['feedback_log'][0].get('activity_name', '')[:50]}")
    
    data_manager.save_user_data(athlete_id, user_data)


@api_bp.route('/strava_webhook', methods=['GET', 'POST'])
def strava_webhook():
    """Handle Strava webhook events"""
    if request.method == 'GET':
        # Subscription validation
        hub_challenge = request.args.get('hub.challenge', '')
        hub_verify_token = request.args.get('hub.verify_token', '')
        
        if hub_verify_token == Config.STRAVA_VERIFY_TOKEN:
            return json.dumps({'hub.challenge': hub_challenge})
        else:
            return 'Invalid verify token', 403
    
    elif request.method == 'POST':
        # Process webhook event
        event_data = request.get_json()
        print(f"--- Webhook event received: {event_data} ---")
        
        # Only process activity update events
        if event_data.get('object_type') == 'activity' and event_data.get('aspect_type') == 'update':
            athlete_id = str(event_data.get('owner_id'))
            activity_id = str(event_data.get('object_id'))
            
            user_data = data_manager.load_user_data(athlete_id)
            if not user_data or 'token' not in user_data:
                print(f"--- Could not find user data for athlete {athlete_id}. Skipping. ---")
                return 'EVENT_RECEIVED', 200
            
            # Queue webhook for delayed processing (5 minute delay to batch multiple activities)
            with webhook_queue_lock:
                # Cancel existing timer if one exists
                if athlete_id in webhook_queue:
                    existing_timer = webhook_queue[athlete_id].get('timer')
                    if existing_timer:
                        existing_timer.cancel()
                        print(f"⏸️  Cancelled existing webhook timer for athlete {athlete_id}")
                
                # Add activity to queue
                if athlete_id not in webhook_queue:
                    webhook_queue[athlete_id] = {
                        'activity_ids': set(),
                        'activity_updates': {},  # Track how many times each activity was updated
                        'timer': None,
                        'last_update': datetime.now().timestamp()
                    }
                
                # Track if this activity was already in the queue (multiple updates)
                was_already_queued = activity_id in webhook_queue[athlete_id]['activity_ids']
                
                webhook_queue[athlete_id]['activity_ids'].add(activity_id)
                
                # Track update count for this activity
                if activity_id not in webhook_queue[athlete_id]['activity_updates']:
                    webhook_queue[athlete_id]['activity_updates'][activity_id] = 0
                webhook_queue[athlete_id]['activity_updates'][activity_id] += 1
                
                webhook_queue[athlete_id]['last_update'] = datetime.now().timestamp()
                
                # Create new timer for 5-minute delay
                timer = threading.Timer(
                    Config.WEBHOOK_DELAY_SECONDS,
                    process_queued_webhooks,
                    args=(athlete_id,)
                )
                timer.daemon = True  # Allow program to exit even if timer is running
                timer.start()
                
                webhook_queue[athlete_id]['timer'] = timer
                
                queue_size = len(webhook_queue[athlete_id]['activity_ids'])
                update_count = webhook_queue[athlete_id]['activity_updates'][activity_id]
                
                if was_already_queued:
                    print(f"📥 Activity {activity_id} updated again (update #{update_count}) - timer reset, will process latest version after {Config.WEBHOOK_DELAY_SECONDS}s")
                else:
                    print(f"📥 Queued activity {activity_id} for athlete {athlete_id} (queue size: {queue_size})")
                    print(f"⏰ Will process after {Config.WEBHOOK_DELAY_SECONDS}s delay (allows batching multiple activities)")
            
            # Return immediately - processing will happen after delay
            return 'EVENT_RECEIVED', 200

        return 'EVENT_RECEIVED', 200

@api_bp.route("/api/garmin-summary")
@login_required
def garmin_summary_api():
    """API endpoint for Garmin health data with trends"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)

    if 'garmin_credentials' not in user_data:
        return jsonify({"error": "No Garmin connection found"}), 404

    today_iso = date.today().isoformat()
    
    # Check cache
    garmin_cache = user_data.get('garmin_cache', {})
    cache_date = garmin_cache.get('last_fetch_date')
    
    if cache_date == today_iso and 'metrics_timeline' in garmin_cache:
        print(f"GARMIN CACHE: Using cached data from {cache_date}")
        return jsonify({
            "today": garmin_cache['today_metrics'],
            "trend_data": garmin_cache['metrics_timeline'],
            "readiness_score": garmin_cache['readiness_score'],
            "readiness_metadata": garmin_cache.get('readiness_metadata'),
            "cached_at": garmin_cache.get('cached_at'),
            "status": "success",
            "cached": True
        })
    
    # Cache miss - fetch fresh data
    print(f"GARMIN CACHE: Fetching fresh data (last fetch: {cache_date})")
    
    try:
        # Fetch 14 days of data
        creds = user_data['garmin_credentials']
        stats_range = garmin_service.fetch_date_range(
            creds['email'],
            creds['password'],
            days=14,
            encrypted_tokenstore=creds.get('tokenstore'),
        )
        
        if not stats_range:
            return jsonify({"error": "Could not fetch Garmin data"}), 500

        # Extract metrics
        metrics_timeline = garmin_service.extract_metrics_timeline(stats_range)
        
        # Calculate readiness (now returns dict with score and metadata)
        readiness_result = garmin_service.calculate_readiness(metrics_timeline)
        readiness_score = readiness_result['score'] if readiness_result else None
        readiness_metadata = readiness_result if readiness_result else None
        
        # Calculate VO2 max changes
        vo2_max_data = garmin_service.calculate_vo2_max_changes(metrics_timeline)
        
        today_metrics = metrics_timeline[-1] if metrics_timeline else None
        
        # Add VO2 max data to today's metrics if available
        if today_metrics and vo2_max_data:
            today_metrics['vo2_max'] = vo2_max_data['vo2_max']
            today_metrics['vo2_max_change_1d'] = vo2_max_data['change_1d']
            today_metrics['vo2_max_change_14d_avg'] = vo2_max_data['change_14d_avg']

        # === FIXED: Only use S3 in production ===
        if USE_S3:
            print("Using S3 storage (production mode)")
            user_data.pop('garmin_history', None)
            
            s3_key = f"athletes/{athlete_id}/garmin_history_raw.json.gz"
            existing_history = s3_manager.load_large_data(s3_key) or {}
            
            for day_stats in stats_range:
                day_date = day_stats.get('fetch_date')
                if day_date:
                    existing_history[day_date] = day_stats
            
            # Keep last 30 days
            cutoff_date = (date.today() - timedelta(days=30)).isoformat()
            existing_history = {
                k: v for k, v in existing_history.items() 
                if k >= cutoff_date
            }
            
            result_key = s3_manager.save_large_data(athlete_id, 'garmin_history_raw', existing_history)
            
            if result_key:
                user_data['garmin_history_metadata'] = {
                    'days_available': len(existing_history),
                    'date_range': {
                        'start': min(existing_history.keys()) if existing_history else today_iso,
                        'end': max(existing_history.keys()) if existing_history else today_iso
                    },
                    'last_updated': today_iso,
                    's3_key': result_key
                }
                safe_save_user_data(athlete_id, user_data)
        else:
            # Local storage (development)
            user_data['garmin_history'] = stats_range
            safe_save_user_data(athlete_id, user_data)
        
        # Cache the processed data
        user_data['garmin_cache'] = {
            'last_fetch_date': today_iso,
            'today_metrics': today_metrics,
            'metrics_timeline': metrics_timeline,
            'readiness_score': readiness_score,
            'readiness_metadata': readiness_metadata,
            'cached_at': datetime.now().isoformat()
        }
        safe_save_user_data(athlete_id, user_data)

        return jsonify({
            "today": today_metrics,
            "trend_data": metrics_timeline,
            "readiness_score": readiness_score,
            "readiness_metadata": readiness_metadata,
            "cached_at": user_data['garmin_cache']['cached_at'],
            "status": "success",
            "cached": False
        })

    except Exception as e:
        err_msg = str(e).lower()
        # Garth can raise AssertionError "OAuth1 token is required for OAuth2 refresh"
        # when tokenstore is invalid/expired or 2FA user has no persisted session
        if "oauth1 token" in err_msg or "oauth2 refresh" in err_msg:
            print(f"Garmin session expired or invalid (OAuth): {e}")
            # Clear tokenstore so next connect uses password (and 2FA if needed)
            if 'garmin_credentials' in user_data and user_data['garmin_credentials'].get('tokenstore'):
                user_data['garmin_credentials'].pop('tokenstore', None)
                safe_save_user_data(athlete_id, user_data)
            return jsonify({
                "error": "Garmin session expired. Please reconnect your Garmin account in Settings.",
                "code": "garmin_session_expired"
            }), 401
        print(f"Error fetching Garmin data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Error fetching Garmin data: {str(e)}"}), 500

@api_bp.route("/api/garmin-refresh", methods=['POST'])
@login_required
def garmin_refresh():
    """Manually refresh Garmin data"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    if 'garmin_cache' in user_data:
        del user_data['garmin_cache']
        safe_save_user_data(athlete_id, user_data)
    
    return jsonify({"status": "cache_cleared", "message": "Refresh the page to fetch new data"})
    """Manually refresh Garmin data"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    if 'garmin_cache' in user_data:
        del user_data['garmin_cache']
        safe_save_user_data(athlete_id, user_data)
    
    return jsonify({"status": "cache_cleared", "message": "Refresh the page to fetch new data"})

# Debug endpoint (only in development)
if os.getenv('APP_DEBUG_MODE') == 'True':
    @api_bp.route("/debug-env")
    def debug_env():
        """Display environment variables for debugging"""
        env_vars = {key: value for key, value in os.environ.items()}
        flask_env = os.getenv('FLASK_ENV', 'Not Set')
        strava_client_id = os.getenv('STRAVA_CLIENT_ID', 'Not Set')
        strava_verify_token = os.getenv('STRAVA_VERIFY_TOKEN', 'Not Set')
        
        response_html = f"""
            <h1>Application Environment (DEBUG MODE)</h1>
            <h2>Key Variables:</h2>
            <ul>
                <li><b>FLASK_ENV:</b> {flask_env}</li>
                <li><b>USE_S3:</b> {USE_S3}</li>
                <li><b>S3_AVAILABLE:</b> {S3_AVAILABLE}</li>
                <li><b>STRAVA_CLIENT_ID:</b> {strava_client_id}</li>
                <li><b>STRAVA_VERIFY_TOKEN:</b> {strava_verify_token}</li>
            </ul>
            <hr>
            <h2>All Environment Variables:</h2>
            <pre>{json.dumps(env_vars, indent=4)}</pre>
        """
        return response_html