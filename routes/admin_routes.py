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


def _admin_athlete_ids():
    """Return set of athlete_ids allowed to use admin-only UI (plan archive, etc.). Empty = any logged-in user."""
    raw = os.getenv("ADMIN_ATHLETE_IDS") or ""
    if not raw.strip():
        return None  # None = no restriction
    return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}


def _can_access_plan_archive_ui(athlete_id):
    """True if the given athlete_id is allowed to use the plan archive UI (list/restore)."""
    allow = _admin_athlete_ids()
    if allow is None:
        return True
    return int(athlete_id) in allow

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
        can_access_plan_archive=_can_access_plan_archive_ui(athlete_id),
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


def _archive_entry_summary(entry):
    """Return a short summary for an archive entry: weeks count, date, reason."""
    pv2 = entry.get("plan_v2")
    weeks_count = len(pv2.get("weeks", [])) if pv2 and isinstance(pv2, dict) else 0
    total_sessions = 0
    if pv2 and isinstance(pv2, dict):
        for w in pv2.get("weeks") or []:
            total_sessions += len(w.get("sessions") or [])
    date_str = entry.get("completed_date") or "—"
    if date_str and len(str(date_str)) > 19:
        date_str = str(date_str)[:19].replace("T", " ")
    reason = entry.get("reason") or "—"
    return {"weeks": weeks_count, "sessions": total_sessions, "date": date_str, "reason": reason}


