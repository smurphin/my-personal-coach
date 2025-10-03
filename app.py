import os
import requests
import json
from flask import Flask, request, redirect, render_template, session, flash, jsonify, url_for
from markupsafe import Markup
import mistune
from dotenv import load_dotenv
from datetime import datetime, timedelta
import jinja2
import bisect
import re
import boto3
from data_manager import data_manager
import vertexai
from vertexai.generative_models import GenerativeModel
import functools
import hashlib

ai_model = "gemini-2.5-flash"

print("!!!!!!!!!! SERVER IS RELOADING RIGHT NOW !!!!!!!!!!")

# Load environment variables from .env file for local development
load_dotenv()

# --- AWS Secrets Manager Integration (for Production) ---
# If running in production, fetch secrets from AWS Secrets Manager
if os.getenv('FLASK_ENV') == 'production':
    secret_name = "my-personal-coach-app-secrets"
    region_name = "eu-west-1"

    # Create a Secrets Manager client
    session_boto = boto3.session.Session()
    client = session_boto.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
        # Decrypts secret using the associated KMS key.
        secret = get_secret_value_response['SecretString']
        secrets = json.loads(secret)

        # Set environment variables from the fetched secret
        os.environ['STRAVA_CLIENT_ID'] = secrets.get('STRAVA_CLIENT_ID')
        os.environ['STRAVA_CLIENT_SECRET'] = secrets.get('STRAVA_CLIENT_SECRET')
        os.environ['STRAVA_VERIFY_TOKEN'] = secrets.get('STRAVA_VERIFY_TOKEN')
        os.environ['FLASK_SECRET_KEY'] = secrets.get('FLASK_SECRET_KEY')
        os.environ['GOOGLE_APPLICATION_CREDENTIALS_JSON'] = secrets.get('GOOGLE_APPLICATION_CREDENTIALS_JSON')

        # Create a temporary file for Google credentials
        with open("/tmp/gcp_creds.json", "w") as f:
            f.write(secrets.get('GOOGLE_APPLICATION_CREDENTIALS_JSON'))
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = "/tmp/gcp_creds.json"

    except Exception as e:
        # You should handle this error appropriately in a production app
        print(f"Error fetching secrets from AWS Secrets Manager: {e}")
        # Potentially raise the exception to stop the app from starting without secrets
        raise e

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "a_default_secret_key_for_development")

# --- Strava Configuration ---
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
STRAVA_API_URL = "https://www.strava.com/api/v3"
SCOPES = "read,activity:read_all,profile:read_all"

if os.getenv('FLASK_ENV') == 'production':
    # In production, use the App Runner service URL
    REDIRECT_URI = "https://www.kaizencoach.training/callback"
else:
    # In local development, use the localhost address
    REDIRECT_URI = "http://127.0.0.1:5000/callback"

GCP_PROJECT_ID = "my-personal-coach-472007"
GCP_LOCATION = "europe-west1"

# --- Initialize Vertex AI ---
vertexai.init(project=GCP_PROJECT_ID, location=GCP_LOCATION)
model = GenerativeModel(model_name=ai_model)

# --- Helper & Analysis Functions ---

def format_seconds(seconds):
    seconds = int(seconds)
    if seconds == 0: return "0s"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours > 0: parts.append(f"{hours}h")
    if minutes > 0: parts.append(f"{minutes}m")
    if secs > 0: parts.append(f"{secs}s")
    return " ".join(parts)

def strava_api_call(f):
    """
    Decorator to handle Strava API calls and token expiration.
    If a 401 Unauthorized is received, it clears the session and redirects to login.
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                # Unauthorized - likely token expired
                session.clear()
                flash("Your session has expired. Please log in again.")
                return redirect('/')
            # For other HTTP errors, re-raise the exception
            raise e
    return decorated_function

@strava_api_call
def get_strava_api_data(access_token, endpoint, params=None):
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(f"{STRAVA_API_URL}/{endpoint}", headers=headers, params=params)
    response.raise_for_status()
    return response.json()

def login_required(f):
    """
    Decorator to ensure a user is logged in before accessing a view.
    If not logged in, redirects to the login page.
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'athlete_id' not in session:
            flash("You must be logged in to view this page.")
            return redirect('/')
        return f(*args, **kwargs)
    return decorated_function

def get_activity_streams(access_token, activity_id):
    """Fetches streams for a single activity."""
    headers = {'Authorization': f'Bearer {access_token}'}
    params = {'keys': 'heartrate,time,watts,distance,altitude', 'key_by_type': True}
    response = requests.get(f"{STRAVA_API_URL}/activities/{activity_id}/streams", headers=headers, params=params)
    return response.json() if response.status_code == 200 else None

def get_athlete_stats(access_token, athlete_id):
    return get_strava_api_data(access_token, f"athletes/{athlete_id}/stats")

def map_race_distance(distance_meters):
    if 4875 <= distance_meters <= 5125: return "5k Race"
    if 9750 <= distance_meters <= 10250: return "10k Race"
    if 20570 <= distance_meters <= 21625: return "Half Marathon Race"
    if 41140 <= distance_meters <= 43250: return "Marathon Race"
    return "Race (Non-Standard Distance)"

