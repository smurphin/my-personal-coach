from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from datetime import datetime
import os
from data_manager import data_manager
from services.garmin_service import garmin_service
from utils.decorators import login_required
from crypto_manager import encrypt, decrypt

# Import S3 manager
try:
    from s3_manager import s3_manager, S3_AVAILABLE
except ImportError:
    print("⚠️  s3_manager not available - S3 storage disabled")
    S3_AVAILABLE = False
    s3_manager = None

# IMPORTANT: Only use S3 in production
USE_S3 = S3_AVAILABLE and os.getenv('FLASK_ENV') == 'production'

# NOTE: url_prefix='/admin' so all routes live under /admin/...
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Key in user_data for 2FA flow (cleared after OTP step or cancel). Stored in DB, not session,
# so the session cookie stays under 4KB (pickled MFA state is ~34KB).
GARMIN_MFA_FLOW_KEY = "_garmin_mfa_flow"


def _get_garmin_mfa_flow(user_data):
    """Return the pending MFA flow dict or None."""
    return user_data.get(GARMIN_MFA_FLOW_KEY)


def _clear_garmin_mfa_flow(athlete_id, user_data):
    """Remove 2FA flow from user_data and save."""
    if GARMIN_MFA_FLOW_KEY in user_data:
        del user_data[GARMIN_MFA_FLOW_KEY]
        data_manager.save_user_data(athlete_id, user_data)


