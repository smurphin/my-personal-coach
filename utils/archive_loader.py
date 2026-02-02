"""
Load and save plan archive from/to S3.

Archive is used for historical reference, AI context (via training_history
summaries), and admin rollback/restore. All archive entries are stored in S3;
DynamoDB only holds archive_s3_key when archive has been offloaded.
"""
import os


def get_user_archive(athlete_id, user_data):
    """
    Return the full plan archive for an athlete (newest first).

    Uses in-memory user_data['archive'] if non-empty; otherwise loads from S3
    when user_data has 'archive_s3_key' (production only). Returns [] if
    nothing is stored.

    Args:
        athlete_id: Athlete ID (string or int).
        user_data: The user_data dict from data_manager.load_user_data.

    Returns:
        list: Archive entries (each has 'plan', optional 'plan_v2', 'completed_date', etc.).
    """
    in_memory = user_data.get('archive')
    if in_memory and isinstance(in_memory, list) and len(in_memory) > 0:
        return in_memory

    s3_key = user_data.get('archive_s3_key')
    if not s3_key:
        return []

    try:
        from s3_manager import s3_manager, S3_AVAILABLE
        if not S3_AVAILABLE or os.getenv('FLASK_ENV') != 'production':
            return []
        data = s3_manager.load_large_data(s3_key)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_user_archive_to_s3(athlete_id, archive_list):
    """
    Save the full plan archive to S3 (overwrites existing object).

    Only writes when S3 is available and FLASK_ENV is production.

    Args:
        athlete_id: Athlete ID (string or int).
        archive_list: List of archive entry dicts (newest first).

    Returns:
        str: S3 key if saved, None otherwise.
    """
    try:
        from s3_manager import s3_manager, S3_AVAILABLE
        if not S3_AVAILABLE or os.getenv('FLASK_ENV') != 'production':
            return None
        key = s3_manager.save_large_data(athlete_id, 'plan_archive', archive_list or [])
        return key
    except Exception:
        return None
