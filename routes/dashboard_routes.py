from flask import Blueprint, render_template, request, redirect, session, jsonify, url_for, flash
from datetime import datetime, date, timedelta
import hashlib
import re
import json
from data_manager import data_manager
from services.training_service import training_service
from services.ai_service import ai_service
from services.strava_service import strava_service
from services.garmin_service import garmin_service
from models.training_plan import TrainingMetrics, TrainingPlan
from markdown_manager import render_markdown_with_toc
from utils.decorators import login_required
from utils.s_and_c_utils import get_routine_link

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route("/")
def index():
    """Landing page / dashboard redirect"""
    if 'athlete_id' in session:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        
        if user_data and 'plan' in user_data:
            return redirect("/dashboard")
        elif user_data:
            return redirect("/onboarding")

    return render_template('index.html', athlete=None)

@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    """Display the main dashboard"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)

    # Check if user has no active plan (chose "go with the flow")
    if user_data and user_data.get('no_active_plan', False):
        # User has no active structured plan but should still see dashboard
        # Show a message that they're going with the flow with links to create a plan
        message_html = '''
        <div class="bg-brand-dark rounded-lg p-6">
            <h3 class="text-xl font-bold text-brand-blue mb-2 text-center">No Active Training Plan</h3>
            <p class="text-brand-light-gray text-center">You're currently going with the flow - no structured training plan is active.</p>
        </div>
        <div class="bg-brand-gray rounded-lg p-4 mt-4 space-y-3">
            <a href="/onboarding" style="text-decoration: none;" class="block w-full bg-brand-blue text-white font-bold py-3 px-6 rounded-lg shadow-lg hover:bg-brand-blue-hover transition-transform transform hover:scale-105 duration-300 ease-in-out text-center">
                Create a New Plan
            </a>
            <a href="/generate_maintenance_plan" style="text-decoration: none;" class="block w-full bg-brand-blue text-white font-bold py-3 px-6 rounded-lg shadow-lg hover:bg-brand-blue-hover transition-transform transform hover:scale-105 duration-300 ease-in-out text-center">
                Create a Maintenance Plan
            </a>
        </div>
        '''
        
        return render_template(
            'dashboard.html',
            current_week_plan=message_html,
            garmin_connected='garmin_credentials' in user_data,
            show_completion_prompt=False,
            plan_finished=False,
            no_active_plan=True,
            get_routine_link=get_routine_link
        )
    
    if not user_data or 'plan' not in user_data:
        return redirect('/onboarding')

    # Check if plan has finished
    plan_finished = False
    show_completion_prompt = False
    current_week_sessions = []
    current_week_html = None
    current_week_number = None
    current_week_start = None
    current_week_end = None
    
    # Load plan_v2 if available, otherwise fall back to markdown
    if 'plan_v2' in user_data:
        try:
            plan_v2 = TrainingPlan.from_dict(user_data['plan_v2'])
            
            # Mark sessions complete based on feedback_log
            # This works even without Strava access_token
            if 'feedback_log' in user_data and user_data['feedback_log']:
                try:
                    feedback_log = user_data['feedback_log']
                    
                    # Build map of activity_id → activity_date from feedback
                    activity_dates = {}
                    for feedback in feedback_log:
                        for activity_id in feedback.get('logged_activity_ids', []):
                            # Parse activity date from feedback
                            # Format: "04-01-2026 09:44:30"
                            try:
                                date_str = feedback.get('activity_date', '')
                                if date_str:
                                    fb_date = datetime.strptime(date_str, '%d-%m-%Y %H:%M:%S')
                                    activity_dates[activity_id] = fb_date.date().strftime('%Y-%m-%d')
                            except ValueError:
                                pass
                    
                    print(f"Found {len(activity_dates)} activities in feedback_log")
                    
                    # If we have Strava access, get activity types for accurate matching
                    activity_types = {}
                    access_token = user_data.get('access_token')
                    if access_token and strava_service:
                        try:
                            activities = strava_service.get_recent_activities(access_token, limit=100)
                            for activity in activities:
                                activity_types[activity['id']] = activity['type'].upper()
                            print(f"Fetched types for {len(activity_types)} activities from Strava")
                        except Exception as e:
                            print(f"Could not fetch from Strava: {e}")
                    
                    # Match activities to sessions
                    matched_count = 0
                    for activity_id, activity_date in activity_dates.items():
                        activity_type = activity_types.get(activity_id)
                        
                        # Find matching session
                        for week in plan_v2.weeks:
                            for plan_session in week.sessions:
                                if plan_session.completed:
                                    continue
                                
                                # Match by date
                                if plan_session.date == activity_date:
                                    # If we have type, verify it matches
                                    if activity_type:
                                        type_match = False
                                        if plan_session.type == 'RUN' and activity_type in ['RUN', 'TRAIL_RUN', 'VIRTUAL_RUN']:
                                            type_match = True
                                        elif plan_session.type == 'BIKE' and activity_type in ['RIDE', 'VIRTUAL_RIDE', 'EBIKERIDE']:
                                            type_match = True
                                        elif plan_session.type == 'SWIM' and activity_type == 'SWIM':
                                            type_match = True
                                        
                                        if not type_match:
                                            continue
                                    
                                    # Mark complete
                                    plan_session.mark_complete(activity_id, activity_date)
                                    matched_count += 1
                                    print(f"  ✓ Marked {plan_session.type} on {plan_session.date} complete")
                                    break  # Move to next activity
                    
                    # Save updated plan_v2 if any sessions marked
                    if matched_count > 0:
                        user_data['plan_v2'] = plan_v2.to_dict()
                        data_manager.save_user_data(athlete_id, user_data)
                        print(f"✓ Marked {matched_count} sessions complete from feedback")
                        
                except Exception as e:
                    print(f"⚠️  Error marking sessions from feedback: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Get current week's sessions
            today = date.today()
            for week in plan_v2.weeks:
                week_start = datetime.strptime(week.start_date, '%Y-%m-%d').date()
                week_end = datetime.strptime(week.end_date, '%Y-%m-%d').date()
                
                if week_start <= today <= week_end:
                    current_week_sessions = [s.to_dict() for s in week.sessions]
                    current_week_number = week.week_number
                    current_week_start = week_start.strftime('%d %b')
                    current_week_end = week_end.strftime('%d %b')
                    break
            
            # Check if plan is finished
            if plan_v2.weeks:
                last_week = plan_v2.weeks[-1]
                last_end = datetime.strptime(last_week.end_date, '%Y-%m-%d').date()
                plan_finished = today > last_end
            
        except Exception as e:
            print(f"Error loading plan_v2: {e}")
            # Fall back to markdown
            current_week_sessions = []
    
    # Fallback to markdown if no plan_v2 or error
    if not current_week_sessions and 'plan' in user_data and user_data.get('plan'):
        is_finished, last_end_date = training_service.is_plan_finished(
            user_data['plan'],
            user_data.get('plan_structure')
        )
        plan_finished = is_finished
        
        current_week_text = training_service.get_current_week_plan(
            user_data['plan'],
            user_data.get('plan_structure')
        )
        current_week_html = render_markdown_with_toc(current_week_text)['content']
    
    # Debug check
    plan_completion_prompted = user_data.get('plan_completion_prompted', False)
    
    # Show prompt if plan is finished and user hasn't been prompted yet
    if plan_finished and not plan_completion_prompted:
        show_completion_prompt = True

    # Check if Garmin is connected
    garmin_connected = 'garmin_credentials' in user_data

    # Extract training metrics for display - work directly with dict (THIS WORKS)
    vdot = None
    vdot_paces = None
    lthr = None
    ftp = None
    
    if 'training_metrics' in user_data:
        try:
            metrics_dict = user_data['training_metrics']
            
            # VDOT (as integer, rounded down) and paces
            if 'vdot' in metrics_dict and metrics_dict['vdot']:
                vdot_data = metrics_dict['vdot']
                if isinstance(vdot_data, dict) and 'value' in vdot_data:
                    vdot = int(vdot_data['value'])
                    vdot_paces = vdot_data.get('paces')
                    
                    # Calculate paces if not stored
                    if not vdot_paces and vdot:
                        try:
                            from utils.vdot_calculator import VDOTCalculator
                            calc = VDOTCalculator()
                            vdot_paces = calc.get_training_paces(vdot)
                        except Exception as e:
                            print(f"Could not calculate VDOT paces: {e}")
            
            # LTHR
            if 'lthr' in metrics_dict and metrics_dict['lthr']:
                lthr_data = metrics_dict['lthr']
                if isinstance(lthr_data, dict) and 'value' in lthr_data:
                    lthr = int(lthr_data['value'])
            
            # FTP
            if 'ftp' in metrics_dict and metrics_dict['ftp']:
                ftp_data = metrics_dict['ftp']
                if isinstance(ftp_data, dict) and 'value' in ftp_data:
                    ftp = int(ftp_data['value'])
                    
            print(f"Dashboard metrics loaded: VDOT={vdot}, LTHR={lthr}, FTP={ftp}")
        except Exception as e:
            print(f"Error loading training metrics: {e}")
            import traceback
            traceback.print_exc()

    # No chat display on dashboard - users can view full chat log separately
    return render_template(
        'dashboard.html',
        current_week_plan=current_week_html,
        current_week_sessions=current_week_sessions,
        current_week_number=current_week_number,
        current_week_start=current_week_start,
        current_week_end=current_week_end,
        garmin_connected=garmin_connected,
        show_completion_prompt=show_completion_prompt,
        plan_finished=plan_finished,
        no_active_plan=False,
        vdot=vdot,
        vdot_paces=vdot_paces,
        lthr=lthr,
        ftp=ftp,
        get_routine_link=get_routine_link
    )

@dashboard_bp.route("/chat", methods=['POST'])
@login_required
def chat():
    """Handle chat messages with the AI coach"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    user_message = request.form.get('user_message')

    if not user_message:
        return redirect('/dashboard')

    # Load chat history
    chat_history = user_data.get('chat_log', [])

    # Add user message
    chat_history.append({
        'role': 'user',
        'content': user_message,
        'timestamp': datetime.now().isoformat()
    })

    # Generate AI response
    # Prefer plan_v2 for structured data, fallback to markdown
    if 'plan_v2' in user_data:
        try:
            plan_v2 = TrainingPlan.from_dict(user_data['plan_v2'])
            training_plan = plan_v2  # Pass structured plan
        except Exception as e:
            print(f"Error loading plan_v2 for chat: {e}")
            training_plan = user_data.get('plan', 'No plan available.')
    else:
        training_plan = user_data.get('plan', 'No plan available.')
    
    feedback_log = user_data.get('feedback_log', [])
    
    # Prepare VDOT context using vdot_context.py helper
    from utils.vdot_context import prepare_vdot_context
    vdot_data = prepare_vdot_context(user_data)

    ai_response_markdown = ai_service.generate_chat_response(
        training_plan,
        feedback_log,
        chat_history,
        vdot_data=vdot_data
    )

    # Add AI response
    chat_history.append({
        'role': 'model',
        'content': ai_response_markdown,
        'timestamp': datetime.now().isoformat()
    })
    user_data['chat_log'] = chat_history

    # Check for plan update in response
    match = re.search(r"```markdown\n(.*?)```", ai_response_markdown, re.DOTALL)
    if match:
        new_plan_markdown = match.group(1).strip()
        user_data['plan'] = new_plan_markdown
        print(f"--- Plan updated via chat! ---")
        print(f"--- New plan length: {len(new_plan_markdown)} characters ---")
        
        # Try to update plan_v2 with changes
        try:
            # Get current plan_v2 as backup
            current_plan_v2 = user_data.get('plan_v2')
            
            # Try parsing the updated markdown
            from utils.migration import parse_ai_response_to_v2
            
            user_inputs = {
                'goal': user_data.get('goal', ''),
                'goal_date': user_data.get('goal_date'),
                'plan_start_date': user_data.get('plan_start_date'),
                'goal_distance': user_data.get('goal_distance')
            }
            
            # Don't attach old plan_structure - let it parse fresh from markdown
            plan_v2, _ = parse_ai_response_to_v2(
                new_plan_markdown,
                athlete_id,
                user_inputs
            )
            
            # Check if parsing was successful
            if plan_v2 and plan_v2.weeks:
                total_sessions = sum(len(week.sessions) for week in plan_v2.weeks)
                
                if total_sessions > 0:
                    # Parsing worked! Update plan_v2
                    user_data['plan_v2'] = plan_v2.to_dict()
                    print(f"✅ plan_v2 updated with {total_sessions} sessions")
                else:
                    # Parsing failed - keep existing plan_v2
                    print(f"⚠️  Parser extracted 0 sessions from updated markdown")
                    print(f"   Keeping existing plan_v2 (likely AI update doesn't match session format)")
                    print(f"   Markdown updated, plan_v2 preserved")
            else:
                print(f"⚠️  Failed to parse updated plan - keeping existing plan_v2")
                
        except Exception as e:
            print(f"⚠️  Error parsing plan update: {e}")
            print(f"   Keeping existing plan_v2")
            # Keep existing plan_v2 - don't break session tracking

        # Invalidate weekly summary cache
        today = datetime.now()
        week_identifier = f"{today.year}-{today.isocalendar().week}"
        if 'weekly_summaries' in user_data and week_identifier in user_data['weekly_summaries']:
            del user_data['weekly_summaries'][week_identifier]
            print(f"--- Invalidated weekly summary cache for {week_identifier}. ---")

    data_manager.save_user_data(athlete_id, user_data)

    # Don't store in session/flash - chat is already saved in DynamoDB
    # Redirect to chat log to see the response
    return redirect('/chat_log')

