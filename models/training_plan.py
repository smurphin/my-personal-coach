"""
Data models for training plans, sessions, and athlete metrics.

This module provides structured data classes for:
- Individual training sessions with completion tracking
- Weekly plan organization
- Complete training plans with metadata
- Training metrics (LTHR, FTP, VDOT) with history
"""
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from copy import deepcopy
import json


@dataclass
class Session:
    """
    Represents a single training session within a training plan.
    
    Attributes:
        id: Unique identifier (e.g., 'w1-mon', 'w2-wed', 'w1-session1')
        day: Day of week (Monday, Tuesday, etc.) - can be generic like "Anytime"
        date: ISO date string (YYYY-MM-DD) - Optional for flexible scheduling
        type: Session type (RUN, BIKE, SWIM, REST, CROSS_TRAIN, etc.)
        priority: Importance (KEY, IMPORTANT, STRETCH, or None)
        duration_minutes: Planned duration
        description: Detailed session description
        zones: Target zones (hr_target, pace_target, power_target)
        scheduled: Whether session is locked to a specific date (Disciplinarian=True, others=False)
        completed: Whether the session was completed
        strava_activity_id: Linked Strava activity ID
        completed_at: ISO timestamp when completed
    """
    id: str
    day: str  # "Monday" or "Anytime this week" or "Weekend"
    type: str  # RUN, BIKE, SWIM, REST, CROSS_TRAIN, STRENGTH
    date: Optional[str] = None  # ISO format YYYY-MM-DD - None for flexible scheduling
    priority: Optional[str] = None  # KEY, IMPORTANT, STRETCH
    duration_minutes: Optional[int] = None
    description: str = ""
    zones: Dict[str, Any] = field(default_factory=dict)  # hr_target, pace_target, power_target, notes
    scheduled: bool = True  # False for Improviser/Minimalist flexible sessions
    completed: bool = False
    strava_activity_id: Optional[int] = None
    completed_at: Optional[str] = None  # ISO timestamp
    s_and_c_routine: Optional[str] = None  # Reference to library routine (e.g., "routine_1_core")
    
    def mark_complete(self, activity_id: int, completed_at: Optional[str] = None):
        """Mark session as completed with activity ID"""
        self.completed = True
        self.strava_activity_id = activity_id
        self.completed_at = completed_at or datetime.now().isoformat()
    
    def mark_incomplete(self):
        """Mark session as not completed"""
        self.completed = False
        self.strava_activity_id = None
        self.completed_at = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Session':
        """Create Session from dictionary"""
        return cls(**data)
    
    def to_markdown(self) -> str:
        """Render session as markdown text"""
        lines = []
        
        # Header with priority - show date only if scheduled
        priority_str = f"[{self.priority}] " if self.priority else ""
        if self.scheduled and self.date:
            lines.append(f"### {priority_str}{self.day} ({self.date})")
        else:
            # Flexible scheduling - just show day/timeframe
            lines.append(f"### {priority_str}{self.day}")
        
        # Type and duration
        if self.type == "REST":
            lines.append("**REST**")
        else:
            duration_str = f" - {self.duration_minutes} min" if self.duration_minutes else ""
            lines.append(f"**{self.type}**{duration_str}")
        
        # Description
        if self.description:
            lines.append(f"{self.description}")
        
        # Zone targets
        if self.zones:
            zone_parts = []
            if 'hr_target' in self.zones and self.zones['hr_target']:
                zone_parts.append(f"HR: {', '.join(self.zones['hr_target'])}")
            if 'pace_target' in self.zones and self.zones['pace_target']:
                zone_parts.append(f"Pace: {', '.join(self.zones['pace_target'])}")
            if 'power_target' in self.zones and self.zones['power_target']:
                zone_parts.append(f"Power: {', '.join(self.zones['power_target'])}")
            if zone_parts:
                lines.append(f"*Zones: {' | '.join(zone_parts)}*")
            if 'notes' in self.zones and self.zones['notes']:
                lines.append(f"*{self.zones['notes']}*")
        
        # Completion status
        if self.completed:
            completed_date = self.completed_at[:10] if self.completed_at else 'unknown'
            lines.append(f"✅ Completed on {completed_date}")
        
        return "\n".join(lines) + "\n"


