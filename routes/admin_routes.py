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

@admin_bp.route("/restore_inactive_plan", methods=['POST'])
@login_required
def restore_inactive_plan():
    """Restore the inactive plan back to active - complete rollback to pre-archive state"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    if 'inactive_plan' not in user_data:
        flash("No inactive plan found to restore.")
        return redirect(url_for('dashboard.dashboard'))
    
    # Complete rollback - restore everything exactly as it was
    inactive_plan = user_data['inactive_plan']
    
    # Restore the plan and structure
    user_data['plan'] = inactive_plan.get('plan')
    if 'plan_structure' in inactive_plan:
        user_data['plan_structure'] = inactive_plan.get('plan_structure')
    
    # Restore feedback_log from archive (it was stored there when archived)
    if 'archive' in user_data and len(user_data['archive']) > 0:
        archived_plan = user_data['archive'][0]
        if 'feedback_log' in archived_plan:
            user_data['feedback_log'] = archived_plan['feedback_log']
    
    # Remove all the flags and metadata that were added
    if 'no_active_plan' in user_data:
        del user_data['no_active_plan']
    if 'plan_completion_choice' in user_data:
        del user_data['plan_completion_choice']
    if 'plan_completion_prompted' in user_data:
        del user_data['plan_completion_prompted']
    
    # Remove the inactive_plan entry (complete rollback)
    del user_data['inactive_plan']
    
    # Remove the most recent archive entry (the one we just created)
    # This restores the archive to its previous state
    if 'archive' in user_data and len(user_data['archive']) > 0:
        # Check if the first archive entry matches what we archived
        # If it was archived today, remove it to complete the rollback
        first_archive = user_data['archive'][0]
        if 'completed_date' in first_archive:
            # Remove the most recent archive entry
            user_data['archive'].pop(0)
            if len(user_data['archive']) == 0:
                del user_data['archive']
    
    # Also remove the most recent training_history entry if it was added during archiving
    if 'training_history' in user_data and len(user_data['training_history']) > 0:
        # The summary was added at index 0, remove it
        user_data['training_history'].pop(0)
        if len(user_data['training_history']) == 0:
            del user_data['training_history']
    
    data_manager.save_user_data(athlete_id, user_data)
    flash("Plan completely restored - database rolled back to pre-archive state!")
    
    return redirect(url_for('dashboard.dashboard'))

@admin_bp.route("/reset_plan_completion_prompt", methods=['POST'])
@login_required
def reset_plan_completion_prompt():
    """Reset the plan completion prompt flag for testing"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    # Remove the flag so the prompt will show again if plan is finished
    if 'plan_completion_prompted' in user_data:
        del user_data['plan_completion_prompted']
        data_manager.save_user_data(athlete_id, user_data)
        flash("Plan completion prompt flag reset. The prompt will show again if your plan has finished.")
    else:
        flash("Plan completion prompt flag was not set.")
    
    return redirect(url_for('dashboard.dashboard'))

@admin_bp.route("/restore_feedback_log_from_archive", methods=['GET', 'POST'])
@login_required
def restore_feedback_log_from_archive():
    """Restore feedback_log entries from archive[0].feedback_log and S3"""
    # If GET request, show a simple confirmation form
    if request.method == 'GET':
        return '''
        <!DOCTYPE html>
        <html>
        <head><title>Restore Feedback Log</title></head>
        <body style="font-family: Arial; padding: 40px; background: #1a1a1a; color: white;">
            <h1>Restore Feedback Log from Archive & S3</h1>
            <p>This will restore all feedback_log entries from archive and S3.</p>
            <form method="POST" style="margin-top: 20px;">
                <button type="submit" style="padding: 10px 20px; background: #00A9FF; color: white; border: none; cursor: pointer; font-size: 16px;">
                    Restore Feedback Log
                </button>
            </form>
            <p style="margin-top: 20px;"><a href="/dashboard" style="color: #00A9FF;">Cancel</a></p>
        </body>
        </html>
        '''
    
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    
    # Get current feedback_log (if any)
    current_feedback_log = user_data.get('feedback_log', [])
    current_activity_ids = {entry.get('activity_id') for entry in current_feedback_log}
    restored_count = 0
    
    # 1. Restore from archive[0].feedback_log if it exists
    if 'archive' in user_data and len(user_data['archive']) > 0:
        archived_feedback_log = user_data['archive'][0].get('feedback_log', [])
        
        if archived_feedback_log:
            for entry in archived_feedback_log:
                activity_id = entry.get('activity_id')
                if activity_id not in current_activity_ids:
                    current_feedback_log.append(entry)
                    current_activity_ids.add(activity_id)
                    restored_count += 1
            
            # Remove feedback_log from archive entry (it's now restored)
            if 'feedback_log' in user_data['archive'][0]:
                del user_data['archive'][0]['feedback_log']
    
    # 2. Also check S3 for any stored feedback_log entries
    try:
        from s3_manager import s3_manager, S3_AVAILABLE
        import os
        
        if S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production':
            s3_key = f"athletes/{athlete_id}/feedback_log.json.gz"
            s3_feedback_log = s3_manager.load_large_data(s3_key)
            
            if s3_feedback_log:
                s3_restored = 0
                for entry in s3_feedback_log:
                    activity_id = entry.get('activity_id')
                    if activity_id not in current_activity_ids:
                        current_feedback_log.append(entry)
                        current_activity_ids.add(activity_id)
                        s3_restored += 1
                
                if s3_restored > 0:
                    restored_count += s3_restored
                    print(f"✅ Restored {s3_restored} entries from S3")
    except Exception as e:
        print(f"⚠️  Error loading feedback_log from S3: {e}")
    
    # Sort by activity_id (most recent first)
    current_feedback_log.sort(key=lambda x: x.get('activity_id', 0), reverse=True)
    
    user_data['feedback_log'] = current_feedback_log
    data_manager.save_user_data(athlete_id, user_data)
    
    if restored_count > 0:
        flash(f"Restored {restored_count} feedback log entries from archive and S3!")
    else:
        flash("No feedback_log entries found to restore.")
    
    return redirect(url_for('feedback.coaching_log'))