def calculate_friel_hr_zones(lthr):
    return {"zones": [
            {"min": 0, "max": int(lthr * 0.85)}, {"min": int(lthr * 0.85), "max": int(lthr * 0.89)},
            {"min": int(lthr * 0.90), "max": int(lthr * 0.94)}, {"min": int(lthr * 0.95), "max": int(lthr * 1.0)},
            {"min": int(lthr * 1.0), "max": -1}],
            "calculation_method": f"Joe Friel (LTHR: {lthr} bpm)"}

def calculate_friel_power_zones(ftp):
    return {"zones": [
            {"min": 0, "max": int(ftp * 0.55)}, {"min": int(ftp * 0.55), "max": int(ftp * 0.74)},
            {"min": int(ftp * 0.75), "max": int(ftp * 0.89)}, {"min": int(ftp * 0.90), "max": int(ftp * 1.04)},
            {"min": int(ftp * 1.05), "max": int(ftp * 1.20)}, {"min": int(ftp * 1.20), "max": int(ftp * 1.50)},
            {"min": int(ftp * 1.50), "max": -1}],
            "calculation_method": f"Joe Friel (Estimated FTP: {ftp} W)"}

def analyze_activity(activity, streams, zones):
    # --- FIX: Initialize zone dictionaries with string keys ---
    analyzed = {"id": activity['id'], "name": activity['name'], "type": activity['type'],
                 "start_date": activity['start_date_local'], "is_race": activity.get('workout_type') == 1,
                 "distance_km": round(activity.get('distance', 0) / 1000, 2),
                 "moving_time_minutes": round(activity.get('moving_time', 0) / 60, 2),
                 "total_elevation_gain_meters": activity.get('total_elevation_gain', 0),
                 "average_speed_kph": round(activity.get('average_speed', 0) * 3.6, 2), # Convert m/s to km/h
                 "average_heartrate": activity.get('average_heartrate'),
                 "max_heartrate": activity.get('max_heartrate'),
                 "time_in_hr_zones": {f"Zone {i+1}": 0 for i in range(5)},
                 "time_in_power_zones": {f"Zone {i+1}": 0 for i in range(7)},
                 "private_note": activity.get('private_note', '')}

    if analyzed["is_race"]: analyzed["race_tag"] = map_race_distance(activity['distance'])
    if not streams: return analyzed
    time_data = streams.get('time', {}).get('data', [])
    if not time_data: return analyzed

    if 'heartrate' in streams:
        hr_data = streams['heartrate']['data']
        hr_zones = zones.get('heart_rate', {}).get('zones', [])
        zone_mins = [z['min'] for z in hr_zones]
        for i in range(1, len(hr_data)):
            duration = time_data[i] - time_data[i-1]
            hr = hr_data[i-1]
            zone_index = bisect.bisect_right(zone_mins, hr) - 1
            # --- FIX: Use the string key to update the value ---
            analyzed["time_in_hr_zones"][f"Zone {zone_index + 1}"] += duration
            
    if 'watts' in streams:
        power_data = streams['watts']['data']
        power_zones = zones.get('power', {}).get('zones', [])
        for i in range(1, len(power_data)):
            duration = time_data[i] - time_data[i-1]
            power = power_data[i-1]
            current_zone_index = 0
            for zone_index, zone_data in enumerate(power_zones):
                 if power >= zone_data['min']:
                    current_zone_index = zone_index
                 else:
                    break
            # --- FIX: Use the string key to update the value ---
            analyzed["time_in_power_zones"][f"Zone {current_zone_index + 1}"] += duration
            
    return analyzed

def find_valid_race_for_vdot(activities, access_token, friel_hr_zones):
    four_weeks_ago = datetime.now() - timedelta(weeks=4)
    for activity in activities:
        activity_date_str = activity['start_date_local'].split('T')[0]
        activity_date = datetime.strptime(activity_date_str, '%Y-%m-%d')
        if activity.get('workout_type') == 1 and activity_date > four_weeks_ago:
            streams = get_activity_streams(access_token, activity['id'])
            if streams and 'heartrate' in streams:
                race_analysis = analyze_activity(activity, streams, {"heart_rate": friel_hr_zones})
                total_time = sum(race_analysis['time_in_hr_zones'].values())
                high_intensity_time = race_analysis['time_in_hr_zones']["Zone 4"] + race_analysis['time_in_hr_zones']["Zone 5"]
                if total_time > 0 and (high_intensity_time / total_time) > 0.5:
                    return {"status": "VDOT Ready", "race_basis": f"{activity['name']} ({activity_date_str})"}
    return {"status": "HR Training Recommended", "reason": "No recent, high-intensity race found."}

def generate_content_from_prompt(prompt_text, **kwargs):
  
    try:
        response = model.generate_content(prompt_text, **kwargs)
        # The GenerativeModel response typically exposes .text; normalize to a string.
        return getattr(response, "text", str(response))
    except Exception as e:
        print(f"Error generating content from prompt: {e}")
        return ""

