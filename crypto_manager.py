# crypto_manager.py
import os
from cryptography.fernet import Fernet, InvalidToken

_fernet = None

def _get_fernet():
    """
    Initializes and returns a Fernet instance.
    This function will only be called when encryption/decryption is needed,
    ensuring environment variables are loaded first.
    """
    global _fernet
    if _fernet is None:
        encryption_key = os.getenv("GARMIN_ENCRYPTION_KEY")
        if not encryption_key:
            raise ValueError("GARMIN_ENCRYPTION_KEY is not set in the environment variables!")
        _fernet = Fernet(encryption_key.encode())
    return _fernet

def encrypt(text: str) -> str:
    """Encrypts a string and returns it as a URL-safe string."""
    if not text:
        return ""
    fernet = _get_fernet()
    return fernet.encrypt(text.encode()).decode()

def decrypt(token: str) -> str:
    """Decrypts a token and returns it as a string."""
    if not token:
        return ""
    try:
        fernet = _get_fernet()
        return fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        print("ERROR: Failed to decrypt token. The key may have changed or the token is invalid.")
        return ""