@dataclass
class Week:
    """
    Represents a week within a training plan.
    
    Attributes:
        week_number: Week number (1-indexed)
        start_date: ISO date string (YYYY-MM-DD)
        end_date: ISO date string (YYYY-MM-DD)
        description: Weekly focus/theme
        sessions: List of Session objects
    """
    week_number: int
    start_date: str  # ISO format YYYY-MM-DD
    end_date: str  # ISO format YYYY-MM-DD
    description: str = ""
    sessions: List[Session] = field(default_factory=list)
    
    def get_session_by_id(self, session_id: str) -> Optional[Session]:
        """Get session by ID"""
        for session in self.sessions:
            if session.id == session_id:
                return session
        return None
    
    def get_session_by_date(self, date_str: str) -> Optional[Session]:
        """Get session by date (YYYY-MM-DD). Returns None for unscheduled sessions."""
        for session in self.sessions:
            if session.date and session.date == date_str:
                return session
        return None
    
    def get_unscheduled_sessions(self) -> List[Session]:
        """Get all sessions that are not locked to specific dates (Improviser/Minimalist)"""
        return [s for s in self.sessions if not s.scheduled or not s.date]
    
    def get_completed_sessions(self) -> List[Session]:
        """Get all completed sessions in this week"""
        return [s for s in self.sessions if s.completed]
    
    def get_pending_sessions(self) -> List[Session]:
        """Get all pending (not completed) sessions in this week"""
        return [s for s in self.sessions if not s.completed]
    
    def completion_percentage(self) -> float:
        """Calculate percentage of sessions completed (excluding REST days)"""
        non_rest = [s for s in self.sessions if s.type != "REST"]
        if not non_rest:
            return 100.0
        completed = [s for s in non_rest if s.completed]
        return (len(completed) / len(non_rest)) * 100
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'week_number': self.week_number,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'description': self.description,
            'sessions': [s.to_dict() for s in self.sessions]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Week':
        """Create Week from dictionary"""
        # Use .get() instead of .pop() to avoid mutating input dict
        sessions_data = data.get('sessions', [])
        
        # Create a copy of data without sessions for initialization
        week_data = {k: v for k, v in data.items() if k != 'sessions'}
        week = cls(**week_data)
        week.sessions = [Session.from_dict(s) for s in sessions_data]
        return week
    
    def to_markdown(self) -> str:
        """Render week as markdown text"""
        lines = []
        lines.append(f"## Week {self.week_number}: {self.start_date} to {self.end_date}")
        if self.description:
            lines.append(f"*{self.description}*\n")
        
        for session in self.sessions:
            lines.append(session.to_markdown())
        
        return "\n".join(lines) + "\n"