def get_current_week_plan(plan_text):
    """
    Parses the training plan to find the current week's sessions based on today's date.
    This version iterates line-by-line and handles markdown headers.
    """
    today = datetime.now().date()
    lines = plan_text.splitlines()
    
    current_week_lines = []
    in_current_week = False

    for line in lines:
        # This is the key change: strip leading whitespace and any leading markdown markers
        # Remove only leading whitespace and any leading '*' or '#' characters, preserving internal spaces
        clean_line = re.sub(r'^[\s\*\#]+', '', line)
        
        # Now check if the cleaned line looks like a week header
        if clean_line.lower().startswith('week ') and ':' in clean_line:
            
            # If we were already capturing the correct week, we've now hit the next one, so we can stop.
            if in_current_week:
                break
            
            # Reset state and check if THIS is the correct week
            in_current_week = False
            current_week_lines = []
            
            date_range_match = re.search(r'(\w+\s\d{1,2})[a-z]{2}\s*-\s*(\w+\s\d{1,2})[a-z]{2}', line)
            if date_range_match:
                try:
                    start_str, end_str = date_range_match.groups()
                    
                    start_date = datetime.strptime(f"{start_str} {today.year}", "%b %d %Y").date()
                    end_date = datetime.strptime(f"{end_str} {today.year}", "%b %d %Y").date()

                    # Handle year rollover
                    if start_date.month > end_date.month and today.month < start_date.month:
                        start_date = start_date.replace(year=today.year - 1)
                    elif start_date.month > end_date.month and today.month >= start_date.month:
                        end_date = end_date.replace(year=today.year + 1)

                    if start_date <= today <= end_date:
                        in_current_week = True
                        
                except ValueError:
                    # Couldn't parse dates from this line, so it's not a valid week header.
                    pass
        
        # If we are in the correct week, add the current line to our list
        if in_current_week:
            current_week_lines.append(line)

    if current_week_lines:
        return "\n".join(current_week_lines)
        
    return "Could not determine the current training week from your plan."

# --- Flask Routes ---

@app.context_processor
def inject_user():
    """Inject user data into all templates."""
    if 'athlete_id' in session:
        user_data = data_manager.load_user_data(session['athlete_id'])
        if user_data:
            return dict(athlete=user_data.get('athlete'))
    return dict(athlete=None)

@app.route("/")
def index():
    if 'athlete_id' in session:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        # --- MODIFIED FLOW ---
        if user_data and 'plan' in user_data:
            return redirect("/dashboard")
        elif user_data:
            # If they are logged in but have no plan, send to onboarding
            return redirect("/onboarding")

    # If no session, show the public landing page.
    return render_template('index.html', athlete=None)

STRAVA_VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN", "a_default_verify_token")

if os.getenv('APP_DEBUG_MODE') == 'True':
    @app.route("/debug-env")
    def debug_env():
        """
        A simple endpoint to display the environment variables
        and confirm how the application is configured.
        """
        env_vars = {key: value for key, value in os.environ.items()}
        flask_env = os.getenv('FLASK_ENV', 'Not Set')
        strava_client_id = os.getenv('STRAVA_CLIENT_ID', 'Not Set')
        strava_verify_token = os.getenv('STRAVA_VERIFY_TOKEN', 'Not Set')
        
        response_html = f"""
            <h1>Application Environment (DEBUG MODE)</h1>
            <h2>Key Variables:</h2>
            <ul>
                <li><b>FLASK_ENV:</b> {flask_env}</li>
                <li><b>STRAVA_CLIENT_ID:</b> {strava_client_id}</li>
                <li><b>STRAVA_VERIFY_TOKEN:</b> {strava_verify_token}</li>
            </ul>
            <hr>
            <h2>All Environment Variables:</h2>
            <pre>{json.dumps(env_vars, indent=4)}</pre>
        """
        return response_html

