import os
import requests
import json
from flask import Flask, request, redirect, render_template, session
from markupsafe import Markup
import mistune
from dotenv import load_dotenv
from datetime import datetime, timedelta
import jinja2
import bisect

import vertexai
from vertexai.generative_models import GenerativeModel

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "a_default_secret_key_for_development")

# --- Configuration ---
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
STRAVA_API_URL = "https://www.strava.com/api/v3"
REDIRECT_URI = "http://127.0.0.1:5000/callback"
SCOPES = "read,activity:read_all,profile:read_all"

GCP_PROJECT_ID = "my-personal-coach-472007"
GCP_LOCATION = "europe-west1"
USERS_DATA_FILE = "users_data.json"

# --- Initialize Vertex AI ---
vertexai.init(project=GCP_PROJECT_ID, location=GCP_LOCATION)
model = GenerativeModel(model_name="gemini-2.5-pro")

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

def get_strava_api_data(access_token, endpoint, params=None):
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(f"{STRAVA_API_URL}/{endpoint}", headers=headers, params=params)
    response.raise_for_status()
    return response.json()

def get_activity_streams(access_token, activity_id):
    headers = {'Authorization': f'Bearer {access_token}'}
    params = {'keys': 'heartrate,time,watts', 'key_by_type': True}
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
    analyzed = {"id": activity['id'], "name": activity['name'], "type": activity['type'],
                 "start_date": activity['start_date_local'], "is_race": activity.get('workout_type') == 1,
                 "distance_km": round(activity.get('distance', 0) / 1000, 2),
                 "moving_time_minutes": round(activity.get('moving_time', 0) / 60, 2),
                 "time_in_hr_zones": {i: 0 for i in range(5)}, "time_in_power_zones": {i: 0 for i in range(7)},
                 "private_note": activity.get('private_note', '')}
    if analyzed["is_race"]: analyzed["race_tag"] = map_race_distance(activity['distance'])
    if not streams: return analyzed
    time_data = streams.get('time', {}).get('data', [])
    if not time_data: return analyzed

    if 'heartrate' in streams:
        hr_data = streams['heartrate']['data']
        hr_zones = zones.get('heart_rate', {}).get('zones', [])
        # Create a list of the minimum heart rate for each zone
        analyzed["time_in_hr_zones"] = {f"Zone {i+1}": 0 for i in range(len(hr_zones))}
        zone_mins = [z['min'] for z in hr_zones]

        for i in range(1, len(hr_data)):
            duration = time_data[i] - time_data[i-1]
            hr = hr_data[i-1]

            # Find the index of the zone this heart rate falls into
            zone_index = bisect.bisect_right(zone_mins, hr) - 1

            # Use the corrected zone_index to update the human-readable key
            # We add 1 to the index to match the "Zone X" naming convention
            analyzed["time_in_hr_zones"][f"Zone {zone_index + 1}"] += duration
            
    if 'watts' in streams:
        power_data = streams['watts']['data']
        power_zones = zones.get('power', {}).get('zones', [])
        for i in range(1, len(power_data)):
            duration = time_data[i] - time_data[i-1]
            power = power_data[i-1]
            
            # --- Refactored with descriptive names ---
            current_zone_index = 0
            for zone_index, zone_data in enumerate(power_zones):
                 if power >= zone_data['min']:
                    current_zone_index = zone_index
                 else:
                    break
            analyzed["time_in_power_zones"][current_zone_index] += duration

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

# --- User Data Management Functions ---

def load_all_user_data():
    """Loads all user data from the JSON file."""
    if not os.path.exists(USERS_DATA_FILE):
        return {}  # Return an empty dict if the file doesn't exist
    with open(USERS_DATA_FILE, 'r') as f:
        return json.load(f)

