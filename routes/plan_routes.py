from flask import Blueprint, render_template, request, redirect, session, flash, url_for
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
from utils.week_dates import generate_week_calendar
from utils.garmin_aggregation import build_garmin_summary
from routes.api_routes import safe_save_user_data

plan_bp = Blueprint('plan', __name__)


def build_recent_training_summary(activities_summary, weeks=6):
    """
    Build a compact summary of recent training from Strava activity list (no full analysis).
    Used to trim plan prompt token usage while still informing the AI of current load.
    """
    if not activities_summary or not isinstance(activities_summary, list):
        return {"summary_text": "No recent activities.", "by_type": {}}
    cutoff = datetime.now() - timedelta(weeks=weeks)
    by_type = {}
    for a in activities_summary:
        try:
            start = datetime.strptime(a.get("start_date_local", "")[:10], "%Y-%m-%d")
            if start < cutoff:
                continue
            atype = (a.get("type") or "Other").replace(" ", "_")
            by_type.setdefault(atype, {"count": 0, "moving_min": 0, "distance_km": 0.0})
            by_type[atype]["count"] += 1
            by_type[atype]["moving_min"] += int((a.get("moving_time") or 0) / 60)
            by_type[atype]["distance_km"] += float((a.get("distance") or 0) / 1000)
        except (ValueError, TypeError, KeyError):
            continue
    parts = [f"{v['count']} {k} ({v['moving_min']} min, {v['distance_km']:.0f} km)" for k, v in sorted(by_type.items())]
    summary_text = f"Last {weeks} weeks: " + "; ".join(parts) if parts else f"No activities in last {weeks} weeks."
    return {"summary_text": summary_text, "weeks": weeks, "by_type": by_type}


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

        # Prefill LTHR/FTP/VDOT from training_metrics for re-onboarding
        training_metrics_prefill = {}
        if user_data:
            metrics = user_data.get('training_metrics', {})
            if isinstance(metrics.get('lthr'), dict) and metrics['lthr'].get('value') is not None:
                training_metrics_prefill['lthr_value'] = metrics['lthr']['value']
            if isinstance(metrics.get('ftp'), dict) and metrics['ftp'].get('value') is not None:
                training_metrics_prefill['ftp_value'] = metrics['ftp']['value']
            vdot_obj = metrics.get('vdot')
            if isinstance(vdot_obj, dict) and vdot_obj.get('value') is not None:
                training_metrics_prefill['vdot_value'] = int(vdot_obj['value']) if vdot_obj['value'] else None
                if vdot_obj.get('detected_from', {}).get('date'):
                    training_metrics_prefill['vdot_date'] = vdot_obj['detected_from']['date'][:10]
                elif vdot_obj.get('detected_at'):
                    training_metrics_prefill['vdot_date'] = str(vdot_obj['detected_at'])[:10]

        return render_template(
            "onboarding.html",
            athlete_profile=athlete_profile,
            training_metrics_prefill=training_metrics_prefill
        )
    except Exception as e:
        print(f"Error loading onboarding: {e}")
        return render_template("onboarding.html", athlete_profile=None, training_metrics_prefill={})