@app.route('/strava_webhook', methods=['GET', 'POST'])
def strava_webhook():
    if request.method == 'GET':
        # This is the initial subscription validation request from Strava
        hub_challenge = request.args.get('hub.challenge', '')
        hub_verify_token = request.args.get('hub.verify_token', '')
        if hub_verify_token == STRAVA_VERIFY_TOKEN:
            return json.dumps({'hub.challenge': hub_challenge})
        else:
            return 'Invalid verify token', 403
    
    elif request.method == 'POST':
        # This is an incoming event from Strava
        event_data = request.get_json()
        print(f"--- Webhook event received: {event_data} ---")
        
        # We only care about activity 'update' events
        if event_data.get('object_type') == 'activity' and event_data.get('aspect_type') == 'update':
            athlete_id = str(event_data.get('owner_id'))
            activity_id = str(event_data.get('object_id'))
            
            # Retrieve user data and token from the database
            user_data = data_manager.load_user_data(athlete_id)
            if not user_data or 'token' not in user_data:
                print(f"--- Could not find user data for athlete {athlete_id}. Skipping feedback generation. ---")
                return 'EVENT_RECEIVED', 200
            
            access_token = user_data['token']['access_token']
            training_plan = user_data.get('plan')
            
            if not training_plan:
                print(f"--- No training plan found for athlete {athlete_id}. Skipping feedback generation. ---")
                return 'EVENT_RECEIVED', 200
                
            if 'feedback_log' not in user_data:
                user_data['feedback_log'] = []

            feedback_log = user_data['feedback_log']
            
            processed_activity_ids = set()
            for entry in feedback_log:
                for act_id in entry.get('logged_activity_ids', [entry.get('activity_id')]):
                    processed_activity_ids.add(str(act_id))

            seven_days_ago = datetime.now() - timedelta(days=7)
            last_fetch_timestamp = int(seven_days_ago.timestamp())

            recent_activities_summary = get_strava_api_data(access_token, "athlete/activities", params={'after': last_fetch_timestamp, 'per_page': 100})
            
            new_activities_to_process = [act for act in recent_activities_summary if str(act['id']) not in processed_activity_ids]

            if not new_activities_to_process:
                print(f"--- No new activities to analyze in the last 7 days for athlete {athlete_id}. ---")
                return 'EVENT_RECEIVED', 200

            new_activities_to_process.reverse()
            
            analyzed_sessions = []
            for activity_summary in new_activities_to_process:
                activity = get_strava_api_data(access_token, f"activities/{activity_summary['id']}")
                if not activity: continue

                streams = get_activity_streams(access_token, activity['id'])
                friel_hr_zones = user_data.get('plan_data', {}).get('friel_hr_zones', {})
                analyzed_session = analyze_activity(activity, streams, {"heart_rate": friel_hr_zones})
                
                for key, seconds in analyzed_session["time_in_hr_zones"].items():
                    analyzed_session["time_in_hr_zones"][key] = format_seconds(seconds)
                analyzed_sessions.append(analyzed_session)

            if not analyzed_sessions:
                print(f"--- Found new activities, but could not analyze their details for athlete {athlete_id}. ---")
                return 'EVENT_RECEIVED', 200

            with open('prompts/feedback_prompt.txt', 'r') as f:
                template = jinja2.Template(f.read())
            prompt = template.render(
                training_plan=training_plan,
                feedback_log_json=json.dumps(feedback_log, indent=2),
                completed_sessions=json.dumps(analyzed_sessions, indent=2),
                training_history=user_data.get('training_history')
            )
            response = model.generate_content(prompt)
            feedback_markdown = response.text

            activity_names = [session['name'] for session in analyzed_sessions]
            # --- Create a descriptive name for the feedback entry using the activity names and passing through to gemini if more than 1 activity, referencing prompts/summarize_activities_prompt.txt if there is only one activity use it's name ---
            if len(activity_names) == 1:
                descriptive_name = f"Feedback for: {activity_names[0]}"
            else:
                summary_prompt_template = jinja2.Template(open('prompts/summarize_activities_prompt.txt').read())
                summary_prompt = summary_prompt_template.render(activity_names=activity_names)
                descriptive_name = generate_content_from_prompt(summary_prompt).strip()
                if not descriptive_name:
                    descriptive_name = f"Feedback for activities: {', '.join(activity_names)}"
            
            all_activity_ids = [s['id'] for s in analyzed_sessions]

            new_log_entry = {
                "activity_id": int(analyzed_sessions[0]['id']), # Use first ID for linking
                "activity_name": descriptive_name,
                "activity_date": (lambda raw=analyzed_sessions[0].get('start_date', ''): (
                    (lambda dp, tp: f"{dp.split('-')[2]}-{dp.split('-')[1]}-{dp.split('-')[0]} {tp.split('.')[0]}")(*raw.rstrip('Z').split('T'))
                    if 'T' in raw.rstrip('Z') else raw
                ))(),
                "feedback_markdown": feedback_markdown,
                "logged_activity_ids": all_activity_ids # Store all IDs
            }
            
            feedback_log.insert(0, new_log_entry)

            match = re.search(r"```markdown\n(.*?)```", feedback_markdown, re.DOTALL)
            if match:
                new_plan_markdown = match.group(1).strip()
                user_data['plan'] = new_plan_markdown
                print(f"--- Plan for athlete {athlete_id} has been updated! ---")
            
            data_manager.save_user_data(athlete_id, user_data)
            
            print(f"--- Successfully generated and saved feedback for athlete {athlete_id} triggered by webhook. ---")

        return 'EVENT_RECEIVED', 200

@app.route("/plan")
@login_required
def view_plan():
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        if not user_data or 'plan' not in user_data:
            return 'No training plan found. Please <a href="/onboarding">generate a plan</a> first.'
        plan_text = user_data['plan']
        plan_html = mistune.html(plan_text)
        return render_template('plan.html', plan_content=plan_html)
    except Exception as e:
        return f"An error occurred while retrieving the plan: {e}", 500

@app.route("/login")
def login():
    auth_redirect_url = (f"https://www.strava.com/oauth/authorize?client_id={STRAVA_CLIENT_ID}"
                       f"&redirect_uri={REDIRECT_URI}&response_type=code&scope={SCOPES}")
    return redirect(auth_redirect_url)