@dataclass
class TrainingPlan:
    """
    Represents a complete training plan with metadata and weeks.
    
    Attributes:
        version: Schema version (current: 2)
        created_at: ISO timestamp of plan creation
        athlete_id: Athlete ID
        athlete_goal: Goal description
        goal_date: Goal race/event date (YYYY-MM-DD)
        goal_distance: Distance/event name
        plan_start_date: When plan starts (YYYY-MM-DD)
        weeks: List of Week objects
        libraries: Dict of reference content (e.g., {"s_and_c": "routine definitions..."})
    """
    version: int = 2
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    athlete_id: Optional[str] = None
    athlete_goal: str = ""
    goal_date: Optional[str] = None  # ISO format YYYY-MM-DD
    goal_distance: Optional[str] = None
    plan_start_date: Optional[str] = None  # ISO format YYYY-MM-DD
    weeks: List[Week] = field(default_factory=list)
    libraries: Dict[str, str] = field(default_factory=dict)  # Reference content (S&C routines, etc.)
    
    def get_week_by_number(self, week_number: int) -> Optional[Week]:
        """Get week by number"""
        for week in self.weeks:
            if week.week_number == week_number:
                return week
        return None
    
    def get_week_by_date(self, date_str: str) -> Optional[Week]:
        """Get week containing a specific date"""
        for week in self.weeks:
            if week.start_date <= date_str <= week.end_date:
                return week
        return None
    
    def get_current_week(self) -> Optional[Week]:
        """Get current week based on today's date"""
        today = date.today().isoformat()
        return self.get_week_by_date(today)
    
    def get_session_by_id(self, session_id: str) -> Optional[Session]:
        """Get session by ID across all weeks"""
        for week in self.weeks:
            session = week.get_session_by_id(session_id)
            if session:
                return session
        return None
    
    def get_session_by_activity(self, activity_id: int) -> Optional[Session]:
        """Find session linked to a Strava activity"""
        for week in self.weeks:
            for session in week.sessions:
                if session.strava_activity_id == activity_id:
                    return session
        return None
    
    def mark_session_complete(self, session_id: str, activity_id: int, completed_at: Optional[str] = None) -> bool:
        """Mark a session as complete. Returns True if found and updated."""
        session = self.get_session_by_id(session_id)
        if session:
            session.mark_complete(activity_id, completed_at)
            return True
        return False
    
    def get_all_completed_sessions(self) -> List[Session]:
        """Get all completed sessions across all weeks"""
        completed = []
        for week in self.weeks:
            completed.extend(week.get_completed_sessions())
        return completed
    
    def overall_completion_percentage(self) -> float:
        """Calculate overall plan completion percentage"""
        all_sessions = []
        for week in self.weeks:
            all_sessions.extend([s for s in week.sessions if s.type != "REST"])
        
        if not all_sessions:
            return 0.0
        
        completed = [s for s in all_sessions if s.completed]
        return (len(completed) / len(all_sessions)) * 100
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'version': self.version,
            'created_at': self.created_at,
            'athlete_id': self.athlete_id,
            'athlete_goal': self.athlete_goal,
            'goal_date': self.goal_date,
            'goal_distance': self.goal_distance,
            'plan_start_date': self.plan_start_date,
            'weeks': [w.to_dict() for w in self.weeks],
            'libraries': self.libraries
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TrainingPlan':
        """Create TrainingPlan from dictionary"""
        # Use deepcopy to avoid mutating input (shallow copy doesn't work with nested dicts)
        data_copy = deepcopy(data)
        
        # Use .get() instead of .pop() for safety (though deepcopy protects us anyway)
        weeks_data = data_copy.get('weeks', [])
        libraries = data_copy.get('libraries', {})
        
        # Create plan data without weeks and libraries
        plan_data = {k: v for k, v in data_copy.items() if k not in ['weeks', 'libraries']}
        
        plan = cls(**plan_data)
        plan.weeks = [Week.from_dict(w) for w in weeks_data]
        plan.libraries = libraries
        return plan
    
    def to_json(self) -> str:
        """Serialize to JSON string"""
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'TrainingPlan':
        """Deserialize from JSON string"""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    def to_markdown(self) -> str:
        """Render complete plan as markdown text"""
        lines = []
        lines.append(f"# Training Plan: {self.athlete_goal}")
        if self.goal_date:
            lines.append(f"**Goal Date:** {self.goal_date}")
        if self.goal_distance:
            lines.append(f"**Goal Distance:** {self.goal_distance}")
        lines.append(f"**Created:** {self.created_at[:10]}\n")
        
        for week in self.weeks:
            lines.append(week.to_markdown())
        
        return "\n".join(lines)


@dataclass
class MetricValue:
    """
    Represents a single metric value with provenance tracking.
    """
    value: float
    detected_at: str  # ISO timestamp
    detected_from: Optional[Dict[str, Any]] = None  # activity_id, activity_name, detection_method
    user_confirmed: bool = False
    user_modified: bool = False
    history: List[Dict[str, Any]] = field(default_factory=list)
    
    def update_value(self, new_value: float, source: Dict[str, Any], user_modified: bool = False):
        """Update metric value and record in history"""
        # Save current value to history
        self.history.append({
            'value': self.value,
            'date': self.detected_at[:10],
            'source': self.detected_from.get('detection_method', 'unknown') if self.detected_from else 'unknown'
        })
        
        # Update to new value
        self.value = new_value
        self.detected_at = datetime.now().isoformat()
        self.detected_from = source
        self.user_confirmed = False  # Requires new confirmation
        self.user_modified = user_modified
    
    def confirm(self):
        """User confirms the current value"""
        self.user_confirmed = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MetricValue':
        """Create from dictionary"""
        return cls(**data)


