from flask import Blueprint, render_template, request, redirect, session
from datetime import datetime, timedelta
import json
import re
from data_manager import data_manager
from services.strava_service import strava_service
from services.training_service import training_service
from services.ai_service import ai_service
from markdown_manager import render_markdown_with_toc
from utils.decorators import login_required
from utils.formatters import format_seconds

plan_bp = Blueprint('plan', __name__)

@plan_bp.route("/onboarding")
def onboarding():
    """Show the onboarding form"""
    return render_template("onboarding.html")

@plan_bp.route("/generate_plan", methods=['POST'])
@login_required
def generate_plan():
    """Generate a new training plan"""
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        
        if not user_data or 'token' not in user_data:
            return 'Could not find your session data. Please <a href="/login">log in</a> again.'

        # Archive existing plan if present
        if 'plan' in user_data and user_data.get('plan'):
            if 'feedback_log' not in user_data:
                user_data['feedback_log'] = []
            
            print(f"--- Found existing plan for athlete {athlete_id}. Generating summary... ---")
            
            # Generate summary of completed plan
            summary_text = ai_service.summarize_training_cycle(
                user_data['plan'],
                user_data['feedback_log']
            )
            
            # Store in training history
            training_history = user_data.get('training_history', [])
            training_history.insert(0, {"summary": summary_text})
            user_data['training_history'] = training_history
            
            # Archive the plan
            if 'archive' not in user_data:
                user_data['archive'] = []
            user_data['archive'].insert(0, {
                'plan': user_data['plan'],
                'feedback_log': user_data['feedback_log']
            })
            
            # Clear current plan data
            del user_data['plan']
            if 'feedback_log' in user_data:
                del user_data['feedback_log']
            if 'plan_structure' in user_data:
                del user_data['plan_structure']
        
        # Gather user inputs
        user_inputs = {
            'goal': request.form.get('user_goal'),
            'sessions_per_week': int(request.form.get('sessions_per_week')),
            'hours_per_week': float(request.form.get('hours_per_week')),
            'lifestyle_context': request.form.get('lifestyle_context'),
            'athlete_type': request.form.get('athlete_type'),
            'lthr': int(request.form.get('lthr')),
            'ftp': int(request.form.get('ftp'))
        }
        
        access_token = user_data['token']['access_token']

        print(f"--- Fetching Strava data for athlete {athlete_id} ---")
        
        # Fetch Strava data
        strava_zones = strava_service.get_athlete_zones(access_token)
        eight_weeks_ago = datetime.now() - timedelta(weeks=8)
        activities_summary = strava_service.get_recent_activities(
            access_token,
            int(eight_weeks_ago.timestamp()),
            per_page=200
        )
        athlete_stats = strava_service.get_athlete_stats(access_token, athlete_id)
        
        # Calculate training zones
        friel_hr_zones = training_service.calculate_friel_hr_zones(user_inputs['lthr'])
        friel_power_zones = training_service.calculate_friel_power_zones(user_inputs['ftp'])
        
        # Check for VDOT-ready race
        vdot_data = training_service.find_valid_race_for_vdot(
            activities_summary,
            access_token,
            friel_hr_zones,
            strava_service
        )
        
        # Analyze activities
        analyzed_activities = []
        one_week_ago = datetime.now() - timedelta(weeks=1)
        
        for activity_summary in activities_summary:
            activity_date = datetime.strptime(
                activity_summary['start_date_local'],
                "%Y-%m-%dT%H:%M:%SZ"
            )
            
            # Get detailed data for recent activities
            if activity_date > one_week_ago:
                activity_to_process = strava_service.get_activity_detail(
                    access_token,
                    activity_summary['id']
                )
            else:
                activity_to_process = activity_summary

            streams = strava_service.get_activity_streams(access_token, activity_to_process['id'])
            analyzed_activity = training_service.analyze_activity(
                activity_to_process,
                streams,
                {"heart_rate": friel_hr_zones, "power": friel_power_zones}
            )
            
            # Format time in zones
            for key, seconds in analyzed_activity["time_in_hr_zones"].items():
                analyzed_activity["time_in_hr_zones"][key] = format_seconds(seconds)
            
            analyzed_activities.append(analyzed_activity)

        # Prepare data for AI
        final_data_for_ai = {
            "athlete_goal": user_inputs['goal'],
            "sessions_per_week": user_inputs['sessions_per_week'],
            "hours_per_week": user_inputs['hours_per_week'],
            "lifestyle_context": user_inputs['lifestyle_context'],
            "athlete_type": user_inputs['athlete_type'],
            "athlete_stats": athlete_stats,
            "strava_zones": strava_zones,
            "friel_hr_zones": friel_hr_zones,
            "friel_power_zones": friel_power_zones,
            "vdot_data": vdot_data,
            "analyzed_activities": analyzed_activities
        }

        print("--- Generating content from Gemini ---")
        
        # Generate plan
        ai_response_text = ai_service.generate_training_plan(
            user_inputs,
            {
                'training_history': user_data.get('training_history'),
                'final_data_for_ai': final_data_for_ai
            }
        )

        # Extract plan structure JSON if present
        plan_structure = None
        plan_markdown = ai_response_text

        json_match = re.search(r"```json\n(.*?)```", ai_response_text, re.DOTALL)
        if json_match:
            json_string = json_match.group(1).strip()
            try:
                plan_structure = json.loads(json_string)
                plan_markdown = ai_response_text[:json_match.start()].strip()
                print(f"--- Successfully parsed plan structure from AI response. ---")
            except json.JSONDecodeError as e:
                print(f"--- ERROR: Could not decode JSON from AI response: {e} ---")
                plan_structure = None
        else:
            print("--- WARNING: No plan structure JSON block found in AI response. ---")

        # Save plan
        user_data['plan'] = plan_markdown
        user_data['plan_structure'] = plan_structure
        user_data['plan_data'] = final_data_for_ai
        
        data_manager.save_user_data(athlete_id, user_data)
        
        # Verify save
        print(f"--- APP: Verifying save operation by reloading data...")
        verified_user_data = data_manager.load_user_data(athlete_id)
        
        if 'plan' in verified_user_data:
            print(f"--- APP: SUCCESS! Reloaded data contains the plan.")
        else:
            print(f"--- APP: FAILURE! Reloaded data does NOT contain the plan.")
            return "Error: The plan was generated but could not be saved to the database. Please check the logs.", 500

        # Render and return plan
        rendered_plan = render_markdown_with_toc(plan_markdown)
        return render_template(
            'plan.html',
            plan_content=rendered_plan['content'],
            plan_toc=rendered_plan['toc']
        )
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"An error occurred during plan generation: {e}", 500

@plan_bp.route("/plan")
@login_required
def view_plan():
    """View the current training plan"""
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        
        if not user_data or 'plan' not in user_data:
            return 'No training plan found. Please <a href="/onboarding">generate a plan</a> first.'
        
        plan_text = user_data['plan']
        rendered_plan = render_markdown_with_toc(plan_text)
        
        return render_template(
            'plan.html',
            plan_content=rendered_plan['content'],
            plan_toc=rendered_plan['toc']
        )
    except Exception as e:
        return f"An error occurred while retrieving the plan: {e}", 500
