from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional


@dataclass
class WeekRange:
    """Represents a single training week in the calendar."""

    week_number: int
    start_date: date
    end_date: date

    def to_dict(self) -> dict:
        """Return a serialisable representation suitable for prompts/JSON."""
        return {
            "week_number": self.week_number,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }


def _to_date(value: date | str) -> date:
    """Normalise input into a `date` object (accepts ISO string or date)."""
    if isinstance(value, date):
        return value
    # Accept full ISO datetime as well, truncating to date
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", "")).date()
    return datetime.fromisoformat(value).date()


def generate_week_calendar(
    plan_start_date: date | str,
    weeks_until_goal: int,
    has_partial_week: bool = False,
    days_in_partial_week: Optional[int] = None,
) -> List[WeekRange]:
    """
    Generate a calendar of training weeks from a start date and week count.

    This helper is the single source of truth for week ranges used by plan
    generation (JSON-first) and any fallback paths. It deliberately keeps the
    rules simple and predictable:

    - Weeks are contiguous, no gaps.
    - Each full week is 7 days.
    - An optional Week 0 can be a partial week when onboarding happens
      mid-week; its length is controlled by `days_in_partial_week`.
    - Date arithmetic naturally handles December→January (year rollover).

    Args:
        plan_start_date: First day the athlete can train (ISO string or date).
        weeks_until_goal: Number of full weeks *after* any partial week.
        has_partial_week: If True, create Week 0 as a partial week starting on
            plan_start_date.
        days_in_partial_week: Length of Week 0 in days (>=1). If omitted while
            has_partial_week is True, defaults to the remaining days until
            the next 7-day boundary.

    Returns:
        List of WeekRange objects in order (Week 0 if partial, then Week 1..N).
    """
    if weeks_until_goal < 0:
        raise ValueError("weeks_until_goal must be non-negative")

    start = _to_date(plan_start_date)
    weeks: List[WeekRange] = []
    current_start = start
    week_number = 0

    # Optional partial Week 0
    if has_partial_week:
        if days_in_partial_week is None:
            # Default: remaining days until we reach a 7-day boundary
            # (e.g. if starting on Wednesday, partial week might be Wed–Sun).
            days_in_partial_week = 7
        if days_in_partial_week <= 0:
            raise ValueError("days_in_partial_week must be positive when provided")

        partial_end = current_start + timedelta(days=days_in_partial_week - 1)
        weeks.append(WeekRange(week_number=week_number, start_date=current_start, end_date=partial_end))

        # Next week starts the day after the partial week
        current_start = partial_end + timedelta(days=1)
        week_number += 1

    # Full 7-day weeks
    for _ in range(weeks_until_goal):
        end = current_start + timedelta(days=6)
        weeks.append(WeekRange(week_number=week_number, start_date=current_start, end_date=end))
        current_start = end + timedelta(days=1)
        week_number += 1

    return weeks