@dataclass
class TrainingMetrics:
    """
    Stores athlete training metrics with history and confirmation tracking.
    
    Attributes:
        version: Schema version
        lthr: Lactate threshold heart rate
        ftp: Functional threshold power
        vdot: VO2max-based training metric
        zones: Calculated training zones
    """
    version: int = 1
    lthr: Optional[MetricValue] = None
    ftp: Optional[MetricValue] = None
    vdot: Optional[MetricValue] = None
    zones: Dict[str, Any] = field(default_factory=dict)  # hr, power, pace
    
    def update_lthr(self, value: int, activity_id: int, activity_name: str, 
                    detection_method: str = 'auto', user_modified: bool = False):
        """Update LTHR value"""
        source = {
            'activity_id': activity_id,
            'activity_name': activity_name,
            'detection_method': detection_method
        }
        
        if self.lthr is None:
            self.lthr = MetricValue(
                value=value,
                detected_at=datetime.now().isoformat(),
                detected_from=source,
                user_modified=user_modified
            )
        else:
            self.lthr.update_value(value, source, user_modified)
    
    def set_lthr_from_lab(self, value: int, test_date: str, notes: str = ""):
        """
        Set LTHR from lab test - this overrides any AI detection and is locked.
        
        Args:
            value: LTHR in bpm
            test_date: Date of lab test (YYYY-MM-DD)
            notes: Optional notes about the test
        """
        source = {
            'activity_id': 0,
            'activity_name': f'Lab test - {test_date}',
            'detection_method': 'lab_measured',
            'notes': notes
        }
        
        if self.lthr is None:
            self.lthr = MetricValue(
                value=value,
                detected_at=test_date + 'T00:00:00Z',
                detected_from=source,
                user_modified=True,
                user_confirmed=True  # Lab tests are pre-confirmed
            )
        else:
            self.lthr.update_value(value, source, user_modified=True)
            self.lthr.confirm()  # Auto-confirm lab values
    
    def update_ftp(self, value: int, activity_id: int, activity_name: str,
                   detection_method: str = 'auto', user_modified: bool = False):
        """Update FTP value"""
        source = {
            'activity_id': activity_id,
            'activity_name': activity_name,
            'detection_method': detection_method
        }
        
        if self.ftp is None:
            self.ftp = MetricValue(
                value=value,
                detected_at=datetime.now().isoformat(),
                detected_from=source,
                user_modified=user_modified
            )
        else:
            self.ftp.update_value(value, source, user_modified)
    
    def set_ftp_from_lab(self, value: int, test_date: str, notes: str = ""):
        """
        Set FTP from lab test - this overrides any AI detection and is locked.
        
        Args:
            value: FTP in watts
            test_date: Date of lab test (YYYY-MM-DD)
            notes: Optional notes about the test
        """
        source = {
            'activity_id': 0,
            'activity_name': f'Lab test - {test_date}',
            'detection_method': 'lab_measured',
            'notes': notes
        }
        
        if self.ftp is None:
            self.ftp = MetricValue(
                value=value,
                detected_at=test_date + 'T00:00:00Z',
                detected_from=source,
                user_modified=True,
                user_confirmed=True
            )
        else:
            self.ftp.update_value(value, source, user_modified=True)
            self.ftp.confirm()
    
    def update_vdot(self, value: float, distance: str, time_seconds: int, 
                    activity_id: int, user_modified: bool = False):
        """Update VDOT value"""
        source = {
            'activity_id': activity_id,
            'distance': distance,
            'time_seconds': time_seconds,
            'detection_method': 'vdot_calculation'
        }
        
        if self.vdot is None:
            self.vdot = MetricValue(
                value=value,
                detected_at=datetime.now().isoformat(),
                detected_from=source,
                user_modified=user_modified
            )
        else:
            self.vdot.update_value(value, source, user_modified)
    
    def recalculate_zones(self, training_service):
        """Recalculate all training zones from current metrics"""
        if self.lthr and self.lthr.value:
            self.zones['hr'] = training_service.calculate_friel_hr_zones(int(self.lthr.value))
        
        if self.ftp and self.ftp.value:
            self.zones['power'] = training_service.calculate_friel_power_zones(int(self.ftp.value))
        
        # VDOT pace zones would be calculated here when implemented
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'version': self.version,
            'lthr': self.lthr.to_dict() if self.lthr else None,
            'ftp': self.ftp.to_dict() if self.ftp else None,
            'vdot': self.vdot.to_dict() if self.vdot else None,
            'zones': self.zones
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TrainingMetrics':
        """Create TrainingMetrics from dictionary"""
        # Use .get() instead of .pop() to avoid mutating input dict
        lthr_data = data.get('lthr', None)
        ftp_data = data.get('ftp', None)
        vdot_data = data.get('vdot', None)
        
        # Create metrics dict without the metric fields
        metrics_data = {k: v for k, v in data.items() if k not in ['lthr', 'ftp', 'vdot']}
        metrics = cls(**metrics_data)
        
        if lthr_data:
            metrics.lthr = MetricValue.from_dict(lthr_data)
        if ftp_data:
            metrics.ftp = MetricValue.from_dict(ftp_data)
        if vdot_data:
            metrics.vdot = MetricValue.from_dict(vdot_data)
        
        return metrics