@app.route("/logout")
@login_required
def logout():
    """
    Logs the user out by clearing the session and deauthorizing the app with Strava.
    """
    try:
        # Get the access token from the user's data
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        access_token = user_data.get('token', {}).get('access_token')

        # If we have a token, tell Strava to deauthorize it
        if access_token:
            deauthorize_payload = {'access_token': access_token}
            requests.post("https://www.strava.com/oauth/deauthorize", data=deauthorize_payload)

    except Exception as e:
        # Even if deauthorization fails, we should still log them out locally.
        print(f"Could not deauthorize from Strava: {e}")
    finally:
        # Clear the session regardless of the outcome
        session.clear()
        flash("You have been successfully logged out.")

    return redirect("/")

@app.route("/callback")
def callback():
    try:
        # Step 1: Exchange auth code for a token from Strava
        auth_code = request.args.get('code')
        token_payload = {
            "client_id": STRAVA_CLIENT_ID, "client_secret": STRAVA_CLIENT_SECRET,
            "code": auth_code, "grant_type": "authorization_code"
        }
        token_response = requests.post("https://www.strava.com/oauth/token", data=token_payload)
        token_response.raise_for_status()
        token_data = token_response.json()
        
        athlete_id = str(token_data['athlete']['id'])

        # Step 2: Load existing user data
        user_data = data_manager.load_user_data(athlete_id)

        # Step 3: If it's a new user, create a clean record
        if not user_data:
            user_data = {
                'athlete_id': athlete_id,
                'token': token_data,
                'athlete': token_data.get('athlete', {})
            }
        # For an existing user, just update the token
        else:
            user_data['token'] = token_data

        # Step 4: Save the complete, correct user data back to the database
        data_manager.save_user_data(athlete_id, user_data)

        # Step 5: Log the user in and redirect
        session['athlete_id'] = athlete_id
        
        if 'plan' in user_data:
             return redirect("/dashboard")
        else:
             return redirect("/onboarding")

    except Exception as e:
        return f"An error occurred during authentication: {e}", 500

@app.route("/onboarding")
def onboarding():
    return render_template("onboarding.html")

@app.route("/generate_plan", methods=['POST'])
@login_required
def generate_plan():
    try:
        athlete_id = session['athlete_id']

        user_data = data_manager.load_user_data(athlete_id)
        if not user_data or 'token' not in user_data:
            return 'Could not find your session data. Please <a href="/login">log in</a> again.'

        if 'plan' in user_data and 'feedback_log' in user_data:
            # This logic for summarizing and archiving a previous plan remains the same.
            print(f"--- Found existing plan for athlete {athlete_id}. Generating summary... ---")
            with open('prompts/summarize_prompt.txt', 'r') as f:
                template = jinja2.Template(f.read())
            prompt = template.render(
                completed_plan=user_data['plan'],
                feedback_log_json=json.dumps(user_data['feedback_log'], indent=2)
            )
            response = model.generate_content(prompt)
            summary_text = response.text
            training_history = user_data.get('training_history', [])
            training_history.insert(0, {"summary": summary_text})
            user_data['training_history'] = training_history
            if 'archive' not in user_data:
                user_data['archive'] = []
            user_data['archive'].insert(0, {'plan': user_data['plan'], 'feedback_log': user_data['feedback_log']})
            del user_data['plan']
            del user_data['feedback_log']
        
        user_goal = request.form.get('user_goal')
        user_sessions_per_week = int(request.form.get('sessions_per_week'))
        user_hours_per_week = float(request.form.get('hours_per_week'))
        user_lifestyle_context = request.form.get('lifestyle_context')
        user_athlete_type = request.form.get('athlete_type')
        user_known_lthr = int(request.form.get('lthr'))
        user_known_ftp = int(request.form.get('ftp'))
        access_token = user_data['token']['access_token']

        print(f"--- Fetching Strava data for athlete {athlete_id} ---")
        strava_zones = get_strava_api_data(access_token, "athlete/zones")
        eight_weeks_ago = datetime.now() - timedelta(weeks=8)
        activities_summary = get_strava_api_data(access_token, "athlete/activities", params={'after': int(eight_weeks_ago.timestamp()), 'per_page': 200})
        athlete_stats = get_athlete_stats(access_token, athlete_id)
        friel_hr_zones = calculate_friel_hr_zones(user_known_lthr)
        friel_power_zones = calculate_friel_power_zones(user_known_ftp)
        vdot_data = find_valid_race_for_vdot(activities_summary, access_token, friel_hr_zones)
        analyzed_activities = []
        one_week_ago = datetime.now() - timedelta(weeks=1)
        for activity_summary in activities_summary:
            activity_date = datetime.strptime(activity_summary['start_date_local'], "%Y-%m-%dT%H:%M:%SZ")
            
            if activity_date > one_week_ago:
                activity_to_process = get_strava_api_data(access_token, f"activities/{activity_summary['id']}")
            else:
                activity_to_process = activity_summary

            streams = get_activity_streams(access_token, activity_to_process['id'])
            analyzed_activity = analyze_activity(activity_to_process, streams, {"heart_rate": friel_hr_zones, "power": friel_power_zones})
            
            for key, seconds in analyzed_activity["time_in_hr_zones"].items():
                 analyzed_activity["time_in_hr_zones"][key] = format_seconds(seconds)
            analyzed_activities.append(analyzed_activity)

        final_data_for_ai = {
            "athlete_goal": user_goal, "sessions_per_week": user_sessions_per_week,
            "hours_per_week": user_hours_per_week, "lifestyle_context": user_lifestyle_context,
            "athlete_type": user_athlete_type, "athlete_stats": athlete_stats,
            "strava_zones": strava_zones, "friel_hr_zones": friel_hr_zones,
            "friel_power_zones": friel_power_zones, "vdot_data": vdot_data,
            "analyzed_activities": analyzed_activities
        }

        with open('prompts/plan_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        prompt = template.render(
            athlete_goal=final_data_for_ai['athlete_goal'], sessions_per_week=final_data_for_ai['sessions_per_week'],
            athlete_type=final_data_for_ai['athlete_type'], lifestyle_context=final_data_for_ai['lifestyle_context'],
            training_history=user_data.get('training_history'), json_data=json.dumps(final_data_for_ai, indent=4)
        )
        
        print("--- Generating content from Gemini ---")
        response = model.generate_content(prompt)
        plan_text = response.text

        user_data['plan'] = plan_text
        user_data['plan_data'] = final_data_for_ai
        
        print(f"--- APP: About to save plan for athlete {athlete_id}. Plan length: {len(plan_text)} chars.")
        data_manager.save_user_data(athlete_id, user_data)
        print(f"--- APP: Save operation completed.")

        # VERIFICATION STEP: Immediately reload the data from DynamoDB
        print(f"--- APP: Verifying save operation by reloading data...")
        verified_user_data = data_manager.load_user_data(athlete_id)
        
        if 'plan' in verified_user_data:
            print(f"--- APP: SUCCESS! Reloaded data contains the plan.")
        else:
            print(f"--- APP: FAILURE! Reloaded data does NOT contain the plan.")
            # Optionally return an error here to make it obvious
            return "Error: The plan was generated but could not be saved to the database. Please check the logs.", 500

        plan_html = mistune.html(plan_text)
        return render_template('plan.html', plan_content=plan_html)
    
    except Exception as e:
        return f"An error occurred during plan generation: {e}", 500

