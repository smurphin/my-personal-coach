import functools
import requests
from flask import session, redirect, flash

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
