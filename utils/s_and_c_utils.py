"""
S&C Library Focus Mapping

Maps AI-prescribed focus areas to specific routine IDs in the library.
"""

# Focus area → routine ID mapping
FOCUS_TO_ROUTINE = {
    "core": "routine_1_core",
    "core focus": "routine_1_core",
    "lower body": "routine_2_lower_body",
    "lower body focus": "routine_2_lower_body",
    "leg": "routine_2_lower_body",
    "legs": "routine_2_lower_body",
    "upper body": "routine_3_upper_body",
    "upper body & back": "routine_3_upper_body",
    "upper body focus": "routine_3_upper_body",
    "back": "routine_3_upper_body",
    "full body": "routine_4_circuit",
    "full body circuit": "routine_4_circuit",
    "circuit": "routine_4_circuit",
}

# Routine ID → anchor link mapping
ROUTINE_ANCHORS = {
    "routine_1_core": "#sc-routine-1-core-focus-approx-35-mins",
    "routine_2_lower_body": "#sc-routine-2-lower-body-focus-approx-35-mins",
    "routine_3_upper_body": "#sc-routine-3-upper-body-back-focus-approx-35-mins",
    "routine_4_circuit": "#sc-routine-4-full-body-circuit-approx-35-mins",
}

# Routine ID → human-readable name
ROUTINE_NAMES = {
    "routine_1_core": "S&C Routine 1: Core Focus",
    "routine_2_lower_body": "S&C Routine 2: Lower Body Focus",
    "routine_3_upper_body": "S&C Routine 3: Upper Body & Back Focus",
    "routine_4_circuit": "S&C Routine 4: Full Body Circuit",
}


def extract_s_and_c_focus(description: str) -> str:
    """
    Extract S&C focus area from session description.
    
    Args:
        description: Session description (e.g., "S&C: Core Focus, 30 mins")
    
    Returns:
        str or None: Focus area (lowercase) or None if not S&C
    
    Examples:
        >>> extract_s_and_c_focus("S&C: Core Focus, 30 mins")
        'core focus'
        >>> extract_s_and_c_focus("S&C Routine A (Core)")
        'core'
        >>> extract_s_and_c_focus("Easy run, 45 mins")
        None
    """
    import re
    
    if not description:
        return None
    
    desc_lower = description.lower()
    
    # Not an S&C session
    if 's&c' not in desc_lower and 'strength' not in desc_lower:
        return None
    
    # Extract focus area from common patterns
    patterns = [
        r's&c[:\s]+([^,\.]+)',  # "S&C: Core Focus, 30 mins"
        r'strength[:\s]+([^,\.]+)',  # "Strength: Lower Body"
        r'routine\s+[a-z]\s*\(([^)]+)\)',  # "Routine A (Core)"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, desc_lower)
        if match:
            focus = match.group(1).strip()
            # Remove duration info
            focus = re.sub(r'\d+\s*min.*', '', focus).strip()
            return focus
    
    # Fallback: Look for keywords directly
    if 'core' in desc_lower:
        return 'core'
    elif 'lower body' in desc_lower or 'leg' in desc_lower:
        return 'lower body'
    elif 'upper body' in desc_lower or 'back' in desc_lower:
        return 'upper body'
    elif 'full body' in desc_lower or 'circuit' in desc_lower:
        return 'full body'
    
    return None


def map_focus_to_routine(focus: str) -> str:
    """
    Map focus area to routine ID.
    
    Args:
        focus: Focus area (e.g., "core focus", "lower body")
    
    Returns:
        str or None: Routine ID (e.g., "routine_1_core") or None if no match
    """
    if not focus:
        return None
    
    focus_clean = focus.lower().strip()
    return FOCUS_TO_ROUTINE.get(focus_clean)


def get_routine_link(routine_id: str) -> str:
    """
    Get full URL to routine in library.
    
    Args:
        routine_id: Routine ID (e.g., "routine_1_core")
    
    Returns:
        str: Full URL with anchor (e.g., "/s-and-c-library#routine-1-core")
    """
    if not routine_id or routine_id not in ROUTINE_ANCHORS:
        return "/s-and-c-library"
    
    return f"/s-and-c-library{ROUTINE_ANCHORS[routine_id]}"


def get_routine_name(routine_id: str) -> str:
    """
    Get human-readable routine name.
    
    Args:
        routine_id: Routine ID (e.g., "routine_1_core")
    
    Returns:
        str: Human-readable name
    """
    return ROUTINE_NAMES.get(routine_id, "S&C Routine")


def process_s_and_c_session(session):
    """
    Process a session to extract and link S&C routine.
    
    Modifies session in place to add s_and_c_routine field.
    
    Args:
        session: Session object
    """
    if session.type != 'STRENGTH':
        return
    
    focus = extract_s_and_c_focus(session.description)
    if focus:
        routine_id = map_focus_to_routine(focus)
        if routine_id:
            session.s_and_c_routine = routine_id


def load_default_s_and_c_library() -> str:
    """
    Load default S&C library content.
    
    Returns:
        str: Library markdown content
    """
    try:
        with open('data/default_s_and_c_library.md', 'r') as f:
            return f.read()
    except FileNotFoundError:
        # Fallback if file doesn't exist
        return """### S&C Routine Definitions

**Note:** S&C library not found. Please contact support.
"""