@admin_bp.route("/plan_archive", methods=["GET"])
@login_required
def plan_archive():
    """List plan archive entries (newest first) with option to restore any snapshot. Restricted if ADMIN_ATHLETE_IDS is set."""
    athlete_id = session["athlete_id"]
    if not _can_access_plan_archive_ui(athlete_id):
        flash("Plan archive is only available to designated admin users.", "error")
        return redirect(url_for("admin.connections"))
    user_data = data_manager.load_user_data(athlete_id)
    from utils.archive_loader import get_user_archive
    archive = get_user_archive(athlete_id, user_data)
    entries = []
    for i, entry in enumerate(archive):
        summary = _archive_entry_summary(entry)
        entries.append({"index": i, **summary})
    current = user_data.get("plan_v2")
    current_weeks = len(current.get("weeks", [])) if current and isinstance(current, dict) else 0
    current_sessions = 0
    if current and isinstance(current, dict):
        for w in current.get("weeks") or []:
            current_sessions += len(w.get("sessions") or [])

    rows = "".join(
        f"""
        <tr>
            <td>{e['index']}</td>
            <td>{e['date']}</td>
            <td>{e['reason']}</td>
            <td>{e['weeks']}</td>
            <td>{e['sessions']}</td>
            <td>
                <form method="POST" action="{url_for('admin.restore_plan_archive')}" style="display:inline;">
                    <input type="hidden" name="archive_index" value="{e['index']}">
                    <input type="hidden" name="csrf_token" value="">
                    <button type="submit" style="padding:6px 12px;background:#00A9FF;color:white;border:none;cursor:pointer;">Restore</button>
                </form>
            </td>
        </tr>
        """
        for e in entries
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Plan Archive</title></head>
    <body style="font-family: Arial; padding: 40px; background: #1a1a1a; color: white;">
        <h1>Plan archive</h1>
        <p>Current plan: <b>{current_weeks}</b> weeks, <b>{current_sessions}</b> sessions.</p>
        <p>Restoring replaces your current plan with the chosen snapshot. The current plan is archived first so you can roll back.</p>
        <table border="1" cellpadding="8" style="border-collapse: collapse;">
            <thead><tr><th>Index</th><th>Date</th><th>Reason</th><th>Weeks</th><th>Sessions</th><th>Action</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
        <p style="margin-top: 20px;"><a href="/plan" style="color: #00A9FF;">Back to plan</a> · <a href="/admin/connections" style="color: #00A9FF;">Connections</a></p>
    </body>
    </html>
    """
    return html


@admin_bp.route("/restore_plan_archive", methods=["POST"])
@login_required
def restore_plan_archive():
    """Restore plan and plan_v2 from a chosen archive entry. Archives current plan first. Restricted if ADMIN_ATHLETE_IDS is set."""
    athlete_id = session["athlete_id"]
    if not _can_access_plan_archive_ui(athlete_id):
        flash("Plan archive restore is only available to designated admin users.", "error")
        return redirect(url_for("admin.connections"))
    archive_index = request.form.get("archive_index", type=int)
    if archive_index is None:
        flash("Missing archive_index.", "error")
        return redirect(url_for("admin.plan_archive"))

    user_data = data_manager.load_user_data(athlete_id)
    from utils.archive_loader import get_user_archive, save_user_archive_to_s3

    archive = get_user_archive(athlete_id, user_data)
    if archive_index < 0 or archive_index >= len(archive):
        flash(f"Invalid archive index: {archive_index}. Valid range 0–{len(archive) - 1}.", "error")
        return redirect(url_for("admin.plan_archive"))

    entry = archive[archive_index]
    restored_plan = entry.get("plan")
    restored_plan_v2 = entry.get("plan_v2")

    if not restored_plan and not restored_plan_v2:
        flash("That archive entry has no plan or plan_v2.", "error")
        return redirect(url_for("admin.plan_archive"))

    # Archive current plan first (so user can roll back if needed)
    if user_data.get("plan") or user_data.get("plan_v2"):
        if "archive" not in user_data:
            user_data["archive"] = []
        user_data["archive"].insert(
            0,
            {
                "plan": user_data.get("plan"),
                "plan_v2": user_data.get("plan_v2"),
                "completed_date": datetime.now().isoformat(),
                "reason": "rollback_from_truncated_plan",
            },
        )
        print(f"📦 Archived current plan before restore (archive now has {len(user_data['archive'])} entries)")

    # Restore
    if restored_plan is not None:
        user_data["plan"] = restored_plan
    if restored_plan_v2 is not None:
        user_data["plan_v2"] = restored_plan_v2
    if restored_plan_v2 and not restored_plan:
        from models.training_plan import TrainingPlan
        try:
            user_data["plan"] = TrainingPlan.from_dict(restored_plan_v2).to_markdown()
        except Exception:
            pass

    data_manager.save_user_data(athlete_id, user_data)
    flash(f"Restored plan from archive snapshot (index {archive_index}). Re-request any small changes (e.g. S&C tweaks) in chat if needed.", "success")
    return redirect(url_for("plan.view_plan"))


def _check_plan_archive_secret():
    """Return (athlete_id, error_response) for API. athlete_id int or None; error_response is (jsonify, status) or None."""
    athlete_id = request.args.get("athlete_id", type=int) or (request.get_json() or {}).get("athlete_id")
    secret = request.args.get("secret", type=str) or (request.get_json() or {}).get("secret") or request.form.get("secret")
    expected = os.getenv("FEEDBACK_TRIGGER_SECRET")
    if not athlete_id:
        return None, (jsonify({"error": "Missing athlete_id"}), 400)
    if not secret or not expected or secret != expected:
        print(f"⚠️  Invalid or missing secret for plan_archive API (athlete_id={athlete_id})")
        return None, (jsonify({"error": "Invalid or missing secret"}), 403)
    return athlete_id, None


@admin_bp.route("/api/plan_archive", methods=["GET"])
def api_plan_archive():
    """
    List plan archive entries for an athlete. Admin/ops only.
    Query: athlete_id, secret (same as FEEDBACK_TRIGGER_SECRET).
    Returns JSON: { entries: [{ index, date, reason, weeks, sessions }], current_weeks, current_sessions }.
    """
    athlete_id, err = _check_plan_archive_secret()
    if err:
        return err[0], err[1]
    user_data = data_manager.load_user_data(athlete_id)
    from utils.archive_loader import get_user_archive
    archive = get_user_archive(athlete_id, user_data)
    entries = [_archive_entry_summary(e) for e in archive]
    for i, e in enumerate(entries):
        e["index"] = i
    current = user_data.get("plan_v2")
    current_weeks = len(current.get("weeks", [])) if current and isinstance(current, dict) else 0
    current_sessions = sum(len(w.get("sessions") or []) for w in (current.get("weeks") or []) if isinstance(current, dict))
    return jsonify({
        "athlete_id": athlete_id,
        "entries": entries,
        "current_weeks": current_weeks,
        "current_sessions": current_sessions,
    })


@admin_bp.route("/api/restore_plan_archive", methods=["POST"])
def api_restore_plan_archive():
    """
    Restore plan from archive for an athlete. Admin/ops only.
    Body (JSON or form): athlete_id, secret, archive_index.
    Uses same secret as FEEDBACK_TRIGGER_SECRET.
    """
    athlete_id, err = _check_plan_archive_secret()
    if err:
        return err[0], err[1]
    data = request.get_json(silent=True) or request.form
    archive_index = data.get("archive_index")
    if archive_index is not None:
        try:
            archive_index = int(archive_index)
        except (TypeError, ValueError):
            archive_index = None
    if archive_index is None:
        return jsonify({"error": "Missing or invalid archive_index"}), 400

    user_data = data_manager.load_user_data(athlete_id)
    from utils.archive_loader import get_user_archive

    archive = get_user_archive(athlete_id, user_data)
    if archive_index < 0 or archive_index >= len(archive):
        return jsonify({"error": f"Invalid archive_index. Valid range 0–{len(archive) - 1}"}), 400

    entry = archive[archive_index]
    restored_plan = entry.get("plan")
    restored_plan_v2 = entry.get("plan_v2")
    if not restored_plan and not restored_plan_v2:
        return jsonify({"error": "That archive entry has no plan or plan_v2"}), 400

    if user_data.get("plan") or user_data.get("plan_v2"):
        if "archive" not in user_data:
            user_data["archive"] = []
        user_data["archive"].insert(
            0,
            {
                "plan": user_data.get("plan"),
                "plan_v2": user_data.get("plan_v2"),
                "completed_date": datetime.now().isoformat(),
                "reason": "rollback_from_truncated_plan",
            },
        )
    if restored_plan is not None:
        user_data["plan"] = restored_plan
    if restored_plan_v2 is not None:
        user_data["plan_v2"] = restored_plan_v2
    if restored_plan_v2 and not restored_plan:
        from models.training_plan import TrainingPlan
        try:
            user_data["plan"] = TrainingPlan.from_dict(restored_plan_v2).to_markdown()
        except Exception:
            pass

    data_manager.save_user_data(athlete_id, user_data)
    return jsonify({"status": "ok", "message": f"Restored plan from archive index {archive_index} for athlete {athlete_id}"})


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
