"""
Pre-aggregate Garmin health data into a concise summary for the assessment (Call A) and prompts.
Produces 7d vs 30d (or 14d when 30 not available) trends for HRV, sleep, body battery, and VO2 max.
"""

from typing import List, Dict, Any, Optional


def _avg(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _trend(current: Optional[float], baseline: Optional[float]) -> str:
    if current is None or baseline is None or baseline == 0:
        return "unknown"
    pct = ((current - baseline) / baseline) * 100
    if pct > 5:
        return "up"
    if pct < -5:
        return "down"
    return "stable"


def build_garmin_summary(metrics_timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build a pre-aggregated Garmin summary from a timeline of daily metrics
    (e.g. from garmin_service.extract_metrics_timeline(stats_range)).

    Returns a dict suitable for the assessment prompt and stored assessment:
    - hrv_7d_avg, hrv_30d_avg (or 14d if < 30 days), hrv_trend
    - sleep_7d_avg, sleep_trend (vs 30d/14d)
    - body_battery_7d_avg, body_battery_trend
    - vo2_max_latest, vo2_max_trend
    """
    if not metrics_timeline:
        return {}

    n = len(metrics_timeline)
    take_7 = metrics_timeline[-7:] if n >= 7 else metrics_timeline
    take_30 = metrics_timeline[-30:] if n >= 30 else metrics_timeline[-14:] if n >= 14 else metrics_timeline

    hrv_7 = [m["hrv_value"] for m in take_7 if m.get("hrv_value") is not None]
    hrv_30 = [m["hrv_value"] for m in take_30 if m.get("hrv_value") is not None]
    sleep_7 = [m["sleep_score"] for m in take_7 if m.get("sleep_score") is not None]
    sleep_30 = [m["sleep_score"] for m in take_30 if m.get("sleep_score") is not None]
    bb_7 = [m.get("body_battery_high") or m.get("body_battery_low") for m in take_7]
    bb_7 = [v for v in bb_7 if v is not None]
    bb_30 = [m.get("body_battery_high") or m.get("body_battery_low") for m in take_30]
    bb_30 = [v for v in bb_30 if v is not None]

    vo2_values = [m["vo2_max"] for m in metrics_timeline if m.get("vo2_max") is not None]
    vo2_latest = vo2_values[-1] if vo2_values else None
    vo2_7d_avg = _avg(vo2_values[-7:]) if len(vo2_values) >= 1 else None
    vo2_30d_avg = _avg(vo2_values[-30:]) if len(vo2_values) >= 1 else None
    vo2_baseline = vo2_30d_avg if vo2_30d_avg is not None else vo2_7d_avg
    vo2_trend = _trend(vo2_latest, vo2_baseline) if vo2_latest and vo2_baseline else "unknown"

    summary = {
        "hrv_7d_avg": _avg(hrv_7),
        "hrv_30d_avg": _avg(hrv_30) if len(take_30) >= 14 else None,
        "hrv_trend": _trend(_avg(hrv_7), _avg(hrv_30)) if hrv_7 and hrv_30 else "unknown",
        "sleep_7d_avg": _avg(sleep_7),
        "sleep_30d_avg": _avg(sleep_30) if len(sleep_30) >= 7 else None,
        "sleep_trend": _trend(_avg(sleep_7), _avg(sleep_30)) if sleep_7 and sleep_30 else "unknown",
        "body_battery_7d_avg": _avg(bb_7),
        "body_battery_30d_avg": _avg(bb_30) if bb_30 else None,
        "body_battery_trend": _trend(_avg(bb_7), _avg(bb_30)) if bb_7 and bb_30 else "unknown",
        "vo2_max_latest": vo2_latest,
        "vo2_max_trend": vo2_trend,
    }
    return {k: v for k, v in summary.items() if v is not None}