def save_all_user_data(data):
    """Saves all user data to the JSON file."""
    with open(USERS_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# --- Flask Routes ---

@app.route("/")
def home():
    athlete_data = None
    plan_exists = False

    # Check the session to see if a user is logged in
    if 'athlete_id' in session:
        athlete_id = session['athlete_id']
        all_users_data = load_all_user_data()
        
        user_data = all_users_data.get(athlete_id)
        if user_data:
            athlete_data = user_data.get('token', {}).get('athlete')
            plan_exists = 'plan' in user_data

    return render_template('index.html', athlete=athlete_data, plan_exists=plan_exists)

@app.route("/plan")
def view_plan():
    try:
        if 'athlete_id' not in session:
            return "You must be logged in to view a plan.", 401
        athlete_id = session['athlete_id']

        all_users_data = load_all_user_data()
        user_data = all_users_data.get(athlete_id)

        if not user_data or 'plan' not in user_data:
            return 'No training plan found. Please <a href="/onboarding">generate a plan</a> first.'

        plan_text = user_data['plan']
        plan_html = mistune.html(plan_text)
        return render_template('plan.html', plan_content=plan_html)

    except Exception as e:
        return f"An error occurred while retrieving the plan: {e}", 500

@app.route("/login")
def login():
    # This route now simply redirects to Strava for authentication.
    auth_redirect_url = (f"https://www.strava.com/oauth/authorize?client_id={STRAVA_CLIENT_ID}"
                       f"&redirect_uri={REDIRECT_URI}&response_type=code&scope={SCOPES}")
    return redirect(auth_redirect_url)

@app.route("/logout")
def logout():
    session.clear() # Clear the user's session
    return redirect("/")

@app.route("/callback")
def callback():
    try:
        # Step 1: Exchange auth code for a token
        auth_code = request.args.get('code')
        token_payload = {
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "code": auth_code,
            "grant_type": "authorization_code"
        }
        token_response = requests.post("https://www.strava.com/oauth/token", data=token_payload)
        token_response.raise_for_status()
        token_data = token_response.json()
        
        athlete_id = str(token_data['athlete']['id']) # Use athlete_id as the key

        # Step 2: Load existing user data and update it
        all_users_data = load_all_user_data()
        
        # Find the user or create a new entry
        if athlete_id not in all_users_data:
            all_users_data[athlete_id] = {} # Create a new user record
        
        # Update the user's record with the new token
        all_users_data[athlete_id]['token'] = token_data
        
        # Save the updated data back to the file
        save_all_user_data(all_users_data)

        # Step 3: Store the user's ID in the session to "log them in"
        session['athlete_id'] = athlete_id
        
        # Step 4: Check if they have a plan, if not, redirect to onboarding
        if 'plan' in all_users_data[athlete_id]:
             return redirect("/")
        else:
             return redirect("/onboarding")

    except Exception as e:
        return f"An error occurred during authentication: {e}", 500

@app.route("/onboarding")
def onboarding():
    return render_template("onboarding.html")

@app.route("/generate_plan", methods=['POST'])
def generate_plan():
    try:
        # --- Step 1: Check for Logged-in User ---
        if 'athlete_id' not in session:
            return "You must be logged in to generate a plan.", 401
        athlete_id = session['athlete_id']

        # --- Step 2: Get User Input & Load User Data ---
        user_goal = request.form.get('user_goal')
        user_sessions_per_week = int(request.form.get('sessions_per_week'))
        user_known_lthr = int(request.form.get('lthr'))
        user_known_ftp = int(request.form.get('ftp'))

        all_users_data = load_all_user_data()
        user_data = all_users_data.get(athlete_id)
        if not user_data or 'token' not in user_data:
            return 'Could not find your session data. Please <a href="/login">log in</a> again.'
        
        access_token = user_data['token']['access_token']

        # --- Step 3: Fetch Strava Data & Analyze ---
        print(f"--- Fetching Strava data for athlete {athlete_id} ---")
        strava_zones = get_strava_api_data(access_token, "athlete/zones")
        activities_summary = get_strava_api_data(access_token, "athlete/activities?per_page=60")
        
        friel_hr_zones = calculate_friel_hr_zones(user_known_lthr)
        friel_power_zones = calculate_friel_power_zones(user_known_ftp)
        vdot_data = find_valid_race_for_vdot(activities_summary, access_token, friel_hr_zones)
        
        analyzed_activities = []
        for activity in activities_summary:
            streams = get_activity_streams(access_token, activity['id'])
            analyzed_activity = analyze_activity(activity, streams, {"heart_rate": friel_hr_zones, "power": friel_power_zones})
            for key, seconds in analyzed_activity["time_in_hr_zones"].items():
                 analyzed_activity["time_in_hr_zones"][key] = format_seconds(seconds)
            analyzed_activities.append(analyzed_activity)

        # --- Step 4: Prepare Data and Call AI ---
        final_data_for_ai = {
            "athlete_goal": user_goal,
            "sessions_per_week": user_sessions_per_week,
            "strava_zones": strava_zones,
            "friel_hr_zones": friel_hr_zones,
            "friel_power_zones": friel_power_zones,
            "vdot_data": vdot_data,
            "analyzed_activities": analyzed_activities
        }

        with open('prompts/plan_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        prompt = template.render(
            athlete_goal=final_data_for_ai['athlete_goal'],
            sessions_per_week=final_data_for_ai['sessions_per_week'],
            json_data=json.dumps(final_data_for_ai, indent=4)
        )
        response = model.generate_content(prompt)
        plan_text = response.text

        # --- Step 5: Save Plan to User's Record and Display It ---
        all_users_data[athlete_id]['plan'] = plan_text
        all_users_data[athlete_id]['plan_data'] = final_data_for_ai # Cache the data used for the plan
        save_all_user_data(all_users_data)
        
        plan_html = mistune.html(plan_text)
        return render_template('plan.html', plan_content=plan_html)

    except Exception as e:
        return f"An error occurred during plan generation: {e}", 500

@app.route("/feedback")
def feedback():
    try:
        # --- Step 1: Check for Logged-in User ---
        if 'athlete_id' not in session:
            return "You must be logged in to get feedback.", 401
        athlete_id = session['athlete_id']

        # --- Step 2: Load User Data and Get Plan/Token ---
        all_users_data = load_all_user_data()
        user_data = all_users_data.get(athlete_id)
        if not user_data or 'token' not in user_data:
            return 'Could not find your session data. Please <a href="/login">log in</a> again.'

        if 'plan' not in user_data:
            return 'No training plan found. Please <a href="/onboarding">generate a plan</a> first.'
        
        training_plan = user_data['plan']
        access_token = user_data['token']['access_token']
        friel_hr_zones = user_data.get('plan_data', {}).get('friel_hr_zones')
        if not friel_hr_zones:
             # Fallback if plan_data wasn't saved correctly
             friel_hr_zones = calculate_friel_hr_zones(160) 

        # --- Step 3: Get Latest Activity and Analyze ---
        latest_activity_list = get_strava_api_data(access_token, "athlete/activities", params={'per_page': 1})
        if not latest_activity_list:
            return "No recent activities found."
        
        latest_activity_id = latest_activity_list[0]['id']
        detailed_activity = get_strava_api_data(access_token, f"activities/{latest_activity_id}")
        streams = get_activity_streams(access_token, detailed_activity['id'])
        analyzed_session = analyze_activity(detailed_activity, streams, {"heart_rate": friel_hr_zones})
        
        for key, seconds in analyzed_session["time_in_hr_zones"].items():
            analyzed_session["time_in_hr_zones"][key] = format_seconds(seconds)

        # --- Step 4: Call AI and Return Feedback ---
        with open('prompts/feedback_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        prompt = template.render(
            training_plan=training_plan,
            completed_session=json.dumps(analyzed_session, indent=4)
        )
        response = model.generate_content(prompt)
        
        feedback_html = mistune.html(response.text)
        return render_template('feedback.html', feedback_content=feedback_html)

    except Exception as e:
        return f"An error occurred during feedback generation: {e}", 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)