@plan_bp.route("/generate_plan", methods=['POST'])
@login_required
def generate_plan():
    """Generate a new training plan"""
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        
        if not user_data or 'token' not in user_data:
            return 'Could not find your session data. Please <a href="/login">log in</a> again.'

        # Do not archive existing plan yet - only archive after we have a valid new plan
        # so that 429/API failures don't wipe the current plan
        had_existing_plan = bool(user_data.get('plan') or user_data.get('plan_v2'))
        
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
        
        # Sports to include in plan (at least one required)
        selected_sports = request.form.getlist('sports')
        if not selected_sports:
            flash('Please select at least one sport to include in your plan.')
            return redirect('/onboarding')

        athlete_profile = {
            'lifestyle_context': lifestyle_context,
            'athlete_type': athlete_type,
            'sports': selected_sports,
            'updated_at': datetime.now().isoformat()
        }
        user_data['athlete_profile'] = athlete_profile
        print(f"--- Saved athlete_profile for athlete {athlete_id} (sports: {selected_sports}) ---")
        
        # Save unit preferences (per sport)
        unit_run = request.form.get('unit_run', 'km')
        unit_ride = request.form.get('unit_ride', 'km')
        unit_swim = request.form.get('unit_swim', 'meters')
        user_data['unit_preferences'] = {
            'run': unit_run,
            'ride': unit_ride,
            'swim': unit_swim
        }
        print(f"--- Saved unit preferences: run={unit_run}, ride={unit_ride}, swim={unit_swim} ---")
        
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
            'ftp': ftp,
            'included_sports': selected_sports,
        }
        
        print(f"--- DEBUG user_inputs['goal']: {user_inputs['goal']} ---")
        
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
        
        # Save LTHR and FTP from form to training_metrics
        metrics_dict = user_data['training_metrics']
        
        if lthr:
            metrics_dict['lthr'] = {
                'value': lthr,
                'detected_at': datetime.now().isoformat(),
                'detected_from': {
                    'activity_id': 0,
                    'activity_name': 'User provided during onboarding',
                    'detection_method': 'user_input'
                },
                'user_confirmed': True,
                'user_modified': False,
                'history': []
            }
            print(f"✅ Saved LTHR: {lthr} bpm to training_metrics")
        
        if ftp:
            metrics_dict['ftp'] = {
                'value': ftp,
                'detected_at': datetime.now().isoformat(),
                'detected_from': {
                    'activity_id': 0,
                    'activity_name': 'User provided during onboarding',
                    'detection_method': 'user_input'
                },
                'user_confirmed': True,
                'user_modified': False,
                'history': []
            }
            print(f"✅ Saved FTP: {ftp} W to training_metrics")
        
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
        
        # Recent training summary (trimmed for prompt token usage; no full activity list)
        recent_training_summary = build_recent_training_summary(
            activities_summary if not isinstance(activities_summary, FlaskResponse) else None,
            weeks=6
        )

        # Week calendar from plan dates (single source of truth for week ranges)
        week_calendar = []
        _plan_start = plan_start_date
        _weeks = weeks_until_goal
        if _weeks is None:
            _plan_start = get_next_monday(include_partial_week=False)
            _weeks = 6
        if _plan_start and _weeks:
            # Respect partial-week semantics from calculate_weeks_until_goal
            full_weeks = _weeks - 1 if has_partial_week else _weeks
            if full_weeks < 0:
                full_weeks = 0
            try:
                week_ranges = generate_week_calendar(
                    _plan_start,
                    full_weeks,
                    has_partial_week=has_partial_week,
                    days_in_partial_week=days_in_partial_week if has_partial_week else None,
                )
                week_calendar = [w.to_dict() for w in week_ranges]
            except (ValueError, TypeError) as e:
                print(f"--- Week calendar build failed: {e} ---")

        # Minimal athlete_stats for prompt (avoid large payloads)
        athlete_stats_trimmed = {}
        if athlete_stats and not isinstance(athlete_stats, FlaskResponse) and isinstance(athlete_stats, dict):
            for k in ("recent_ride_totals", "recent_run_totals", "ytd_ride_totals", "ytd_run_totals", "all_ride_totals", "all_run_totals"):
                if k in athlete_stats and athlete_stats[k]:
                    athlete_stats_trimmed[k] = athlete_stats[k]

        # Call A: Training assessment (fresh on new plan) – Garmin summary + AI assessment
        garmin_summary_for_assessment = None
        if 'garmin_credentials' in user_data:
            try:
                from services.garmin_service import garmin_service
                creds = user_data['garmin_credentials']
                stats_range = garmin_service.fetch_date_range(
                    creds['email'], creds['password'], days=30,
                    encrypted_tokenstore=creds.get('tokenstore')
                )
                if stats_range:
                    metrics_timeline = garmin_service.extract_metrics_timeline(stats_range)
                    garmin_summary_for_assessment = build_garmin_summary(metrics_timeline)
                    print(f"--- Garmin summary built ({len(metrics_timeline)} days) ---")
            except Exception as e:
                print(f"--- Garmin fetch for assessment failed: {e} ---")
        training_metrics_for_assessment = user_data.get('training_metrics') or {}
        if vdot_data:
            training_metrics_for_assessment = {**training_metrics_for_assessment, 'vdot_context': vdot_data}
        print("--- Call A: Generating training assessment ---")
        assessment = ai_service.generate_assessment(
            recent_training_summary=recent_training_summary,
            athlete_stats=athlete_stats_trimmed,
            training_metrics=training_metrics_for_assessment,
            garmin_summary=garmin_summary_for_assessment
        )
        if assessment:
            user_data['assessment'] = assessment
            user_data['assessment_updated_at'] = datetime.now().isoformat()
            print(f"--- Assessment stored (snippet: {len(assessment.get('current_fitness_snippet', ''))} chars) ---")

        # Prepare data for AI (trimmed: summary not full activity list, minimal stats/zones; includes assessment)
        # Store upcoming_commitments separately so weekly summary/chat can reference "constraints for this plan"
        final_data_for_ai = {
            "athlete_goal": user_inputs['goal'],
            "sessions_per_week": user_inputs['sessions_per_week'],
            "hours_per_week": user_inputs['hours_per_week'],
            "lifestyle_context": user_inputs['lifestyle_context'],
            "upcoming_commitments": upcoming_commitments,
            "athlete_type": user_inputs['athlete_type'],
            "included_sports": user_inputs['included_sports'],
            "recent_training_summary": recent_training_summary,
            "week_calendar": week_calendar,
            "athlete_stats": athlete_stats_trimmed,
            "strava_zones": strava_zones if strava_zones and not isinstance(strava_zones, FlaskResponse) else {},
            "friel_hr_zones": friel_hr_zones,
            "friel_power_zones": friel_power_zones,
            "vdot_data": vdot_data,
            "goal_includes_cycling": goal_includes_cycling,
            "weeks_until_goal": weeks_until_goal,
            "goal_date": goal_date,
            "plan_start_date": plan_start_date,
            "has_partial_week": has_partial_week,
            "days_in_partial_week": days_in_partial_week,
            "assessment": assessment,
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
        
        # generate_training_plan returns (TrainingPlan, markdown_text, preamble_markdown)
        try:
            plan_v2, plan_markdown, preamble_markdown = ai_service.generate_training_plan(
                user_inputs,
                {
                    'training_history': user_data.get('training_history'),
                    'final_data_for_ai': final_data_for_ai,
                    'athlete_id': athlete_id
                },
                vdot_data=vdot_data
            )
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "Resource exhausted" in err_str:
                flash("Plan generation failed: the coach service is busy (rate limit). Please try again in a few minutes.", "error")
            else:
                flash(f"Plan generation failed: {err_str[:200]}", "error")
            return redirect(url_for('plan.onboarding'))

        if plan_v2 is None or len(plan_v2.weeks) == 0:
            print("--- Plan generation failed (no weeks) ---")
            flash("Plan generation failed: the coach returned an empty plan. This can happen when the service is busy. Please try again in a few minutes.", "error")
            return redirect(url_for('plan.onboarding'))

        print(f"--- Plan generated successfully ---")

        # Archive existing plan only now that we have a valid new plan
        if had_existing_plan:
            if 'feedback_log' not in user_data:
                user_data['feedback_log'] = []
            print(f"--- Archiving previous plan for athlete {athlete_id} ---")
            # Use plan_v2 for summarization when available (structured-data-first)
            completed_plan_for_summary = None
            if user_data.get('plan_v2'):
                try:
                    from models.training_plan import TrainingPlan
                    completed_plan_for_summary = TrainingPlan.from_dict(user_data['plan_v2'])
                except Exception:
                    pass
            if completed_plan_for_summary is None and user_data.get('plan'):
                completed_plan_for_summary = user_data['plan']
            if completed_plan_for_summary is not None:
                summary_text = ai_service.summarize_training_cycle(
                    completed_plan_for_summary,
                    user_data['feedback_log']
                )
                training_history = user_data.get('training_history', [])
                training_history.insert(0, {"summary": summary_text})
                user_data['training_history'] = training_history
            if 'archive' not in user_data:
                user_data['archive'] = []
            archive_entry = {'completed_date': datetime.now().isoformat()}
            if user_data.get('plan') is not None:
                archive_entry['plan'] = user_data['plan']
            if user_data.get('plan_v2') is not None:
                archive_entry['plan_v2'] = user_data['plan_v2']
            user_data['archive'].insert(0, archive_entry)
            if 'plan' in user_data:
                del user_data['plan']
            if 'plan_structure' in user_data:
                del user_data['plan_structure']
            if 'plan_v2' in user_data:
                del user_data['plan_v2']

        # No need to call parse_ai_response_to_v2 again - already done above
        plan_structure = None

        # Save JSON as primary source of truth; do not store full markdown for new plans
        user_data['plan_v2'] = plan_v2.to_dict() if plan_v2 else None  # Structured sessions
        # Keep any legacy plan structure field for backwards compatibility only
        user_data['plan_structure'] = plan_structure
        if preamble_markdown:
            final_data_for_ai['plan_preamble_markdown'] = preamble_markdown
        user_data['plan_data'] = final_data_for_ai
        
        # Clear no_active_plan flag if it exists (user is creating a new plan)
        if 'no_active_plan' in user_data:
            del user_data['no_active_plan']
        if 'inactive_plan' in user_data:
            del user_data['inactive_plan']
        
        safe_save_user_data(athlete_id, user_data)
        
        # Verify save (JSON-first: plan_v2 is the source of truth)
        print(f"--- APP: Verifying save operation by reloading data...")
        verified_user_data = data_manager.load_user_data(athlete_id)
        
        if 'plan_v2' in verified_user_data or 'plan' in verified_user_data:
            print(f"--- APP: SUCCESS! Reloaded data contains the plan (JSON and/or legacy markdown).")
        else:
            print(f"--- APP: FAILURE! Reloaded data does NOT contain the plan_v2 or legacy markdown plan.")
            return "Error: The plan was generated but could not be saved to the database. Please check the logs.", 500

        # Success! Redirect to plan page
        flash("Your training plan has been generated successfully!", "success")
        return redirect(url_for('plan.view_plan'))
    
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
            import markdown
            plan_v2 = TrainingPlan.from_dict(user_data['plan_v2'])
            
            # Preamble: prefer stored AI preamble (coherent overview); else intro block from plan markdown
            plan_preamble_html = None
            stored_preamble = user_data.get('plan_data', {}).get('plan_preamble_markdown')
            if stored_preamble:
                plan_preamble_html = markdown.markdown(
                    stored_preamble,
                    extensions=['tables', 'fenced_code']
                )
            elif 'plan' in user_data:
                import re
                plan_markdown = user_data['plan']
                # Split at first "## Week" (to_markdown uses "## Week N:", not "### Week")
                parts = re.split(r'\n## Week \d+:', plan_markdown, maxsplit=1)
                if len(parts) > 0:
                    preamble_md = parts[0].strip()
                    if preamble_md:
                        plan_preamble_html = markdown.markdown(
                            preamble_md,
                            extensions=['tables', 'fenced_code']
                        )
            
            return render_template(
                'plan_v2.html',
                plan=plan_v2,
                athlete_goal=plan_v2.athlete_goal,
                goal_date=plan_v2.goal_date,
                plan_preamble_html=plan_preamble_html,
                get_routine_link=get_routine_link
            )
        else:
            # Fallback to old markdown plan (guard against missing/empty key)
            plan_text = user_data.get('plan', '') or ''
            if not plan_text:
                return render_template('no_plan.html', show_modal=True)
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
            safe_save_user_data(athlete_id, user_data)
            # Redirect to onboarding to create a new plan
            return redirect('/onboarding')
        
        elif choice == 'maintenance':
            # Store that they want a maintenance plan
            user_data['plan_completion_choice'] = 'maintenance'
            user_data['plan_completion_prompted'] = True
            safe_save_user_data(athlete_id, user_data)
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
                
                # Generate summary of completed plan (pass plan_v2 when available so we don't feed markdown to AI)
                completed_plan_for_summary = None
                if user_data.get('plan_v2'):
                    try:
                        from models.training_plan import TrainingPlan
                        completed_plan_for_summary = TrainingPlan.from_dict(user_data['plan_v2'])
                    except Exception:
                        pass
                if completed_plan_for_summary is None:
                    completed_plan_for_summary = user_data['plan']
                summary_text = ai_service.summarize_training_cycle(
                    completed_plan_for_summary,
                    user_data['feedback_log']
                )
                
                # Store in training history
                training_history = user_data.get('training_history', [])
                training_history.insert(0, {"summary": summary_text})
                user_data['training_history'] = training_history
                
                # Archive the plan ONLY (not feedback_log - that stays forever)
                if 'archive' not in user_data:
                    user_data['archive'] = []
                archive_entry = {
                    'plan': user_data['plan'],
                    'completed_date': datetime.now().isoformat()
                    # NOTE: feedback_log is NOT archived - it remains in user_data['feedback_log']
                }
                if user_data.get('plan_v2') is not None:
                    archive_entry['plan_v2'] = user_data['plan_v2']
                user_data['archive'].insert(0, archive_entry)
                
                # Store the plan as inactive (not deleted) so dashboard can still access it (include plan_v2 for structured restore)
                user_data['inactive_plan'] = {
                    'plan': user_data['plan'],
                    'plan_structure': user_data.get('plan_structure'),
                    'plan_v2': user_data.get('plan_v2'),
                    'completed_date': datetime.now().isoformat()
                }
                
                # Clear active plan data but keep it accessible via inactive_plan
                # DO NOT delete feedback_log - it's permanent coaching history
                del user_data['plan']
                # feedback_log stays - never delete it!
                if 'plan_structure' in user_data:
                    del user_data['plan_structure']
                if 'plan_v2' in user_data:
                    del user_data['plan_v2']
            
            safe_save_user_data(athlete_id, user_data)
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
            
            # Generate summary of completed plan (pass plan_v2 when available so we don't feed markdown to AI)
            completed_plan_for_summary = None
            if user_data.get('plan_v2'):
                try:
                    from models.training_plan import TrainingPlan
                    completed_plan_for_summary = TrainingPlan.from_dict(user_data['plan_v2'])
                except Exception:
                    pass
            if completed_plan_for_summary is None:
                completed_plan_for_summary = user_data['plan']
            summary_text = ai_service.summarize_training_cycle(
                completed_plan_for_summary,
                user_data['feedback_log']
            )
            
            # Store in training history
            training_history = user_data.get('training_history', [])
            training_history.insert(0, {"summary": summary_text})
            user_data['training_history'] = training_history
            
            # Archive the plan ONLY (not feedback_log - that stays forever)
            if 'archive' not in user_data:
                user_data['archive'] = []
            archive_entry = {
                'plan': user_data['plan'],
                'completed_date': datetime.now().isoformat()
                # NOTE: feedback_log is NOT archived - it remains in user_data['feedback_log']
            }
            if user_data.get('plan_v2') is not None:
                archive_entry['plan_v2'] = user_data['plan_v2']
            user_data['archive'].insert(0, archive_entry)
            
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
        
        # Prepare user inputs for maintenance plan (same shape as main plan so we can use generate_training_plan = JSON-first)
        plan_data = user_data.get('plan_data', {})
        athlete_profile = user_data.get('athlete_profile', {})
        included_sports = athlete_profile.get('sports') or plan_data.get('included_sports') or ['Run']
        user_inputs = {
            'goal': f"Maintenance training plan for {weeks} weeks",
            'sessions_per_week': sessions_per_week,
            'hours_per_week': hours_per_week,
            'lifestyle_context': '',
            'athlete_type': plan_data.get('athlete_type', 'General'),
            'included_sports': included_sports,
            'maintenance_weeks': weeks
        }
        
        # Week calendar for JSON-first (single source of truth for week dates)
        week_ranges = generate_week_calendar(plan_start_date, weeks, has_partial_week=False)
        week_calendar = [w.to_dict() for w in week_ranges]
        vdot_data = prepare_vdot_context(user_data)
        
        final_data_for_ai = {
            "athlete_goal": user_inputs['goal'],
            "sessions_per_week": user_inputs['sessions_per_week'],
            "hours_per_week": user_inputs['hours_per_week'],
            "athlete_type": user_inputs['athlete_type'],
            "included_sports": included_sports,
            "athlete_stats": athlete_stats if athlete_stats and not isinstance(athlete_stats, FlaskResponse) else {},
            "strava_zones": strava_zones if strava_zones and not isinstance(strava_zones, FlaskResponse) else {},
            "friel_hr_zones": friel_hr_zones,
            "friel_power_zones": friel_power_zones,
            "maintenance_weeks": weeks,
            "weeks_until_goal": weeks,
            "goal_date": goal_date_str,
            "plan_start_date": plan_start_date,
            "has_partial_week": False,
            "days_in_partial_week": 0,
            "week_calendar": week_calendar,
            "recent_training_summary": {"summary_text": "Maintenance plan - no recent summary.", "by_type": {}},
        }
        
        def clean_for_json(obj):
            if isinstance(obj, FlaskResponse):
                return None
            if isinstance(obj, dict):
                return {k: clean_for_json(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [clean_for_json(item) for item in obj]
            try:
                json.dumps(obj)
                return obj
            except (TypeError, ValueError):
                return str(obj)
        
        final_data_for_ai = clean_for_json(final_data_for_ai)
        
        print("--- Generating maintenance plan (JSON-first via generate_training_plan) ---")
        print(f"--- Requesting {weeks}-week plan from {plan_start_date} to {goal_date_str} ---")
        
        # Use same JSON-first flow as main plan generation
        try:
            plan_v2, plan_markdown, _ = ai_service.generate_training_plan(
                user_inputs,
                {
                    'training_history': user_data.get('training_history'),
                    'final_data_for_ai': final_data_for_ai,
                    'athlete_id': athlete_id
                },
                vdot_data=vdot_data
            )
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "Resource exhausted" in err_str:
                flash("Maintenance plan generation failed: the coach service is busy. Please try again in a few minutes.", "error")
            else:
                flash(f"Maintenance plan generation failed: {err_str[:200]}", "error")
            return redirect('/generate_maintenance_plan')
        
        if plan_v2 is None or len(plan_v2.weeks) == 0:
            flash("Maintenance plan generation failed: the coach returned an empty plan. Please try again.", "error")
            return redirect('/generate_maintenance_plan')
        
        # Save both formats (plan_v2 already has correct week dates from calendar)
        # For maintenance plans, also keep JSON as primary; avoid storing full markdown
        user_data['plan_v2'] = plan_v2.to_dict()
        user_data['plan_structure'] = None  # Deprecated; week dates are in plan_v2
        user_data['plan_data'] = final_data_for_ai
        user_data['plan_completion_choice'] = None  # Clear the choice
        user_data['plan_completion_prompted'] = False  # Clear the prompt flag
        
        # Clear no_active_plan flag if it exists (user is creating a new plan)
        if 'no_active_plan' in user_data:
            del user_data['no_active_plan']
        if 'inactive_plan' in user_data:
            del user_data['inactive_plan']
        
        safe_save_user_data(athlete_id, user_data)
        
        flash("Your maintenance plan has been generated successfully!", "success")
        return redirect(url_for('plan.view_plan'))
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"An error occurred during maintenance plan generation: {e}", 500



# Plan Generation Helper Functions