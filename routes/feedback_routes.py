from flask import Blueprint, render_template, jsonify, session, request
from datetime import datetime, timedelta
import re
from data_manager import data_manager
from services.strava_service import strava_service
from services.training_service import training_service
from services.ai_service import ai_service
from services.garmin_service import garmin_service
from markdown_manager import render_markdown_with_toc
from utils.decorators import login_required
from utils.formatters import format_seconds, format_activity_date
from utils.session_matcher import match_sessions_batch

feedback_bp = Blueprint('feedback', __name__)

def safe_save_user_data(athlete_id, user_data):
    """
    Wrapper for data_manager.save_user_data that trims data to fit DynamoDB limits.
    Keeps only last 20 feedback entries and 30 chat messages.
    """
    # Trim feedback_log
    if 'feedback_log' in user_data and len(user_data['feedback_log']) > 20:
        print(f"⚠️  Trimming feedback_log from {len(user_data['feedback_log'])} to 20 entries")
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


def process_feedback_markdown(feedback_markdown):
    """
    Process feedback markdown to extract and render plan updates nicely.
    
    Returns:
        tuple: (processed_markdown, plan_html or None)
    """
    if '[PLAN_UPDATED]' not in feedback_markdown:
        # No plan update - return as-is
        return feedback_markdown, None
    
    # Extract the plan markdown from the code block
    match = re.search(r"```markdown\n(.*?)```", feedback_markdown, re.DOTALL)
    if not match:
        # Marker found but no code block - return as-is
        return feedback_markdown, None
    
    plan_markdown = match.group(1).strip()
    
    # Remove the [PLAN_UPDATED] marker and the code block from the feedback
    processed_markdown = feedback_markdown.replace('[PLAN_UPDATED]', '').strip()
    processed_markdown = re.sub(r"```markdown\n.*?```", "", processed_markdown, flags=re.DOTALL).strip()
    
    # Render the plan markdown separately
    plan_html = render_markdown_with_toc(plan_markdown)['content']
    
    # Wrap the plan in a nice styled section
    plan_section = f"""
    <div class="mt-8 border-l-4 border-brand-blue bg-brand-dark-gray/50 rounded-r-lg p-6">
        <h3 class="text-xl font-bold text-brand-blue mb-4">📋 Updated Training Plan</h3>
        <div class="prose prose-invert max-w-none">
            {plan_html}
        </div>
    </div>
    """
    
    return processed_markdown, plan_section



@feedback_bp.route("/feedback")
@login_required
def feedback():
    """Renders the loading page for feedback generation"""
    return render_template('feedback.html')

