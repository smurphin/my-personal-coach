import vertexai
from vertexai.generative_models import GenerativeModel
from google.oauth2 import service_account
import jinja2
import json
from config import Config
from models.training_plan import TrainingPlan
from utils.migration import parse_ai_response_to_v2


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
            vertexai.init(
                project=Config.GCP_PROJECT_ID,
                location=Config.GCP_LOCATION
            )
            print(f"🔓 Vertex AI initialized with ADC for environment: {Config.ENVIRONMENT}")
            print(f"📍 Project: {Config.GCP_PROJECT_ID}, Location: {Config.GCP_LOCATION}")
    
    def _build_metrics_context(self, training_metrics):
        """
        Build a formatted metrics context string for AI prompts.
        
        Args:
            training_metrics: Dict containing vdot, lthr, ftp with nested value/source/date_set
        
        Returns:
            Formatted string with metrics and zones, or None if no metrics
        """
        if not training_metrics:
            return None
        
        lines = []
        lines.append("\n## ATHLETE'S CURRENT METRICS\n")
        lines.append("**CRITICAL: Use these ACTUAL values, never estimate or make up metrics!**\n")
        
        # VDOT with training paces
        if 'vdot' in training_metrics and training_metrics['vdot']:
            vdot_data = training_metrics['vdot']
            if isinstance(vdot_data, dict) and 'value' in vdot_data:
                vdot = int(vdot_data['value'])  # Always integer, rounded down
                
                lines.append(f"\n### VDOT: {vdot}")
                lines.append(f"Source: {vdot_data.get('source', 'Unknown')}")
                
                # Use stored paces if available, otherwise calculate
                paces = vdot_data.get('paces')
                if not paces:
                    try:
                        from vdot_calculator import get_training_paces
                        paces = get_training_paces(vdot)
                        print(f"Warning: VDOT paces not stored, calculated on-the-fly")
                    except Exception as e:
                        print(f"Warning: Could not load VDOT paces: {e}")
                
                if paces:
                    lines.append(f"\n**Training Paces for VDOT {vdot} (from Jack Daniels' tables):**")
                    lines.append(f"- Easy: {paces['easy_min']} - {paces['easy_max']} per km")
                    lines.append(f"- Marathon: {paces['marathon']} per km")
                    lines.append(f"- Threshold: {paces['threshold']} per km")
                    lines.append(f"- Interval (VO2max): {paces['interval']} per km")
                    lines.append(f"- Repetition: {paces['repetition']} per km")
                else:
                    lines.append(f"Note: Use VDOT {vdot} for pace calculations")
        
        # LTHR with heart rate zones
        if 'lthr' in training_metrics and training_metrics['lthr']:
            lthr_data = training_metrics['lthr']
            if isinstance(lthr_data, dict) and 'value' in lthr_data:
                lthr = lthr_data['value']
                
                lines.append(f"\n### LTHR (Lactate Threshold Heart Rate): {lthr} bpm")
                lines.append(f"Source: {lthr_data.get('source', 'Unknown')}")
                lines.append(f"\n**Heart Rate Zones (Joe Friel Method):**")
                lines.append(f"- Zone 1 (Recovery): <{int(lthr * 0.85)} bpm")
                lines.append(f"- Zone 2 (Aerobic): {int(lthr * 0.85)}-{int(lthr * 0.89)} bpm")
                lines.append(f"- Zone 3 (Tempo): {int(lthr * 0.90)}-{int(lthr * 0.94)} bpm")
                lines.append(f"- Zone 4 (Threshold): {int(lthr * 0.95)}-{lthr} bpm")
                lines.append(f"- Zone 5 (VO2max+): >{lthr} bpm")
        
        # FTP with power zones
        if 'ftp' in training_metrics and training_metrics['ftp']:
            ftp_data = training_metrics['ftp']
            if isinstance(ftp_data, dict) and 'value' in ftp_data:
                ftp = ftp_data['value']
                
                lines.append(f"\n### FTP (Functional Threshold Power): {ftp} W")
                lines.append(f"Source: {ftp_data.get('source', 'Unknown')}")
                lines.append(f"\n**Power Zones (Joe Friel Method):**")
                lines.append(f"- Zone 1 (Active Recovery): <{int(ftp * 0.55)} W")
                lines.append(f"- Zone 2 (Endurance): {int(ftp * 0.55)}-{int(ftp * 0.75)} W")
                lines.append(f"- Zone 3 (Tempo): {int(ftp * 0.76)}-{int(ftp * 0.90)} W")
                lines.append(f"- Zone 4 (Threshold): {int(ftp * 0.91)}-{int(ftp * 1.05)} W")
                lines.append(f"- Zone 5 (VO2max): {int(ftp * 1.06)}-{int(ftp * 1.20)} W")
                lines.append(f"- Zone 6 (Anaerobic): {int(ftp * 1.21)}-{int(ftp * 1.50)} W")
                lines.append(f"- Zone 7 (Neuromuscular): >{int(ftp * 1.50)} W")
        
        if len(lines) > 2:  # Has content beyond header
            return "\n".join(lines)
        
        return None
    
    def generate_content(self, prompt_text, **kwargs):
        """Generate content from a prompt"""
        try:
            response = self.model.generate_content(prompt_text, **kwargs)
            return getattr(response, "text", str(response))
        except Exception as e:
            print(f"Error generating content from prompt: {e}")
            return ""
    
    def generate_training_plan(self, user_inputs, athlete_data, vdot_data=None):
        """
        Generate a training plan and return both structured and markdown formats.
        
        Returns:
            Tuple of (TrainingPlan, markdown_text)
        """
        with open('prompts/plan_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        # FIX: Extract duration parameters from final_data, NOT from user_inputs
        final_data = athlete_data.get('final_data_for_ai', {})
        weeks_until_goal = final_data.get('weeks_until_goal')
        goal_date = final_data.get('goal_date')
        plan_start_date = final_data.get('plan_start_date')
        has_partial_week = final_data.get('has_partial_week', False)
        days_in_partial_week = final_data.get('days_in_partial_week', 0)
        
        # DEBUG: Log template variables
        print(f"--- DEBUG Template Variables ---")
        print(f"  weeks_until_goal: {weeks_until_goal} (type: {type(weeks_until_goal)})")
        print(f"  goal_date: {goal_date} (type: {type(goal_date)})")
        print(f"  plan_start_date: {plan_start_date} (type: {type(plan_start_date)})")
        print(f"  has_partial_week: {has_partial_week}")
        print(f"  days_in_partial_week: {days_in_partial_week}")
        print(f"  athlete_type: {user_inputs['athlete_type']}")
        print(f"--- END DEBUG ---")
        
        prompt = template.render(
            athlete_goal=user_inputs['goal'],
            sessions_per_week=user_inputs['sessions_per_week'],
            hours_per_week=user_inputs.get('hours_per_week'),
            athlete_type=user_inputs['athlete_type'],
            lifestyle_context=user_inputs['lifestyle_context'],
            training_history=athlete_data.get('training_history'),
            json_data=json.dumps(final_data, indent=4),
            weeks_until_goal=weeks_until_goal,
            goal_date=goal_date,
            plan_start_date=plan_start_date,
            has_partial_week=has_partial_week,
            days_in_partial_week=days_in_partial_week,
            vdot_data=vdot_data,
            friel_hr_zones=final_data.get('friel_hr_zones'),
            friel_power_zones=final_data.get('friel_power_zones')
        )
        
        # Generate AI response
        ai_response = self.generate_content(prompt)
        
        # Parse into structured format
        try:
            plan_v2, markdown_text = parse_ai_response_to_v2(
                ai_response,
                athlete_id=str(athlete_data.get('athlete_id')),
                user_inputs=user_inputs
            )
            print(f"✅ Generated structured plan with {len(plan_v2.weeks)} weeks")
            return plan_v2, markdown_text
        except Exception as e:
            print(f"⚠️  Failed to parse structured plan: {e}")
            print(f"Falling back to markdown-only")
            # Fallback: return markdown only, plan_v2 will be None
            return None, ai_response
    
    def generate_feedback(self, training_plan, feedback_log, completed_sessions, 
                          training_history=None, garmin_health_stats=None, incomplete_sessions=None,
                          vdot_data=None, athlete_profile=None):
        """Generate feedback for completed training sessions"""
        with open('prompts/feedback_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        # If training_plan is a TrainingPlan object, convert to markdown
        if isinstance(training_plan, TrainingPlan):
            training_plan_text = training_plan.to_markdown()
        else:
            training_plan_text = training_plan
        
        prompt = template.render(
            training_plan=training_plan_text,
            feedback_log_json=json.dumps(feedback_log, indent=2),
            completed_sessions=json.dumps(completed_sessions, indent=2),
            training_history=training_history,
            garmin_health_stats=garmin_health_stats,
            incomplete_sessions=incomplete_sessions,
            vdot_data=vdot_data,
            athlete_profile=athlete_profile
        )
        
        return self.generate_content(prompt)
    
    def generate_chat_response(self, training_plan, feedback_log, chat_history, vdot_data=None, athlete_profile=None):
        """Generate a chat response from the coach"""
        with open('prompts/chat_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        # If training_plan is a TrainingPlan object, convert to markdown
        if isinstance(training_plan, TrainingPlan):
            training_plan_text = training_plan.to_markdown()
        else:
            training_plan_text = training_plan
        
        prompt = template.render(
            training_plan=training_plan_text,
            feedback_log_json=json.dumps(feedback_log, indent=2),
            chat_history_json=json.dumps(chat_history, indent=2),
            vdot_data=vdot_data,
            athlete_profile=athlete_profile
        )
        
        return self.generate_content(prompt)
    
    def generate_weekly_summary(self, current_week_text, athlete_goal, latest_feedback=None, 
                                chat_history=None, garmin_health_stats=None, vdot_data=None):
        """Generate a weekly summary for the dashboard"""
        
        # Debug logging
        print(f"DEBUG: Generating weekly summary")
        print(f"  - Week text length: {len(current_week_text) if current_week_text else 0}")
        print(f"  - Athlete goal: {athlete_goal}")
        print(f"  - Has feedback: {latest_feedback is not None}")
        print(f"  - Has chat history: {chat_history is not None and len(chat_history) > 0 if chat_history else False}")
        print(f"  - Has Garmin data: {garmin_health_stats is not None}")
        print(f"  - Has VDOT data: {vdot_data is not None and vdot_data.get('current_vdot') is not None}")
        if vdot_data and vdot_data.get('current_vdot'):
            print(f"  - VDOT value: {vdot_data.get('current_vdot')}")
        
        with open('prompts/dashboard_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        from datetime import datetime
        prompt = template.render(
            today_date=datetime.now().strftime("%A, %B %d, %Y"),
            athlete_goal=athlete_goal,
            training_plan=current_week_text,
            latest_feedback=latest_feedback,
            chat_history=json.dumps(chat_history, indent=2) if chat_history else None,
            garmin_health_stats=garmin_health_stats,
            vdot_data=vdot_data  # Pass VDOT data to prompt template
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
        
        # If completed_plan is a TrainingPlan object, convert to markdown
        if isinstance(completed_plan, TrainingPlan):
            completed_plan_text = completed_plan.to_markdown()
        else:
            completed_plan_text = completed_plan
        
        prompt = template.render(
            completed_plan=completed_plan_text,
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