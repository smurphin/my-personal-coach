from flask import Blueprint, redirect, request, session, flash
from config import Config
from data_manager import data_manager
from services.strava_service import strava_service
from utils.decorators import login_required

auth_bp = Blueprint('auth', __name__)

@auth_bp.route("/login")
def login():
    """Redirect to Strava OAuth authorization"""
    auth_redirect_url = (
        f"https://www.strava.com/oauth/authorize?client_id={Config.STRAVA_CLIENT_ID}"
        f"&redirect_uri={Config.REDIRECT_URI}&response_type=code&scope={Config.SCOPES}"
    )
    return redirect(auth_redirect_url)

@auth_bp.route("/callback")
def callback():
    """Handle OAuth callback from Strava"""
    try:
        # Step 1: Exchange auth code for token
        auth_code = request.args.get('code')
        token_data = strava_service.exchange_token(auth_code)
        
        athlete_id = str(token_data['athlete']['id'])

        # Step 2: Load existing user data
        user_data = data_manager.load_user_data(athlete_id)

        # Step 3: Create or update user record
        if not user_data:
            user_data = {
                'athlete_id': athlete_id,
                'token': token_data,
                'athlete': token_data.get('athlete', {})
            }
        else:
            user_data['token'] = token_data

        # Step 4: Save user data
        data_manager.save_user_data(athlete_id, user_data)

        # Step 5: Log the user in
        session['athlete_id'] = athlete_id
        
        if 'plan' in user_data:
            return redirect("/dashboard")
        else:
            return redirect("/onboarding")

    except Exception as e:
        return f"An error occurred during authentication: {e}", 500

@auth_bp.route("/logout")
@login_required
def logout():
    """Log the user out and deauthorize from Strava"""
    try:
        athlete_id = session['athlete_id']
        user_data = data_manager.load_user_data(athlete_id)
        access_token = user_data.get('token', {}).get('access_token')

        if access_token:
            strava_service.deauthorize(access_token)

    except Exception as e:
        print(f"Could not deauthorize from Strava: {e}")
    finally:
        session.clear()
        flash("You have been successfully logged out.")

    return redirect("/")
