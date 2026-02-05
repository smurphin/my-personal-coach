from flask import Blueprint, render_template, request, redirect, session, jsonify, url_for, flash
from datetime import datetime, date, timedelta
import hashlib
import os
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

from utils.plan_utils import archive_and_restore_past_weeks

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
    week_day_statuses = []
    
    # Load plan_v2 if available, otherwise fall back to markdown
    if 'plan_v2' in user_data:
        try:
            plan_v2 = TrainingPlan.from_dict(user_data['plan_v2'])
            
            # AUTO-REPARSE: Check if plan_v2 has weeks with 0 sessions (parsing issue)
            # This can happen if the parser didn't match a new format
            weeks_with_no_sessions = [w for w in plan_v2.weeks if len(w.sessions) == 0]
            if weeks_with_no_sessions and 'plan' in user_data:
                week_numbers = [w.week_number for w in weeks_with_no_sessions]
                print(f"⚠️  Detected {len(weeks_with_no_sessions)} weeks with 0 sessions: {week_numbers}")
                print(f"   Attempting to reparse from markdown...")
                
                try:
                    from utils.migration import migrate_plan_to_v2
                    plan_data = user_data.get('plan_data', {})
                    user_inputs = {
                        'goal': user_data.get('goal', ''),
                        'goal_date': user_data.get('goal_date'),
                        'plan_start_date': user_data.get('plan_start_date'),
                        'goal_distance': user_data.get('goal_distance')
                    }
                    
                    # Reparse from markdown
                    reparsed_plan = migrate_plan_to_v2(
                        user_data['plan'],
                        plan_data,
                        athlete_id,
                        user_inputs
                    )
                    
                    # Check if reparse improved the plan
                    reparsed_weeks_with_sessions = sum(1 for w in reparsed_plan.weeks if len(w.sessions) > 0)
                    original_weeks_with_sessions = sum(1 for w in plan_v2.weeks if len(w.sessions) > 0)
                    reparsed_total_sessions = sum(len(w.sessions) for w in reparsed_plan.weeks)
                    original_total_sessions = sum(len(w.sessions) for w in plan_v2.weeks)
                    
                    # Check if we're fixing empty weeks (any empty week in original that now has sessions)
                    original_empty_weeks = {w.week_number for w in plan_v2.weeks if len(w.sessions) == 0}
                    reparsed_fixed_weeks = {w.week_number for w in reparsed_plan.weeks if w.week_number in original_empty_weeks and len(w.sessions) > 0}
                    fixing_empty_weeks = len(reparsed_fixed_weeks) > 0
                    
                    # Accept reparse if:
                    # 1. It doesn't reduce weeks with sessions (structural integrity)
                    # 2. AND either: total sessions increased OR we're fixing empty weeks (improved parsing quality)
                    weeks_improved = reparsed_weeks_with_sessions >= original_weeks_with_sessions
                    sessions_improved = reparsed_total_sessions > original_total_sessions
                    
                    if weeks_improved and (sessions_improved or fixing_empty_weeks):
                        if fixing_empty_weeks:
                            print(f"   📊 Fixed {len(reparsed_fixed_weeks)} empty weeks: {sorted(reparsed_fixed_weeks)}")
                        # Reparse was successful - update plan_v2
                        # Preserve completed sessions from original plan_v2
                        existing_completed = {}
                        for week in plan_v2.weeks:
                            for sess in week.sessions:
                                if sess.completed:
                                    existing_completed[sess.id] = {
                                        'completed': True,
                                        'strava_activity_id': sess.strava_activity_id if hasattr(sess, 'strava_activity_id') else None,
                                        'completed_at': sess.completed_at if hasattr(sess, 'completed_at') else None
                                    }
                        
                        # Restore completed sessions in reparsed plan
                        restored_count = 0
                        for week in reparsed_plan.weeks:
                            for sess in week.sessions:
                                if sess.id in existing_completed:
                                    sess.completed = True
                                    sess.strava_activity_id = existing_completed[sess.id]['strava_activity_id']
                                    sess.completed_at = existing_completed[sess.id]['completed_at']
                                    restored_count += 1
                        
                        # Update plan_v2
                        plan_v2 = reparsed_plan
                        user_data['plan_v2'] = plan_v2.to_dict()
                        data_manager.save_user_data(athlete_id, user_data)
                        print(f"   ✅ Reparse successful! Restored {restored_count} completed sessions")
                        print(f"   📊 Now have {reparsed_weeks_with_sessions} weeks with sessions (was {original_weeks_with_sessions})")
                    else:
                        print(f"   ⚠️  Reparse didn't improve session count - keeping original plan_v2")
                        
                except Exception as e:
                    print(f"   ❌ Reparse failed: {e}")
                    import traceback
                    traceback.print_exc()
            
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
            current_week_obj = None
            for week in plan_v2.weeks:
                # Check if dates exist before parsing
                if not week.start_date or not week.end_date:
                    print(f"   ⚠️  Week {week.week_number} has missing dates - skipping date-based check")
                    continue
                
                week_start = datetime.strptime(week.start_date, '%Y-%m-%d').date()
                week_end = datetime.strptime(week.end_date, '%Y-%m-%d').date()
                
                if week_start <= today <= week_end:
                    current_week_sessions = [s.to_dict() for s in week.sessions]
                    current_week_number = week.week_number
                    current_week_start = week_start.strftime('%d %b')
                    current_week_end = week_end.strftime('%d %b')
                    current_week_obj = week
                    break
            
            # Fallback: If no current week found by date, try using get_current_week() method
            # This uses the same logic but might handle edge cases better
            if not current_week_obj:
                try:
                    current_week_obj = plan_v2.get_current_week()
                    if current_week_obj:
                        current_week_sessions = [s.to_dict() for s in current_week_obj.sessions]
                        current_week_number = current_week_obj.week_number
                        if current_week_obj.start_date and current_week_obj.end_date:
                            week_start = datetime.strptime(current_week_obj.start_date, '%Y-%m-%d').date()
                            week_end = datetime.strptime(current_week_obj.end_date, '%Y-%m-%d').date()
                            current_week_start = week_start.strftime('%d %b')
                            current_week_end = week_end.strftime('%d %b')
                        print(f"   ✓ Found current week {current_week_number} using get_current_week()")
                except Exception as e:
                    print(f"   ⚠️  get_current_week() also failed: {e}")
            
            # Final fallback: Use first week with sessions if still no current week found
            if not current_week_obj and plan_v2.weeks:
                for week in plan_v2.weeks:
                    if week.sessions:  # Has at least one session
                        current_week_obj = week
                        current_week_sessions = [s.to_dict() for s in week.sessions]
                        current_week_number = week.week_number
                        if week.start_date and week.end_date:
                            week_start = datetime.strptime(week.start_date, '%Y-%m-%d').date()
                            week_end = datetime.strptime(week.end_date, '%Y-%m-%d').date()
                            current_week_start = week_start.strftime('%d %b')
                            current_week_end = week_end.strftime('%d %b')
                        print(f"   ⚠️  Using fallback: Week {current_week_number} (first week with sessions)")
                        break
            
            # Debug: Log what we found
            if current_week_obj:
                print(f"   ✓ Current week found: Week {current_week_number} with {len(current_week_sessions)} sessions")
            else:
                print(f"   ⚠️  No current week found - plan_v2 has {len(plan_v2.weeks)} weeks")
            
            # Calculate day statuses for weekly plan tile (not needed for new tile, but keeping for compatibility)
            week_day_statuses = []
            if current_week_obj and current_week_obj.start_date:
                week_start_date = datetime.strptime(current_week_obj.start_date, '%Y-%m-%d').date()
                for i in range(7):
                    day_date = week_start_date + timedelta(days=i)
                    day_date_str = day_date.isoformat()
                    
                    # Find session for this day
                    day_session = None
                    for plan_session in current_week_obj.sessions:
                        if plan_session.date == day_date_str:
                            day_session = plan_session
                            break
                    
                    week_day_statuses.append({
                        'date': day_date_str,
                        'date_obj': day_date,
                        'is_today': day_date == today,
                        'is_past': day_date < today,
                        'is_future': day_date > today,
                        'session': day_session.to_dict() if day_session else None
                    })
            
            # Check if plan is finished
            if plan_v2.weeks:
                last_week = plan_v2.weeks[-1]
                if last_week.end_date:  # Only check if date exists
                    last_end = datetime.strptime(last_week.end_date, '%Y-%m-%d').date()
                    plan_finished = today >= last_end  # Use >= so plan ending today is considered finished
                    print(f"   DEBUG: Plan completion check - today={today}, last_end={last_end}, plan_finished={plan_finished}")
                else:
                    # If last week has no end date, assume plan is not finished
                    print("   ⚠️  Last week has no end_date - cannot determine if plan is finished")
                    plan_finished = False
            else:
                print("   ⚠️  plan_v2 has no weeks - cannot determine if plan is finished")
                plan_finished = False
            
        except Exception as e:
            print(f"Error loading plan_v2: {e}")
            import traceback
            traceback.print_exc()
            # Fall back to markdown
            current_week_sessions = []
            # Try to get current week number from plan_v2_obj if available
            if 'plan_v2_obj' in locals() and plan_v2_obj:
                try:
                    current_week_obj = plan_v2_obj.get_current_week()
                    if current_week_obj:
                        current_week_number = current_week_obj.week_number
                except:
                    pass
    
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
    print(f"   DEBUG: plan_finished={plan_finished}, plan_completion_prompted={plan_completion_prompted}, will_show_prompt={plan_finished and not plan_completion_prompted}")
    if plan_finished and not plan_completion_prompted:
        show_completion_prompt = True
        print(f"   ✓ Setting show_completion_prompt=True")

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

    # Pass plan_v2 for weekly plan tile if available
    plan_v2_obj = None
    if 'plan_v2' in user_data:
        try:
            plan_v2_obj = TrainingPlan.from_dict(user_data['plan_v2'])
        except Exception as e:
            print(f"Error loading plan_v2 for template: {e}")
    
    # Check for pending VDOT confirmation
    pending_vdot = None
    if 'training_metrics' in user_data:
        metrics_dict = user_data['training_metrics']
        if 'vdot' in metrics_dict and metrics_dict['vdot']:
            vdot_data = metrics_dict['vdot']
            if isinstance(vdot_data, dict):
                # Check if VDOT is pending confirmation
                user_confirmed = vdot_data.get('user_confirmed', True)  # Default to True for backwards compat
                pending_confirmation = vdot_data.get('pending_confirmation', False)
                
                if not user_confirmed or pending_confirmation:
                    # VDOT needs user confirmation
                    pending_vdot = vdot_data.copy()
                    print(f"📋 Pending VDOT detected: {pending_vdot.get('value')} (needs user confirmation)")
    
    # Check for pending FTP confirmation
    pending_ftp = None
    if 'training_metrics' in user_data:
        metrics_dict = user_data['training_metrics']
        if 'ftp' in metrics_dict and metrics_dict['ftp']:
            ftp_data = metrics_dict['ftp']
            if isinstance(ftp_data, dict):
                # Check if FTP is pending confirmation
                user_confirmed = ftp_data.get('user_confirmed', True)  # Default to True for backwards compat
                pending_confirmation = ftp_data.get('pending_confirmation', False)
                
                if not user_confirmed or pending_confirmation:
                    # FTP needs user confirmation
                    pending_ftp = ftp_data.copy()
                    print(f"📋 Pending FTP detected: {pending_ftp.get('value')}W (needs user confirmation)")
    
    # Get unit preferences for dashboard display
    unit_prefs = user_data.get('unit_preferences', {
        'run': 'km',
        'ride': 'km',
        'swim': 'meters'
    })
    
    # No chat display on dashboard - users can view full chat log separately
    return render_template(
        'dashboard.html',
        current_week_plan=current_week_html,
        current_week_sessions=current_week_sessions,
        current_week_number=current_week_number,
        current_week_start=current_week_start,
        current_week_end=current_week_end,
        plan_v2=plan_v2_obj,
        week_day_statuses=week_day_statuses if 'week_day_statuses' in locals() else [],
        garmin_connected=garmin_connected,
        show_completion_prompt=show_completion_prompt,
        plan_finished=plan_finished,
        no_active_plan=False,
        vdot=vdot,
        vdot_paces=vdot_paces,
        lthr=lthr,
        ftp=ftp,
        pending_vdot=pending_vdot,
        pending_ftp=pending_ftp,
        get_routine_link=get_routine_link,
        unit_prefs=unit_prefs
    )