@feedback_bp.route("/feedback/<int:activity_id>")
@login_required
def view_specific_feedback(activity_id):
    """View feedback for a specific activity"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    feedback_log = user_data.get('feedback_log', [])
    
    # Load additional entries from S3 if available (same as coaching_log)
    try:
        from s3_manager import s3_manager, S3_AVAILABLE
        import os
        
        if S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production':
            s3_key = f"athletes/{athlete_id}/feedback_log.json.gz"
            s3_feedback_log = s3_manager.load_large_data(s3_key)
            
            if s3_feedback_log:
                # Merge S3 entries with DynamoDB entries (avoid duplicates by activity_id)
                dynamodb_activity_ids = {entry.get('activity_id') for entry in feedback_log}
                for entry in s3_feedback_log:
                    s3_activity_id = entry.get('activity_id')
                    if s3_activity_id not in dynamodb_activity_ids:
                        feedback_log.append(entry)
                        dynamodb_activity_ids.add(s3_activity_id)
                
                print(f"✅ Loaded {len(s3_feedback_log)} additional feedback_log entries from S3 for viewing")
    except Exception as e:
        print(f"⚠️  Error loading feedback_log from S3: {e}")
    
    print(f"--- Looking for feedback for activity_id: {activity_id} (total entries: {len(feedback_log)}) ---")
    
    for idx, entry in enumerate(feedback_log):
        entry_activity_id = entry.get('activity_id')
        logged_ids = entry.get('logged_activity_ids', [])
        
        print(f"Entry {idx}: activity_id={entry_activity_id}, logged_ids={logged_ids}")
        
        if entry_activity_id == activity_id or activity_id in logged_ids:
            print(f"--- MATCH FOUND at index {idx} ---")
            
            # Process feedback to extract plan updates
            processed_markdown, plan_html = process_feedback_markdown(entry['feedback_markdown'])
            feedback_html = render_markdown_with_toc(processed_markdown)['content']
            
            # Append plan HTML if it exists
            if plan_html:
                feedback_html += plan_html
            
            return render_template(
                'feedback.html',
                feedback_content=feedback_html,
                activity_id=activity_id
            )
    
    print(f"--- NO MATCH FOUND for activity_id: {activity_id} ---")
    return "Feedback for that activity could not be found.", 404

@feedback_bp.route("/log")
@login_required
def coaching_log():
    """Display the coaching log with all feedback entries (from DynamoDB + S3)"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    feedback_log = user_data.get('feedback_log', [])
    
    # Load additional entries from S3 if available
    try:
        from s3_manager import s3_manager, S3_AVAILABLE
        import os
        
        if S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production':
            s3_key = f"athletes/{athlete_id}/feedback_log.json.gz"
            s3_feedback_log = s3_manager.load_large_data(s3_key)
            
            if s3_feedback_log:
                # Merge S3 entries with DynamoDB entries (avoid duplicates by activity_id)
                dynamodb_activity_ids = {entry.get('activity_id') for entry in feedback_log}
                for entry in s3_feedback_log:
                    activity_id = entry.get('activity_id')
                    if activity_id not in dynamodb_activity_ids:
                        feedback_log.append(entry)
                        dynamodb_activity_ids.add(activity_id)
                
                # Sort by activity_id (most recent first)
                feedback_log.sort(key=lambda x: x.get('activity_id', 0), reverse=True)
                print(f"✅ Loaded {len(s3_feedback_log)} additional feedback_log entries from S3")
    except Exception as e:
        print(f"⚠️  Error loading feedback_log from S3: {e}")
    
    return render_template('coaching_log.html', log_entries=feedback_log)

