#!/usr/bin/env python3
"""
Regression test (script-style) for Strava splits/laps parsing in TrainingService.analyze_activity().

Usage:
    python test_splits_laps_analysis.py
"""

import importlib.util
import os


def _load_training_service():
    """
    Load `services/training_service.py` without importing the `services` package.

    The `services` package `__init__.py` imports other modules (e.g. Strava/AWS deps) which
    aren't needed for this unit-level regression test.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    svc_path = os.path.join(here, "services", "training_service.py")
    spec = importlib.util.spec_from_file_location("training_service_mod", svc_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.training_service


def main():
    training_service = _load_training_service()

    # Minimal activity with lap structure similar to issue #85.
    # We simulate alternating hard/easy intervals via average_speed.
    activity = {
        "id": 123,
        "name": "Afternoon Run",
        "type": "Run",
        "start_date_local": "2025-11-30T16:00:00Z",
        "workout_type": 0,
        "distance": 11140.0,
        "moving_time": 3600,
        "total_elevation_gain": 50.0,
        "average_speed": 3.09,
        "average_heartrate": 145.0,
        "max_heartrate": 175.0,
        "laps": [
            {"name": "Lap 1", "lap_index": 1, "distance": 180.0, "elapsed_time": 180, "average_speed": 1.6, "average_heartrate": 130, "pace_zone": 1},
            {"name": "Lap 2", "lap_index": 2, "distance": 760.0, "elapsed_time": 180, "average_speed": 4.2, "average_heartrate": 164, "pace_zone": 5},
            {"name": "Lap 3", "lap_index": 3, "distance": 240.0, "elapsed_time": 180, "average_speed": 1.3, "average_heartrate": 131, "pace_zone": 1},
            {"name": "Lap 4", "lap_index": 4, "distance": 690.0, "elapsed_time": 180, "average_speed": 3.8, "average_heartrate": 162, "pace_zone": 4},
            {"name": "Lap 5", "lap_index": 5, "distance": 320.0, "elapsed_time": 180, "average_speed": 1.8, "average_heartrate": 137, "pace_zone": 1},
            {"name": "Lap 6", "lap_index": 6, "distance": 740.0, "elapsed_time": 180, "average_speed": 4.1, "average_heartrate": 163, "pace_zone": 5},
            {"name": "Lap 7", "lap_index": 7, "distance": 300.0, "elapsed_time": 180, "average_speed": 1.7, "average_heartrate": 136, "pace_zone": 1},
            {"name": "Lap 8", "lap_index": 8, "distance": 700.0, "elapsed_time": 180, "average_speed": 4.0, "average_heartrate": 162, "pace_zone": 4},
        ],
        "splits_metric": [
            {"split": 1, "distance": 1000.0, "elapsed_time": 360, "average_speed": 2.78, "average_heartrate": 140, "pace_zone": 2},
            {"split": 2, "distance": 1000.0, "elapsed_time": 350, "average_speed": 2.86, "average_heartrate": 142, "pace_zone": 2},
        ],
    }

    analyzed = training_service.analyze_activity(activity, streams=None, zones={})

    assert analyzed["distance"] == 11140.0
    assert analyzed["moving_time"] == 3600

    assert "laps_summary" in analyzed
    assert analyzed["laps_summary"]["kind"] == "laps"
    assert analyzed["laps_summary"]["count"] == 8
    assert len(analyzed["laps_summary"]["segments"]) == 8

    assert "splits_metric_summary" in analyzed
    assert analyzed["splits_metric_summary"]["count"] == 2

    intervals = analyzed.get("intervals_detected") or {}
    assert intervals.get("has_intervals") is True, f"Expected intervals, got: {intervals}"

    print("✅ PASS: splits/laps summaries and interval detection are present and sane")


if __name__ == "__main__":
    main()