@dashboard_bp.route("/chat", methods=['POST'])
@login_required
def chat():
    """Handle chat messages with the AI coach"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    user_message = request.form.get('user_message')

    # Load athlete profile for lifestyle context and athlete type
    athlete_profile = user_data.get('athlete_profile', {})
    if not athlete_profile:
        # Fallback to legacy plan_data if profile doesn't exist
        plan_data = user_data.get('plan_data', {})
        athlete_profile = {
            'lifestyle_context': plan_data.get('lifestyle_context'),
            'athlete_type': plan_data.get('athlete_type')
        }

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

    # Attach unit preferences to athlete_profile so AI can use them for units
    unit_prefs = user_data.get('unit_preferences', {
        'run': 'km',
        'ride': 'km',
        'swim': 'meters'
    })
    if isinstance(athlete_profile, dict):
        athlete_profile = {**athlete_profile, 'unit_preferences': unit_prefs}

    ai_response_markdown, plan_update_json, change_summary = ai_service.generate_chat_response(
        training_plan,
        feedback_log,
        chat_history,
        vdot_data=vdot_data,
        athlete_profile=athlete_profile  # Includes lifestyle, type, and unit preferences
    )

    # CRITICAL: Ensure we're not storing raw JSON - extract response_text if needed
    # This is a safety check in case extraction in generate_chat_response failed
    if isinstance(ai_response_markdown, str):
        if (ai_response_markdown.strip().startswith('{') or ai_response_markdown.strip().startswith('```')) and 'response_text' in ai_response_markdown:
            print(f"⚠️  WARNING: ai_response_markdown still looks like JSON, attempting extraction...")
            try:
                import json
                # Try to extract from markdown code block or direct JSON
                if ai_response_markdown.strip().startswith('```'):
                    json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', ai_response_markdown, re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group(1))
                        if isinstance(parsed, dict) and 'response_text' in parsed:
                            ai_response_markdown = parsed.get('response_text', ai_response_markdown)
                            # Also extract change_summary and plan_update_json if not already set
                            if not change_summary:
                                change_summary = parsed.get('change_summary_markdown') or parsed.get('change_summary')
                            if not plan_update_json and 'plan_v2' in parsed:
                                from utils.plan_validator import validate_and_load_plan_v2
                                validated_plan, _ = validate_and_load_plan_v2(parsed['plan_v2'])
                                if validated_plan:
                                    plan_update_json = validated_plan.to_dict()
                            print(f"✅ Extracted response_text from JSON in chat route (safety check)")
                else:
                    parsed = json.loads(ai_response_markdown.strip())
                    if isinstance(parsed, dict) and 'response_text' in parsed:
                        ai_response_markdown = parsed.get('response_text', ai_response_markdown)
                        if not change_summary:
                            change_summary = parsed.get('change_summary_markdown') or parsed.get('change_summary')
                        if not plan_update_json and 'plan_v2' in parsed:
                            from utils.plan_validator import validate_and_load_plan_v2
                            validated_plan, _ = validate_and_load_plan_v2(parsed['plan_v2'])
                            if validated_plan:
                                plan_update_json = validated_plan.to_dict()
                        print(f"✅ Extracted response_text from JSON in chat route (safety check)")
            except Exception as e:
                print(f"⚠️  Safety check extraction failed: {e}")

    # If we have a structured change summary from the AI, append it to the response
    # so the athlete clearly sees what changed in their plan.
    # Format response with change summary AFTER the message if available
    if change_summary:
        summary_section = "\n\n---\n\n### Plan Update Summary\n\n" + change_summary.strip()
        combined_response = ai_response_markdown + summary_section
    else:
        combined_response = ai_response_markdown
    
    # CRITICAL: Convert escape sequences to actual characters
    # The AI sometimes returns literal \n\n instead of actual newlines
    # This happens when the response is serialized/deserialized or contains raw escape sequences
    if isinstance(combined_response, str):
        # Handle Python-style escape sequences (e.g., \n, \t)
        # First, try to decode unicode escapes safely
        try:
            # Replace common escape sequences
            combined_response = combined_response.replace('\\n', '\n')
            combined_response = combined_response.replace('\\t', '\t')
            combined_response = combined_response.replace('\\r', '\r')
            # Handle escaped backslashes (\\ becomes \)
            combined_response = combined_response.replace('\\\\', '\\')
        except Exception as e:
            print(f"⚠️  Error processing escape sequences: {e}")

    # Add AI response
    chat_history.append({
        'role': 'model',
        'content': combined_response,
        'timestamp': datetime.now().isoformat()
    })
    user_data['chat_log'] = chat_history

    # NEW: Handle JSON-first plan updates (preferred method)
    if plan_update_json:
        print(f"✅ Found JSON plan update in chat response!")
        print(f"   Plan has {len(plan_update_json.get('weeks', []))} weeks")
        
        # Get current plan_v2 as backup for archiving
        current_plan_v2_dict = user_data.get('plan_v2')
        
        # SAFEGUARD: Archive and restore past weeks
        try:
            # #region agent log
            try:
                import json as _json
                _log_entry = {
                    "sessionId": "debug-session",
                    "runId": "chat-json-update",
                    "hypothesisId": "H1",
                    "location": "routes/dashboard_routes.py:chat:before_archive",
                    "message": "Processing JSON plan update from chat",
                    "data": {
                        "has_current_plan_v2": bool(current_plan_v2_dict),
                        "new_weeks": len(plan_update_json.get("weeks", []))
                    },
                    "timestamp": int(datetime.now().timestamp() * 1000),
                }
                with open("/home/darrenmurphy/git/.cursor/debug.log", "a") as _f:
                    _f.write(_json.dumps(_log_entry) + "\n")
            except Exception:
                pass
            # #endregion agent log

            new_plan_v2_obj = TrainingPlan.from_dict(plan_update_json)
            if current_plan_v2_dict:
                # current_plan_v2_dict is the raw dict; archive_and_restore_past_weeks
                # expects the dict and will construct its own TrainingPlan object
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
            
            # CRITICAL: Archive old plan BEFORE overwriting (same as feedback/api_routes)
            if 'plan' in user_data and user_data.get('plan'):
                if 'archive' not in user_data:
                    user_data['archive'] = []
                user_data['archive'].insert(0, {
                    'plan': user_data['plan'],
                    'plan_v2': user_data.get('plan_v2'),
                    'completed_date': datetime.now().isoformat(),
                    'reason': 'regenerated_via_chat_json'
                })
                print(f"📦 Archived old plan before chat JSON update (archive now has {len(user_data['archive'])} entries)")
            
            # Update plan_v2
            user_data['plan_v2'] = new_plan_v2_obj.to_dict()
            
            # Also update markdown plan for backward compatibility
            user_data['plan'] = new_plan_v2_obj.to_markdown()
            
            # Store change summary for display
            if change_summary:
                user_data['last_plan_change_summary'] = change_summary
                print(f"   📋 Change summary: {change_summary[:100]}...")
           
            # #region agent log
            try:
                import json as _json
                _log_entry = {
                    "sessionId": "debug-session",
                    "runId": "chat-json-update",
                    "hypothesisId": "H1",
                    "location": "routes/dashboard_routes.py:chat:after_archive",
                    "message": "Applied JSON plan update from chat",
                    "data": {
                        "final_weeks": len(new_plan_v2_obj.weeks),
                        "final_sessions": sum(len(w.sessions) for w in new_plan_v2_obj.weeks),
                    },
                    "timestamp": int(datetime.now().timestamp() * 1000),
                }
                with open("/home/darrenmurphy/git/.cursor/debug.log", "a") as _f:
                    _f.write(_json.dumps(_log_entry) + "\n")
            except Exception:
                pass
            # #endregion agent log

            print(f"--- Plan updated via JSON! ---")
            print(f"--- New plan has {len(new_plan_v2_obj.weeks)} weeks with {sum(len(w.sessions) for w in new_plan_v2_obj.weeks)} sessions ---")
            
        except Exception as e:
            print(f"⚠️  Error processing JSON plan update: {e}")
            import traceback
            traceback.print_exc()
            # Fall through to markdown parsing as fallback
    
    # FALLBACK: Check for markdown plan update in response (legacy support)
    if not plan_update_json:
        print(f"🔍 Checking for markdown plan update in chat response (length: {len(combined_response)} chars)")
        match = re.search(r"```\s*markdown\s*\n(.*?)```", combined_response, re.DOTALL)
        if not match:
            # Try without requiring newline after markdown keyword
            match = re.search(r"```\s*markdown\s+(.*?)```", combined_response, re.DOTALL)
        if not match:
            # Try with any whitespace
            match = re.search(r"```\s*markdown\s*(.*?)```", combined_response, re.DOTALL)
        
        if match:
            print(f"✅ Found markdown plan update in chat response!")
            new_plan_markdown = match.group(1).strip()
            # CRITICAL: Archive old plan BEFORE overwriting (same as feedback/api_routes)
            if 'plan' in user_data and user_data.get('plan'):
                if 'archive' not in user_data:
                    user_data['archive'] = []
                user_data['archive'].insert(0, {
                    'plan': user_data['plan'],
                    'plan_v2': user_data.get('plan_v2'),
                    'completed_date': datetime.now().isoformat(),
                    'reason': 'regenerated_via_chat_markdown'
                })
                print(f"📦 Archived old plan before chat markdown update (archive now has {len(user_data['archive'])} entries)")
            user_data['plan'] = new_plan_markdown
            print(f"--- Plan updated via markdown (legacy)! ---")
            print(f"--- New plan length: {len(new_plan_markdown)} characters ---")
            
            # Try to update plan_v2 with changes (only if we found a markdown update)
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
                    if existing_completed:
                        print(f"   📋 Preserving {len(existing_completed)} completed sessions")
                
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
                        # SAFEGUARD: Archive and restore past weeks
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
                        
                        # Parsing worked! Update plan_v2
                        user_data['plan_v2'] = plan_v2.to_dict()
                        final_week_count = len(plan_v2.weeks)
                        print(f"✅ plan_v2 updated with {final_week_count} weeks ({total_sessions} sessions)")
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

    from routes.api_routes import safe_save_user_data
    safe_save_user_data(athlete_id, user_data)

    # Don't store in session/flash - chat is already saved in DynamoDB
    # Redirect to chat log to see the response
    return redirect('/chat_log')

def _get_timestamp(msg):
    """Sort key for chat messages (oldest first)."""
    return msg.get('timestamp') or ''


@dashboard_bp.route("/chat_log")
@login_required
def chat_log_list():
    """Display all chat conversations. Merges DynamoDB (recent) with S3 (older) when archived."""
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        recent = user_data.get('chat_log', [])

        # Load older messages from S3 if archived (load whenever key is set, so staging works too)
        older_from_s3 = []
        s3_key = user_data.get('chat_log_s3_key')
        if s3_key:
            try:
                from s3_manager import s3_manager, S3_AVAILABLE
                if S3_AVAILABLE:
                    older_from_s3 = s3_manager.load_large_data(s3_key) or []
            except Exception as e:
                print(f"⚠️  Error loading chat archive from S3: {e}")

        # Merge: older (S3) + recent (DynamoDB), sort by timestamp
        all_messages = list(older_from_s3) + list(recent)
        all_messages.sort(key=_get_timestamp)

        # Pagination: ?older=N means show last (30+N) messages
        older_offset = request.args.get('older', 0, type=int)
        older_offset = max(0, min(older_offset, max(0, len(all_messages) - 30)))
        show_count = 30 + older_offset
        chat_history = all_messages[-show_count:] if all_messages else []
        has_older = len(all_messages) > len(chat_history)
        next_older_offset = older_offset + 30 if has_older else older_offset

        # Convert markdown to HTML
        for message in chat_history:
            if message.get('role') == 'model' and 'content' in message:
                try:
                    content = message['content']
                    
                    # Extract response_text from JSON if stored as JSON (similar to feedback extraction)
                    if isinstance(content, str):
                        content_str = content.strip()
                        # Check if it's JSON wrapped in markdown code blocks or plain JSON
                        if (content_str.startswith('```') or content_str.startswith('{')) and 'response_text' in content_str:
                            print(f"🔍 Detected JSON in stored chat message, extracting response_text...")
                            try:
                                # Handle markdown code blocks
                                if content_str.startswith('```'):
                                    json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', content_str, re.DOTALL)
                                    if json_match:
                                        parsed = json.loads(json_match.group(1))
                                        if isinstance(parsed, dict) and 'response_text' in parsed:
                                            content = parsed.get('response_text', content)
                                            print(f"✅ Extracted response_text from markdown-wrapped JSON")
                                else:
                                    # Try direct JSON parse
                                    parsed = json.loads(content_str)
                                    if isinstance(parsed, dict) and 'response_text' in parsed:
                                        content = parsed.get('response_text', content)
                                        print(f"✅ Extracted response_text from JSON")
                            except Exception as e:
                                print(f"⚠️  Failed to extract JSON from chat message: {e}")
                    
                    # CRITICAL: Convert escape sequences to actual characters before rendering
                    # Handle Python-style escape sequences (e.g., \n, \t)
                    if isinstance(content, str):
                        content = content.replace('\\n', '\n')
                        content = content.replace('\\t', '\t')
                        content = content.replace('\\r', '\r')
                        # Handle escaped backslashes (\\ becomes \)
                        content = content.replace('\\\\', '\\')
                    message['content'] = render_markdown_with_toc(content)['content']
                except Exception as e:
                    print(f"Error rendering markdown for message: {e}")

        return render_template(
            'chat_log.html',
            chat_history=chat_history,
            has_older=has_older,
            next_older_offset=next_older_offset,
        )
    except Exception as e:
        print(f"Error in chat_log route: {e}")
        import traceback
        traceback.print_exc()
        return f"Error loading chat log: {str(e)}", 500

@dashboard_bp.route("/clear_chat", methods=['POST'])
@login_required
def clear_chat():
    """Permanently delete all chat history (DynamoDB and S3 archive)."""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)

    if 'chat_log' in user_data:
        del user_data['chat_log']
    if 'chat_archive' in user_data:
        del user_data['chat_archive']
    if 'chat_log_s3_key' in user_data:
        try:
            from s3_manager import s3_manager, S3_AVAILABLE
            if S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production':
                s3_manager.delete_large_data(user_data['chat_log_s3_key'])
        except Exception as e:
            print(f"⚠️  Error deleting chat archive from S3: {e}")
        del user_data['chat_log_s3_key']

    from routes.api_routes import safe_save_user_data
    safe_save_user_data(athlete_id, user_data)

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
            
            # Prepare VDOT context for AI (to prevent AI from using old/incorrect VDOT values)
            from utils.vdot_context import prepare_vdot_context
            vdot_data = prepare_vdot_context(user_data, debug=False)  # Disable debug to reduce noise
            
            # Generate summary with AI
            weekly_summary = ai_service.generate_weekly_summary(
                current_week_text,
                user_data.get('plan_data', {}).get('athlete_goal', 'your goal'),
                feedback_log[0].get('feedback_markdown') if feedback_log else None,
                chat_log,
                garmin_data,
                vdot_data=vdot_data  # Pass VDOT data to prevent AI from using old values
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
    
    # Extract lifestyle context from athlete_profile
    # Onboarding stores: athlete_profile.lifestyle_context (combined field)
    # Settings needs: lifestyle.training_constraints for display
    lifestyle = {}
    if 'athlete_profile' in user_data:
        profile = user_data['athlete_profile']
        print(f"DEBUG Settings GET: athlete_profile exists")
        print(f"  athlete_type in profile: {profile.get('athlete_type', 'NOT SET')}")
        lifestyle = {
            'work_pattern': '',  # Not stored separately anymore
            'family_commitments': '',  # Not stored separately anymore
            'training_constraints': profile.get('lifestyle_context', ''),  # Display combined field
            'athlete_type': profile.get('athlete_type', 'IMPROVISER')  # Use saved value
        }
        print(f"  athlete_type being sent to template: {lifestyle['athlete_type']}")
    elif 'lifestyle' in user_data:
        # Fallback to old structure if it exists
        print(f"DEBUG Settings GET: Using legacy lifestyle structure")
        lifestyle = user_data['lifestyle']
        print(f"  athlete_type from legacy: {lifestyle.get('athlete_type', 'NOT SET')}")
    else:
        # No profile at all - use defaults
        print(f"DEBUG Settings GET: No profile found, using defaults")
        lifestyle = {
            'work_pattern': '',
            'family_commitments': '',
            'training_constraints': '',
            'athlete_type': 'IMPROVISER'
        }
    
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
        ftp_date=ftp_date,
        unit_prefs=user_data.get('unit_preferences', {
            'run': 'km',
            'ride': 'km',
            'swim': 'meters'
        })
    )

@dashboard_bp.route("/settings/update", methods=['POST'])
@login_required
def update_settings():
    """Update user settings"""
    from flask import flash
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    # Update athlete_profile with lifestyle context
    # Combine all three fields back into lifestyle_context
    work_pattern = request.form.get('work_pattern', '').strip()
    family_commitments = request.form.get('family_commitments', '').strip()
    training_constraints = request.form.get('training_constraints', '').strip()
    athlete_type = request.form.get('athlete_type', 'IMPROVISER')
    
    # Combine into single lifestyle_context field
    context_parts = []
    if work_pattern:
        context_parts.append(f"Work: {work_pattern}")
    if family_commitments:
        context_parts.append(f"Family: {family_commitments}")
    if training_constraints:
        context_parts.append(training_constraints)
    
    lifestyle_context = "\n\n".join(context_parts) if context_parts else None
    
    # Save to athlete_profile (matches onboarding structure)
    user_data['athlete_profile'] = {
        'lifestyle_context': lifestyle_context,
        'athlete_type': athlete_type,
        'updated_at': datetime.now().isoformat()
    }
    
    # Also save to legacy lifestyle structure for backward compatibility
    lifestyle = {
        'work_pattern': work_pattern,
        'family_commitments': family_commitments,
        'training_constraints': training_constraints,
        'athlete_type': athlete_type
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

                # Only update and flash if VDOT actually changed
                existing_vdot = None
                try:
                    if 'vdot' in metrics_dict and isinstance(metrics_dict['vdot'], dict):
                        existing_vdot = int(metrics_dict['vdot'].get('value')) if metrics_dict['vdot'].get('value') is not None else None
                except (ValueError, TypeError):
                    existing_vdot = None

                if existing_vdot != vdot_int:
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
    
    # === Unit preferences (per sport) ===
    # Simple per-sport unit preferences so AI can answer in the athlete's preferred units.
    unit_run = request.form.get('unit_run', 'km')
    unit_ride = request.form.get('unit_ride', 'km')
    unit_swim = request.form.get('unit_swim', 'meters')
    user_data['unit_preferences'] = {
        'run': unit_run,
        'ride': unit_ride,
        'swim': unit_swim
    }
    print(f"🔧 DEBUG: Setting unit_preferences for athlete {athlete_id}: {user_data['unit_preferences']}")
    
    try:
        data_manager.save_user_data(athlete_id, user_data)
        print(f"✅ DEBUG: Successfully saved unit_preferences to database for athlete {athlete_id}")
        # Verify it was saved by loading it back
        verify_data = data_manager.load_user_data(athlete_id)
        if 'unit_preferences' in verify_data:
            print(f"✅ DEBUG: Verified unit_preferences in DB: {verify_data.get('unit_preferences')}")
        else:
            print(f"⚠️  DEBUG: WARNING - unit_preferences NOT found in DB after save!")
    except Exception as e:
        print(f"❌ DEBUG: Error saving unit_preferences for athlete {athlete_id}: {e}")
        import traceback
        traceback.print_exc()
        flash('Error saving unit preferences', 'error')
    
    flash('Settings updated successfully!', 'success')
    
    return redirect('/settings')

@dashboard_bp.route("/confirm_vdot", methods=['POST'])
@login_required
def confirm_vdot():
    """Confirm or deny pending VDOT update"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    action = request.form.get('action')  # 'accept' or 'deny'
    rejection_reason = request.form.get('rejection_reason', '').strip()  # Optional reason
    
    if 'training_metrics' in user_data and 'vdot' in user_data['training_metrics']:
        vdot_data = user_data['training_metrics']['vdot']
        
        if action == 'accept':
            vdot_data['user_confirmed'] = True
            vdot_data['pending_confirmation'] = False
            flash(f"VDOT {vdot_data['value']} confirmed!", 'success')
            print(f"✅ User confirmed VDOT {vdot_data['value']}")
        
        elif action == 'deny':
            # Store rejection info before removing the pending VDOT
            rejected_vdot = vdot_data.get('value')
            detected_from = vdot_data.get('detected_from', {})
            
            # Initialize vdot_rejections array if it doesn't exist
            if 'training_metrics' not in user_data:
                user_data['training_metrics'] = {'version': 1}
            if 'vdot_rejections' not in user_data['training_metrics']:
                user_data['training_metrics']['vdot_rejections'] = []
            
            # Store rejection info
            rejection_info = {
                'rejected_vdot': rejected_vdot,
                'rejected_at': datetime.now().isoformat(),
                'detected_from': {
                    'activity_id': detected_from.get('activity_id'),
                    'activity_name': detected_from.get('activity_name', 'Unknown'),
                    'distance': detected_from.get('distance'),
                    'distance_meters': detected_from.get('distance_meters'),
                    'time_seconds': detected_from.get('time_seconds'),
                    'is_race': detected_from.get('is_race', False),
                    'intensity_reason': detected_from.get('intensity_reason')
                }
            }
            
            if rejection_reason:
                rejection_info['user_reason'] = rejection_reason
            
            # Add to rejections array (keep last 10 rejections)
            user_data['training_metrics']['vdot_rejections'].append(rejection_info)
            if len(user_data['training_metrics']['vdot_rejections']) > 10:
                user_data['training_metrics']['vdot_rejections'] = user_data['training_metrics']['vdot_rejections'][-10:]
            
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
            
            print(f"❌ User rejected VDOT {rejected_vdot} from {detected_from.get('activity_name', 'Unknown')}")
            if rejection_reason:
                print(f"   Reason: {rejection_reason}")
        
        data_manager.save_user_data(athlete_id, user_data)
    
    return redirect('/dashboard')