@app.route("/api/weekly-summary")
@login_required
def weekly_summary_api():
    """API endpoint to get the weekly AI summary with smart caching."""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    if not user_data or 'plan' not in user_data:
        return jsonify({"error": "Plan not found"}), 404

    force_refresh = False
    now = datetime.now()
    
    # 1. Get identifiers for the current state
    week_identifier = f"{now.year}-{now.isocalendar().week}"
    current_plan_hash = hashlib.sha256(user_data['plan'].encode()).hexdigest()
    feedback_log = user_data.get('feedback_log', [])
    chat_log = user_data.get('chat_log', [])
    
    # Use the timestamp of the latest chat message as a trigger
    latest_chat_timestamp = chat_log[-1]['timestamp'] if chat_log else None
    latest_feedback_id = feedback_log[0]['activity_id'] if feedback_log else None

    if 'weekly_summaries' not in user_data:
        user_data['weekly_summaries'] = {}

    cached_summary_data = user_data['weekly_summaries'].get(week_identifier)

    if not cached_summary_data:
        print("CACHE: No summary found. Forcing refresh.")
        force_refresh = True
    else:
        cached_timestamp = datetime.fromisoformat(cached_summary_data.get('timestamp'))
        if (now - cached_timestamp) > timedelta(hours=24):
            print("CACHE: Summary is older than 24 hours. Forcing refresh.")
            force_refresh = True
        elif cached_summary_data.get('plan_hash') != current_plan_hash:
            print("CACHE: Plan has been updated. Forcing refresh.")
            force_refresh = True
        elif cached_summary_data.get('last_feedback_id') != latest_feedback_id:
            print("CACHE: New feedback has been added. Forcing refresh.")
            force_refresh = True
        # NEW CONDITION: Check if a new chat message has been added
        elif cached_summary_data.get('last_chat_timestamp') != latest_chat_timestamp:
            print("CACHE: New chat message has been added. Forcing refresh.")
            force_refresh = True

    if force_refresh:
        print("CACHE: Generating new summary from AI.")
        current_week_text = get_current_week_plan(user_data['plan'])
        
        with open('prompts/dashboard_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        prompt = template.render(
            today_date=now.strftime("%A, %B %d, %Y"),
            athlete_goal=user_data.get('plan_data', {}).get('athlete_goal', 'your goal'),
            training_plan=current_week_text,
            latest_feedback=feedback_log[0]['feedback_markdown'] if feedback_log else None,
            # Pass the chat history to the prompt
            chat_history=json.dumps(chat_log, indent=2) if chat_log else None
        )
        
        weekly_summary = generate_content_from_prompt(prompt).strip()
        
        # Save the new summary and all its context for future checks
        user_data['weekly_summaries'][week_identifier] = {
            'summary': weekly_summary,
            'timestamp': now.isoformat(),
            'plan_hash': current_plan_hash,
            'last_feedback_id': latest_feedback_id,
            'last_chat_timestamp': latest_chat_timestamp
        }
        data_manager.save_user_data(athlete_id, user_data)
    else:
        print("CACHE: Using cached summary.")
        weekly_summary = cached_summary_data['summary']
        
    return jsonify({'summary': weekly_summary})