@feedback_bp.route("/api/get-feedback")
@login_required
def get_feedback_api():
    """API endpoint to generate or retrieve feedback"""
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        
        # Load athlete profile for lifestyle context and athlete type
        athlete_profile = user_data.get('athlete_profile', {})
        if not athlete_profile:
            # Fallback to legacy plan_data if profile doesn't exist
            plan_data = user_data.get('plan_data', {})
            athlete_profile = {
                'lifestyle_context': plan_data.get('lifestyle_context'),
                'athlete_type': plan_data.get('athlete_type')
            }
        
        # Ensure token is valid (refresh if needed)
        access_token = strava_service.ensure_valid_token(athlete_id, user_data, data_manager)

        if not access_token:
            return jsonify({
                'error': 'Your Strava connection has expired. Please <a href="/logout">log out</a> and log in again.'
            }), 401

        if 'feedback_log' not in user_data:
            user_data['feedback_log'] = []

        feedback_log = user_data['feedback_log']
        
        # Load additional entries from S3 if available (needed for viewing old feedback)
        try:
            from s3_manager import s3_manager, S3_AVAILABLE
            import os
            
            if S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production':
                s3_key = f"athletes/{athlete_id}/feedback_log.json.gz"
                s3_feedback_log = s3_manager.load_large_data(s3_key)
                
                if s3_feedback_log:
                    # Merge S3 entries with DynamoDB entries (avoid duplicates by activity_id)
                    dynamodb_activity_ids = {entry.get('activity_id') for entry in feedback_log}
                    for entry in s3_feedback_log:
                        s3_activity_id = entry.get('activity_id')
                        if s3_activity_id not in dynamodb_activity_ids:
                            feedback_log.append(entry)
                            dynamodb_activity_ids.add(s3_activity_id)
                    
                    print(f"✅ API: Loaded {len(s3_feedback_log)} additional feedback_log entries from S3")
        except Exception as e:
            print(f"⚠️  API: Error loading feedback_log from S3: {e}")
        
        # Check if a specific activity_id was requested (viewing existing feedback)
        requested_activity_id = request.args.get('activity_id', type=int)
        
        if requested_activity_id:
            # Find and return specific feedback - NO PLAN CHECK NEEDED for viewing
            for entry in feedback_log:
                entry_activity_id = entry.get('activity_id')
                logged_ids = entry.get('logged_activity_ids', [])
                
                if entry_activity_id == requested_activity_id or requested_activity_id in logged_ids:
                    # Process feedback to extract plan updates
                    processed_markdown, plan_html = process_feedback_markdown(entry['feedback_markdown'])
                    feedback_html = render_markdown_with_toc(processed_markdown)['content']
                    
                    # Append plan HTML if it exists
                    if plan_html:
                        feedback_html += plan_html
                    
                    return jsonify({'feedback_html': feedback_html})
            
            return jsonify({'error': f'Feedback for activity {requested_activity_id} not found'}), 404
        
        # No specific activity - user wants to generate NEW feedback
        # NOW check if they have a plan (required for generating new feedback)
        training_plan = user_data.get('plan')
        has_plan_v2 = 'plan_v2' in user_data
        
        if not training_plan and not has_plan_v2:
            # Allow viewing existing feedback, but not generating new
            if feedback_log:
                # Show most recent existing feedback instead of blocking
                processed_markdown, plan_html = process_feedback_markdown(feedback_log[0]['feedback_markdown'])
                feedback_html = render_markdown_with_toc(processed_markdown)['content']
                
                if plan_html:
                    feedback_html += plan_html
                
                return jsonify({
                    'feedback_html': feedback_html,
                    'message': 'You can view past coaching feedback, but creating a plan is needed to analyze new activities.'
                })
            else:
                # No plan and no existing feedback
                return jsonify({
                    'message': 'No training plan found. Please <a href="/onboarding">generate a plan</a> to get coaching feedback on your activities.'
                })
        
        # Check for new activities to process
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
            return jsonify({'error': 'Failed to fetch activities from Strava'}), 500
        
        new_activities_to_process = [
            act for act in recent_activities_summary
            if str(act['id']) not in processed_activity_ids
        ]

        if not new_activities_to_process:
            if feedback_log:
                # Process feedback to extract plan updates
                processed_markdown, plan_html = process_feedback_markdown(feedback_log[0]['feedback_markdown'])
                feedback_html = render_markdown_with_toc(processed_markdown)['content']
                
                # Append plan HTML if it exists
                if plan_html:
                    feedback_html += plan_html
                
                return jsonify({'feedback_html': feedback_html})
            else:
                return jsonify({'message': "No new activities to analyze in the last 7 days."})

        new_activities_to_process.reverse()
        
        # Analyze new activities
        analyzed_sessions = []
        raw_activities = []  # Store raw Strava data for VDOT detection

        # Load Friel zones from plan_data (same source used for plan generation)
        plan_data = user_data.get('plan_data', {}) or {}
        friel_hr_zones = plan_data.get('friel_hr_zones') or {}
        friel_power_zones = plan_data.get('friel_power_zones') or {}

        if friel_power_zones:
            print("✅ Loaded Friel power zones for feedback analysis")
        else:
            print("ℹ️  No Friel power zones found in plan_data; power zone analysis will be limited")

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
            
            # Debug: Log laps/splits data for interval sessions
            if analyzed_session.get("intervals_detected", {}).get("has_intervals"):
                laps_count = analyzed_session.get("laps_summary", {}).get("count", 0)
                splits_metric_count = analyzed_session.get("splits_metric_summary", {}).get("count", 0)
                splits_standard_count = analyzed_session.get("splits_standard_summary", {}).get("count", 0)
                preferred = analyzed_session.get("preferred_segment_summary")
                print(f"\n🔍 INTERVAL SESSION DETECTED:")
                print(f"   Laps count: {laps_count}")
                print(f"   Splits metric count: {splits_metric_count}")
                print(f"   Splits standard count: {splits_standard_count}")
                print(f"   Preferred segment: {preferred}")
                print(f"   Detection method: {analyzed_session.get('intervals_detected', {}).get('detection_method')}")
            
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
            return jsonify({"message": "Found new activities, but could not analyze their details. Please try again."})

        # === AI-Assisted Session Completion Tracking ===
        incomplete_sessions_text = None
        
        if 'plan_v2' in user_data:
            try:
                activity_date = datetime.fromisoformat(
                    analyzed_sessions[0]['start_date'].replace('Z', '')
                ).date()
                
                print(f"\n=== AI-Assisted Session Matching ===")
                print(f"Activity: {analyzed_sessions[0].get('name')}")
                print(f"Activity date: {activity_date}")
                print(f"Activity type: {analyzed_sessions[0].get('type')}")
                
                # Find week containing this activity date
                plan = user_data['plan_v2']
                matched_week = None
                
                for week in plan['weeks']:
                    week_start = datetime.fromisoformat(week['start_date']).date()
                    week_end = datetime.fromisoformat(week['end_date']).date()
                    
                    if week_start <= activity_date <= week_end:
                        matched_week = week
                        print(f"✅ Found week {week['week_number']}: {week_start} to {week_end}")
                        break
                
                if matched_week:
                    # Get incomplete sessions of matching type in this week
                    activity_type_map = {
                        'Run': 'RUN',
                        'VirtualRun': 'RUN',
                        'Ride': 'BIKE',
                        'VirtualRide': 'BIKE',
                        'Swim': 'SWIM'
                    }
                    expected_type = activity_type_map.get(analyzed_sessions[0].get('type'))
                    
                    incomplete_sessions = [
                        s for s in matched_week['sessions'] 
                        if not s.get('completed', False) 
                        and s.get('type') != 'REST'
                        and (not expected_type or s.get('type') == expected_type)
                    ]
                    
                    if incomplete_sessions:
                        print(f"Found {len(incomplete_sessions)} incomplete {expected_type or 'ANY'} sessions")
                        
                        # Prepare text for AI prompt
                        session_list = []
                        for s in incomplete_sessions:
                            session_list.append(
                                f"[{s['id']}] {s.get('type', 'UNKNOWN')}: {s.get('description', 'No description')}"
                            )
                        incomplete_sessions_text = "\n".join(session_list)
                        print(f"Sessions to match:\n{incomplete_sessions_text}")
                    else:
                        print(f"ℹ️  No incomplete {expected_type or 'ANY'} sessions in week {matched_week['week_number']}")
                else:
                    print(f"⚠️  Activity {activity_date} doesn't fall within any plan week")
                    print(f"   Available weeks:")
                    for week in plan['weeks']:
                        print(f"     Week {week['week_number']}: {week['start_date']} to {week['end_date']}")
                
                print("=" * 70)
                
            except Exception as e:
                print(f"⚠️  Error preparing session data for AI: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"ℹ️  No plan_v2 found - skipping session completion tracking")
        
        # Fetch Garmin data for the activity date
        garmin_data_for_activity = None
        if 'garmin_credentials' in user_data:
            first_activity_date_iso = datetime.fromisoformat(
                analyzed_sessions[0]['start_date'].replace('Z', '')
            ).date().isoformat()
            
            # Check stored history first
            if 'garmin_history' in user_data and first_activity_date_iso in user_data['garmin_history']:
                print(f"--- Using stored Garmin data for feedback on {first_activity_date_iso}. ---")
                garmin_data_for_activity = user_data['garmin_history'][first_activity_date_iso]
            else:
                print(f"--- No stored Garmin data for {first_activity_date_iso}. Fetching now. ---")
                garmin_data_for_activity = garmin_service.authenticate_and_fetch(
                    user_data['garmin_credentials']['email'],
                    user_data['garmin_credentials']['password'],
                    first_activity_date_iso
                )
                
                if garmin_data_for_activity:
                    if 'garmin_history' not in user_data:
                        user_data['garmin_history'] = {}
                    user_data['garmin_history'][first_activity_date_iso] = garmin_data_for_activity
                    safe_save_user_data(athlete_id, user_data)
        
        # Check for VDOT detection from completed activity (ONLY for running activities)
        # Fix for issue #87: VDOT should only be calculated from running activities
        if raw_activities and analyzed_sessions:
            from services.vdot_detection_service import vdot_detection_service
            from utils.vdot_calculator import VDOTCalculator
            
            # Use RAW activity for VDOT detection
            raw_activity = raw_activities[0]['activity']
            activity_type = raw_activity.get('type', '')
            
            # Only check running activities for VDOT (fix for issue #87)
            if activity_type in ['Run', 'VirtualRun']:
                print("\n" + "="*70)
                print("VDOT DETECTION - DEBUG LOG")
                print("="*70)
                
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
            else:
                print(f"\nℹ️  Skipping VDOT detection: Activity type '{activity_type}' is not a running activity")
                vdot_result = None
            
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
                
                # Skip update if VDOT value hasn't changed (avoid unnecessary recalculations)
                if current_vdot is not None and current_vdot == new_vdot:
                    print(f"   ⏭️  Skipping update: VDOT value unchanged ({new_vdot})")
                else:
                    # Always update when a new VDOT is detected (can go up or down)
                    will_update = True
                    print(f"   Will update: {will_update} (always update when detected)")
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
                    
                    # Save immediately
                    safe_save_user_data(athlete_id, user_data)
                    
                    print(f"\n✅ VDOT data saved to DynamoDB")
                    print("="*70 + "\n")
                    
                    print(f"✅ New VDOT {new_vdot} stored (pending user confirmation)")
                    
                    # Flash message to user
                    from flask import flash
                    race_name = vdot_result['activity_name']
                    flash(f'New VDOT {new_vdot} detected from {race_name}! Please review in your dashboard.', 'info')
            
            # Check for FTP detection from completed cycling activity
            # Only check cycling activities for FTP (similar to VDOT for running)
            if activity_type in ['Ride', 'VirtualRide']:
                from services.ftp_detection_service import ftp_detection_service
                
                print("\n" + "="*70)
                print("FTP DETECTION - DEBUG LOG")
                print("="*70)
                
                # Get power zones from analyzed session
                analyzed_session = analyzed_sessions[0]
                time_in_power_zones_raw = analyzed_session.get('time_in_power_zones', {})
                
                # Get HR zones for validation (use raw unformatted data)
                time_in_hr_zones = {}
                if raw_activities and raw_activities[0].get('time_in_zones'):
                    # Use raw unformatted HR zones (stored before formatting)
                    raw_hr_zones = raw_activities[0]['time_in_zones']
                    for zone, value in raw_hr_zones.items():
                        if isinstance(value, (int, float)):
                            time_in_hr_zones[zone] = int(value)
                
                # Get streams for power data
                activity_id = raw_activity.get('id')
                streams = strava_service.get_activity_streams(access_token, activity_id)
                
                print(f"📊 Cycling activity being analyzed:")
                print(f"   Name: {raw_activity.get('name')}")
                print(f"   Time: {raw_activity.get('moving_time')} seconds")
                print(f"   Type: {raw_activity.get('type')}")
                print(f"   Average Power: {raw_activity.get('average_watts', 'N/A')} W")
                print(f"\n⏱️  Time in power zones:")
                for zone, time_str in time_in_power_zones_raw.items():
                    print(f"   {zone}: {time_str}")
                print(f"\n⏱️  Time in HR zones:")
                for zone, time_secs in time_in_hr_zones.items():
                    if time_secs and isinstance(time_secs, (int, float)):
                        minutes = int(time_secs / 60)
                        seconds = int(time_secs % 60)
                        print(f"   {zone}: {minutes}:{seconds:02d} ({time_secs}s)")
                
                print(f"\n🔍 Calling ftp_detection_service...")
                ftp_result = ftp_detection_service.calculate_ftp_from_activity(
                    raw_activity,
                    streams,
                    time_in_power_zones_raw,
                    time_in_hr_zones
                )
                
                if ftp_result:
                    print(f"\n✅ FTP DETECTION SUCCESSFUL!")
                    print(f"   Test Duration: {ftp_result['test_duration']}")
                    print(f"   Average Power: {ftp_result['average_power']} W")
                    print(f"   Calculated FTP: {ftp_result['ftp']} W")
                    print(f"   Is FTP Test: {ftp_result['is_ftp_test']}")
                    print(f"   Reason: {ftp_result['intensity_reason']}")
                    
                    # Get current FTP
                    current_ftp = None
                    if 'training_metrics' in user_data and 'ftp' in user_data['training_metrics']:
                        current_ftp_data = user_data['training_metrics']['ftp']
                        if isinstance(current_ftp_data, dict):
                            current_ftp = current_ftp_data.get('value')
                    
                    new_ftp = int(ftp_result['ftp'])
                    
                    print(f"\n💾 FTP STORAGE:")
                    print(f"   Current FTP in DB: {current_ftp}")
                    print(f"   New FTP calculated: {new_ftp}")
                    
                    # Skip update if FTP value hasn't changed (avoid unnecessary recalculations)
                    if current_ftp is not None and current_ftp == new_ftp:
                        print(f"   ⏭️  Skipping update: FTP value unchanged ({new_ftp})")
                    else:
                        print(f"\n🎯 UPDATING FTP: {current_ftp} → {new_ftp}")
                        
                        # Initialize training_metrics if needed
                        if 'training_metrics' not in user_data:
                            user_data['training_metrics'] = {'version': 1}
                        
                        # Store previous FTP for rollback
                        previous_ftp = None
                        if 'ftp' in user_data['training_metrics']:
                            previous_ftp = user_data['training_metrics']['ftp'].copy()
                        
                        # Store new FTP with pending confirmation
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
                        
                        print(f"\n💾 Stored in training_metrics['ftp']:")
                        print(f"   value: {new_ftp}")
                        print(f"   source: FTP_TEST_DETECTION")
                        print(f"   user_confirmed: False")
                        print(f"   detected_from: {ftp_result['activity_name']}")
                        
                        # Save immediately
                        safe_save_user_data(athlete_id, user_data)
                        
                        print(f"\n✅ FTP data saved to DynamoDB")
                        print("="*70 + "\n")
                        
                        print(f"✅ New FTP {new_ftp}W stored (pending user confirmation)")
                        
                        # Flash message to user
                        from flask import flash
                        activity_name = ftp_result['activity_name']
                        flash(f'New FTP {new_ftp}W detected from {activity_name}! Please review in your dashboard.', 'info')
                else:
                    print(f"\n❌ Activity does not qualify for FTP calculation")
                    print(f"   (Not an FTP test or insufficient intensity)")
                    print("="*70 + "\n")
            else:
                print(f"\nℹ️  Skipping FTP detection: Activity type '{activity_type}' is not a cycling activity")

        # Generate feedback
        # Use plan_v2 if available, otherwise fall back to markdown
        if 'plan_v2' in user_data:
            from models.training_plan import TrainingPlan
            training_plan = TrainingPlan.from_dict(user_data['plan_v2'])
            print(f"✅ Using structured plan_v2 for feedback generation")
        else:
            training_plan = user_data.get('plan')
            print(f"ℹ️  Using markdown plan for feedback generation (plan_v2 not found)")
        
        # Pass incomplete_sessions_text prepared earlier
        if incomplete_sessions_text:
            print(f"DEBUG: Passing incomplete sessions to AI")
        
        # Prepare VDOT context for AI using vdot_context.py
        from utils.vdot_context import prepare_vdot_context
        
        print("\n" + "="*70)
        print("AI PROMPT PREPARATION - DEBUG LOG")
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
        print(f"   - Training plan: {'plan_v2' if 'plan_v2' in user_data else 'markdown'}")
        print(f"   - Analyzed sessions: {len(analyzed_sessions)}")
        print(f"   - Feedback log entries: {len(feedback_log)}")
        print(f"   - VDOT data: {'Yes' if vdot_data and vdot_data.get('current_vdot') else 'No'}")
        print(f"   - Garmin data: {'Yes' if garmin_data_for_activity else 'No'}")
        print("="*70 + "\n")
        
        # Generate feedback (now returns tuple: feedback_text, plan_update_json, change_summary)
        feedback_text, plan_update_json, change_summary = ai_service.generate_feedback(
            training_plan,
            feedback_log,
            analyzed_sessions,
            user_data.get('training_history'),
            garmin_data_for_activity,
            incomplete_sessions_text,
            vdot_data=vdot_data,
            athlete_profile=athlete_profile  # Pass lifestyle context and athlete type
        )
        
        print("\n" + "="*70)
        print("AI RESPONSE - DEBUG LOG")
        print("="*70)
        print(f"✅ AI response received ({len(feedback_text)} characters)")
        
        # Check if AI mentioned VDOT
        if 'VDOT' in feedback_text or 'vdot' in feedback_text.lower():
            print(f"\n🔍 AI mentioned VDOT in response")
            
            # Extract lines mentioning VDOT for debugging
            vdot_lines = [line.strip() for line in feedback_text.split('\n') 
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
            if re.search(r'calculate.*vdot|vdot.*calculate', feedback_text, re.IGNORECASE):
                print(f"\n⚠️  WARNING: AI used phrase 'calculate VDOT' - this should not happen!")
            
            if re.search(r'based on this.*vdot|vdot.*based on', feedback_text, re.IGNORECASE):
                print(f"\n⚠️  WARNING: AI said 'based on this, VDOT...' - might be calculating!")
            
            # Check if AI used the correct VDOT value
            if vdot_data and vdot_data.get('current_vdot'):
                expected_vdot = int(vdot_data['current_vdot'])
                if f"VDOT {expected_vdot}" in feedback_text or f"VDOT of {expected_vdot}" in feedback_text:
                    print(f"\n✅ AI correctly referenced VDOT {expected_vdot}")
                else:
                    print(f"\n⚠️  WARNING: Expected VDOT {expected_vdot} not found in response")
                    
                    # Look for other VDOT numbers
                    vdot_numbers = re.findall(r'VDOT[:\s]+(\d+)', feedback_text, re.IGNORECASE)
                    if vdot_numbers:
                        print(f"   Found these VDOT values instead: {', '.join(set(vdot_numbers))}")
        else:
            print(f"\nℹ️  AI did not mention VDOT (expected if no VDOT established)")
        
        print("="*70 + "\n")
        
        # === Parse AI response for session completion ===
        session_match = re.search(r'\[COMPLETED:([^\]]+)\]', feedback_text)
        if session_match and 'plan_v2' in user_data:
            session_id = session_match.group(1).strip()
            print(f"\n🤖 AI identified completed session: {session_id}")
            
            try:
                plan = user_data['plan_v2']
                
                print(f"   Plan has {len(plan['weeks'])} weeks")
                total_sessions = sum(len(w['sessions']) for w in plan['weeks'])
                print(f"   Total sessions: {total_sessions}")
                
                # Find the session by iterating through weeks
                matched_session = None
                matched_week_idx = None
                matched_session_idx = None
                
                print(f"   Searching for session: {session_id}")
                
                for week_idx, week in enumerate(plan['weeks']):
                    print(f"   Week {week_idx}: {len(week['sessions'])} sessions")
                    
                    for sess_idx, sess in enumerate(week['sessions']):
                        if sess['id'] == session_id:
                            matched_session = sess
                            matched_week_idx = week_idx
                            matched_session_idx = sess_idx
                            print(f"   ✅ FOUND matching session: {sess['id']}")
                            break
                    
                    if matched_session:
                        break
                
                if matched_session and not matched_session.get('completed', False):
                    # Mark session complete directly in dict
                    plan['weeks'][matched_week_idx]['sessions'][matched_session_idx]['completed'] = True
                    plan['weeks'][matched_week_idx]['sessions'][matched_session_idx]['strava_activity_id'] = str(analyzed_sessions[0]['id'])
                    plan['weeks'][matched_week_idx]['sessions'][matched_session_idx]['completed_at'] = analyzed_sessions[0]['start_date']
                    
                    # Save updated plan
                    user_data['plan_v2'] = plan
                    safe_save_user_data(athlete_id, user_data)
                    print(f"✅ Marked {session_id} complete and saved")
                
                elif matched_session and matched_session.get('completed', False):
                    print(f"ℹ️  Session {session_id} already completed")
                else:
                    print(f"⚠️  Session {session_id} not found in plan")
                    print(f"   Available session IDs:")
                    for week in plan['weeks']:
                        for sess in week['sessions']:
                            print(f"     - {sess['id']}")
                
                # Remove marker from feedback display
                feedback_text = re.sub(r'\[COMPLETED:[^\]]+\]', '', feedback_text).strip()
                
            except Exception as e:
                print(f"⚠️  Error marking session complete from AI: {e}")
                import traceback
                traceback.print_exc()
        elif session_match:
            print(f"ℹ️  AI identified session but no plan_v2 available")

        # Create descriptive name
        activity_names = [sess['name'] for sess in analyzed_sessions]
        if len(activity_names) == 1:
            descriptive_name = f"Feedback for: {activity_names[0]}"
        else:
            descriptive_name = ai_service.summarize_activities(activity_names)
            if not descriptive_name:
                descriptive_name = f"Feedback for activities: {', '.join(activity_names)}"
        
        all_activity_ids = [s['id'] for s in analyzed_sessions]

        # Create log entry
        new_log_entry = {
            "activity_id": int(analyzed_sessions[0]['id']),
            "activity_name": descriptive_name,
            "activity_date": format_activity_date(analyzed_sessions[0].get('start_date', '')),
            "feedback_markdown": feedback_text,  # Use feedback_text from tuple
            "logged_activity_ids": all_activity_ids
        }
        
        feedback_log.insert(0, new_log_entry)

        # === PLAN UPDATES FROM FEEDBACK ===
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
                print(f"✅ Plan updated via feedback")
                
                # Try to update plan_v2
                try:
                    # Get current plan_v2 as backup
                    current_plan_v2 = user_data.get('plan_v2')
                    
                    # CRITICAL: Extract completed sessions BEFORE parsing
                    existing_completed = {}  # session_id -> {completed, strava_activity_id, completed_at}
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
                    
                    # CRITICAL: Preserve plan_structure to maintain week dates
                    # Without this, weeks get None dates and cause strptime errors
                    plan_structure = user_data.get('plan_structure')
                    if plan_structure and 'weeks' in plan_structure:
                        print(f"   Preserving plan_structure with {len(plan_structure['weeks'])} weeks")
                        # Create AI response with JSON structure
                        import json
                        json_block = f"\n\n```json\n{json.dumps(plan_structure)}\n```"
                        ai_response_with_structure = new_plan_markdown + json_block
                        
                        plan_v2, _ = parse_ai_response_to_v2(
                            ai_response_with_structure,
                            athlete_id,
                            user_inputs
                        )
                    else:
                        # No plan_structure available - parse from markdown only
                        print(f"   ⚠️  No plan_structure found - parsing markdown only (dates may be None)")
                        plan_v2, _ = parse_ai_response_to_v2(
                            new_plan_markdown,
                            athlete_id,
                            user_inputs
                        )
                    
                    # Check if parsing succeeded
                    if plan_v2 and plan_v2.weeks:
                        total_sessions = sum(len(week.sessions) for week in plan_v2.weeks)
                        
                        if total_sessions > 0:
                            # SAFEGUARD: Archive and restore past weeks
                            from utils.plan_utils import archive_and_restore_past_weeks
                            plan_v2 = archive_and_restore_past_weeks(current_plan_v2, plan_v2)
                            
                            # CRITICAL: Restore completed status for matching sessions
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
                            print(f"   ✅ plan_v2 updated with {total_sessions} sessions")
                        else:
                            print(f"   ⚠️  Parser extracted 0 sessions - keeping existing plan_v2")
                    else:
                        print(f"   ⚠️  Failed to parse - keeping existing plan_v2")
                        
                except Exception as e:
                    print(f"   ⚠️  Error parsing: {e}")
                    print(f"      Keeping existing plan_v2")
            else:
                print(f"⚠️ [PLAN_UPDATED] marker found but no markdown code block")
        
        # CRITICAL: Verify plan_v2 integrity before saving
        if 'plan_v2' in user_data:
            plan_check = user_data['plan_v2']
            if isinstance(plan_check, dict):
                if 'weeks' not in plan_check or not plan_check['weeks']:
                    print(f"❌ CRITICAL: plan_v2 corrupted before final save!")
                    print(f"   plan_v2 has no weeks - RELOADING user_data to prevent corruption")
                    # Reload to get fresh copy
                    user_data = data_manager.load_user_data(athlete_id)
                    print(f"   ✅ Reloaded user_data")
                else:
                    # Check if weeks have sessions
                    try:
                        total_sessions = sum(len(w.get('sessions', [])) for w in plan_check['weeks'])
                        print(f"   ✅ plan_v2 integrity check passed: {len(plan_check['weeks'])} weeks, {total_sessions} sessions")
                    except Exception as e:
                        print(f"   ⚠️  Error checking sessions: {e}")
        
        safe_save_user_data(athlete_id, user_data)

        # Process feedback to extract plan updates for display
        processed_markdown, plan_html = process_feedback_markdown(feedback_text)
        feedback_html = render_markdown_with_toc(processed_markdown)['content']
        
        # Append plan HTML if it exists
        if plan_html:
            feedback_html += plan_html
        
        return jsonify({'feedback_html': feedback_html})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f"An error occurred: {e}"}), 500