@dashboard_bp.route("/confirm_ftp", methods=['POST'])
@login_required
def confirm_ftp():
    """Confirm or deny pending FTP update"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    action = request.form.get('action')  # 'accept' or 'deny'
    rejection_reason = request.form.get('rejection_reason', '').strip()  # Optional reason
    
    if 'training_metrics' in user_data and 'ftp' in user_data['training_metrics']:
        ftp_data = user_data['training_metrics']['ftp']
        
        if action == 'accept':
            ftp_data['user_confirmed'] = True
            ftp_data['pending_confirmation'] = False
            flash(f"FTP {ftp_data['value']}W confirmed!", 'success')
            print(f"✅ User confirmed FTP {ftp_data['value']}W")
        
        elif action == 'deny':
            # Store rejection info before removing the pending FTP
            rejected_ftp = ftp_data.get('value')
            detected_from = ftp_data.get('detected_from', {})
            
            # Initialize ftp_rejections array if it doesn't exist
            if 'training_metrics' not in user_data:
                user_data['training_metrics'] = {'version': 1}
            if 'ftp_rejections' not in user_data['training_metrics']:
                user_data['training_metrics']['ftp_rejections'] = []
            
            # Store rejection info
            rejection_info = {
                'rejected_ftp': rejected_ftp,
                'rejected_at': datetime.now().isoformat(),
                'detected_from': {
                    'activity_id': detected_from.get('activity_id'),
                    'activity_name': detected_from.get('activity_name', 'Unknown'),
                    'test_duration': detected_from.get('test_duration'),
                    'average_power': detected_from.get('average_power'),
                    'is_ftp_test': detected_from.get('is_ftp_test', False),
                    'intensity_reason': detected_from.get('intensity_reason')
                }
            }
            
            if rejection_reason:
                rejection_info['user_reason'] = rejection_reason
            
            # Add to rejections array (keep last 10 rejections)
            user_data['training_metrics']['ftp_rejections'].append(rejection_info)
            if len(user_data['training_metrics']['ftp_rejections']) > 10:
                user_data['training_metrics']['ftp_rejections'] = user_data['training_metrics']['ftp_rejections'][-10:]
            
            # Remove the pending FTP, restore previous if it exists
            old_ftp = ftp_data.get('previous_value')
            if old_ftp:
                # Restore previous FTP
                user_data['training_metrics']['ftp'] = old_ftp
                flash('FTP update rejected, previous value restored', 'info')
            else:
                # No previous, just delete
                del user_data['training_metrics']['ftp']
                flash('FTP update rejected', 'info')
            
            print(f"❌ User rejected FTP {rejected_ftp}W from {detected_from.get('activity_name', 'Unknown')}")
            if rejection_reason:
                print(f"   Reason: {rejection_reason}")
        
        data_manager.save_user_data(athlete_id, user_data)
    
    return redirect('/dashboard')

@dashboard_bp.route("/reparse_plan", methods=['POST'])
@login_required
def reparse_plan():
    """Manually trigger reparse of plan from markdown to plan_v2"""
    athlete_id = session.get('athlete_id')
    if not athlete_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    user_data = data_manager.load_user_data(athlete_id)
    if not user_data or 'plan' not in user_data:
        return jsonify({'success': False, 'error': 'No plan found to reparse'}), 400
    
    try:
        from utils.migration import migrate_plan_to_v2
        
        plan_data = user_data.get('plan_data', {})
        user_inputs = {
            'goal': user_data.get('goal', ''),
            'goal_date': user_data.get('goal_date'),
            'plan_start_date': user_data.get('plan_start_date'),
            'goal_distance': user_data.get('goal_distance')
        }
        
        # Preserve completed sessions from existing plan_v2
        existing_completed = {}
        if 'plan_v2' in user_data:
            try:
                old_plan_v2 = TrainingPlan.from_dict(user_data['plan_v2'])
                for week in old_plan_v2.weeks:
                    for sess in week.sessions:
                        if sess.completed:
                            existing_completed[sess.id] = {
                                'completed': True,
                                'strava_activity_id': sess.strava_activity_id if hasattr(sess, 'strava_activity_id') else None,
                                'completed_at': sess.completed_at if hasattr(sess, 'completed_at') else None
                            }
            except Exception as e:
                print(f"⚠️  Could not load existing plan_v2 for preservation: {e}")
        
        # Reparse from markdown
        reparsed_plan = migrate_plan_to_v2(
            user_data['plan'],
            plan_data,
            athlete_id,
            user_inputs
        )
        
        # Restore completed sessions
        restored_count = 0
        for week in reparsed_plan.weeks:
            for sess in week.sessions:
                if sess.id in existing_completed:
                    sess.completed = True
                    sess.strava_activity_id = existing_completed[sess.id]['strava_activity_id']
                    sess.completed_at = existing_completed[sess.id]['completed_at']
                    restored_count += 1
        
        # Archive old plan before replacing (so reparse can be rolled back)
        if 'plan' in user_data and user_data.get('plan'):
            if 'archive' not in user_data:
                user_data['archive'] = []
            user_data['archive'].insert(0, {
                'plan': user_data['plan'],
                'plan_v2': user_data.get('plan_v2'),
                'completed_date': datetime.now().isoformat(),
                'reason': 'reparse_plan_v2'
            })
            print(f"📦 Archived old plan before reparse (archive now has {len(user_data['archive'])} entries)")
        
        # Update plan_v2
        user_data['plan_v2'] = reparsed_plan.to_dict()
        data_manager.save_user_data(athlete_id, user_data)
        
        total_sessions = sum(len(w.sessions) for w in reparsed_plan.weeks)
        weeks_with_sessions = sum(1 for w in reparsed_plan.weeks if len(w.sessions) > 0)
        
        return jsonify({
            'success': True,
            'message': f'Plan reparsed successfully',
            'stats': {
                'weeks': len(reparsed_plan.weeks),
                'weeks_with_sessions': weeks_with_sessions,
                'total_sessions': total_sessions,
                'restored_completed': restored_count
            }
        })
        
    except Exception as e:
        print(f"❌ Reparse failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
