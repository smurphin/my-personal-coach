from flask import Flask, session
from config import Config
from data_manager import data_manager

def create_app():
    """Application factory"""
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(Config)
    Config.init_app(app)
    
    # Register context processor for user data
    @app.context_processor
    def inject_user():
        """Inject user data into all templates"""
        if 'athlete_id' in session:
            user_data = data_manager.load_user_data(session['athlete_id'])
            if user_data:
                return dict(athlete=user_data.get('athlete'))
        return dict(athlete=None)
    
    # Register blueprints
    from routes.auth_routes import auth_bp
    from routes.plan_routes import plan_bp
    from routes.feedback_routes import feedback_bp
    from routes.dashboard_routes import dashboard_bp
    from routes.admin_routes import admin_bp
    from routes.api_routes import api_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(plan_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    
    print("✅ Application initialized successfully")
    
    return app

# Create the app
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
