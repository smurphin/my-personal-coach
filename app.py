from flask import Flask, session, render_template, url_for
from config import Config
from data_manager import data_manager

def create_app():
    """Application factory"""
    app = Flask(__name__)

    # Load configuration
    app.config.from_object(Config)
    Config.init_app(app)

    # Friendly error pages (no raw stack traces)
    @app.errorhandler(500)
    def internal_error(e):
        return render_template(
            'error.html',
            title='Something went wrong',
            message='An unexpected error occurred. Please try again.',
            retry_url=url_for('plan.onboarding'),
            retry_label='Back to Set Your Goals'
        ), 500

    @app.errorhandler(502)
    def bad_gateway(e):
        return render_template(
            'error.html',
            title='Service temporarily unavailable',
            message='The server is temporarily unable to respond. Please try again in a moment.',
            retry_url=url_for('plan.onboarding'),
            retry_label='Try again'
        ), 502

    @app.errorhandler(503)
    def service_unavailable(e):
        return render_template(
            'error.html',
            title='Service temporarily unavailable',
            message='The server is busy. Please try again in a moment.',
            retry_url=url_for('plan.onboarding'),
            retry_label='Try again'
        ), 503

    @app.errorhandler(504)
    def gateway_timeout(e):
        return render_template(
            'error.html',
            title='Request took too long',
            message='Plan generation can take 1–2 minutes. The request timed out before finishing. Please try again; your data has not been changed.',
            retry_url=url_for('plan.onboarding'),
            retry_label='Back to Set Your Goals'
        ), 504

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
    from routes.help_routes import help_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(plan_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(help_bp)
    
    print("✅ Application initialized successfully")
    
    return app

# Create the app
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