@dashboard_bp.route("/chat_log")
@login_required
def chat_log_list():
    """Display all chat conversations"""
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        chat_history = user_data.get('chat_log', [])

        # Convert markdown to HTML
        for message in chat_history:
            if message.get('role') == 'model' and 'content' in message:
                try:
                    message['content'] = render_markdown_with_toc(message['content'])['content']
                except Exception as e:
                    print(f"Error rendering markdown for message: {e}")

        return render_template('chat_log.html', chat_history=chat_history)
    except Exception as e:
        print(f"Error in chat_log route: {e}")
        import traceback
        traceback.print_exc()
        return f"Error loading chat log: {str(e)}", 500

@dashboard_bp.route("/clear_chat", methods=['POST'])
@login_required
def clear_chat():
    """Permanently delete all chat history"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    if 'chat_log' in user_data:
        del user_data['chat_log']
    if 'chat_archive' in user_data:
        del user_data['chat_archive']
        
    data_manager.save_user_data(athlete_id, user_data)
    
    flash("Your chat history has been permanently deleted.")
        
    return redirect(request.referrer or url_for('dashboard.dashboard'))

@dashboard_bp.route("/api/weekly-summary")
@login_required
def weekly_summary_api():
    """API endpoint for weekly summary with smart caching (6-hour window)"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    # Handle case where user has no active plan ("go with the flow" mode)
    if user_data and user_data.get('no_active_plan', False):
        print("--- User in 'go with the flow' mode - generating health-focused summary ---")
        
        # Generate a summary focused on health metrics and recent activity
        try:
            # Fetch latest Garmin data
            garmin_data = None
            try:
                garmin_data = garmin_service.fetch_yesterday_data(user_data)
            except Exception as e:
                print(f"Warning: Could not fetch Garmin data: {e}")
            
            # Use a simpler prompt for no-plan mode
            summary_prompt = f"""Today is {datetime.now().strftime("%A, %B %d, %Y")}.

The athlete is currently taking a break from structured training and going with the flow. They have no active training plan.

Please provide a brief, encouraging weekly summary focusing on:
1. Their recent health and recovery metrics (if available)
2. General encouragement to stay active and listen to their body
3. Remind them they can create a new plan anytime they're ready

Keep it positive, brief (2-3 paragraphs), and supportive.

"""
            
            if garmin_data:
                summary_prompt += f"\n\nRecent Health Metrics:\n{json.dumps(garmin_data, indent=2)}"
            
            weekly_summary = ai_service.generate_content(summary_prompt)
            
            if not weekly_summary or not weekly_summary.strip():
                # Fallback message if AI fails
                weekly_summary = """**Taking a Break** 🌊

You're currently going with the flow without a structured training plan. This is a great time to focus on activities you enjoy, maintain your fitness, and listen to your body.

When you're ready to get back to structured training, you can create a new plan or set up a maintenance plan anytime."""
            
            return jsonify({
                'summary': weekly_summary,
                'cached': False,
                'no_plan_mode': True,
                'generated_at': datetime.now().isoformat()
            })
            
        except Exception as e:
            print(f"ERROR generating no-plan summary: {e}")
            import traceback
            traceback.print_exc()
            weekly_summary = """**Taking a Break** 🌊

You're currently going with the flow without a structured training plan. When you're ready, you can create a new plan anytime."""
            return jsonify({
                'summary': weekly_summary,
                'cached': False,
                'error': True,
                'no_plan_mode': True
            })
    
    if not user_data or 'plan' not in user_data:
        return jsonify({"error": "Plan not found"}), 404

    now = datetime.now()
    week_identifier = f"{now.year}-{now.isocalendar().week}"
    current_plan_hash = hashlib.sha256(user_data['plan'].encode()).hexdigest()
    
    feedback_log = user_data.get('feedback_log', [])
    chat_log = user_data.get('chat_log', [])
    
    latest_chat_timestamp = chat_log[-1]['timestamp'] if chat_log else None
    latest_feedback_id = feedback_log[0]['activity_id'] if feedback_log else None

    if 'weekly_summaries' not in user_data:
        user_data['weekly_summaries'] = {}

    cached_summary_data = user_data['weekly_summaries'].get(week_identifier)
    force_refresh = request.args.get('force', 'false').lower() == 'true'
    weekly_summary = None
    cache_age_hours = None

    # Check if refresh is needed
    if force_refresh:
        print("CACHE: Manual force refresh requested.")
    elif not cached_summary_data:
        print("CACHE: No summary found. Forcing refresh.")
        force_refresh = True
    else:
        # Defensive: check if cached data has 'summary' key (old cache format might not)
        weekly_summary = cached_summary_data.get('summary')
        if not weekly_summary:
            print("CACHE: Old cache format detected (no 'summary' key). Forcing refresh.")
            force_refresh = True
        else:
            # Check if cache is still valid
            try:
                cached_timestamp = datetime.fromisoformat(cached_summary_data.get('timestamp'))
                cache_age_hours = (now - cached_timestamp).total_seconds() / 3600
                
                # Use 6-hour cache window instead of 24 hours
                if cache_age_hours > 6:
                    print(f"CACHE: Summary is {cache_age_hours:.1f} hours old (>6 hour threshold). Forcing refresh.")
                    force_refresh = True
                elif cached_summary_data.get('plan_hash') != current_plan_hash:
                    print("CACHE: Plan updated. Forcing refresh.")
                    force_refresh = True
                elif cached_summary_data.get('last_feedback_id') != latest_feedback_id:
                    print("CACHE: New feedback added. Forcing refresh.")
                    force_refresh = True
                elif cached_summary_data.get('last_chat_timestamp') != latest_chat_timestamp:
                    print("CACHE: New chat message added. Forcing refresh.")
                    force_refresh = True
                else:
                    print(f"CACHE: Using cached summary (age: {cache_age_hours:.1f}h).")
            except Exception as e:
                print(f"CACHE: Error checking cache validity: {e}. Forcing refresh.")
                force_refresh = True

    if force_refresh:
        print("CACHE: Generating new summary from AI.")
        try:
            current_week_text = training_service.get_current_week_plan(user_data['plan'])
            
            # Fetch latest Garmin data
            garmin_data = None
            try:
                garmin_data = garmin_service.fetch_yesterday_data(user_data)
            except Exception as e:
                print(f"Warning: Could not fetch Garmin data: {e}")
            
            # Generate summary with AI
            weekly_summary = ai_service.generate_weekly_summary(
                current_week_text,
                user_data.get('plan_data', {}).get('athlete_goal', 'your goal'),
                feedback_log[0].get('feedback_markdown') if feedback_log else None,
                chat_log,
                garmin_data
            )
            
            if not weekly_summary or not weekly_summary.strip():
                raise Exception("AI returned empty summary")
                
            # Save summary to cache
            user_data['weekly_summaries'][week_identifier] = {
                'summary': weekly_summary,
                'timestamp': now.isoformat(),
                'plan_hash': current_plan_hash,
                'last_feedback_id': latest_feedback_id,
                'last_chat_timestamp': latest_chat_timestamp
            }
            data_manager.save_user_data(athlete_id, user_data)
            print("CACHE: Successfully generated and saved new summary.")
            
            return jsonify({
                'summary': weekly_summary,
                'cached': False,
                'generated_at': now.isoformat()
            })
            
        except Exception as e:
            print(f"ERROR generating weekly summary: {e}")
            import traceback
            traceback.print_exc()
            weekly_summary = "Unable to generate summary at this time. Please try refreshing in a moment."
            return jsonify({
                'summary': weekly_summary,
                'cached': False,
                'error': True
            })
    else:
        return jsonify({
            'summary': weekly_summary,
            'cached': True,
            'age_hours': round(cache_age_hours, 1) if cache_age_hours else None,
            'generated_at': cached_summary_data.get('timestamp')
        })

