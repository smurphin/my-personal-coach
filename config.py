import os
import json
import boto3
from dotenv import load_dotenv

# Load environment variables from .env file for local development
load_dotenv()

class Config:
    """Base configuration"""
    
    # Environment determination
    # Local dev: FLASK_ENV=development automatically means 'dev' environment
    # Production: FLASK_ENV=production + ENVIRONMENT var distinguishes prod/staging/demo
    if os.getenv('FLASK_ENV') == 'production':
        ENVIRONMENT = os.getenv("ENVIRONMENT", "prod")  # Default to prod if not specified
    else:
        ENVIRONMENT = "dev"  # Always dev for local
    
    # Flask
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "a_default_secret_key_for_development")
    DEBUG = os.getenv("FLASK_ENV") != "production"
    
    # Strava - Will be set from env vars (either .env or AWS Secrets Manager)
    STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
    STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
    STRAVA_API_URL = "https://www.strava.com/api/v3"
    STRAVA_VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN", "a_default_verify_token")
    SCOPES = "read,activity:read_all,profile:read_all"
    
    # Redirect URI mapping per environment
    # For your own environments (dev/staging/prod), configure ALL these URLs 
    # in your single Strava app's "Authorization Callback Domain" settings
    # For demo instances with friends' Strava apps, set via STRAVA_REDIRECT_URI env var
    REDIRECT_URIS = {
        'dev': 'http://127.0.0.1:5000/callback',
        'staging': 'https://staging.kaizencoach.training/callback',
        'prod': 'https://www.kaizencoach.training/callback',
        'demo': 'https://demo.kaizencoach.training/callback',
        'mark': 'https://mark.kaizencoach.training/callback',
        'shane': 'https://shane.kaizencoach.training/callback'

    }
    
    # Allow override via environment variable (useful for demo instances)
    REDIRECT_URI = os.getenv('STRAVA_REDIRECT_URI') or REDIRECT_URIS.get(ENVIRONMENT, 'http://127.0.0.1:5000/callback')
    
    # Google Cloud / Vertex AI - Project mapping per environment
    GCP_PROJECTS = {
        'dev': 'kaizencoach-dev',
        'staging': 'kaizencoach-staging',
        'prod': 'kaizencoach-prod',
        'demo': 'kaizencoach-demo',
        'mark': 'kaizencoach-mark',
        'shane': 'kaizencoach-shane'
    }
    
    GCP_PROJECT_ID = GCP_PROJECTS.get(ENVIRONMENT, 'kaizencoach-dev')
    GCP_LOCATION = "global"
    AI_MODEL = "gemini-2.5-flash" #gemini-3-flash-preview - rolled back as tries to assume too much and doesn't follow rules well
    
    # AWS Resources - Keep prod names as-is (legacy), new naming for other envs
    AWS_REGION = "eu-west-1"
    
    # DynamoDB Table - prod keeps legacy name to avoid migration during testing
    if ENVIRONMENT == 'prod':
        DYNAMODB_TABLE = "my-personal-coach-users"
    else:
        DYNAMODB_TABLE = f"{ENVIRONMENT}-kaizencoach-users"
    
    # S3 Bucket - prod keeps existing name to avoid migration during testing
    if ENVIRONMENT == 'prod':
        S3_BUCKET = "kaizencoach-data"
    else:
        S3_BUCKET = f"{ENVIRONMENT}-kaizencoach-data"
    
    # Garmin
    GARMIN_ENCRYPTION_KEY = os.getenv("GARMIN_ENCRYPTION_KEY")
    
    @classmethod
    def init_app(cls, app):
        """Initialize application with configuration"""
        print(f"🚀 Initializing kAIzen Coach - Environment: {cls.ENVIRONMENT}")
        print(f"📊 GCP Project: {cls.GCP_PROJECT_ID}")
        print(f"🗄️  DynamoDB Table: {cls.DYNAMODB_TABLE}")
        
        # If running in production, fetch secrets from AWS Secrets Manager
        if os.getenv('FLASK_ENV') == 'production':
            print("🔐 Loading secrets from AWS Secrets Manager...")
            cls._load_aws_secrets()
            
            # CRITICAL: Update class variables AFTER secrets are loaded
            cls.STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
            cls.STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
            cls.STRAVA_VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN")
            cls.SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
            cls.GARMIN_ENCRYPTION_KEY = os.getenv("GARMIN_ENCRYPTION_KEY")
            
            print(f"✅ Secrets loaded - STRAVA_CLIENT_ID: {cls.STRAVA_CLIENT_ID[:8] if cls.STRAVA_CLIENT_ID else 'MISSING'}...")
        else:
            print("🔧 Development mode - using .env file")
            # In dev, check for local GCP credentials file
            if not os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
                print("⚠️  Warning: GOOGLE_APPLICATION_CREDENTIALS not set for local dev")
    
    @staticmethod
    def _load_aws_secrets():
        """Load secrets from AWS Secrets Manager in production"""
        # Environment-specific secret name
        environment = os.getenv('ENVIRONMENT', 'prod')
        
        # Prod keeps legacy secret name to avoid migration during testing
        if environment == 'prod':
            secret_name = "my-personal-coach-app-secrets"
        else:
            # Other environments use: {environment}-kaizencoach-app-secrets
            secret_name = f"{environment}-kaizencoach-app-secrets"
        
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
            
            # Optional: For demo instances with custom Strava apps
            if secrets.get('STRAVA_REDIRECT_URI'):
                os.environ['STRAVA_REDIRECT_URI'] = secrets.get('STRAVA_REDIRECT_URI')

            # Create a temporary file for Google credentials
            with open("/tmp/gcp_creds.json", "w") as f:
                f.write(secrets.get('GOOGLE_APPLICATION_CREDENTIALS_JSON'))
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = "/tmp/gcp_creds.json"

        except Exception as e:
            print(f"❌ Error fetching secrets from AWS Secrets Manager: {e}")
            raise e
    
    @staticmethod
    def get_gcp_credentials():
        """Get GCP service account credentials for Vertex AI initialization"""
        # In production, credentials are already written to /tmp/gcp_creds.json
        if os.getenv('FLASK_ENV') == 'production':
            creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
            if creds_path and os.path.exists(creds_path):
                with open(creds_path, 'r') as f:
                    return json.load(f)
            return None
        else:
            # In development, use GOOGLE_APPLICATION_CREDENTIALS if set
            creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
            if creds_path and os.path.exists(creds_path):
                with open(creds_path, 'r') as f:
                    return json.load(f)
            return None