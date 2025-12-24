import vertexai
from vertexai.generative_models import GenerativeModel
from google.oauth2 import service_account
import jinja2
import json
from config import Config

class AIService:
    """Service for AI/LLM interactions using Google's Gemini"""
    
    def __init__(self):
        self._initialize_vertex_ai()
        self.model = GenerativeModel(model_name=Config.AI_MODEL)
        print(f"✅ AI Service initialized with model: {Config.AI_MODEL}")
    
    def _initialize_vertex_ai(self):
        """Initialize Vertex AI with environment-specific credentials"""
        creds_dict = Config.get_gcp_credentials()
        
        if creds_dict:
            # Use explicit service account credentials
            credentials = service_account.Credentials.from_service_account_info(
                creds_dict,
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )
            vertexai.init(
                project=Config.GCP_PROJECT_ID,
                location=Config.GCP_LOCATION,
                credentials=credentials
            )
            print(f"🔐 Vertex AI initialized with service account for environment: {Config.ENVIRONMENT}")
            print(f"📍 Project: {Config.GCP_PROJECT_ID}, Location: {Config.GCP_LOCATION}")
        else:
            # Fall back to Application Default Credentials (ADC)
            # This works when running locally with `gcloud auth application-default login`
            vertexai.init(
                project=Config.GCP_PROJECT_ID,
                location=Config.GCP_LOCATION
            )
            print(f"🔓 Vertex AI initialized with ADC for environment: {Config.ENVIRONMENT}")
            print(f"📍 Project: {Config.GCP_PROJECT_ID}, Location: {Config.GCP_LOCATION}")
    
    def generate_content(self, prompt_text, **kwargs):
        """Generate content from a prompt"""
        try:
            response = self.model.generate_content(prompt_text, **kwargs)
            return getattr(response, "text", str(response))
        except Exception as e:
            print(f"Error generating content from prompt: {e}")
            return ""
    
    def generate_training_plan(self, user_inputs, athlete_data):
        """Generate a training plan based on user inputs and athlete data"""
        with open('prompts/plan_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        prompt = template.render(
            athlete_goal=user_inputs['goal'],
            sessions_per_week=user_inputs['sessions_per_week'],
            athlete_type=user_inputs['athlete_type'],
            lifestyle_context=user_inputs['lifestyle_context'],
            training_history=athlete_data.get('training_history'),
            json_data=json.dumps(athlete_data['final_data_for_ai'], indent=4)
        )
        
        return self.generate_content(prompt)
    
    def generate_maintenance_plan(self, user_inputs, athlete_data):
        """Generate a maintenance training plan for a specified period"""
        # Use the same prompt template but with maintenance-specific context
        with open('prompts/plan_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        # Modify the goal to emphasize maintenance
        maintenance_weeks = user_inputs.get('maintenance_weeks', 4)
        from datetime import datetime
        today = datetime.now()
        
        maintenance_goal = f"Maintenance training plan for {maintenance_weeks} week{'s' if maintenance_weeks != 1 else ''} starting from {today.strftime('%B %d, %Y')}. Focus on maintaining current fitness level with consistent, moderate-intensity training. No specific race or performance goal - just maintaining fitness and health. Keep sessions simple, varied, and sustainable. Use current date ({today.strftime('%B %d, %Y')}) as the starting point for Week 1."
        
        # Pass training_history - it's a summary of the last plan and is relevant
        training_history = athlete_data.get('training_history')
        
        # Log what's being passed
        final_data = athlete_data['final_data_for_ai']
        print(f"--- Maintenance plan data summary ---")
        print(f"  - Goal: {maintenance_goal}")
        print(f"  - Sessions per week: {user_inputs['sessions_per_week']} (from form)")
        print(f"  - Hours per week: {user_inputs.get('hours_per_week', 'N/A')} (from form)")
        print(f"  - Athlete type: {user_inputs.get('athlete_type', 'General')} (from onboarding)")
        print(f"  - Lifestyle context: NOT included (bare bones plan)")
        print(f"  - Weeks: {maintenance_weeks}")
        print(f"  - Training history: {'Included (last plan summary)' if training_history else 'None'}")
        print(f"  - Has athlete_stats: {bool(final_data.get('athlete_stats'))}")
        print(f"  - Has strava_zones: {bool(final_data.get('strava_zones'))}")
        print(f"  - Has friel_hr_zones: {bool(final_data.get('friel_hr_zones'))}")
        print(f"  - Has analyzed_activities: {'analyzed_activities' in final_data} (should be False)")
        print(f"  - Has vdot_data: {'vdot_data' in final_data} (should be False)")
        print(f"  - Has lifestyle_context: {'lifestyle_context' in final_data} (should be False)")
        print(f"  - Starting date: {today.strftime('%Y-%m-%d')}")
        
        prompt = template.render(
            athlete_goal=maintenance_goal,
            sessions_per_week=user_inputs['sessions_per_week'],
            athlete_type=user_inputs.get('athlete_type', 'General'),  # From onboarding
            lifestyle_context='',  # Empty for maintenance plans - keep it bare bones
            training_history=training_history,  # Summary of last plan
            json_data=json.dumps(final_data, indent=4)
        )
        
        print(f"--- Maintenance plan prompt generated (with training_history summary and athlete_type from onboarding, no lifestyle_context) ---")
        
        return self.generate_content(prompt)
    
    def generate_feedback(self, training_plan, feedback_log, completed_sessions, 
                          training_history=None, garmin_health_stats=None):
        """Generate feedback for completed training sessions"""
        with open('prompts/feedback_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        prompt = template.render(
            training_plan=training_plan,
            feedback_log_json=json.dumps(feedback_log, indent=2),
            completed_sessions=json.dumps(completed_sessions, indent=2),
            training_history=training_history,
            garmin_health_stats=garmin_health_stats
        )
        
        return self.generate_content(prompt)
    
    def generate_chat_response(self, training_plan, feedback_log, chat_history):
        """Generate a chat response from the coach"""
        with open('prompts/chat_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        prompt = template.render(
            training_plan=training_plan,
            feedback_log_json=json.dumps(feedback_log, indent=2),
            chat_history_json=json.dumps(chat_history, indent=2)
        )
        
        return self.generate_content(prompt)
    
    def generate_weekly_summary(self, current_week_text, athlete_goal, latest_feedback=None, 
                                chat_history=None, garmin_health_stats=None):
        """Generate a weekly summary for the dashboard"""
        
        # Debug logging
        print(f"DEBUG: Generating weekly summary")
        print(f"  - Week text length: {len(current_week_text) if current_week_text else 0}")
        print(f"  - Athlete goal: {athlete_goal}")
        print(f"  - Has feedback: {latest_feedback is not None}")
        print(f"  - Has chat history: {chat_history is not None and len(chat_history) > 0 if chat_history else False}")
        print(f"  - Has Garmin data: {garmin_health_stats is not None}")
        
        with open('prompts/dashboard_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        from datetime import datetime
        prompt = template.render(
            today_date=datetime.now().strftime("%A, %B %d, %Y"),
            athlete_goal=athlete_goal,
            training_plan=current_week_text,
            latest_feedback=latest_feedback,
            chat_history=json.dumps(chat_history, indent=2) if chat_history else None,
            garmin_health_stats=garmin_health_stats
        )
        
        print(f"DEBUG: Prompt length: {len(prompt)} characters")
        print(f"DEBUG: Calling Vertex AI...")
        
        result = self.generate_content(prompt)
        
        print(f"DEBUG: AI response length: {len(result) if result else 0}")
        if not result or not result.strip():
            print("WARNING: AI returned empty response!")
        else:
            print(f"DEBUG: Response preview: {result[:200]}...")
        
        return result
    
    def summarize_training_cycle(self, completed_plan, feedback_log):
        """Summarize a completed training cycle"""
        with open('prompts/summarize_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        prompt = template.render(
            completed_plan=completed_plan,
            feedback_log_json=json.dumps(feedback_log, indent=2)
        )
        
        return self.generate_content(prompt)
    
    def summarize_activities(self, activity_names):
        """Create a descriptive name for multiple activities"""
        with open('prompts/summarize_activities_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        prompt = template.render(activity_names=activity_names)
        return self.generate_content(prompt).strip()

# Create singleton instance
ai_service = AIService()