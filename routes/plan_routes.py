from flask import Blueprint, render_template, request, redirect, session, flash
from datetime import datetime, timedelta
import json
import re
from dateutil import parser as date_parser
from data_manager import data_manager
from services.strava_service import strava_service
from services.training_service import training_service
from services.ai_service import ai_service
from services.vdot_detection_service import vdot_detection_service
from models.training_plan import TrainingMetrics
from markdown_manager import render_markdown_with_toc
from utils.decorators import login_required
from utils.formatters import format_seconds
from utils.migration import parse_ai_response_to_v2
from utils.s_and_c_utils import get_routine_link, load_default_s_and_c_library, process_s_and_c_session
from utils.vdot_context import prepare_vdot_context

plan_bp = Blueprint('plan', __name__)


def get_next_monday(include_partial_week=False):
    """
    Get the date of the next Monday (or today if today is Monday)
    
    Args:
        include_partial_week: If True and today is not Monday, returns today
                             to allow for a partial "Week 0"
    
    Returns:
        date: The start date for the plan
    """
    today = datetime.now().date()
    
    if include_partial_week and today.weekday() != 0:
        # Start today to avoid wasting days
        return today
    
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:  # Today is Monday
        return today
    return today + timedelta(days=days_until_monday)


def calculate_weeks_until_goal(goal_date_str, start_date=None, include_partial_week=True):
    """
    Calculate the number of complete weeks from start date to goal date.
    
    Args:
        goal_date_str: Goal date as string (various formats supported)
        start_date: Optional start date (defaults to next Monday, or today if include_partial_week)
        include_partial_week: If True, starts today (creates Week 0 if not Monday)
    
    Returns:
        tuple: (weeks_count, start_date_str, goal_date_str, has_partial_week, days_in_partial_week) 
               or (None, None, None, False, 0)
    """
    try:
        # Parse the goal date - be flexible with formats
        goal_date = date_parser.parse(goal_date_str, fuzzy=True)
        
        # If no start date provided, use next Monday or today based on preference
        if start_date is None:
            start_date = get_next_monday(include_partial_week=include_partial_week)
        elif isinstance(start_date, str):
            start_date = date_parser.parse(start_date).date()
        
        # Ensure goal_date is date object
        goal_date = goal_date.date()
        
        # Calculate days difference
        days_diff = (goal_date - start_date).days
        if days_diff < 0:
            print(f"--- WARNING: Goal date {goal_date} is in the past relative to start date {start_date} ---")
            return None, None, None, False, 0
        
        # Check if we have a partial week at the start
        has_partial_week = (start_date.weekday() != 0)  # Not Monday
        days_in_partial_week = 0
        
        if has_partial_week:
            # Days until next Monday
            days_in_partial_week = 7 - start_date.weekday()
            # Calculate full weeks after the partial week
            days_after_partial = days_diff - days_in_partial_week
            full_weeks = (days_after_partial // 7) + (1 if days_after_partial % 7 > 0 else 0)
            # Total weeks = partial week (Week 0) + full weeks
            weeks_count = full_weeks + 1
        else:
            # Starting on Monday, no partial week
            weeks_count = (days_diff // 7) + (1 if days_diff % 7 > 0 else 0)
        
        # Ensure at least 1 week for very short timeframes
        if weeks_count < 1:
            weeks_count = 1
        
        # Format dates for prompt
        start_str = start_date.strftime('%Y-%m-%d')
        goal_str = goal_date.strftime('%Y-%m-%d')
        
        return weeks_count, start_str, goal_str, has_partial_week, days_in_partial_week
        
    except (ValueError, TypeError) as e:
        print(f"--- Could not parse goal date from '{goal_date_str}': {e} ---")
        return None, None, None, False, 0


# ============================================================================
# VALIDATION REMOVED (January 2026)
# ============================================================================
# Markdown-based validation was causing production crashes and false failures.
# Validation will be re-implemented once structured data (JSON) is in place.
# 
# Previous issues:
# - Regex parsing too fragile (found duplicates, missed variations)
# - AI occasionally skipped weeks (e.g., Week 10)
# - Retries didn't help (prompt ambiguity repeated)
# - Site crashed after max retries instead of delivering plan
#
# Future validation (with JSON structure):
# - Parse JSON schema instead of markdown regex
# - Reliable week counting: [w['week_number'] for w in plan['weeks']]
# - Graceful degradation: warn user but deliver plan
# - Specific error messages for AI to fix
# ============================================================================


def extract_goal_date_from_text(goal_text):
    """
    Try to extract a date from the goal text.
    
    First tries to extract date-like patterns with regex, then parses them.
    
    Looks for patterns like:
    - "on March 29, 2025"
    - "on Saturday 21st March 2025"
    - "March 29th"
    - "21/03/2025"
    - "2025-03-29"
    
    Args:
        goal_text: The goal description text
    
    Returns:
        str or None: Extracted date string (YYYY-MM-DD) or None if not found
    """
    if not goal_text:
        return None
    
    import re
    
    # Define patterns to extract date-like strings
    # Order matters - more specific patterns first
    date_patterns = [
        # Full dates with year
        r'\bon\s+[A-Za-z]+\s+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})',  # on Saturday 21st March 2025
        r'([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})',  # March 21st, 2025 or March 21, 2025
        r'(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})',  # 21st March 2025
        r'(\d{4}-\d{2}-\d{2})',  # 2025-03-29
        r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})',  # 21/03/2025 or 03/21/2025
        # Dates without year (will default to next occurrence)
        r'\bon\s+[A-Za-z]+\s+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+)',  # on Saturday 21st March
        r'([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)',  # March 21st or March 21
        r'(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+)',  # 21st March
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, goal_text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            print(f"--- Extracted date string: '{date_str}' from goal text ---")
            
            try:
                # Parse the extracted date string
                # Use current year + 1 as default for dates without year
                # This ensures we don't accidentally parse dates in the past
                current_year = datetime.now().year
                default_date = datetime(current_year, 1, 1)
                
                parsed_date = date_parser.parse(date_str, default=default_date)
                
                # If the parsed date is in the past, try next year
                if parsed_date.date() <= datetime.now().date():
                    print(f"--- Date {parsed_date.date()} is in past, trying next year ---")
                    default_date = datetime(current_year + 1, 1, 1)
                    parsed_date = date_parser.parse(date_str, default=default_date)
                
                # Only return if still in the future
                if parsed_date.date() > datetime.now().date():
                    result = parsed_date.strftime('%Y-%m-%d')
                    print(f"--- Successfully parsed goal date: {result} ---")
                    return result
                else:
                    print(f"--- Date {parsed_date.date()} is still in past, skipping ---")
                    
            except (ValueError, TypeError) as e:
                print(f"--- Could not parse extracted date '{date_str}': {e} ---")
                continue
    
    print(f"--- Could not extract valid future date from goal text ---")
    return None


@plan_bp.route("/onboarding")
@login_required
def onboarding():
    """Show the onboarding form"""
    try:
        athlete_id = session.get('athlete_id')
        user_data = data_manager.load_user_data(athlete_id) if athlete_id else None
        
        # Get existing athlete profile if available
        athlete_profile = user_data.get('athlete_profile') if user_data else None
        
        # For legacy users, check plan_data for lifestyle_context if not in profile
        if user_data and not athlete_profile:
            plan_data = user_data.get('plan_data', {})
            legacy_lifestyle_context = plan_data.get('lifestyle_context')
            legacy_athlete_type = plan_data.get('athlete_type')
            
            # Create temporary profile dict for template prepopulation
            if legacy_lifestyle_context or legacy_athlete_type:
                athlete_profile = {
                    'lifestyle_context': legacy_lifestyle_context,
                    'athlete_type': legacy_athlete_type
                }
                print(f"--- Loading legacy profile data for athlete {athlete_id} ---")
        
        return render_template("onboarding.html", athlete_profile=athlete_profile)
    except Exception as e:
        print(f"Error loading onboarding: {e}")
        return render_template("onboarding.html", athlete_profile=None)

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
            
            # Archive the plan ONLY (not feedback_log - that stays forever)
            if 'archive' not in user_data:
                user_data['archive'] = []
            user_data['archive'].insert(0, {
                'plan': user_data['plan'],
                'completed_date': datetime.now().isoformat()
                # NOTE: feedback_log is NOT archived - it remains in user_data['feedback_log']
            })
            
            # Clear current plan data
            # DO NOT delete feedback_log - it's permanent coaching history
            del user_data['plan']
            # feedback_log stays - never delete it!
            if 'plan_structure' in user_data:
                del user_data['plan_structure']
        
        # Gather user inputs
        lthr_raw = request.form.get('lthr', '').strip()
        ftp_raw = request.form.get('ftp', '').strip()
        sessions_raw = request.form.get('sessions_per_week', '').strip()
        hours_raw = request.form.get('hours_per_week', '').strip()
        
        # Validate numeric fields
        validation_errors = []
        
        lthr = None
        if lthr_raw:
            try:
                lthr = int(lthr_raw)
                if lthr <= 0:
                    validation_errors.append('LTHR must be a positive number')
            except ValueError:
                validation_errors.append('LTHR must be a valid number')
        
        ftp = None
        if ftp_raw:
            try:
                ftp = int(ftp_raw)
                if ftp <= 0:
                    validation_errors.append('FTP must be a positive number')
            except ValueError:
                validation_errors.append('FTP must be a valid number')
        
        sessions_per_week = None
        if sessions_raw:
            try:
                sessions_per_week = int(sessions_raw)
                if sessions_per_week <= 0:
                    validation_errors.append('Sessions per week must be a positive number')
            except ValueError:
                validation_errors.append('Sessions per week must be a valid number')
        
        hours_per_week = None
        if hours_raw:
            try:
                hours_per_week = float(hours_raw)
                if hours_per_week <= 0:
                    validation_errors.append('Hours per week must be a positive number')
            except ValueError:
                validation_errors.append('Hours per week must be a valid number')
        
        # If there are validation errors, redirect back to onboarding
        if validation_errors:
            for error in validation_errors:
                flash(error)
            return redirect('/onboarding')
        
        # Save persistent athlete profile separately
        lifestyle_context = request.form.get('lifestyle_context', '').strip() or None
        athlete_type = request.form.get('athlete_type') or None
        
        # Check if we need to migrate from legacy structure
        if not user_data.get('athlete_profile'):
            # Check for legacy data in plan_data
            plan_data = user_data.get('plan_data', {})
            if not lifestyle_context and plan_data.get('lifestyle_context'):
                lifestyle_context = plan_data.get('lifestyle_context')
                print(f"--- Migrating legacy lifestyle_context to athlete_profile ---")
            if not athlete_type and plan_data.get('athlete_type'):
                athlete_type = plan_data.get('athlete_type')
                print(f"--- Migrating legacy athlete_type to athlete_profile ---")
        
        athlete_profile = {
            'lifestyle_context': lifestyle_context,
            'athlete_type': athlete_type,
            'updated_at': datetime.now().isoformat()
        }
        user_data['athlete_profile'] = athlete_profile
        print(f"--- Saved athlete_profile for athlete {athlete_id} ---")
        
        # Get upcoming commitments (specific to this plan, not saved to profile)
        upcoming_commitments = request.form.get('upcoming_commitments', '').strip() or None
        
        # Combine lifestyle context and upcoming commitments for AI
        # Lifestyle context is persistent, upcoming commitments are plan-specific
        combined_context = lifestyle_context
        if upcoming_commitments:
            if combined_context:
                combined_context += f"\n\nUpcoming commitments for this training cycle:\n{upcoming_commitments}"
            else:
                combined_context = f"Upcoming commitments for this training cycle:\n{upcoming_commitments}"
        
        user_inputs = {
            'goal': request.form.get('user_goal') or None,
            'sessions_per_week': sessions_per_week,
            'hours_per_week': hours_per_week,
            'lifestyle_context': combined_context,  # Combined context for AI
            'athlete_type': athlete_type,
            'lthr': lthr,
            'ftp': ftp
        }
        
        # Get goal date - prioritize explicit date picker over text extraction
        goal_date_str = request.form.get('goal_date', '').strip()
        
        if goal_date_str:
            # User provided explicit date via date picker
            print(f"--- Goal date from form field: {goal_date_str} ---")
        else:
            # Try to extract goal date from goal text
            goal_date_str = extract_goal_date_from_text(user_inputs['goal'])
            if goal_date_str:
                print(f"--- Goal date extracted from text: {goal_date_str} ---")
            else:
                print(f"--- No goal date found, will generate default 6-week plan ---")
        
        # Calculate plan duration if goal date found
        weeks_until_goal = None
        plan_start_date = None
        goal_date = None
        has_partial_week = False
        days_in_partial_week = 0
        
        if goal_date_str:
            weeks_until_goal, plan_start_date, goal_date, has_partial_week, days_in_partial_week = calculate_weeks_until_goal(
                goal_date_str,
                include_partial_week=True  # Start training today if not Monday
            )
            if weeks_until_goal:
                if has_partial_week:
                    print(f"--- Calculated plan duration: {weeks_until_goal} weeks ({days_in_partial_week} days partial Week 0 + {weeks_until_goal-1} full weeks) from {plan_start_date} to {goal_date} ---")
                else:
                    print(f"--- Calculated plan duration: {weeks_until_goal} weeks from {plan_start_date} to {goal_date} ---")
            else:
                print(f"--- Could not calculate plan duration from goal date: {goal_date_str} ---")
        
        access_token = user_data['token']['access_token']

        print(f"--- Fetching Strava data for athlete {athlete_id} ---")
        
        # Fetch Strava data - check for Response objects (from decorator redirects)
        from flask import Response as FlaskResponse
        
        strava_zones = strava_service.get_athlete_zones(access_token)
        if isinstance(strava_zones, FlaskResponse):
            return strava_zones  # Redirect response from decorator
        
        eight_weeks_ago = datetime.now() - timedelta(weeks=8)
        activities_summary = strava_service.get_recent_activities(
            access_token,
            int(eight_weeks_ago.timestamp()),
            per_page=200
        )
        if isinstance(activities_summary, FlaskResponse):
            return activities_summary  # Redirect response from decorator
        
        athlete_stats = strava_service.get_athlete_stats(access_token, athlete_id)
        if isinstance(athlete_stats, FlaskResponse):
            return athlete_stats  # Redirect response from decorator
        
        # Track whether zones are estimated or user-provided
        lthr_estimated = False
        ftp_estimated = False
        
        # Estimate zones from activity data if not provided by user
        # Only estimate if we have valid activities data (not a Response object)
        if (not user_inputs['lthr'] or not user_inputs['ftp']) and activities_summary and not isinstance(activities_summary, FlaskResponse):
            print(f"--- Estimating zones from activity history ---")
            estimated_zones = training_service.estimate_zones_from_activities(activities_summary)
            
            if not user_inputs['lthr'] and estimated_zones['lthr']:
                user_inputs['lthr'] = estimated_zones['lthr']
                lthr_estimated = True
                print(f"--- Estimated LTHR: {estimated_zones['lthr']} bpm ---")
            
            if not user_inputs['ftp'] and estimated_zones['ftp']:
                user_inputs['ftp'] = estimated_zones['ftp']
                ftp_estimated = True
                print(f"--- Estimated FTP: {estimated_zones['ftp']} W ---")
        
        # Calculate training zones (only if values provided or estimated)
        friel_hr_zones = training_service.calculate_friel_hr_zones(user_inputs['lthr']) if user_inputs['lthr'] else None
        friel_power_zones = training_service.calculate_friel_power_zones(user_inputs['ftp']) if user_inputs['ftp'] else None
        
        # Add metadata to zone data for the AI
        if friel_hr_zones:
            if lthr_estimated:
                friel_hr_zones['estimated'] = True
                friel_hr_zones['estimation_note'] = f"Estimated from recent max HR data (88% of max)"
            else:
                friel_hr_zones['estimated'] = False
                friel_hr_zones['user_provided'] = True
                friel_hr_zones['note'] = "User-provided LTHR value - should be trusted as tested/accurate"
        
        if friel_power_zones:
            if ftp_estimated:
                friel_power_zones['estimated'] = True
                friel_power_zones['estimation_note'] = f"Estimated from recent high-effort rides"
            else:
                friel_power_zones['estimated'] = False
                friel_power_zones['user_provided'] = True
                friel_power_zones['note'] = "User-provided FTP value - should be trusted as tested/accurate"
        
        # Initialize training_metrics if not present
        if 'training_metrics' not in user_data:
            user_data['training_metrics'] = TrainingMetrics(version=1).to_dict()
            print(f"--- Initialized training_metrics for athlete {athlete_id} ---")
        
        # Scan for VDOT-worthy races in last 8 weeks (only if we have valid activities)
        if activities_summary and not isinstance(activities_summary, FlaskResponse):
            print(f"--- Scanning {len(activities_summary)} activities for VDOT-qualifying races ---")
            
            qualifying_races = []
            metrics = TrainingMetrics.from_dict(user_data['training_metrics'])
            
            for activity in activities_summary:
                # Only process runs
                if activity.get('type') not in ['Run', 'VirtualRun']:
                    continue
                
                try:
                    # Get detailed activity with HR zones
                    detailed = strava_service.get_activity(access_token, activity['id'])
                    
                    # Calculate time in zones from activity data
                    time_in_zones = {'Z1': 0, 'Z2': 0, 'Z3': 0, 'Z4': 0, 'Z5': 0}
                    
                    if detailed.get('has_heartrate') and friel_hr_zones:
                        # Get streams for detailed zone calculation
                        streams = strava_service.get_activity_streams(access_token, detailed['id'])
                        if streams and 'heartrate' in streams:
                            hr_data = streams['heartrate']['data']
                            time_data = streams['time']['data']
                            
                            # Calculate time in each zone
                            for i, hr in enumerate(hr_data):
                                duration = 1  # 1 second per data point (approximate)
                                if i > 0:
                                    duration = time_data[i] - time_data[i-1]
                                
                                # Determine zone based on HR
                                if hr < friel_hr_zones['Z1'][1]:
                                    time_in_zones['Z1'] += duration
                                elif hr < friel_hr_zones['Z2'][1]:
                                    time_in_zones['Z2'] += duration
                                elif hr < friel_hr_zones['Z3'][1]:
                                    time_in_zones['Z3'] += duration
                                elif hr < friel_hr_zones['Z4'][1]:
                                    time_in_zones['Z4'] += duration
                                else:
                                    time_in_zones['Z5'] += duration
                    
                    # Check if qualifies for VDOT
                    vdot_result = vdot_detection_service.calculate_vdot_from_activity(
                        detailed,
                        time_in_zones
                    )
                    
                    if vdot_result:
                        qualifying_races.append({
                            'activity_id': activity['id'],
                            'name': activity['name'],
                            'date': activity['start_date_local'][:10],
                            'vdot': int(vdot_result['vdot']),
                            'distance': vdot_result['distance'],
                            'time_seconds': vdot_result['time_seconds'],
                            'is_race': vdot_result['is_race']
                        })
                        print(f"✅ Qualifying race: {activity['name']} - VDOT {int(vdot_result['vdot'])}")
                
                except Exception as e:
                    # Silently skip activities that fail (don't break plan generation)
                    continue
            
            # Use most recent qualifying race for VDOT
            if qualifying_races:
                qualifying_races.sort(key=lambda x: x['date'], reverse=True)
                most_recent = qualifying_races[0]
                
                print(f"--- Using most recent race for VDOT: {most_recent['name']} (VDOT {most_recent['vdot']}) ---")
                
                # Update training_metrics with detected VDOT
                metrics.update_vdot(
                    value=float(most_recent['vdot']),
                    activity_id=most_recent['activity_id'],
                    activity_name=most_recent['name'],
                    detection_method='csv_lookup',
                    distance=most_recent['distance'],
                    activity_time=most_recent['time_seconds']
                )
                user_data['training_metrics'] = metrics.to_dict()
                print(f"--- Stored VDOT {most_recent['vdot']} in training_metrics ---")
            else:
                print(f"--- No qualifying races found in last 8 weeks ---")
        
        # Prepare VDOT context for AI prompt
        vdot_data = prepare_vdot_context(user_data)
        
        # Check if we need to add goal_includes_cycling for the prompt
        goal_includes_cycling = False
        if user_inputs.get('goal'):
            goal_lower = user_inputs['goal'].lower()
            goal_includes_cycling = 'cycling' in goal_lower or 'triathlon' in goal_lower or 'bike' in goal_lower
        
        # Analyze activities (only if we have valid activities data)
        analyzed_activities = []
        if activities_summary and not isinstance(activities_summary, FlaskResponse):
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
                
                # Build zones dict, ensuring we don't pass None values
                zones_for_analysis = {}
                if friel_hr_zones:
                    zones_for_analysis['heart_rate'] = friel_hr_zones
                if friel_power_zones:
                    zones_for_analysis['power'] = friel_power_zones
                
                analyzed_activity = training_service.analyze_activity(
                    activity_to_process,
                    streams,
                    zones_for_analysis
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
            "goal_includes_cycling": goal_includes_cycling,
            "analyzed_activities": analyzed_activities,
            # Add calculated duration parameters
            "weeks_until_goal": weeks_until_goal,
            "goal_date": goal_date,
            "plan_start_date": plan_start_date,
            "has_partial_week": has_partial_week,
            "days_in_partial_week": days_in_partial_week
        }

        print("--- Generating content from Gemini ---")
        if weeks_until_goal:
            if has_partial_week:
                print(f"--- Requesting {weeks_until_goal}-week plan ({days_in_partial_week} days Week 0 + {weeks_until_goal-1} full weeks) from {plan_start_date} to {goal_date} ---")
            else:
                print(f"--- Requesting {weeks_until_goal}-week plan from {plan_start_date} to {goal_date} ---")
        else:
            print(f"--- No goal date provided, requesting default 6-week plan ---")
        
        # Generate plan - VALIDATION TEMPORARILY DISABLED
        # Markdown parsing validation was causing false failures and crashing the site
        # Will re-enable once structured data (JSON) is implemented
        print(f"--- Generating plan (validation disabled) ---")
        
        # Prepare VDOT context for AI
        vdot_data = prepare_vdot_context(user_data)
        
        ai_response_text = ai_service.generate_training_plan(
            user_inputs,
            {
                'training_history': user_data.get('training_history'),
                'final_data_for_ai': final_data_for_ai,
                'athlete_id': athlete_id
            },
            vdot_data=vdot_data
        )
        
        print(f"--- Plan generated successfully ---")

        # Parse AI response into structured format
        plan_v2, plan_markdown = parse_ai_response_to_v2(
            ai_response_text, athlete_id, user_inputs
        )
        
        # Extract plan_structure JSON separately
        plan_structure = None
        json_match = re.search(r"```json\n(.*?)```", ai_response_text, re.DOTALL)
        if json_match:
            try:
                plan_structure = json.loads(json_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Save both formats
        user_data['plan'] = plan_markdown  # Markdown for display
        user_data['plan_structure'] = plan_structure  # Week dates JSON
        user_data['plan_v2'] = plan_v2.to_dict()  # Structured sessions
        user_data['plan_data'] = final_data_for_ai
        
        # Clear no_active_plan flag if it exists (user is creating a new plan)
        if 'no_active_plan' in user_data:
            del user_data['no_active_plan']
        if 'inactive_plan' in user_data:
            del user_data['inactive_plan']
        
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
        
        # If no plan, render a nice page with options instead of ugly error
        if not user_data or ('plan' not in user_data and 'plan_v2' not in user_data) or user_data.get('no_active_plan', False):
            return render_template(
                'no_plan.html',
                show_modal=True
            )
        
        # Use plan_v2 if available (structured data), otherwise fall back to markdown
        if 'plan_v2' in user_data:
            from models.training_plan import TrainingPlan
            plan_v2 = TrainingPlan.from_dict(user_data['plan_v2'])
            
            return render_template(
                'plan_v2.html',
                plan=plan_v2,
                athlete_goal=plan_v2.athlete_goal,
                goal_date=plan_v2.goal_date,
                get_routine_link=get_routine_link
            )
        else:
            # Fallback to old markdown plan
            plan_text = user_data['plan']
            rendered_plan = render_markdown_with_toc(plan_text)
            
            return render_template(
                'plan.html',
                plan_content=rendered_plan['content'],
                plan_toc=rendered_plan['toc']
            )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"An error occurred while retrieving the plan: {e}", 500

@plan_bp.route("/s-and-c-library")
@login_required
def view_s_and_c_library():
    """View the S&C exercise library"""
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        
        # Get S&C library from plan_v2
        library_content = None
        if 'plan_v2' in user_data:
            from models.training_plan import TrainingPlan
            plan_v2 = TrainingPlan.from_dict(user_data['plan_v2'])
            library_content = plan_v2.libraries.get('s_and_c')
        
        if not library_content:
            # No library in current plan - use default
            library_content = load_default_s_and_c_library()
        
        # Render markdown to HTML
        rendered_library = render_markdown_with_toc(library_content)
        
        return render_template(
            's_and_c_library.html',
            library_content=rendered_library['content'],
            library_toc=rendered_library['toc']
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"An error occurred while retrieving the S&C library: {e}", 500

@plan_bp.route("/plan_completion_choice", methods=['POST'])
@login_required
def plan_completion_choice():
    """Handle user's choice after plan completion"""
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        
        if not user_data:
            return redirect('/dashboard')
        
        choice = request.form.get('choice')
        
        if choice == 'new_plan':
            # Clear the plan_completion_prompted flag so they can create a new plan
            if 'plan_completion_prompted' in user_data:
                del user_data['plan_completion_prompted']
            data_manager.save_user_data(athlete_id, user_data)
            # Redirect to onboarding to create a new plan
            return redirect('/onboarding')
        
        elif choice == 'maintenance':
            # Store that they want a maintenance plan
            user_data['plan_completion_choice'] = 'maintenance'
            user_data['plan_completion_prompted'] = True
            data_manager.save_user_data(athlete_id, user_data)
            # Redirect to maintenance plan generation page
            return redirect('/generate_maintenance_plan')
        
        elif choice == 'no_plan':
            # Store that they want no structured plan - keep plan data but mark as inactive
            user_data['plan_completion_choice'] = 'no_plan'
            user_data['plan_completion_prompted'] = True
            user_data['no_active_plan'] = True  # Flag to indicate no structured training
            
            # Archive the completed plan but keep it accessible
            # IMPORTANT: feedback_log should NEVER be archived - it's permanent coaching history
            if 'plan' in user_data and user_data.get('plan'):
                if 'feedback_log' not in user_data:
                    user_data['feedback_log'] = []
                
                # Generate summary of completed plan
                summary_text = ai_service.summarize_training_cycle(
                    user_data['plan'],
                    user_data['feedback_log']
                )
                
                # Store in training history
                training_history = user_data.get('training_history', [])
                training_history.insert(0, {"summary": summary_text})
                user_data['training_history'] = training_history
                
                # Archive the plan ONLY (not feedback_log - that stays forever)
                if 'archive' not in user_data:
                    user_data['archive'] = []
                user_data['archive'].insert(0, {
                    'plan': user_data['plan'],
                    'completed_date': datetime.now().isoformat()
                    # NOTE: feedback_log is NOT archived - it remains in user_data['feedback_log']
                })
                
                # Store the plan as inactive (not deleted) so dashboard can still access it
                user_data['inactive_plan'] = {
                    'plan': user_data['plan'],
                    'plan_structure': user_data.get('plan_structure'),
                    'completed_date': datetime.now().isoformat()
                }
                
                # Clear active plan data but keep it accessible via inactive_plan
                # DO NOT delete feedback_log - it's permanent coaching history
                del user_data['plan']
                # feedback_log stays - never delete it!
                if 'plan_structure' in user_data:
                    del user_data['plan_structure']
            
            data_manager.save_user_data(athlete_id, user_data)
            flash("You're now going with the flow - no structured training plan. You can create a new plan anytime from the dashboard.")
            return redirect('/dashboard')
        
        else:
            flash("Invalid choice. Please try again.")
            return redirect('/dashboard')
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f"An error occurred: {e}")
        return redirect('/dashboard')

@plan_bp.route("/generate_maintenance_plan")
@login_required
def generate_maintenance_plan_form():
    """Show form to generate a maintenance plan"""
    return render_template("maintenance_plan_form.html")

@plan_bp.route("/generate_maintenance_plan", methods=['POST'])
@login_required
def generate_maintenance_plan():
    """Generate a maintenance plan for a specified period"""
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        
        if not user_data or 'token' not in user_data:
            return 'Could not find your session data. Please <a href="/login">log in</a> again.'
        
        # Get maintenance plan parameters
        weeks = request.form.get('weeks', '').strip()
        sessions_per_week = request.form.get('sessions_per_week', '').strip()
        hours_per_week = request.form.get('hours_per_week', '').strip()
        
        # Validate weeks
        try:
            weeks = int(weeks)
            if weeks <= 0 or weeks > 52:
                raise ValueError("Weeks must be between 1 and 52")
        except ValueError as e:
            flash(f"Invalid number of weeks: {e}")
            return redirect('/generate_maintenance_plan')
        
        # Validate sessions_per_week
        try:
            sessions_per_week = int(sessions_per_week)
            if sessions_per_week <= 0 or sessions_per_week > 14:
                raise ValueError("Sessions per week must be between 1 and 14")
        except ValueError as e:
            flash(f"Invalid number of sessions per week: {e}")
            return redirect('/generate_maintenance_plan')
        
        # Validate hours_per_week
        try:
            hours_per_week = float(hours_per_week)
            if hours_per_week <= 0 or hours_per_week > 30:
                raise ValueError("Hours per week must be between 1 and 30")
        except ValueError as e:
            flash(f"Invalid number of hours per week: {e}")
            return redirect('/generate_maintenance_plan')
        
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
            
            # Archive the plan ONLY (not feedback_log - that stays forever)
            if 'archive' not in user_data:
                user_data['archive'] = []
            user_data['archive'].insert(0, {
                'plan': user_data['plan'],
                'completed_date': datetime.now().isoformat()
                # NOTE: feedback_log is NOT archived - it remains in user_data['feedback_log']
            })
            
            # Clear current plan data
            # DO NOT delete feedback_log - it's permanent coaching history
            del user_data['plan']
            # feedback_log stays - never delete it!
            if 'plan_structure' in user_data:
                del user_data['plan_structure']
        
        # Get user's current fitness data for context
        access_token = user_data['token']['access_token']
        
        # Fetch Strava data - check for Response objects (from decorator redirects)
        from flask import Response as FlaskResponse
        
        strava_zones = strava_service.get_athlete_zones(access_token)
        if isinstance(strava_zones, FlaskResponse):
            return strava_zones  # Redirect response from decorator
        
        eight_weeks_ago = datetime.now() - timedelta(weeks=8)
        activities_summary = strava_service.get_recent_activities(
            access_token,
            int(eight_weeks_ago.timestamp()),
            per_page=200
        )
        if isinstance(activities_summary, FlaskResponse):
            return activities_summary  # Redirect response from decorator
        
        athlete_stats = strava_service.get_athlete_stats(access_token, athlete_id)
        if isinstance(athlete_stats, FlaskResponse):
            return athlete_stats  # Redirect response from decorator
        
        # Get existing zones from plan_data if available
        plan_data = user_data.get('plan_data', {})
        friel_hr_zones = plan_data.get('friel_hr_zones')
        friel_power_zones = plan_data.get('friel_power_zones')
        
        # If zones not available, estimate them
        if not friel_hr_zones or not friel_power_zones:
            if activities_summary:  # Only estimate if we have activities
                estimated_zones = training_service.estimate_zones_from_activities(activities_summary)
                if estimated_zones['lthr']:
                    friel_hr_zones = training_service.calculate_friel_hr_zones(estimated_zones['lthr'])
                if estimated_zones['ftp']:
                    friel_power_zones = training_service.calculate_friel_power_zones(estimated_zones['ftp'])
        
        # Calculate duration parameters for maintenance plan
        start_date = get_next_monday()
        goal_date = start_date + timedelta(weeks=weeks)
        plan_start_date = start_date.strftime('%Y-%m-%d')
        goal_date_str = goal_date.strftime('%Y-%m-%d')
        
        # Prepare user inputs for maintenance plan
        # Get athlete_type from onboarding (influences session planning), but use form values for sessions/hours
        plan_data = user_data.get('plan_data', {})
        user_inputs = {
            'goal': f"Maintenance training plan for {weeks} weeks",
            'sessions_per_week': sessions_per_week,  # From form
            'hours_per_week': hours_per_week,  # From form
            'lifestyle_context': None,  # Not used for maintenance plans - keep it bare bones
            'athlete_type': plan_data.get('athlete_type', 'General'),  # Keep from onboarding
            'maintenance_weeks': weeks
        }
        
        # Prepare data for AI - current fitness metrics only, bare bones for maintenance
        # Filter out any Response objects or None values that shouldn't be there
        final_data_for_ai = {
            "athlete_goal": user_inputs['goal'],
            "sessions_per_week": user_inputs['sessions_per_week'],
            "hours_per_week": user_inputs['hours_per_week'],
            "athlete_type": user_inputs['athlete_type'],
            # Only include current fitness data, not historical analyzed activities or old vdot_data
            "athlete_stats": athlete_stats if athlete_stats and not isinstance(athlete_stats, FlaskResponse) else {},
            "strava_zones": strava_zones if strava_zones and not isinstance(strava_zones, FlaskResponse) else {},
            "friel_hr_zones": friel_hr_zones if friel_hr_zones and not isinstance(friel_hr_zones, FlaskResponse) else None,
            "friel_power_zones": friel_power_zones if friel_power_zones and not isinstance(friel_power_zones, FlaskResponse) else None,
            "maintenance_weeks": weeks,
            # Add calculated duration parameters
            "weeks_until_goal": weeks,
            "goal_date": goal_date_str,
            "plan_start_date": plan_start_date
            # Explicitly NOT including: analyzed_activities, vdot_data, lifestyle_context (bare bones maintenance plan)
        }
        
        # Validate that final_data_for_ai is JSON-serializable before proceeding
        def is_json_serializable(obj):
            """Check if an object is JSON serializable"""
            try:
                json.dumps(obj)
                return True
            except (TypeError, ValueError):
                return False
        
        def clean_for_json(obj):
            """Recursively clean object to make it JSON-serializable"""
            if isinstance(obj, FlaskResponse):
                return None
            elif isinstance(obj, dict):
                return {k: clean_for_json(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [clean_for_json(item) for item in obj]
            elif not is_json_serializable(obj):
                # Convert non-serializable objects to strings
                return str(obj)
            return obj
        
        # Clean the data structure to ensure it's JSON-serializable
        final_data_for_ai = clean_for_json(final_data_for_ai)
        
        # Final validation
        try:
            json.dumps(final_data_for_ai, indent=4)
        except TypeError as e:
            print(f"--- ERROR: final_data_for_ai still contains non-serializable data after cleaning: {e} ---")
            flash(f"Error preparing data for AI. Please try again.")
            return redirect('/generate_maintenance_plan')
        
        print("--- Generating maintenance plan from Gemini ---")
        print(f"--- Maintenance plan data being passed: sessions_per_week={user_inputs['sessions_per_week']}, hours_per_week={user_inputs['hours_per_week']}, weeks={weeks} ---")
        print(f"--- Requesting {weeks}-week plan from {plan_start_date} to {goal_date_str} ---")
        print(f"--- Athlete type: {user_inputs['athlete_type']} (from onboarding), Lifestyle context: NOT included (bare bones plan) ---")
        print(f"--- Including training_history summary (last plan summary), NOT including analyzed_activities, vdot_data, or lifestyle_context ---")
        
        # Generate maintenance plan - pass training_history summary but not old plan details
        ai_response_text = ai_service.generate_maintenance_plan(
            user_inputs,
            {
                'training_history': user_data.get('training_history'),  # Include summary of last plan
                'final_data_for_ai': final_data_for_ai
            }
        )
        
        # Validate duration
        is_valid, actual_weeks, message = validate_plan_duration(ai_response_text, weeks)
        if not is_valid:
            print(f"--- WARNING: Maintenance plan validation failed: {message} ---")
            flash(f"Warning: Generated plan is {actual_weeks} weeks instead of requested {weeks} weeks.", "warning")
        
        # Parse AI response into structured format
        plan_v2, plan_markdown = parse_ai_response_to_v2(
            ai_response_text, athlete_id, user_inputs
        )
        
        # Extract plan_structure JSON separately
        plan_structure = None
        json_match = re.search(r"```json\n(.*?)```", ai_response_text, re.DOTALL)
        if json_match:
            try:
                plan_structure = json.loads(json_match.group(1).strip())
            except json.JSONDecodeError:
                pass
        
        # Save both formats
        user_data['plan'] = plan_markdown  # Markdown for display
        user_data['plan_structure'] = plan_structure  # Week dates JSON
        user_data['plan_v2'] = plan_v2.to_dict()  # Structured sessions
        user_data['plan_data'] = final_data_for_ai
        user_data['plan_completion_choice'] = None  # Clear the choice
        user_data['plan_completion_prompted'] = False  # Clear the prompt flag
        
        # Clear no_active_plan flag if it exists (user is creating a new plan)
        if 'no_active_plan' in user_data:
            del user_data['no_active_plan']
        if 'inactive_plan' in user_data:
            del user_data['inactive_plan']
        
        data_manager.save_user_data(athlete_id, user_data)
        
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
        return f"An error occurred during maintenance plan generation: {e}", 500



# Plan Generation Helper Functions