@dashboard_bp.route("/api/refresh-weekly-summary", methods=['POST'])
@login_required
def refresh_weekly_summary():
    """Clear weekly summary cache"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    today = datetime.now()
    week_identifier = f"{today.year}-{today.isocalendar().week}"

    if 'weekly_summaries' in user_data and week_identifier in user_data['weekly_summaries']:
        del user_data['weekly_summaries'][week_identifier]
        data_manager.save_user_data(athlete_id, user_data)
        return jsonify({'status': 'success', 'message': 'Cache cleared.'})
        
    return jsonify({'status': 'no_op', 'message': 'No cache to clear.'})

@dashboard_bp.route("/settings", methods=['GET'])
@login_required
def settings():
    """Display user settings page"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    # Extract training metrics - work directly with dict (THIS WORKS)
    vdot = lthr = ftp = None
    vdot_source = lthr_source = ftp_source = None
    vdot_date = lthr_date = ftp_date = None
    vdot_paces = None
    
    if 'training_metrics' in user_data:
        metrics_dict = user_data['training_metrics']
        
        # Extract VDOT (as integer) and paces
        if 'vdot' in metrics_dict and metrics_dict['vdot']:
            vdot_data = metrics_dict['vdot']
            if isinstance(vdot_data, dict) and 'value' in vdot_data:
                vdot = int(vdot_data['value'])  # Always integer, rounded down
                vdot_source = vdot_data.get('source')
                vdot_date = vdot_data.get('date_set')
                vdot_paces = vdot_data.get('paces')  # May be None if not calculated yet
                
                # Calculate paces if not stored
                if not vdot_paces and vdot:
                    try:
                        from utils.vdot_calculator import VDOTCalculator
                        calc = VDOTCalculator()
                        vdot_paces = calc.get_training_paces(vdot)
                    except Exception as e:
                        print(f"Could not calculate VDOT paces: {e}")
        
        # Extract LTHR
        if 'lthr' in metrics_dict and metrics_dict['lthr']:
            lthr_data = metrics_dict['lthr']
            if isinstance(lthr_data, dict) and 'value' in lthr_data:
                lthr = lthr_data.get('value')
                lthr_source = lthr_data.get('source')
                lthr_date = lthr_data.get('date_set')
        
        # Extract FTP
        if 'ftp' in metrics_dict and metrics_dict['ftp']:
            ftp_data = metrics_dict['ftp']
            if isinstance(ftp_data, dict) and 'value' in ftp_data:
                ftp = ftp_data.get('value')
                ftp_source = ftp_data.get('source')
                ftp_date = ftp_data.get('date_set')
    
    # Extract lifestyle context
    lifestyle = user_data.get('lifestyle', {})
    
    return render_template(
        'settings.html',
        lifestyle=lifestyle,
        vdot=vdot,
        vdot_source=vdot_source,
        vdot_date=vdot_date,
        vdot_paces=vdot_paces,
        lthr=lthr,
        lthr_source=lthr_source,
        lthr_date=lthr_date,
        ftp=ftp,
        ftp_source=ftp_source,
        ftp_date=ftp_date
    )

