import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
from google.oauth2 import service_account
import jinja2
import json
from typing import Optional
from config import Config
from models.training_plan import TrainingPlan
from utils.migration import parse_ai_response_to_v2
from utils.plan_validator import extract_json_from_ai_response, extract_feedback_text_by_structure, validate_and_load_plan_v2


def sanitize_feedback_log_for_ai(feedback_log):
    """
    Sanitize feedback_log before passing to AI to prevent format contamination.
    Extracts actual feedback text from any JSON that might be stored.
    
    Args:
        feedback_log: List of feedback log entries
        
    Returns:
        Sanitized feedback_log with extracted text (not raw JSON)
    """
    sanitized = []
    for entry in feedback_log:
        sanitized_entry = entry.copy()
        if 'feedback_markdown' in sanitized_entry:
            feedback_markdown = sanitized_entry['feedback_markdown']
            
            # Extract feedback_text from JSON if needed (same logic as extract_feedback_text_from_json)
            if isinstance(feedback_markdown, dict):
                if 'feedback_text' in feedback_markdown:
                    sanitized_entry['feedback_markdown'] = feedback_markdown.get('feedback_text', '')
                    continue
                feedback_markdown = json.dumps(feedback_markdown)
            
            feedback_str = str(feedback_markdown).strip()
            
            # Check if it's wrapped in markdown code blocks
            if feedback_str.startswith('```'):
                import re
                json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', feedback_str, re.DOTALL)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group(1))
                        if isinstance(parsed, dict) and 'feedback_text' in parsed:
                            sanitized_entry['feedback_markdown'] = parsed.get('feedback_text', feedback_str)
                            continue
                    except json.JSONDecodeError:
                        pass
            
            # Check if it's plain JSON
            if feedback_str.startswith('{') and 'feedback_text' in feedback_str:
                try:
                    parsed = json.loads(feedback_str)
                    if isinstance(parsed, dict) and 'feedback_text' in parsed:
                        sanitized_entry['feedback_markdown'] = parsed.get('feedback_text', feedback_str)
                        continue
                except json.JSONDecodeError:
                    pass
            
            # If no extraction needed, keep as-is
            sanitized_entry['feedback_markdown'] = feedback_markdown
        
        sanitized.append(sanitized_entry)
    return sanitized


