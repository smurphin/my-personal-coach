import os
import json
import boto3
from dotenv import load_dotenv

# Load environment variables from .env file for local development
load_dotenv()

class Config:
    """Base configuration"""
    
    # Flask
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "a_default_secret_key_for_development")
    DEBUG = os.getenv("FLASK_ENV") != "production"
    
    # Strava
    STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
    STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
    STRAVA_API_URL = "https://www.strava.com/api/v3"
    STRAVA_VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN", "a_default_verify_token")
    SCOPES = "read,activity:read_all,profile:read_all"
    
    # Redirect URI based on environment
    if os.getenv('FLASK_ENV') == 'production':
        REDIRECT_URI = "https://www.kaizencoach.training/callback"
    else:
        REDIRECT_URI = "http://127.0.0.1:5000/callback"
    
    # Google Cloud / Vertex AI
    GCP_PROJECT_ID = "my-personal-coach-472007"
    GCP_LOCATION = "europe-west1"
    AI_MODEL = "gemini-2.5-flash"
    
    # AWS
    AWS_REGION = "eu-west-1"
    DYNAMODB_TABLE = "my-personal-coach-users"
    S3_BUCKET = "kaizencoach-data"
    
    # Garmin
    GARMIN_ENCRYPTION_KEY = os.getenv("GARMIN_ENCRYPTION_KEY")
    
    @staticmethod
    def init_app(app):
        """Initialize application with configuration"""
        # If running in production, fetch secrets from AWS Secrets Manager
        if os.getenv('FLASK_ENV') == 'production':
            Config._load_aws_secrets()
    
    @staticmethod
    def _load_aws_secrets():
        """Load secrets from AWS Secrets Manager in production"""
        secret_name = "my-personal-coach-app-secrets"
        region_name = "eu-west-1"

        session_boto = boto3.session.Session()
        client = session_boto.client(
            service_name='secretsmanager',
            region_name=region_name
        )

        try:
            get_secret_value_response = client.get_secret_value(SecretId=secret_name)
            secret = get_secret_value_response['SecretString']
            secrets = json.loads(secret)

            # Set environment variables from the fetched secret
            os.environ['STRAVA_CLIENT_ID'] = secrets.get('STRAVA_CLIENT_ID')
            os.environ['STRAVA_CLIENT_SECRET'] = secrets.get('STRAVA_CLIENT_SECRET')
            os.environ['STRAVA_VERIFY_TOKEN'] = secrets.get('STRAVA_VERIFY_TOKEN')
            os.environ['FLASK_SECRET_KEY'] = secrets.get('FLASK_SECRET_KEY')
            os.environ['GOOGLE_APPLICATION_CREDENTIALS_JSON'] = secrets.get('GOOGLE_APPLICATION_CREDENTIALS_JSON')
            os.environ['GARMIN_ENCRYPTION_KEY'] = secrets.get('GARMIN_ENCRYPTION_KEY')

            # Create a temporary file for Google credentials
            with open("/tmp/gcp_creds.json", "w") as f:
                f.write(secrets.get('GOOGLE_APPLICATION_CREDENTIALS_JSON'))
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = "/tmp/gcp_creds.json"

        except Exception as e:
            print(f"Error fetching secrets from AWS Secrets Manager: {e}")
            raise e
