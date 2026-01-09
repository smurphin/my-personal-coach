from flask import Blueprint, request, jsonify, session
from datetime import datetime, date, timedelta
import json
import os
import jinja2
import re
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

def safe_save_user_data(athlete_id, user_data):
    """
    Wrapper for data_manager.save_user_data that trims data to fit DynamoDB limits.
    Keeps only last 20 feedback entries and 30 chat messages.
    IMPORTANT: Trimmed feedback_log entries are saved to S3 for permanent storage.
    """
    # Trim feedback_log - but save trimmed entries to S3 first
    if 'feedback_log' in user_data and len(user_data['feedback_log']) > 20:
        trimmed_entries = user_data['feedback_log'][20:]  # Entries beyond the first 20
        print(f"⚠️  Trimming feedback_log from {len(user_data['feedback_log'])} to 20 entries")
        
        # Save trimmed entries to S3 for permanent storage
        try:
            from s3_manager import s3_manager, S3_AVAILABLE
            import os
            
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
    
    # Trim chat_log
    if 'chat_log' in user_data and len(user_data['chat_log']) > 30:
        print(f"⚠️  Trimming chat_log from {len(user_data['chat_log'])} to 30 messages")
        user_data['chat_log'] = user_data['chat_log'][-30:]
    
    # Remove analyzed_activities if present
    if 'analyzed_activities' in user_data:
        print(f"⚠️  Removing analyzed_activities from DynamoDB")
        del user_data['analyzed_activities']
    
    # Remove duplicate garmin_history if metadata exists
    if 'garmin_history_metadata' in user_data and 'garmin_history' in user_data:
        print(f"⚠️  Removing duplicate garmin_history (already in S3)")
        del user_data['garmin_history']
    
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
            
            user_data = data_manager.load_user_data(athlete_id)
            if not user_data or 'token' not in user_data:
                print(f"--- Could not find user data for athlete {athlete_id}. Skipping. ---")
                return 'EVENT_RECEIVED', 200
            
            # Ensure token is valid (refresh if needed)
            access_token = strava_service.ensure_valid_token(athlete_id, user_data, data_manager)
            
            if not access_token:
                print(f"❌ Could not get valid token for athlete {athlete_id} - marking as disconnected")
                
                # Mark athlete as disconnected
                user_data['strava_connected'] = False
                user_data['strava_disconnected_at'] = datetime.now().isoformat()
                user_data['strava_disconnect_reason'] = 'token_refresh_failed'
                safe_save_user_data(athlete_id, user_data)
                
                return 'EVENT_RECEIVED', 200
            
            training_plan = user_data.get('plan')
            
            if not training_plan:
                print(f"--- No training plan found for athlete {athlete_id}. Skipping. ---")
                return 'EVENT_RECEIVED', 200
                
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
            
            # Check if API call failed
            if not isinstance(recent_activities_summary, list):
                print(f"⚠️ Strava API call failed in webhook handler for athlete {athlete_id}")
                
                # Log the actual error response for debugging
                if hasattr(recent_activities_summary, 'status_code'):
                    print(f"   Status Code: {recent_activities_summary.status_code}")
                if hasattr(recent_activities_summary, 'get_json'):
                    try:
                        error_data = recent_activities_summary.get_json()
                        print(f"   Error Response: {error_data}")
                    except:
                        pass
                
                # Check token validity
                token_data = user_data.get('token', {})
                print(f"   Token expires_at: {token_data.get('expires_at', 'unknown')}")
                print(f"   Current time: {datetime.now().timestamp()}")
                
                return 'EVENT_RECEIVED', 200  # Return 200 so Strava doesn't retry
            
            new_activities_to_process = [
                act for act in recent_activities_summary
                if str(act['id']) not in processed_activity_ids
            ]

            if not new_activities_to_process:
                print(f"--- No new activities to analyze for athlete {athlete_id}. ---")
                return 'EVENT_RECEIVED', 200

            new_activities_to_process.reverse()
            
            # Analyze new activities
            analyzed_sessions = []
            raw_activities = []  # Store raw Strava data for VDOT detection
            friel_hr_zones = user_data.get('plan_data', {}).get('friel_hr_zones', {})
            
            for activity_summary in new_activities_to_process:
                activity = strava_service.get_activity_detail(access_token, activity_summary['id'])
                if not activity:
                    continue

                streams = strava_service.get_activity_streams(access_token, activity['id'])
                analyzed_session = training_service.analyze_activity(
                    activity,
                    streams,
                    {"heart_rate": friel_hr_zones}
                )
                
                # Store raw time_in_zones BEFORE formatting
                raw_time_in_zones = analyzed_session["time_in_hr_zones"].copy()
                
                # Format time in zones for display
                for key, seconds in analyzed_session["time_in_hr_zones"].items():
                    analyzed_session["time_in_hr_zones"][key] = format_seconds(seconds)
                
                analyzed_sessions.append(analyzed_session)
                raw_activities.append({
                    'activity': activity,  # Raw Strava activity
                    'time_in_zones': raw_time_in_zones  # Unformatted time_in_zones
                })

            if not analyzed_sessions:
                return jsonify({"message": "Found new activities, but could not analyze their details."})

            # Fetch Garmin data
            first_activity_date_iso = datetime.fromisoformat(
                analyzed_sessions[0]['start_date'].replace('Z', '')
            ).date().isoformat()
            
            garmin_data_for_activity = None
            if 'garmin_credentials' in user_data:
                garmin_data_for_activity = garmin_service.authenticate_and_fetch(
                    user_data['garmin_credentials']['email'],
                    user_data['garmin_credentials']['password'],
                    first_activity_date_iso
                )

            # ============================================
            # VDOT DETECTION (same as feedback_routes.py)
            # ============================================
            if raw_activities and analyzed_sessions:
                from services.vdot_detection_service import vdot_detection_service
                from utils.vdot_calculator import VDOTCalculator
                
                print("\n" + "="*70)
                print("VDOT DETECTION - DEBUG LOG (WEBHOOK)")
                print("="*70)
                
                # Use RAW activity for VDOT detection
                raw_activity = raw_activities[0]['activity']
                time_in_zones_raw = raw_activities[0]['time_in_zones']  # Unformatted
                
                # Convert zone keys: 'Zone 1' -> 'Z1', 'Zone 2' -> 'Z2', etc.
                time_in_zones = {}
                for zone_name, zone_time in time_in_zones_raw.items():
                    if 'Zone' in zone_name:
                        # Convert 'Zone 1' to 'Z1', 'Zone 2' to 'Z2', etc.
                        zone_num = zone_name.replace('Zone ', '')
                        time_in_zones[f'Z{zone_num}'] = zone_time
                    else:
                        # Already in correct format
                        time_in_zones[zone_name] = zone_time
                
                print(f"📊 Activity being analyzed:")
                print(f"   Name: {raw_activity.get('name')}")
                print(f"   Distance: {raw_activity.get('distance')} meters")
                print(f"   Time: {raw_activity.get('moving_time')} seconds")
                print(f"   Type: {raw_activity.get('type')}")
                print(f"   Workout Type: {raw_activity.get('workout_type')} (1=Race)")
                print(f"\n⏱️  Time in zones:")
                for zone, time_secs in time_in_zones.items():
                    if time_secs and isinstance(time_secs, (int, float)):
                        minutes = int(time_secs / 60)
                        seconds = int(time_secs % 60)
                        percent = (time_secs / raw_activity.get('moving_time', 1)) * 100
                        print(f"   {zone}: {minutes}:{seconds:02d} ({time_secs}s, {percent:.1f}%)")
                
                print(f"\n🔍 Calling vdot_detection_service...")
                vdot_result = vdot_detection_service.calculate_vdot_from_activity(
                    raw_activity,  # Pass RAW activity
                    time_in_zones  # Now with correct Z1, Z2, Z3, Z4, Z5 keys!
                )
                
                if vdot_result:
                    print(f"\n✅ VDOT DETECTION SUCCESSFUL!")
                    print(f"   Distance: {vdot_result['distance']}")
                    print(f"   Time: {vdot_result['time_seconds']}s")
                    print(f"   Calculated VDOT: {vdot_result['vdot']}")
                    print(f"   Is Race: {vdot_result['is_race']}")
                    print(f"   Reason: {vdot_result['intensity_reason']}")
                else:
                    print(f"\n❌ Activity does not qualify for VDOT calculation")
                    print(f"   (Not a race or insufficient intensity)")
                
                if vdot_result:
                    # Get current VDOT
                    current_vdot = None
                    if 'training_metrics' in user_data and 'vdot' in user_data['training_metrics']:
                        current_vdot_data = user_data['training_metrics']['vdot']
                        if isinstance(current_vdot_data, dict):
                            current_vdot = current_vdot_data.get('value')
                    
                    new_vdot = int(vdot_result['vdot'])
                    
                    print(f"\n💾 VDOT STORAGE:")
                    print(f"   Current VDOT in DB: {current_vdot}")
                    print(f"   New VDOT calculated: {new_vdot}")
                    print(f"   Will update: {not current_vdot or new_vdot >= (current_vdot + 1)}")
                    
                    # Only flag for confirmation if significant improvement (>= 1 point)
                    if not current_vdot or new_vdot >= (current_vdot + 1):
                        print(f"\n🎯 UPDATING VDOT: {current_vdot} → {new_vdot}")
                        
                        # Calculate paces from Jack Daniels' tables
                        print(f"\n📏 Calculating training paces from VDOT {new_vdot}...")
                        calc = VDOTCalculator()
                        paces = calc.get_training_paces(new_vdot)
                        
                        print(f"✅ Training paces calculated:")
                        for pace_name, pace_value in paces.items():
                            print(f"   {pace_name}: {pace_value}")
                        
                        # Initialize training_metrics if needed
                        if 'training_metrics' not in user_data:
                            user_data['training_metrics'] = {'version': 1}
                        
                        # Store previous VDOT for rollback
                        previous_vdot = None
                        if 'vdot' in user_data['training_metrics']:
                            previous_vdot = user_data['training_metrics']['vdot'].copy()
                        
                        # Store new VDOT with pending confirmation
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
                        
                        print(f"\n💾 Stored in training_metrics['vdot']:")
                        print(f"   value: {new_vdot}")
                        print(f"   source: RACE_DETECTION")
                        print(f"   user_confirmed: False")
                        print(f"   paces: {len(paces)} pace entries")
                        print(f"   detected_from: {vdot_result['activity_name']}")
                        
                        # Save immediately (before AI call)
                        safe_save_user_data(athlete_id, user_data)
                        
                        print(f"\n✅ VDOT data saved to DynamoDB")
                        print("="*70 + "\n")
                        
                        # Reload user_data to get the updated training_metrics
                        user_data = data_manager.load_user_data(athlete_id)
            
            # Prepare VDOT context for AI
            from utils.vdot_context import prepare_vdot_context
            
            print("\n" + "="*70)
            print("AI PROMPT PREPARATION - DEBUG LOG (WEBHOOK)")
            print("="*70)
            print(f"📝 Preparing VDOT context for AI...")
            
            vdot_data = prepare_vdot_context(user_data)
            
            if vdot_data and vdot_data.get('current_vdot'):
                print(f"\n✅ VDOT data prepared for AI:")
                print(f"   current_vdot: {vdot_data['current_vdot']}")
                print(f"   easy_pace: {vdot_data.get('easy_pace')}")
                print(f"   marathon_pace: {vdot_data.get('marathon_pace')}")
                print(f"   threshold_pace: {vdot_data.get('threshold_pace')}")
                print(f"   interval_pace: {vdot_data.get('interval_pace')}")
                print(f"   repetition_pace: {vdot_data.get('repetition_pace')}")
                if vdot_data.get('source_activity'):
                    print(f"   source_activity: {vdot_data['source_activity']}")
            else:
                print(f"\nℹ️  No VDOT data available - athlete hasn't completed qualifying effort")
            
            print(f"\n🤖 Calling AI service with:")
            print(f"   - Analyzed sessions: {len(analyzed_sessions)}")
            print(f"   - Feedback log entries: {len(feedback_log)}")
            print(f"   - VDOT data: {'Yes' if vdot_data and vdot_data.get('current_vdot') else 'No'}")
            print(f"   - Garmin data: {'Yes' if garmin_data_for_activity else 'No'}")
            print("="*70 + "\n")

            # Generate feedback (NOW WITH VDOT DATA)
            feedback_markdown = ai_service.generate_feedback(
                training_plan,
                feedback_log,
                analyzed_sessions,
                user_data.get('training_history'),
                garmin_data_for_activity,
                incomplete_sessions=None,  # Webhook doesn't use this yet
                vdot_data=vdot_data  # PASS VDOT DATA TO AI
            )
            
            print("\n" + "="*70)
            print("AI RESPONSE - DEBUG LOG (WEBHOOK)")
            print("="*70)
            print(f"✅ AI response received ({len(feedback_markdown)} characters)")
            
            # Check if AI mentioned VDOT
            if 'VDOT' in feedback_markdown or 'vdot' in feedback_markdown.lower():
                print(f"\n🔍 AI mentioned VDOT in response")
                
                # Extract lines mentioning VDOT for debugging
                vdot_lines = [line.strip() for line in feedback_markdown.split('\n') 
                             if 'vdot' in line.lower() and line.strip()]
                
                if vdot_lines:
                    print(f"   Found {len(vdot_lines)} lines mentioning VDOT:")
                    for i, line in enumerate(vdot_lines[:5], 1):  # Show first 5
                        # Truncate long lines
                        display_line = line[:100] + "..." if len(line) > 100 else line
                        print(f"   {i}. {display_line}")
                    if len(vdot_lines) > 5:
                        print(f"   ... and {len(vdot_lines) - 5} more")
                
                # Check for problematic patterns
                if re.search(r'calculate.*vdot|vdot.*calculate', feedback_markdown, re.IGNORECASE):
                    print(f"\n⚠️  WARNING: AI used phrase 'calculate VDOT' - this should not happen!")
                
                if re.search(r'based on this.*vdot|vdot.*based on', feedback_markdown, re.IGNORECASE):
                    print(f"\n⚠️  WARNING: AI said 'based on this, VDOT...' - might be calculating!")
                
                # Check if AI used the correct VDOT value
                if vdot_data and vdot_data.get('current_vdot'):
                    expected_vdot = int(vdot_data['current_vdot'])
                    if f"VDOT {expected_vdot}" in feedback_markdown or f"VDOT of {expected_vdot}" in feedback_markdown:
                        print(f"\n✅ AI correctly referenced VDOT {expected_vdot}")
                    else:
                        print(f"\n⚠️  WARNING: Expected VDOT {expected_vdot} not found in response")
                        
                        # Look for other VDOT numbers
                        vdot_numbers = re.findall(r'VDOT[:\s]+(\d+)', feedback_markdown, re.IGNORECASE)
                        if vdot_numbers:
                            print(f"   Found these VDOT values instead: {', '.join(set(vdot_numbers))}")
            else:
                print(f"\nℹ️  AI did not mention VDOT (expected if no VDOT established)")
            
            print("="*70 + "\n")

            # Create descriptive name
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
                "feedback_markdown": feedback_markdown,
                "logged_activity_ids": all_activity_ids
            }
            
            feedback_log.insert(0, new_log_entry)

            # Check for plan update - only update if [PLAN_UPDATED] marker is present
            if '[PLAN_UPDATED]' in feedback_markdown:
                match = re.search(r"```markdown\n(.*?)```", feedback_markdown, re.DOTALL)
                if match:
                    new_plan_markdown = match.group(1).strip()
                    user_data['plan'] = new_plan_markdown
                    print(f"✅ Plan for athlete {athlete_id} has been updated via webhook!")
                else:
                    print(f"⚠️ [PLAN_UPDATED] marker found but no markdown code block for athlete {athlete_id}")
            else:
                print(f"ℹ️ No plan update needed for athlete {athlete_id} - marker not found")
            
            safe_save_user_data(athlete_id, user_data)
            print(f"--- Successfully generated and saved feedback for athlete {athlete_id} via webhook. ---")

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
            "readiness_metadata": garmin_cache.get('readiness_metadata'),  # May be None for old cache
            "cached_at": garmin_cache.get('cached_at'),  # Timestamp of when data was cached
            "status": "success",
            "cached": True
        })
    
    # Cache miss - fetch fresh data
    print(f"GARMIN CACHE: Fetching fresh data (last fetch: {cache_date})")
    
    try:
        # Fetch 14 days of data
        stats_range = garmin_service.fetch_date_range(
            user_data['garmin_credentials']['email'],
            user_data['garmin_credentials']['password'],
            days=14
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
            else:
                print("S3 SAVE FAILED: Falling back to local storage")
                if 'garmin_history' not in user_data:
                    user_data['garmin_history'] = {}
                for day_stats in stats_range:
                    day_date = day_stats.get('fetch_date')
                    if day_date:
                        user_data['garmin_history'][day_date] = day_stats
        else:
            # Local dev or S3 not available - store locally
            print("Using local storage (development mode)")
            if 'garmin_history' not in user_data:
                user_data['garmin_history'] = {}
            
            for day_stats in stats_range:
                day_date = day_stats.get('fetch_date')
                if day_date:
                    user_data['garmin_history'][day_date] = day_stats
            
            # Keep last 30 days
            cutoff_date = (date.today() - timedelta(days=30)).isoformat()
            user_data['garmin_history'] = {
                k: v for k, v in user_data['garmin_history'].items() 
                if k >= cutoff_date
            }
        
        # Update cache
        cached_at = datetime.utcnow().isoformat() + 'Z'
        user_data['garmin_cache'] = {
            'last_fetch_date': today_iso,
            'today_metrics': today_metrics,
            'metrics_timeline': metrics_timeline,
            'readiness_score': readiness_score,
            'readiness_metadata': readiness_metadata,  # Store full details for transparency
            'cached_at': cached_at  # Store when data was cached
        }
        
        safe_save_user_data(athlete_id, user_data)
        print(f"GARMIN CACHE: Fresh data cached for {today_iso} at {cached_at}")

        return jsonify({
            "today": today_metrics,
            "trend_data": metrics_timeline,
            "readiness_score": readiness_score,
            "readiness_metadata": readiness_metadata,  # Include metadata for dashboard display
            "cached_at": cached_at,  # Include timestamp for display
            "status": "success",
            "cached": False
        })

    except Exception as e:
        print(f"Error in Garmin API: {e}")
        import traceback
        traceback.print_exc()
        
        # Return stale cache if available
        if cache_date and 'metrics_timeline' in garmin_cache:
            print(f"GARMIN CACHE: Fetch failed, returning stale cache from {cache_date}")
            return jsonify({
                "today": garmin_cache['today_metrics'],
                "trend_data": garmin_cache['metrics_timeline'],
                "readiness_score": garmin_cache['readiness_score'],
                "readiness_metadata": garmin_cache.get('readiness_metadata'),
                "cached_at": garmin_cache.get('cached_at'),  # Include timestamp even for stale cache
                "status": "success",
                "cached": True,
                "warning": f"Using cached data from {cache_date}"
            })
        
        return jsonify({"error": str(e)}), 500

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