def sanitize_chat_history_for_ai(chat_history):
    """
    Sanitize chat_history before passing to AI to prevent format contamination.
    Extracts actual response text from any JSON that might be stored.
    
    Args:
        chat_history: List of chat messages
        
    Returns:
        Sanitized chat_history with extracted text (not raw JSON)
    """
    sanitized = []
    for message in chat_history:
        sanitized_message = message.copy()
        if message.get('role') == 'model' and 'content' in sanitized_message:
            content = sanitized_message['content']
            # Check if content is JSON wrapped in markdown code blocks
            if isinstance(content, str):
                content_str = content.strip()
                if (content_str.startswith('```') or content_str.startswith('{')) and 'response_text' in content_str:
                    try:
                        import re
                        # Extract from markdown code block or direct JSON
                        if content_str.startswith('```'):
                            json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', content_str, re.DOTALL)
                            if json_match:
                                parsed = json.loads(json_match.group(1))
                                if isinstance(parsed, dict) and 'response_text' in parsed:
                                    sanitized_message['content'] = parsed.get('response_text', content)
                                    print(f"🧹 Sanitized chat history: extracted response_text from JSON")
                        else:
                            parsed = json.loads(content_str)
                            if isinstance(parsed, dict) and 'response_text' in parsed:
                                sanitized_message['content'] = parsed.get('response_text', content)
                                print(f"🧹 Sanitized chat history: extracted response_text from JSON")
                    except Exception as e:
                        print(f"⚠️  Failed to sanitize chat message: {e}")
        sanitized.append(sanitized_message)
    return sanitized


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
    
    def _get_generation_config(self):
        """Build GenerationConfig from Config if any params are set."""
        cfg = {}
        if Config.AI_TEMPERATURE is not None:
            cfg['temperature'] = Config.AI_TEMPERATURE
        if Config.AI_MAX_OUTPUT_TOKENS is not None:
            cfg['max_output_tokens'] = Config.AI_MAX_OUTPUT_TOKENS
        # Thinking level (Gemini 3 only): MINIMAL, LOW, MEDIUM, HIGH. Skip for 2.5—API errors.
        # vertexai.generative_models may not export ThinkingConfig in all versions; use proto first.
        if Config.AI_THINKING_LEVEL and 'gemini-3' in Config.AI_MODEL.lower():
            level_str = Config.AI_THINKING_LEVEL.strip().upper()
            thinking_config = None
            # Try 1: proto (google.cloud.aiplatform_v1beta1) — no dependency on vertexai export
            try:
                from google.cloud.aiplatform_v1beta1.types import GenerationConfig as GCConfig
                thinking_level_enum = getattr(GCConfig, 'ThinkingLevel', None)
                if thinking_level_enum is not None:
                    level_enum = getattr(
                        thinking_level_enum,
                        f'THINKING_LEVEL_{level_str}',
                        getattr(thinking_level_enum, level_str, getattr(thinking_level_enum, 'LOW', None))
                    )
                    if level_enum is not None:
                        thinking_config = GCConfig.ThinkingConfig(thinking_level=level_enum)
            except (ImportError, AttributeError):
                pass
            # Try 2: vertexai.generative_models.ThinkingConfig (if available in this SDK version)
            if thinking_config is None:
                try:
                    from vertexai.generative_models import ThinkingConfig
                    thinking_config = ThinkingConfig(thinking_level=level_str)
                except (ImportError, TypeError, ValueError, AttributeError):
                    pass
            if thinking_config is not None:
                cfg['thinking_config'] = thinking_config
                print(f"✅ AI_THINKING_LEVEL={level_str} applied for Gemini 3")
            else:
                print(f"⚠️  AI_THINKING_LEVEL={Config.AI_THINKING_LEVEL} not applied: could not build ThinkingConfig")
        return GenerationConfig(**cfg) if cfg else None

    def generate_content(self, prompt_text, **kwargs):
        """Generate content from a prompt. Retries on 429 (rate limit); re-raises after retries."""
        import time
        gen_config = self._get_generation_config()
        if gen_config is not None and 'generation_config' not in kwargs:
            kwargs['generation_config'] = gen_config
        last_error = None
        for attempt in range(3):
            try:
                response = self.model.generate_content(prompt_text, **kwargs)
                return getattr(response, "text", str(response))
            except Exception as e:
                last_error = e
                err_str = str(e)
                if "429" in err_str or "Resource exhausted" in err_str:
                    if attempt < 2:
                        # Longer backoff (15s, 45s) so retries don't add to TPM spike; 429 = quota/contention, not timeout
                        delay = [15, 45][attempt]
                        print(f"Rate limit (429) - retrying in {delay}s (attempt {attempt + 1}/3)")
                        time.sleep(delay)
                    else:
                        print(f"Error generating content from prompt (429 after retries): {e}")
                        raise
                else:
                    print(f"Error generating content from prompt: {e}")
                    return ""
        print(f"Error generating content from prompt: {last_error}")
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
            if len(plan_v2.weeks) == 0:
                print("⚠️  AI returned no weeks - treating as generation failure")
                return None, None
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
        """
        Generate feedback for completed training sessions.
        
        Returns:
            Tuple of (feedback_text, plan_update_json, change_summary)
            - feedback_text: The full AI feedback (markdown or extracted from JSON)
            - plan_update_json: Updated plan_v2 JSON if plan was updated, None otherwise
            - change_summary: Brief summary of changes for the athlete, None if no plan update
        """
        with open('prompts/feedback_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        # If training_plan is a TrainingPlan object, pass it as JSON for structured updates
        if isinstance(training_plan, TrainingPlan):
            training_plan_json = training_plan.to_dict()
            training_plan_text = training_plan.to_markdown()  # Keep markdown for context
        else:
            training_plan_json = None
            training_plan_text = training_plan
        
        # CRITICAL: Sanitize feedback_log before passing to AI to prevent format contamination
        # If feedback_log contains JSON wrapped in markdown, the AI might copy that format
        sanitized_feedback_log = sanitize_feedback_log_for_ai(feedback_log)
        
        prompt = template.render(
            training_plan=training_plan_text,
            training_plan_json=json.dumps(training_plan_json, indent=2) if training_plan_json else None,
            feedback_log_json=json.dumps(sanitized_feedback_log, indent=2),
            completed_sessions=json.dumps(completed_sessions, indent=2),
            training_history=training_history,
            garmin_health_stats=garmin_health_stats,
            incomplete_sessions=incomplete_sessions,
            vdot_data=vdot_data,
            athlete_profile=athlete_profile
        )
        
        ai_response = self.generate_content(prompt)
        
        # EXTRACTION STRATEGY:
        # The AI may return responses in multiple formats:
        # 1. Pure JSON: {"feedback_text": "...", "plan_v2": {...}, "change_summary_markdown": "..."}
        # 2. JSON in markdown code blocks: ```json\n{...}\n```
        # 3. Plain markdown (no JSON) - when no plan update is needed
        # 4. Mixed formats (JSON with extra text)
        # 
        # We need to handle ALL formats robustly to ensure we always extract the actual feedback text,
        # not the raw JSON structure. This prevents the "JSON rendered directly" bug.
        
        # Try to extract JSON plan update from response
        plan_update_json = None
        change_summary = None
        feedback_text = ai_response
        
        print(f"🔍 AI response length: {len(ai_response)} characters")
        print(f"🔍 AI response starts with: {ai_response[:100]}...")
        print(f"🔍 AI response ends with: ...{ai_response[-100:]}")
        
        # STEP 1: Try to extract JSON (handles JSON responses)
        extracted_json = extract_json_from_ai_response(ai_response)
        if extracted_json:
            print(f"✅ Successfully extracted JSON from AI response")
            print(f"   Keys in extracted JSON: {list(extracted_json.keys())}")
            
            # Check if this is a plan update response
            if 'plan_v2' in extracted_json:
                plan_data = extracted_json['plan_v2']
                validated_plan, error = validate_and_load_plan_v2(plan_data)
                
                if validated_plan:
                    plan_update_json = validated_plan.to_dict()
                    change_summary = extracted_json.get('change_summary_markdown', 
                                                       extracted_json.get('change_summary', None))
                    # Extract feedback_text from JSON if present, otherwise use full response
                    feedback_text = extracted_json.get('feedback_text', ai_response)
                    print(f"✅ Extracted valid plan_v2 update from feedback response ({len(validated_plan.weeks)} weeks)")
                else:
                    print(f"⚠️  Extracted plan_v2 JSON but validation failed: {error}")
                    # Still try to extract feedback_text even if plan validation failed
                    feedback_text = extracted_json.get('feedback_text', ai_response)
                    change_summary = extracted_json.get('change_summary_markdown', 
                                                       extracted_json.get('change_summary', None))
            elif 'change_summary' in extracted_json or 'change_summary_markdown' in extracted_json:
                # Just a summary, no plan update
                change_summary = extracted_json.get('change_summary_markdown') or extracted_json.get('change_summary')
                feedback_text = extracted_json.get('feedback_text', ai_response)
            elif 'feedback_text' in extracted_json:
                # JSON response with just feedback_text (no plan update or change summary)
                feedback_text = extracted_json.get('feedback_text', ai_response)
                change_summary = extracted_json.get('change_summary_markdown') or extracted_json.get('change_summary')
        else:
            print(f"⚠️  Failed to extract JSON from AI response - treating as plain markdown")
        
        # STEP 2: Fallback - if feedback_text is still the raw response, try direct JSON parse
        # This handles cases where the entire response IS valid JSON
        if feedback_text == ai_response:
            if feedback_text.strip().startswith('{') and 'feedback_text' in feedback_text:
                try:
                    fallback_json = json.loads(feedback_text.strip())
                    if isinstance(fallback_json, dict) and 'feedback_text' in fallback_json:
                        feedback_text = fallback_json.get('feedback_text', feedback_text)
                        change_summary = fallback_json.get('change_summary_markdown') or fallback_json.get('change_summary')
                        plan_update_json = fallback_json.get('plan_v2')  # Also check for plan_v2
                        print(f"✅ Extracted feedback_text from JSON response (direct parse fallback)")
                except (json.JSONDecodeError, AttributeError) as e:
                    print(f"⚠️  Direct JSON parse fallback failed: {e}")
                    # If it's not JSON, assume it's plain markdown (which is fine)
                    print(f"✅ Treating response as plain markdown (no JSON extraction needed)")
        
        # STEP 3: Final validation - ensure we have actual text, not raw JSON
        # This is a safety net in case all extraction methods failed
        if feedback_text == ai_response and feedback_text.strip().startswith('{'):
            # Still looks like JSON - try structure-based extraction first (handles unescaped quotes)
            extracted_text = extract_feedback_text_by_structure(feedback_text)
            if extracted_text and len(extracted_text) > 50:
                feedback_text = extracted_text
                print(f"✅ Extracted feedback_text using structure-based fallback")
            else:
                try:
                    # Fallback: regex (may truncate at first unescaped quote in content)
                    import re
                    feedback_match = re.search(r'"feedback_text"\s*:\s*"((?:[^"\\]|\\.|\\n)*)"', feedback_text, re.DOTALL)
                    if feedback_match:
                        extracted_text = feedback_match.group(1).replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
                        if extracted_text and len(extracted_text) > 50:
                            feedback_text = extracted_text
                            print(f"✅ Extracted feedback_text using regex fallback")
                except Exception as e:
                    print(f"⚠️  Regex extraction fallback failed: {e}")
            
            # OPTIONAL: Try to salvage plan_v2 from a malformed JSON response.
            # Even if the overall JSON is invalid (e.g. due to unescaped quotes in feedback_text),
            # the plan_v2 sub-object may still be valid JSON. We attempt to extract and validate it.
            if plan_update_json is None and '"plan_v2"' in ai_response:
                try:
                    import re
                    import json as json_module
                    
                    plan_key_match = re.search(r'"plan_v2"\s*:\s*\{', ai_response)
                    if plan_key_match:
                        start_idx = plan_key_match.start(0)
                        # Find the opening brace for the plan_v2 object
                        brace_start = ai_response.find('{', plan_key_match.start())
                        if brace_start != -1:
                            brace_count = 0
                            end_idx = None
                            for i, ch in enumerate(ai_response[brace_start:], brace_start):
                                if ch == '{':
                                    brace_count += 1
                                elif ch == '}':
                                    brace_count -= 1
                                    if brace_count == 0:
                                        end_idx = i
                                        break
                            if end_idx is not None:
                                plan_str = ai_response[brace_start:end_idx + 1]
                                try:
                                    plan_candidate = json_module.loads(plan_str)
                                    validated_plan, error = validate_and_load_plan_v2(plan_candidate)
                                    if validated_plan:
                                        plan_update_json = validated_plan.to_dict()
                                        print("✅ Salvaged plan_v2 from malformed JSON response")
                                    else:
                                        print(f"⚠️  Salvaged plan_v2 candidate failed validation: {error}")
                                except Exception as inner_e:
                                    print(f"⚠️  Failed to parse salvaged plan_v2 JSON: {inner_e}")
                except Exception as e:
                    print(f"⚠️  Error while attempting to salvage plan_v2 from malformed JSON: {e}")
        
        # STEP 4: Clean up the feedback_text - remove any remaining JSON artifacts
        # If feedback_text still contains JSON structure, it means extraction partially failed
        if feedback_text.strip().startswith('{') and '"feedback_text"' in feedback_text:
            print(f"⚠️  WARNING: feedback_text still appears to be JSON - extraction may have failed")
            print(f"   Attempting one final extraction...")
            # Last resort: try to parse and extract using the globally imported json module
            try:
                final_attempt = json.loads(feedback_text.strip())
                if isinstance(final_attempt, dict) and 'feedback_text' in final_attempt:
                    feedback_text = final_attempt['feedback_text']
                    print(f"✅ Final extraction attempt succeeded")
            except Exception:
                print(f"⚠️  Final extraction attempt failed - storing as-is (will be handled by display-time extraction)")
        
        # FINAL SAFETY CHECK: Ensure feedback_text is NOT JSON-wrapped before returning
        # This prevents storing markdown-wrapped JSON in the database
        if feedback_text.strip().startswith('```') or (feedback_text.strip().startswith('{') and '"feedback_text"' in feedback_text):
            print(f"⚠️  CRITICAL: feedback_text still contains JSON wrapper - forcing extraction")
            # Try one more aggressive extraction
            try:
                # Remove markdown code blocks if present
                cleaned = feedback_text.strip()
                if cleaned.startswith('```'):
                    # Extract content between ```json and ```
                    import re
                    match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', cleaned, re.DOTALL)
                    if match:
                        cleaned = match.group(1)
                
                # Parse JSON and extract feedback_text
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict) and 'feedback_text' in parsed:
                    feedback_text = parsed['feedback_text']
                    print(f"✅ CRITICAL FIX: Extracted feedback_text from JSON wrapper (length: {len(feedback_text)})")
                elif isinstance(parsed, dict) and 'response_text' in parsed:
                    feedback_text = parsed['response_text']
                    print(f"✅ CRITICAL FIX: Extracted response_text from JSON wrapper (length: {len(feedback_text)})")
            except Exception as e:
                print(f"❌ CRITICAL: Final extraction failed: {e}")
                # Try structure-based extraction first (handles unescaped quotes in content)
                extracted = extract_feedback_text_by_structure(cleaned)
                if extracted and len(extracted) > 100:
                    feedback_text = extracted
                    print(f"✅ CRITICAL FIX: Extracted via structure-based fallback (length: {len(feedback_text)})")
                else:
                    # Last resort: regex (may truncate at first unescaped quote)
                    try:
                        import re
                        pattern = r'"feedback_text"\s*:\s*"((?:[^"\\]|\\.|\\n)*)"'
                        match = re.search(pattern, feedback_text, re.DOTALL)
                        if match:
                            extracted = match.group(1).replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
                            if len(extracted) > 100:
                                feedback_text = extracted
                                print(f"✅ CRITICAL FIX: Extracted via regex fallback (length: {len(feedback_text)})")
                    except Exception as regex_error:
                        print(f"❌ CRITICAL: Regex fallback also failed: {regex_error}")
                    # At this point, we've failed all extraction attempts
                    # Log the issue but don't crash - the display-time extraction will handle it
        
        # Normalize over-escaped quotes: model sometimes outputs \" in JSON so parsed string
        # contains literal backslash-quote; convert to single quote for display.
        feedback_text = feedback_text.replace('\\"', '"')
        
        print(f"✅ Final feedback_text length: {len(feedback_text)} characters")
        print(f"✅ Final feedback_text preview: {feedback_text[:200]}...")
        
        # region agent log
        try:
            import json as _json
            import hashlib as _hashlib
            import time as _time
            _log_entry = {
                "sessionId": "debug-session",
                "runId": "pre-fix",
                "hypothesisId": "H1",
                "location": "services/ai_service.py:generate_feedback",
                "message": "Final feedback_text before return",
                "data": {
                    "length": len(feedback_text),
                    "sha256": _hashlib.sha256(feedback_text.encode("utf-8")).hexdigest(),
                },
                "timestamp": int(_time.time() * 1000),
            }
            with open("/home/darren/git/.cursor/debug.log", "a") as _f:
                _f.write(_json.dumps(_log_entry) + "\n")
        except Exception:
            pass
        # endregion
        
        # VERIFY: Ensure we're not returning JSON-wrapped content
        if feedback_text.strip().startswith('```') or (feedback_text.strip().startswith('{') and '"feedback_text"' in feedback_text[:500]):
            print(f"❌ WARNING: feedback_text STILL contains JSON wrapper after all extraction attempts!")
            print(f"   First 500 chars: {feedback_text[:500]}")
        
        return feedback_text, plan_update_json, change_summary
    
    def generate_chat_response(self, training_plan, feedback_log, chat_history, vdot_data=None, athlete_profile=None):
        """
        Generate a chat response from the coach.
        
        Returns:
            Tuple of (response_text, plan_update_json, change_summary)
            - response_text: The full AI response (markdown or JSON string)
            - plan_update_json: Updated plan_v2 JSON if plan was updated, None otherwise
            - change_summary: Brief summary of changes for the athlete, None if no plan update
        """
        with open('prompts/chat_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        
        # If training_plan is a TrainingPlan object, pass it as JSON for structured updates
        if isinstance(training_plan, TrainingPlan):
            training_plan_json = training_plan.to_dict()
            training_plan_text = training_plan.to_markdown()  # Keep markdown for context
        else:
            training_plan_json = None
            training_plan_text = training_plan
        
        # Get user message from chat history (last user message)
        user_message = ""
        if chat_history:
            for msg in reversed(chat_history):
                if msg.get('role') == 'user':
                    user_message = msg.get('content', '')
                    break
        
        # CRITICAL: Sanitize feedback_log and chat_history before passing to AI to prevent format contamination
        # If these contain JSON wrapped in markdown, the AI might copy that format
        sanitized_feedback_log = sanitize_feedback_log_for_ai(feedback_log)
        sanitized_chat_history = sanitize_chat_history_for_ai(chat_history)
        
        prompt = template.render(
            user_message=user_message,
            training_plan=training_plan_text,
            training_plan_json=json.dumps(training_plan_json, indent=2) if training_plan_json else None,
            feedback_log_json=json.dumps(sanitized_feedback_log, indent=2),
            chat_history_json=json.dumps(sanitized_chat_history, indent=2),
            vdot_data=vdot_data,
            athlete_profile=athlete_profile
        )
        
        ai_response = self.generate_content(prompt)
        
        print(f"🔍 Chat AI response length: {len(ai_response)} characters")
        print(f"🔍 Chat AI response starts with: {ai_response[:100]}...")
        
        # Try to extract JSON plan update from response
        plan_update_json = None
        change_summary = None
        response_text = ai_response
        
        extracted_json = extract_json_from_ai_response(ai_response)
        if extracted_json:
            print(f"✅ Successfully extracted JSON from chat response")
            print(f"   Keys in extracted JSON: {list(extracted_json.keys())}")
            
            # Check if this is a plan update response
            if 'plan_v2' in extracted_json:
                plan_data = extracted_json['plan_v2']
                validated_plan, error = validate_and_load_plan_v2(plan_data)
                
                if validated_plan:
                    plan_update_json = validated_plan.to_dict()
                    change_summary = extracted_json.get('change_summary_markdown', 
                                                       extracted_json.get('change_summary', None))
                    # Extract response_text from JSON if present, otherwise use full response
                    response_text = extracted_json.get('response_text', ai_response)
                    print(f"✅ Extracted valid plan_v2 update from chat response ({len(validated_plan.weeks)} weeks)")
                    print(f"   Response text length: {len(response_text)}")
                else:
                    print(f"⚠️  Extracted plan_v2 JSON but validation failed: {error}")
                    # Still try to extract response_text even if plan validation failed
                    response_text = extracted_json.get('response_text', ai_response)
                    change_summary = extracted_json.get('change_summary_markdown', 
                                                       extracted_json.get('change_summary', None))
            elif 'change_summary' in extracted_json or 'change_summary_markdown' in extracted_json:
                # Just a summary, no plan update
                change_summary = extracted_json.get('change_summary_markdown') or extracted_json.get('change_summary')
                response_text = extracted_json.get('response_text', ai_response)
            elif 'response_text' in extracted_json:
                # JSON response with just response_text (no plan update or change summary)
                response_text = extracted_json.get('response_text', ai_response)
                change_summary = extracted_json.get('change_summary_markdown') or extracted_json.get('change_summary')
        else:
            print(f"⚠️  Failed to extract JSON from chat response - treating as plain markdown")
        
        # Fallback: if response_text is still the raw response, try direct JSON parse
        if response_text == ai_response:
            if response_text.strip().startswith('{') and 'response_text' in response_text:
                try:
                    fallback_json = json.loads(response_text.strip())
                    if isinstance(fallback_json, dict) and 'response_text' in fallback_json:
                        response_text = fallback_json.get('response_text', response_text)
                        change_summary = fallback_json.get('change_summary_markdown') or fallback_json.get('change_summary')
                        plan_update_json = fallback_json.get('plan_v2')  # Also check for plan_v2
                        print(f"✅ Extracted response_text from JSON response (direct parse fallback)")
                except (json.JSONDecodeError, AttributeError) as e:
                    print(f"⚠️  Direct JSON parse fallback failed: {e}")
            elif response_text.strip().startswith('```') and 'response_text' in response_text:
                # Handle markdown code block wrapper
                print(f"🔍 Chat response wrapped in markdown code block")
                import re
                json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', response_text, re.DOTALL)
                if json_match:
                    try:
                        fallback_json = json.loads(json_match.group(1).strip())
                        if isinstance(fallback_json, dict) and 'response_text' in fallback_json:
                            response_text = fallback_json.get('response_text', response_text)
                            change_summary = fallback_json.get('change_summary_markdown') or fallback_json.get('change_summary')
                            plan_update_json = fallback_json.get('plan_v2')
                            print(f"✅ Extracted response_text from markdown-wrapped JSON")
                    except (json.JSONDecodeError, AttributeError) as e:
                        print(f"⚠️  Failed to parse JSON from markdown code block: {e}")
        
        # Final validation - ensure we have actual text, not raw JSON
        if response_text == ai_response and (response_text.strip().startswith('{') or response_text.strip().startswith('```')):
            print(f"⚠️  WARNING: response_text still appears to be JSON/markdown - extraction may have failed")
            print(f"   Attempting one final extraction...")
            try:
                # Last resort: try to parse and extract
                final_attempt = json.loads(response_text.strip().lstrip('```json').rstrip('```').strip())
                if isinstance(final_attempt, dict) and 'response_text' in final_attempt:
                    response_text = final_attempt['response_text']
                    print(f"✅ Final extraction attempt succeeded")
            except:
                print(f"⚠️  Final extraction attempt failed - storing as-is")
        
        print(f"✅ Final chat response_text length: {len(response_text)} characters")
        print(f"✅ Final chat response_text preview: {response_text[:200]}...")
        
        return response_text, plan_update_json, change_summary
    
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

    def match_activity_to_session(self, activity_data: dict, incomplete_sessions_text: str) -> Optional[str]:
        """
        Use AI to match a completed activity to one of the candidate planned sessions.
        Same logic as feedback flow: AI sees activity + candidates and returns [COMPLETED:session_id].
        Returns session_id if matched, None otherwise.
        """
        import re
        name = activity_data.get('name', '') or 'Unnamed'
        act_type = activity_data.get('type', '')
        start_date = activity_data.get('start_date', '')
        if start_date:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(start_date.replace('Z', ''))
                start_date = dt.strftime('%Y-%m-%d %H:%M')
            except Exception:
                pass
        moving_sec = activity_data.get('moving_time') or 0
        duration_str = f"{int(moving_sec // 60)} min" if moving_sec else "—"
        distance_m = activity_data.get('distance')
        if distance_m is not None and distance_m > 0:
            distance_str = f"{distance_m / 1000:.2f} km" if distance_m >= 1000 else f"{int(distance_m)} m"
        else:
            distance_str = "—"
        lines = [
            f"Name: {name}",
            f"Type: {act_type}",
            f"Date: {start_date}",
            f"Duration: {duration_str}",
            f"Distance: {distance_str}",
        ]
        private_note = activity_data.get('private_note', '').strip()
        if private_note:
            lines.append(f"Private note: {private_note}")
        time_in_zones = activity_data.get('time_in_hr_zones') or {}
        if time_in_zones and isinstance(time_in_zones, dict):
            zone_parts = [f"{k}: {v}" for k, v in time_in_zones.items() if v]
            if zone_parts:
                lines.append("Time in HR zones: " + ", ".join(zone_parts))
        activity_summary = "\n".join(lines)
        with open('prompts/session_match_prompt.txt', 'r') as f:
            template = jinja2.Template(f.read())
        prompt = template.render(
            activity_summary=activity_summary,
            incomplete_sessions=incomplete_sessions_text
        )
        response = self.generate_content(prompt)
        if not response:
            return None
        match = re.search(r'\[COMPLETED:([^\]]+)\]', response)
        if match:
            return match.group(1).strip()
        return None


# Create singleton instance
ai_service = AIService()