@dashboard_bp.route("/settings/update", methods=['POST'])
@login_required
def update_settings():
    """Update user settings"""
    from flask import flash
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    # Get existing lifestyle or create new
    existing_lifestyle = user_data.get('lifestyle', {})
    
    # Update lifestyle context - preserve athlete_type if not provided in form
    lifestyle = {
        'work_pattern': request.form.get('work_pattern', ''),
        'family_commitments': request.form.get('family_commitments', ''),
        'training_constraints': request.form.get('training_constraints', ''),
        'athlete_type': request.form.get('athlete_type', existing_lifestyle.get('athlete_type', 'DISCIPLINARIAN'))
    }
    user_data['lifestyle'] = lifestyle
    
    # Update training metrics - work directly with dict (THIS WORKS)
    try:
        # Get existing metrics or create new dict
        if 'training_metrics' not in user_data:
            user_data['training_metrics'] = {'version': 1}
        
        metrics_dict = user_data['training_metrics']
        
        # Update VDOT if provided (as integer, rounded DOWN)
        vdot_value = request.form.get('vdot')
        if vdot_value and vdot_value.strip():
            try:
                vdot_float = float(vdot_value)
                vdot_int = int(vdot_float)  # Always round DOWN
                
                # Calculate training paces using VDOTCalculator
                try:
                    from utils.vdot_calculator import VDOTCalculator
                    calc = VDOTCalculator()
                    paces = calc.get_training_paces(vdot_int)
                except Exception as e:
                    print(f"Warning: Could not calculate VDOT paces: {e}")
                    paces = None
                
                metrics_dict['vdot'] = {
                    'value': vdot_int,
                    'source': 'USER_OVERRIDE',
                    'date_set': datetime.now().isoformat(),
                    'user_confirmed': True,
                    'pending_confirmation': False,
                    'paces': paces  # Store ALL paces from Jack Daniels' tables
                }
                print(f"Updated VDOT: {vdot_float} → {vdot_int} (rounded down)")
                if paces:
                    print(f"  Stored {len(paces)} paces from Jack Daniels' tables")
                flash(f'VDOT updated to {vdot_int}', 'success')
            except ValueError:
                flash('Invalid VDOT value', 'error')
        
        # Update LTHR if provided
        lthr_value = request.form.get('lthr')
        if lthr_value and lthr_value.strip():
            try:
                lthr_int = int(lthr_value)
                metrics_dict['lthr'] = {
                    'value': lthr_int,
                    'source': 'USER_OVERRIDE',
                    'date_set': datetime.now().isoformat(),
                    'user_confirmed': True
                }
                print(f"Updated LTHR: {lthr_int}")
            except ValueError:
                flash('Invalid LTHR value', 'error')
        
        # Update FTP if provided
        ftp_value = request.form.get('ftp')
        if ftp_value and ftp_value.strip():
            try:
                ftp_int = int(ftp_value)
                metrics_dict['ftp'] = {
                    'value': ftp_int,
                    'source': 'USER_OVERRIDE',
                    'date_set': datetime.now().isoformat(),
                    'user_confirmed': True
                }
                print(f"Updated FTP: {ftp_int}")
            except ValueError:
                flash('Invalid FTP value', 'error')
        
        user_data['training_metrics'] = metrics_dict
        
    except Exception as e:
        print(f"Error updating metrics: {e}")
        import traceback
        traceback.print_exc()
        flash('Error updating training metrics', 'error')
    
    data_manager.save_user_data(athlete_id, user_data)
    flash('Settings updated successfully!', 'success')
    
    return redirect('/settings')

@dashboard_bp.route("/confirm_vdot", methods=['POST'])
@login_required
def confirm_vdot():
    """Confirm or deny pending VDOT update"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    action = request.form.get('action')  # 'accept' or 'deny'
    
    if 'training_metrics' in user_data and 'vdot' in user_data['training_metrics']:
        vdot_data = user_data['training_metrics']['vdot']
        
        if action == 'accept':
            vdot_data['user_confirmed'] = True
            vdot_data['pending_confirmation'] = False
            flash(f"VDOT {vdot_data['value']} confirmed!", 'success')
            print(f"✅ User confirmed VDOT {vdot_data['value']}")
        
        elif action == 'deny':
            # Remove the pending VDOT, restore previous if it exists
            old_vdot = vdot_data.get('previous_value')
            if old_vdot:
                # Restore previous VDOT
                user_data['training_metrics']['vdot'] = old_vdot
                flash('VDOT update rejected, previous value restored', 'info')
            else:
                # No previous, just delete
                del user_data['training_metrics']['vdot']
                flash('VDOT update rejected', 'info')
            print(f"❌ User rejected VDOT update")
        
        data_manager.save_user_data(athlete_id, user_data)
    
    return redirect('/dashboard')