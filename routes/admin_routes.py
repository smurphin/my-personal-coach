from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from datetime import datetime
import os
from data_manager import data_manager
from services.garmin_service import garmin_service
from utils.decorators import login_required

# Import S3 manager
try:
    from s3_manager import s3_manager, S3_AVAILABLE
except ImportError:
    print("⚠️  s3_manager not available - S3 storage disabled")
    S3_AVAILABLE = False
    s3_manager = None

# IMPORTANT: Only use S3 in production
USE_S3 = S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production'

admin_bp = Blueprint('admin', __name__)

@admin_bp.route("/connections")
@login_required
def connections():
    """Display connections page"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    garmin_connected = 'garmin_credentials' in user_data
    
    return render_template('connections.html', garmin_connected=garmin_connected)

@admin_bp.route("/garmin_login", methods=['POST'])
@login_required
def garmin_login():
    """Connect Garmin account"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)

    email = request.form.get('garmin_email')
    password = request.form.get('garmin_password')

    # Test login
    from garmin_manager import GarminManager
    garmin_manager = GarminManager(email, password)
    
    if garmin_manager.login():
        # Store encrypted credentials
        user_data['garmin_credentials'] = garmin_service.store_credentials(email, password)
        
        # Invalidate weekly summary cache
        today = datetime.now()
        week_identifier = f"{today.year}-{today.isocalendar().week}"
        if 'weekly_summaries' in user_data and week_identifier in user_data['weekly_summaries']:
            del user_data['weekly_summaries'][week_identifier]
            print(f"--- Invalidated weekly summary cache for {week_identifier} due to new Garmin connection. ---")
            
        data_manager.save_user_data(athlete_id, user_data)
        flash("Successfully connected to Garmin!", "success")
    else:
        flash("Could not connect to Garmin. Please check your credentials.", "error")

    return redirect(url_for('admin.connections'))

@admin_bp.route("/garmin_disconnect", methods=['POST'])
@login_required
def garmin_disconnect():
    """Disconnect Garmin account"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    # Remove Garmin credentials and all related data
    user_data.pop('garmin_credentials', None)
    user_data.pop('garmin_data', None)
    user_data.pop('garmin_history', None)
    user_data.pop('garmin_history_metadata', None)
    user_data.pop('garmin_cache', None)
    
    # === FIXED: Only clean up S3 in production ===
    if USE_S3:
        print("Cleaning up S3 storage (production mode)")
        s3_key = f"athletes/{athlete_id}/garmin_history_raw.json.gz"
        s3_manager.delete_large_data(s3_key)
    else:
        print("Skipping S3 cleanup (development mode)")
    
    # Invalidate weekly summary cache
    today = datetime.now()
    week_identifier = f"{today.year}-{today.isocalendar().week}"
    if 'weekly_summaries' in user_data and week_identifier in user_data['weekly_summaries']:
        del user_data['weekly_summaries'][week_identifier]
        print(f"--- Invalidated weekly summary cache for {week_identifier} due to Garmin disconnect. ---")
            
    data_manager.save_user_data(athlete_id, user_data)
    flash("Successfully disconnected from Garmin.", "success")
    
    return redirect(url_for('admin.connections'))

@admin_bp.route("/delete_data")
@login_required
def delete_data():
    """Delete all user data"""
    athlete_id = session['athlete_id']
    data_manager.delete_user_data(athlete_id)
    session.clear()
    return redirect("/")