@app.route("/api/refresh-weekly-summary", methods=['POST'])
@login_required
def refresh_weekly_summary():
    """Clears the cached weekly summary, forcing a regeneration on next load."""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    today = datetime.now()
    week_identifier = f"{today.year}-{today.isocalendar().week}"

    if 'weekly_summaries' in user_data and week_identifier in user_data['weekly_summaries']:
        del user_data['weekly_summaries'][week_identifier]
        data_manager.save_user_data(athlete_id, user_data)
        return jsonify({'status': 'success', 'message': 'Cache cleared.'})
        
    return jsonify({'status': 'no_op', 'message': 'No cache to clear.'})

@app.route("/dashboard")
@login_required
def dashboard():
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)

    if not user_data or 'plan' not in user_data:
        return redirect('/onboarding')

    current_week_text = get_current_week_plan(user_data['plan'])
    current_week_html = mistune.html(current_week_text)

    # Pop the user message and chat response from the session
    user_message = session.pop('user_message', None)
    chat_response_markdown = session.pop('chat_response', None)
    chat_response_html = mistune.html(chat_response_markdown) if chat_response_markdown else None

    return render_template(
        'dashboard.html',
        current_week_plan=current_week_html,
        user_message=user_message,
        chat_response=chat_response_html
    )
@app.route("/chat", methods=['POST'])
@login_required
def chat():
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    user_message = request.form.get('user_message')

    if not user_message:
        return redirect('/dashboard')

    # Load existing chat history or start a new one
    chat_history = user_data.get('chat_log', [])

    # Add the new user message to the history
    chat_history.append({'role': 'user', 'content': user_message, 'timestamp': datetime.now().isoformat()})

    # Prepare other context
    training_plan = user_data.get('plan', 'No plan available.')
    feedback_log = user_data.get('feedback_log', [])

    # Generate the AI's response using the full history
    with open('prompts/chat_prompt.txt', 'r') as f:
        template = jinja2.Template(f.read())

    prompt = template.render(
        training_plan=training_plan,
        feedback_log_json=json.dumps(feedback_log, indent=2),
        chat_history_json=json.dumps(chat_history, indent=2)
    )

    ai_response_markdown = generate_content_from_prompt(prompt).strip()

    # Add the AI's response to the history
    chat_history.append({'role': 'model', 'content': ai_response_markdown, 'timestamp': datetime.now().isoformat()})
    user_data['chat_log'] = chat_history

    # Check for and apply a new plan if the AI provided one
    match = re.search(r"```markdown\n(.*?)```", ai_response_markdown, re.DOTALL)
    if match:
        new_plan_markdown = match.group(1).strip()
        user_data['plan'] = new_plan_markdown
        print(f"--- Plan for athlete {athlete_id} has been updated via chat! ---")

        # Invalidate the weekly summary cache
        today = datetime.now()
        week_identifier = f"{today.year}-{today.isocalendar().week}"
        if 'weekly_summaries' in user_data and week_identifier in user_data['weekly_summaries']:
            del user_data['weekly_summaries'][week_identifier]
            print(f"--- Invalidated weekly summary cache for {week_identifier}. ---")

    data_manager.save_user_data(athlete_id, user_data)

    # Store the user's message and the AI's response in the session
    session['user_message'] = user_message
    session['chat_response'] = ai_response_markdown

    return redirect('/dashboard')

