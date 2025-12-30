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
        if not training_plan:
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
            
            # Format time in zones
            for key, seconds in analyzed_session["time_in_hr_zones"].items():
                analyzed_session["time_in_hr_zones"][key] = format_seconds(seconds)
            
            analyzed_sessions.append(analyzed_session)

        if not analyzed_sessions:
            return jsonify({"message": "Found new activities, but could not analyze their details. Please try again."})

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

        # Generate feedback
        feedback_markdown = ai_service.generate_feedback(
            training_plan,
            feedback_log,
            analyzed_sessions,
            user_data.get('training_history'),
            garmin_data_for_activity
        )

        # Create descriptive name
        activity_names = [session['name'] for session in analyzed_sessions]
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
                print(f"✅ Plan updated via feedback")
            else:
                print(f"⚠️ [PLAN_UPDATED] marker found but no markdown code block")
        else:
            print(f"ℹ️ No plan update needed - marker not found")
        
        safe_save_user_data(athlete_id, user_data)

        # Process feedback to extract plan updates for display
        processed_markdown, plan_html = process_feedback_markdown(feedback_markdown)
        feedback_html = render_markdown_with_toc(processed_markdown)['content']
        
        # Append plan HTML if it exists
        if plan_html:
            feedback_html += plan_html
        
        return jsonify({'feedback_html': feedback_html})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f"An error occurred: {e}"}), 500