@admin_bp.route("/connections")
@login_required
def connections():
    """Display connections page"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    garmin_connected = 'garmin_credentials' in user_data
    mfa_flow = _get_garmin_mfa_flow(user_data)
    garmin_mfa_pending = bool(mfa_flow)
    garmin_mfa_email = (mfa_flow or {}).get("email", "")

    return render_template(
        'connections.html',
        garmin_connected=garmin_connected,
        garmin_mfa_pending=garmin_mfa_pending,
        garmin_mfa_email=garmin_mfa_email,
    )


@admin_bp.route("/garmin_login", methods=['POST'])
@login_required
def garmin_login():
    """Connect Garmin account. Supports 2FA: if MFA is required, stores state and shows OTP form."""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)

    email = request.form.get('garmin_email')
    password = request.form.get('garmin_password')

    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(url_for('admin.connections'))

    from garmin_manager import GarminManager, serialize_mfa_state

    # Try 2FA-aware login first: if account has 2FA we get back state and show OTP form
    garmin_manager = GarminManager(email, password, return_on_mfa=True)
    success, mfa_state = garmin_manager.login_step1_mfa()

    if success and mfa_state is None:
        # No 2FA or already fully logged in
        tokenstore = garmin_manager.get_tokenstore()
        user_data['garmin_credentials'] = garmin_service.store_credentials(email, password, tokenstore=tokenstore)
        today = datetime.now()
        week_identifier = f"{today.year}-{today.isocalendar().week}"
        if 'weekly_summaries' in user_data and week_identifier in user_data['weekly_summaries']:
            del user_data['weekly_summaries'][week_identifier]
            print(f"--- Invalidated weekly summary cache for {week_identifier} due to new Garmin connection. ---")
        data_manager.save_user_data(athlete_id, user_data)
        flash("Successfully connected to Garmin!", "success")
        return redirect(url_for('admin.connections'))

    if mfa_state is not None:
        # 2FA required: store state in user_data (not session) so cookie stays under 4KB
        encoded = serialize_mfa_state(mfa_state)
        if encoded:
            user_data[GARMIN_MFA_FLOW_KEY] = {
                "email": email,
                "password_encrypted": encrypt(password),
                "state_encrypted": encrypt(encoded),
            }
            data_manager.save_user_data(athlete_id, user_data)
            flash("Enter the verification code sent to your email or phone.", "info")
            return redirect(url_for('admin.connections'))
        # Serialization failed
        flash("Could not continue with 2FA. Please try again.", "error")
        return redirect(url_for('admin.connections'))

    # login_step1_mfa returned (False, None) – login failed (e.g. bad credentials)
    flash("Could not connect to Garmin. Please check your credentials.", "error")
    return redirect(url_for('admin.connections'))


@admin_bp.route("/garmin_otp", methods=['POST'])
@login_required
def garmin_otp():
    """Complete Garmin connection with 2FA OTP."""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    mfa_flow = _get_garmin_mfa_flow(user_data)

    otp = request.form.get('garmin_otp')
    if not otp or not mfa_flow:
        _clear_garmin_mfa_flow(athlete_id, user_data)
        flash("Session expired or missing OTP. Please try connecting again.", "error")
        return redirect(url_for('admin.connections'))

    email = mfa_flow.get("email")
    encrypted_password = mfa_flow.get("password_encrypted")
    encrypted_state = mfa_flow.get("state_encrypted")
    if not email or not encrypted_password or not encrypted_state:
        _clear_garmin_mfa_flow(athlete_id, user_data)
        flash("Invalid session state. Please try connecting again.", "error")
        return redirect(url_for('admin.connections'))

    try:
        password = decrypt(encrypted_password)
        encoded_state = decrypt(encrypted_state)
    except Exception as e:
        print(f"Garmin 2FA decrypt error: {e}")
        _clear_garmin_mfa_flow(athlete_id, user_data)
        flash("Session invalid. Please try connecting again.", "error")
        return redirect(url_for('admin.connections'))

    if not password or not encoded_state:
        _clear_garmin_mfa_flow(athlete_id, user_data)
        flash("Session invalid. Please try connecting again.", "error")
        return redirect(url_for('admin.connections'))

    from garmin_manager import GarminManager, deserialize_mfa_state

    mfa_state = deserialize_mfa_state(encoded_state)
    if not mfa_state:
        _clear_garmin_mfa_flow(athlete_id, user_data)
        flash("Session expired. Please start the Garmin connection again.", "error")
        return redirect(url_for('admin.connections'))

    # Clear MFA flow before proceeding so a repeat submit doesn't reuse it
    _clear_garmin_mfa_flow(athlete_id, user_data)
    user_data = data_manager.load_user_data(athlete_id)

    try:
        garmin_manager = GarminManager(email, password)
        if garmin_manager.resume_login(mfa_state, otp):
            tokenstore = garmin_manager.get_tokenstore()
            user_data['garmin_credentials'] = garmin_service.store_credentials(email, password, tokenstore=tokenstore)
            today = datetime.now()
            week_identifier = f"{today.year}-{today.isocalendar().week}"
            if 'weekly_summaries' in user_data and week_identifier in user_data['weekly_summaries']:
                del user_data['weekly_summaries'][week_identifier]
                print(f"--- Invalidated weekly summary cache for {week_identifier} due to new Garmin connection. ---")
            data_manager.save_user_data(athlete_id, user_data)
            flash("Successfully connected to Garmin!", "success")
        else:
            flash("Invalid verification code or session expired. Please try connecting again.", "error")
    except (TypeError, ValueError, KeyError) as e:
        print(f"Garmin 2FA error: {e}")
        import traceback
        traceback.print_exc()
        flash("Something went wrong during verification. Please try connecting again from the start.", "error")

    return redirect(url_for('admin.connections'))


@admin_bp.route("/garmin_mfa_cancel", methods=['POST'])
@login_required
def garmin_mfa_cancel():
    """Cancel 2FA flow and clear stored state."""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    _clear_garmin_mfa_flow(athlete_id, user_data)
    flash("Garmin connection cancelled.", "info")
    return redirect(url_for('admin.connections'))


@admin_bp.route("/garmin_disconnect", methods=['POST'])
@login_required
def garmin_disconnect():
    """Disconnect Garmin account"""
    athlete_id = session['athlete_id']
    user_data = data_manager.load_user_data(athlete_id)
    _clear_garmin_mfa_flow(athlete_id, user_data)

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
    
    # Restore feedback_log from archive (load from S3 if archive is offloaded)
    from utils.archive_loader import get_user_archive, save_user_archive_to_s3
    archive = get_user_archive(athlete_id, user_data)
    if len(archive) > 0:
        archived_plan = archive[0]
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
    if len(archive) > 0 and 'completed_date' in archive[0]:
        archive_after_rollback = archive[1:]
        save_user_archive_to_s3(athlete_id, archive_after_rollback)
        # Ensure DynamoDB doesn't hold archive (already empty when offloaded)
        user_data.pop('archive', None)
    
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
    
    # 1. Restore from archive[0].feedback_log if it exists (load from S3 if offloaded)
    from utils.archive_loader import get_user_archive, save_user_archive_to_s3
    archive = get_user_archive(athlete_id, user_data)
    if len(archive) > 0:
        archived_feedback_log = archive[0].get('feedback_log', [])
        
        if archived_feedback_log:
            for entry in archived_feedback_log:
                activity_id = entry.get('activity_id')
                if activity_id not in current_activity_ids:
                    current_feedback_log.append(entry)
                    current_activity_ids.add(activity_id)
                    restored_count += 1
            
            # Remove feedback_log from archive entry and save back to S3
            if 'feedback_log' in archive[0]:
                archive[0] = {k: v for k, v in archive[0].items() if k != 'feedback_log'}
                save_user_archive_to_s3(athlete_id, archive)
    
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


@admin_bp.route("/tidy_storage", methods=["POST"])
@login_required
def tidy_storage():
    """
    Run the same trim/archive logic as safe_save_user_data without generating a new plan.
    Use this to shrink your DynamoDB item (archive → S3, trim plan_data, feedback_log, chat_log)
    so you stay under the 400 KB limit. Safe to run anytime.
    """
    athlete_id = session["athlete_id"]
    try:
        from routes.api_routes import safe_save_user_data
        user_data = data_manager.load_user_data(athlete_id)
        safe_save_user_data(athlete_id, user_data)
        flash("Storage optimized: archive and large data trimmed/archived to S3. Your data is unchanged.", "success")
    except Exception as e:
        print(f"Error in tidy_storage: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Optimization failed: {e}", "error")
    return redirect(url_for("admin.connections"))


@admin_bp.route("/api/trigger_feedback", methods=["POST"])
def trigger_feedback_api():
    """
    Remote trigger to process Strava activities and generate feedback for a given athlete.
    
    This is intended for admin/ops use (e.g. from AppRunner) and is protected by a shared secret.
    """
    athlete_id = request.args.get("athlete_id", type=int)
    secret = request.args.get("secret", type=str)
    expected_secret = os.getenv("FEEDBACK_TRIGGER_SECRET")

    if not athlete_id:
        return jsonify({"error": "Missing athlete_id parameter"}), 400

    if not secret or not expected_secret or secret != expected_secret:
        print(f"⚠️  Invalid or missing secret for trigger_feedback_api (athlete_id={athlete_id})")
        return jsonify({"error": "Invalid or missing secret parameter"}), 403

    try:
        # Import here to avoid circular imports at module load time
        from routes.api_routes import _trigger_webhook_processing

        print(f"🚀 Triggering feedback processing for athlete {athlete_id} via admin API")
        _trigger_webhook_processing(athlete_id)
        return jsonify({"status": "ok", "message": f"Feedback processing triggered for athlete {athlete_id}"}), 200
    except Exception as e:
        print(f"❌ Error in trigger_feedback_api for athlete {athlete_id}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to trigger feedback: {e}"}), 500