@app.route("/api/get-feedback")
@login_required
def get_feedback_api():
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        access_token = user_data.get('token', {}).get('access_token')

        if not access_token:
            return jsonify({'error': 'Authentication error'}), 401

        training_plan = user_data.get('plan')
        if not training_plan:
            return jsonify({'message': 'No training plan found. Please <a href="/onboarding">generate a plan</a> first.'})

        if 'feedback_log' not in user_data:
            user_data['feedback_log'] = []

        feedback_log = user_data['feedback_log']
        
        processed_activity_ids = set()
        for entry in feedback_log:
            for act_id in entry.get('logged_activity_ids', [entry.get('activity_id')]):
                processed_activity_ids.add(str(act_id))

        seven_days_ago = datetime.now() - timedelta(days=7)
        last_fetch_timestamp = int(seven_days_ago.timestamp())

        recent_activities_summary = get_strava_api_data(access_token, "athlete/activities", params={'after': last_fetch_timestamp, 'per_page': 100})
        
        new_activities_to_process = [act for act in recent_activities_summary if str(act['id']) not in processed_activity_ids]

        if not new_activities_to_process:
            if feedback_log:
            # No new activities, but there is old feedback to show
                feedback_html = mistune.html(feedback_log[0]['feedback_markdown'])
                return jsonify({'feedback_html': feedback_html}) # FIXED
            else:
                return jsonify({'message': "No new activities to analyze in the last 7 days."})

        new_activities_to_process.reverse()
        
        analyzed_sessions = []
        for activity_summary in new_activities_to_process:
            activity = get_strava_api_data(access_token, f"activities/{activity_summary['id']}")
            if not activity: continue

            streams = get_activity_streams(access_token, activity['id'])
            friel_hr_zones = user_data.get('plan_data', {}).get('friel_hr_zones', {})
            analyzed_session = analyze_activity(activity, streams, {"heart_rate": friel_hr_zones})
            
            for key, seconds in analyzed_session["time_in_hr_zones"].items():
                analyzed_session["time_in_hr_zones"][key] = format_seconds(seconds)
            analyzed_sessions.append(analyzed_session)

        if not analyzed_sessions:
            return jsonify({"message": "Found new activities, but could not analyze their details. Please try again."})

        with open('prompts/feedback_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        prompt = template.render(
            training_plan=training_plan,
            feedback_log_json=json.dumps(feedback_log, indent=2),
            completed_sessions=json.dumps(analyzed_sessions, indent=2),
            training_history=user_data.get('training_history')
        )
        response = model.generate_content(prompt)
        feedback_markdown = response.text

        activity_names = [session['name'] for session in analyzed_sessions]
        if len(activity_names) == 1:
            descriptive_name = f"Feedback for: {activity_names[0]}"
        else:
            summary_prompt_template = jinja2.Template(open('prompts/summarize_activities_prompt.txt').read())
            summary_prompt = summary_prompt_template.render(activity_names=activity_names)
            descriptive_name = generate_content_from_prompt(summary_prompt).strip()
            if not descriptive_name:
                descriptive_name = f"Feedback for activities: {', '.join(activity_names)}"
        
        all_activity_ids = [s['id'] for s in analyzed_sessions]

        new_log_entry = {
            "activity_id": int(analyzed_sessions[0]['id']),
            "activity_name": descriptive_name,
            "activity_date": (lambda raw=analyzed_sessions[0].get('start_date', ''): (
                (lambda dp, tp: f"{dp.split('-')[2]}-{dp.split('-')[1]}-{dp.split('-')[0]} {tp.split('.')[0]}")(*raw.rstrip('Z').split('T'))
                if 'T' in raw.rstrip('Z') else raw
            ))(),
            "feedback_markdown": feedback_markdown,
            "logged_activity_ids": all_activity_ids
        }
        
        feedback_log.insert(0, new_log_entry)

        match = re.search(r"```markdown\n(.*?)```", feedback_markdown, re.DOTALL)
        if match:
            new_plan_markdown = match.group(1).strip()
            user_data['plan'] = new_plan_markdown
        
        data_manager.save_user_data(athlete_id, user_data)

        feedback_html = mistune.html(feedback_markdown)
        return jsonify({'feedback_html': feedback_html})

    except Exception as e:
        return jsonify({'error': f"An error occurred: {e}"}), 500
    
@app.route("/feedback")
@login_required
def feedback():
    """Renders the loading page for feedback generation."""
    return render_template('feedback.html')
    
@app.route("/log")
@login_required
def coaching_log():
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    feedback_log = user_data.get('feedback_log', [])
    return render_template('coaching_log.html', log_entries=feedback_log)

@app.route("/delete_data")
@login_required
def delete_data():
    athlete_id = session['athlete_id']
    
    # Call the data manager to delete the user's record
    data_manager.delete_user_data(athlete_id)
    
    # Clear the session to log the user out
    session.clear()
    
    # Redirect to the homepage
    return redirect("/")

@app.route("/feedback/<int:activity_id>")
@login_required
def view_specific_feedback(activity_id):
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    feedback_log = user_data.get('feedback_log', [])
    for entry in feedback_log:
        if entry.get('activity_id') == activity_id:
            feedback_html = mistune.html(entry['feedback_markdown'])
            return render_template('feedback.html', feedback_content=feedback_html, activity_id=activity_id)
    return "Feedback for that activity could not be found.", 404

@app.route("/chat_log")
@login_required
def chat_log_list():
    """Displays a list of all chat conversations."""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    chat_history = user_data.get('chat_log', [])

    # Convert markdown to HTML for each message in the chat history
    for message in chat_history:
        if message['role'] == 'model':
            message['content'] = mistune.html(message['content'])

    return render_template('chat_log.html', chat_history=chat_history)

@app.route("/clear_chat", methods=['POST'])
@login_required
def clear_chat():
    """Permanently deletes all chat history (active and archived)."""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    # Delete both the active log and the archive
    if 'chat_log' in user_data:
        del user_data['chat_log']
    if 'chat_archive' in user_data:
        del user_data['chat_archive']
        
    data_manager.save_user_data(athlete_id, user_data)
    flash("Your chat history has been permanently deleted.")
        
    # Redirect back to the page the user came from (dashboard or chat_log)
    return redirect(request.referrer or url_for('dashboard'))

if __name__ == "__main__":
    app.run(debug=True, port=5000)