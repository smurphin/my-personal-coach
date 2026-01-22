"""
Plan validation utilities for ensuring plan_v2 JSON integrity.

This module validates TrainingPlan objects and JSON structures
to ensure they meet the required schema before saving to DynamoDB.
"""
from typing import Dict, Any, List, Optional, Tuple
from models.training_plan import TrainingPlan, Week, Session


def validate_plan_v2_json(plan_data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate a plan_v2 JSON structure.
    
    Args:
        plan_data: Dictionary representing a TrainingPlan
        
    Returns:
        Tuple of (is_valid, error_message)
        If valid, error_message is None
    """
    try:
        # Check required top-level fields
        if 'version' not in plan_data:
            return False, "Missing required field: 'version'"
        
        if plan_data.get('version') != 2:
            return False, f"Invalid version: {plan_data.get('version')}. Expected 2"
        
        if 'weeks' not in plan_data:
            return False, "Missing required field: 'weeks'"
        
        if not isinstance(plan_data['weeks'], list):
            return False, "Field 'weeks' must be a list"
        
        if len(plan_data['weeks']) == 0:
            return False, "Plan must have at least one week"
        
        # Validate each week
        week_numbers = set()
        for week_idx, week_data in enumerate(plan_data['weeks']):
            if not isinstance(week_data, dict):
                return False, f"Week {week_idx} is not a dictionary"
            
            if 'week_number' not in week_data:
                return False, f"Week {week_idx} missing 'week_number'"
            
            week_num = week_data['week_number']
            if not isinstance(week_num, int):
                return False, f"Week {week_idx} 'week_number' must be an integer"
            
            if week_num in week_numbers:
                return False, f"Duplicate week_number: {week_num}"
            week_numbers.add(week_num)
            
            if 'sessions' not in week_data:
                return False, f"Week {week_num} missing 'sessions'"
            
            if not isinstance(week_data['sessions'], list):
                return False, f"Week {week_num} 'sessions' must be a list"
            
            # Validate each session
            session_ids = set()
            for sess_idx, session_data in enumerate(week_data['sessions']):
                if not isinstance(session_data, dict):
                    return False, f"Week {week_num}, Session {sess_idx} is not a dictionary"
                
                # Required fields
                required_fields = ['id', 'type', 'day']
                for field in required_fields:
                    if field not in session_data:
                        return False, f"Week {week_num}, Session {sess_idx} missing '{field}'"
                
                # Validate session ID format
                sess_id = session_data['id']
                if not isinstance(sess_id, str) or not sess_id:
                    return False, f"Week {week_num}, Session {sess_idx} 'id' must be a non-empty string"
                
                if sess_id in session_ids:
                    return False, f"Week {week_num}, Session {sess_idx} duplicate session id: {sess_id}"
                session_ids.add(sess_id)
                
                # Validate session type
                sess_type = session_data['type']
                valid_types = ['RUN', 'BIKE', 'SWIM', 'STRENGTH', 'OTHER', 'REST', 'CROSS_TRAIN']
                if sess_type not in valid_types:
                    return False, f"Week {week_num}, Session {sess_idx} invalid type '{sess_type}'. Must be one of: {valid_types}"
                
                # Validate priority if present
                if 'priority' in session_data and session_data['priority']:
                    priority = session_data['priority']
                    valid_priorities = ['KEY', 'IMPORTANT', 'STRETCH']
                    if priority not in valid_priorities:
                        return False, f"Week {week_num}, Session {sess_idx} invalid priority '{priority}'. Must be one of: {valid_priorities}"
        
        return True, None
        
    except Exception as e:
        return False, f"Validation error: {str(e)}"


def validate_and_load_plan_v2(plan_data: Dict[str, Any]) -> Tuple[Optional[TrainingPlan], Optional[str]]:
    """
    Validate and load a plan_v2 JSON structure into a TrainingPlan object.
    
    Args:
        plan_data: Dictionary representing a TrainingPlan
        
    Returns:
        Tuple of (TrainingPlan, error_message)
        If successful, error_message is None
        If failed, TrainingPlan is None and error_message contains the reason
    """
    is_valid, error_msg = validate_plan_v2_json(plan_data)
    
    if not is_valid:
        return None, error_msg
    
    try:
        plan = TrainingPlan.from_dict(plan_data)
        return plan, None
    except Exception as e:
        return None, f"Failed to load TrainingPlan: {str(e)}"


def extract_json_from_ai_response(response_text: str) -> Optional[Dict[str, Any]]:
    """
    Extract JSON from AI response that may contain markdown code blocks or extra text.
    
    Handles responses like:
    - Pure JSON: {"plan_v2": {...}, "change_summary": "..."}
    - JSON in markdown: ```json\n{...}\n```
    - JSON with extra text before/after
    
    Args:
        response_text: Raw AI response text
        
    Returns:
        Parsed JSON dictionary, or None if extraction fails
    """
    import json
    import re
    
    # Try to find JSON in markdown code blocks first
    json_block_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
    match = re.search(json_block_pattern, response_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    
    # Try to find JSON object directly (look for { ... } that spans multiple lines)
    json_object_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    matches = re.finditer(json_object_pattern, response_text, re.DOTALL)
    
    # Try each potential JSON object, starting with the largest
    json_candidates = []
    for match in matches:
        try:
            candidate = json.loads(match.group(0))
            if isinstance(candidate, dict) and ('plan_v2' in candidate or 'weeks' in candidate):
                json_candidates.append((len(match.group(0)), candidate))
        except json.JSONDecodeError:
            continue
    
    if json_candidates:
        # Return the largest valid JSON object
        json_candidates.sort(key=lambda x: x[0], reverse=True)
        return json_candidates[0][1]
    
    # Last resort: try parsing the entire response as JSON
    try:
        return json.loads(response_text.strip())
    except json.JSONDecodeError:
        return None
