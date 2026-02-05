"""
Utility functions for training plan operations.
"""

from datetime import date, datetime
from typing import Optional, Dict, Any
from models.training_plan import TrainingPlan, Week


def archive_and_restore_past_weeks(current_plan_v2: Optional[Dict[str, Any]], new_plan_v2: Optional[TrainingPlan]) -> Optional[TrainingPlan]:
    """
    Safeguard function to archive past weeks before plan regeneration
    and merge them back into the new plan.
    
    Args:
        current_plan_v2: Current plan_v2 dict (before regeneration)
        new_plan_v2: New TrainingPlan object (after parsing AI response)
    
    Returns:
        Modified new_plan_v2 with past weeks merged back in, or None if invalid
    """
    if not new_plan_v2 or not new_plan_v2.weeks:
        return new_plan_v2
    
    archived_past_weeks = []
    today = date.today()
    
    if current_plan_v2 and 'weeks' in current_plan_v2:
        try:
            plan_v2_obj = TrainingPlan.from_dict(current_plan_v2)
            for week in plan_v2_obj.weeks:
                # Check if week is in the past (end_date is before today)
                if week.end_date:
                    try:
                        week_end = datetime.strptime(week.end_date, '%Y-%m-%d').date()
                        if week_end < today:
                            # Archive this past week
                            archived_past_weeks.append(week.to_dict())
                            print(f"   📦 Archived past week {week.week_number} (ended {week.end_date})")
                    except (ValueError, TypeError):
                        pass  # Skip weeks with invalid dates
        except Exception as e:
            print(f"   ⚠️  Error archiving past weeks: {e}")
    
    if archived_past_weeks:
        # Convert archived weeks back to Week objects
        past_week_objects = []
        for week_dict in archived_past_weeks:
            try:
                past_week = Week.from_dict(week_dict)
                past_week_objects.append(past_week)
            except Exception as e:
                print(f"   ⚠️  Could not restore archived week {week_dict.get('week_number')}: {e}")
                continue
        
        if past_week_objects:
            # Sort by week_number to ensure correct order
            past_week_objects.sort(key=lambda w: w.week_number)
            
            # Find the highest week number in archived past weeks
            max_past_week_num = max(w.week_number for w in past_week_objects) if past_week_objects else 0
            
            # Get the new plan's weeks (before merging)
            new_plan_weeks = list(new_plan_v2.weeks)
            
            # Check if new plan starts from Week 1 (AI regenerated everything)
            new_plan_min_week = min(w.week_number for w in new_plan_weeks) if new_plan_weeks else 1
            
            if new_plan_min_week == 1 and max_past_week_num > 0:
                # AI regenerated from Week 1 (or Week 0) - we're prepending past weeks, so drop
                # the first N weeks from the new plan (same period as our past weeks). Use the
                # count of past weeks (covers Week 0 + Week 1, etc.), not max_past_week_num.
                num_past_weeks = len(past_week_objects)
                print(f"   🔄 New plan starts at Week 1, but we have {num_past_weeks} past week(s) (up to Week {max_past_week_num})")
                print(f"   🔢 Dropping first {num_past_weeks} weeks from new plan (same period as archived past weeks)")
                new_plan_weeks = new_plan_weeks[num_past_weeks:]
                if not new_plan_weeks:
                    print(f"   ⚠️  New plan had only past weeks; keeping archived past weeks only")
                else:
                    # Renumber remaining new weeks to follow past weeks (1-based)
                    for i, week in enumerate(new_plan_weeks):
                        week.week_number = max_past_week_num + 1 + i
            
            # Avoid duplicating weeks: if a past week's week_number already exists
            # in the new plan, skip that past week (the new plan's version wins).
            new_plan_week_numbers = {w.week_number for w in new_plan_weeks}
            filtered_past_weeks = []
            for w in past_week_objects:
                if w.week_number in new_plan_week_numbers:
                    print(f"   ⚠️  Skipping archived past week {w.week_number} because new plan already has this week number")
                else:
                    filtered_past_weeks.append(w)
            
            # Merge: filtered past weeks + (possibly renumbered) new weeks
            new_plan_v2.weeks = filtered_past_weeks + new_plan_weeks
            # Renumber consecutive, preserving week 0 if it was in the past (partial-week case)
            has_week_zero = any(w.week_number == 0 for w in past_week_objects)
            start = 0 if has_week_zero else 1
            for i, week in enumerate(new_plan_v2.weeks):
                week.week_number = start + i
            print(f"   ✅ Merged {len(filtered_past_weeks)} archived past weeks back into plan")
            print(f"   📊 Final plan: {len(new_plan_v2.weeks)} weeks total (Weeks {new_plan_v2.weeks[0].week_number} to {new_plan_v2.weeks[-1].week_number})")
    
    return new_plan_v2

