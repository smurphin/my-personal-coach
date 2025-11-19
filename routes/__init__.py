# Routes package
from .auth_routes import auth_bp
from .plan_routes import plan_bp
from .feedback_routes import feedback_bp
from .dashboard_routes import dashboard_bp
from .admin_routes import admin_bp
from .api_routes import api_bp

__all__ = [
    'auth_bp',
    'plan_bp',
    'feedback_bp',
    'dashboard_bp',
    'admin_bp',
    'api_bp'
]
