import os
import requests
import json
from flask import Flask, request, redirect, render_template
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

# --- Configuration ---
STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
STRAVA_API_URL = "https://www.strava.com/api/v3"
REDIRECT_URI = "http://127.0.0.1:5000/callback"
SCOPES = "read,activity:read_all,profile:read_all"

GCP_PROJECT_ID = "my-personal-coach-472007"
GCP_LOCATION = "europe-west1"
DATA_CACHE_FILE = "strava_data.json"
PLAN_FILE = "training_plan.md"
TOKEN_CACHE_FILE = "strava_token.json"

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
                 if hr >= zone_data['min']:
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
                high_intensity_time = race_analysis['time_in_hr_zones'][3] + race_analysis['time_in_hr_zones'][4]
                if total_time > 0 and (high_intensity_time / total_time) > 0.5:
                    return {"status": "VDOT Ready", "race_basis": f"{activity['name']} ({activity_date_str})"}
    return {"status": "HR Training Recommended", "reason": "No recent, high-intensity race found."}

# --- Flask Routes ---

@app.route("/")
def home():
    plan_exists = os.path.exists(PLAN_FILE)
    feedback_link = '<p><a href="/feedback" style="font-size: 24px;">Get Feedback on Your Last Session</a></p>' if plan_exists else ''
    return f'''
        <a href="/login" style="font-size: 24px;">Generate/Refresh Your Training Plan</a>
        {feedback_link}
        <p><small>(Note: Logging in will fetch fresh data from Strava and overwrite your cache)</small></p>
    '''

@app.route("/login")
def login():
    if os.path.exists(DATA_CACHE_FILE): os.remove(DATA_CACHE_FILE)
    if os.path.exists(PLAN_FILE): os.remove(PLAN_FILE)
    if os.path.exists(TOKEN_CACHE_FILE): os.remove(TOKEN_CACHE_FILE)
    
    auth_redirect_url = (f"https://www.strava.com/oauth/authorize?client_id={STRAVA_CLIENT_ID}"
                       f"&redirect_uri={REDIRECT_URI}&response_type=code&scope={SCOPES}")
    return redirect(auth_redirect_url)

@app.route("/callback")
def callback():
    try:
        # Step 1: Handle the OAuth callback from Strava
        auth_code = request.args.get('code')
        if not auth_code:
            return "Authentication error: No code provided. Please try logging in again."

        # Step 2: Exchange the auth code for an access token
        token_payload = {
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "code": auth_code,
            "grant_type": "authorization_code"
        }
        token_response = requests.post("https://www.strava.com/oauth/token", data=token_payload)
        token_response.raise_for_status()
        token_data = token_response.json()

        # Step 3: Save the token data to our cache file
        with open(TOKEN_CACHE_FILE, 'w') as f:
            json.dump(token_data, f)

        # Step 4: Redirect the user to the new onboarding form to input their goals
        return redirect("/onboarding")

    except Exception as e:
        return f"An error occurred during authentication: {e}", 500

@app.route("/onboarding")
def onboarding():
    return render_template("onboarding.html")

@app.route("/generate_plan", methods=['POST'])
def generate_plan():
    try:
        # --- Step 1: Get User Input From Form ---
        user_goal = request.form.get('user_goal')
        user_sessions_per_week = int(request.form.get('sessions_per_week'))
        user_known_lthr = int(request.form.get('lthr'))
        user_known_ftp = int(request.form.get('ftp'))

        # --- Step 2: Load Token and Fetch Strava Data ---
        if not os.path.exists(TOKEN_CACHE_FILE):
            return 'No valid session. Please <a href="/login">log in</a> again.'
        with open(TOKEN_CACHE_FILE, 'r') as f:
            token_data = json.load(f)
        access_token = token_data['access_token']
        athlete_id = token_data['athlete']['id']

        print("--- Fetching new data from Strava API for plan generation ---")
        strava_zones = get_strava_api_data(access_token, "athlete/zones")
        activities_summary = get_strava_api_data(access_token, "athlete/activities?per_page=60")
        athlete_stats = get_athlete_stats(access_token, athlete_id)

        # --- Step 3: Analyze Data Using User Input ---
        friel_hr_zones = calculate_friel_hr_zones(user_known_lthr)
        friel_power_zones = calculate_friel_power_zones(user_known_ftp)
        vdot_data = find_valid_race_for_vdot(activities_summary, access_token, friel_hr_zones)

        analyzed_activities = []
        for activity in activities_summary:
            streams = get_activity_streams(access_token, activity['id'])
            all_friel_zones = {"heart_rate": friel_hr_zones, "power": friel_power_zones}
            analyzed_activity = analyze_activity(activity, streams, all_friel_zones)
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
            "vdot_paces": {"Easy": "5:30-6:10", "Threshold": "4:45", "Interval": "4:20"} if vdot_data["status"] == "VDOT Ready" else None,
            "analyzed_activities": analyzed_activities
        }

        with open(DATA_CACHE_FILE, 'w') as f:
            json.dump(final_data_for_ai, f, indent=4)

        with open('prompts/plan_prompt.txt', 'r') as f:
            prompt_template_string = f.read()

        template = jinja2.Template(prompt_template_string)
        prompt = template.render(
            athlete_goal=final_data_for_ai['athlete_goal'],
            sessions_per_week=final_data_for_ai['sessions_per_week'],
            json_data=json.dumps(final_data_for_ai, indent=4)
        )

        response = model.generate_content(prompt)

        # --- Step 5: Save Plan and Display It ---
        with open(PLAN_FILE, 'w') as f:
            f.write(response.text)

        # Convert markdown response to HTML
        plan_html = mistune.html(response.text)
        return render_template('plan.html', plan_content=plan_html)

    except Exception as e:
        return f"An error occurred during plan generation: {e}", 500

@app.route("/feedback")
def feedback():
    try:
        if not os.path.exists(TOKEN_CACHE_FILE):
            return 'No valid session. Please <a href="/login">log in</a> again to refresh your data.'
        with open(TOKEN_CACHE_FILE, 'r') as f:
            token_data = json.load(f)
        access_token = token_data['access_token']

        if not os.path.exists(PLAN_FILE):
            return 'No training plan found. Please <a href="/login">generate a plan</a> first.'
        with open(PLAN_FILE, 'r') as f:
            training_plan = f.read()

        latest_activity_list = get_strava_api_data(access_token, "athlete/activities", params={'per_page': 1})
        if not latest_activity_list:
            return "No recent activities found."
        latest_activity_id = latest_activity_list[0]['id']

        detailed_activity = get_strava_api_data(access_token, f"activities/{latest_activity_id}")
        
        friel_hr_zones = calculate_friel_hr_zones(160)
        
        streams = get_activity_streams(access_token, detailed_activity['id'])
        analyzed_session = analyze_activity(detailed_activity, streams, {"heart_rate": friel_hr_zones})
        
        for i, seconds in analyzed_session["time_in_hr_zones"].items():
            analyzed_session["time_in_hr_zones"][i] = format_seconds(seconds)

        with open('prompts/feedback_prompt.txt', 'r') as f:
            prompt_template_string = f.read()
            
        template = jinja2.Template(prompt_template_string)
        prompt = template.render(
            training_plan=training_plan,
            completed_session=json.dumps(analyzed_session, indent=4)
        )

        response = model.generate_content(prompt)
        return Markup(mistune.html(response.text))

    except Exception as e:
        return f"An error occurred during feedback generation: {e}", 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)