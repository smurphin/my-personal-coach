from flask import Blueprint, render_template, request, redirect, session, jsonify, url_for, flash
from datetime import datetime, date, timedelta
import hashlib
import re
from data_manager import data_manager
from services.training_service import training_service
from services.ai_service import ai_service
from services.garmin_service import garmin_service
from markdown_manager import render_markdown_with_toc
from utils.decorators import login_required

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
        # Show a message that they're going with the flow
        message_html = '<div class="bg-brand-dark rounded-lg p-6 text-center"><h3 class="text-xl font-bold text-brand-blue mb-2">No Active Training Plan</h3><p class="text-brand-light-gray mb-4">You\'re currently going with the flow - no structured training plan is active.</p><p class="text-brand-light-gray mb-4">You can create a new plan anytime by clicking "Generate a New Plan" in the navigation.</p></div>'
        
        return render_template(
            'dashboard.html',
            current_week_plan=message_html,
            garmin_connected='garmin_credentials' in user_data,
            show_completion_prompt=False,
            plan_finished=False,
            no_active_plan=True
        )
    
    if not user_data or 'plan' not in user_data:
        return redirect('/onboarding')

    # Check if plan has finished
    plan_finished = False
    show_completion_prompt = False
    
    if 'plan' in user_data and user_data.get('plan'):
        is_finished, last_end_date = training_service.is_plan_finished(
            user_data['plan'],
            user_data.get('plan_structure')
        )
        plan_finished = is_finished
        
        # TEMPORARY: Force plan_finished for testing if flag is False and plan exists
        # Remove this after testing!
        if not plan_finished and not user_data.get('plan_completion_prompted', False):
            print("DEBUG: Temporarily forcing plan_finished=True for testing (remove this after testing!)")
            plan_finished = True
        
        # Debug check
        plan_completion_prompted = user_data.get('plan_completion_prompted', False)
        print(f"DEBUG: plan_finished={plan_finished}, last_end_date={last_end_date}, plan_completion_prompted={plan_completion_prompted}, show_prompt={plan_finished and not plan_completion_prompted}")
        
        # Show prompt if plan is finished and user hasn't been prompted yet
        if plan_finished and not plan_completion_prompted:
            show_completion_prompt = True
            print(f"DEBUG: Setting show_completion_prompt to True")

    current_week_text = training_service.get_current_week_plan(
        user_data['plan'],
        user_data.get('plan_structure')
    )
    current_week_html = render_markdown_with_toc(current_week_text)['content']

    # Check if Garmin is connected
    garmin_connected = 'garmin_credentials' in user_data

    # No chat display on dashboard - users can view full chat log separately
    return render_template(
        'dashboard.html',
        current_week_plan=current_week_html,
        garmin_connected=garmin_connected,
        show_completion_prompt=show_completion_prompt,
        plan_finished=plan_finished,
        no_active_plan=False
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
    training_plan = user_data.get('plan', 'No plan available.')
    feedback_log = user_data.get('feedback_log', [])

    ai_response_markdown = ai_service.generate_chat_response(
        training_plan,
        feedback_log,
        chat_history
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