# Cycling zones and feedback

This note explains how the coach uses heart rate and power zones for cycling (Ride / VirtualRide) and why HR and power zone numbers often don’t align.

## Zone systems

- **Heart rate:** 5 zones, **Joe Friel LTHR-based** (Lactate Threshold Heart Rate). Used for all activities. Prescribed in the plan as “Zone 1”, “Zone 2”, etc.
- **Power:** 7 zones, **FTP-based** (Functional Threshold Power), following Coggan/Friel methodology. Used only when the athlete has an FTP set and the activity has power data.

The two systems use different references (LTHR vs FTP) and different numbers of zones. They are **not** meant to map one-to-one.

## Why HR and power zones often don’t match

- It is normal for “HR Zone 2” to coincide with “Power Zone 2 or 3” (or even a one–two zone gap in either direction). This can reflect aerobic efficiency, decoupling, or different sensitivity of HR vs power to fatigue and fitness.
- Per Joe Friel ([Should Heart Rate and Power Zones Agree?](https://joefrieltraining.com/should-heart-rate-and-power-zones-agree/)), he has never had an athlete whose HR and power zones agreed exactly. A one-zone gap is common; a two-zone gap is unusual but not unheard of. When HR is low for a given power, that often indicates good aerobic fitness relative to power – not a fault.
- So a “mismatch” between HR zone and power zone in feedback is **expected** and should not be framed as a calibration error or something that “must be resolved” unless there is other evidence (e.g. athlete reports wrong readings, or an explicitly power-targeted session is way off).

## How cycling feedback is judged

- **Prescribed target:** Cycling sessions in the plan are typically prescribed by **HR** (e.g. “Zone 2”, “Zone 1–2”). Adherence is judged on **HR zone distribution**.
- **Power:** When power data exists, it is reported for context. Power zone distribution is **not** used to criticise the session when the athlete has hit the prescribed HR zones. Wording like “You stayed in HR Zone 1–2 as intended; power showed time in Power Zones 2–3, which is a normal pattern” is used instead of “discrepancy” or “calibration issue”.
- **No power meter:** If an activity has no power data (no power meter on the bike), feedback is based purely on HR (and duration, distance, notes). The coach does **not** suggest the power meter is broken or needs fixing; many bikes don’t have a power meter and that is expected.

## References

- Joe Friel: [Should Heart Rate and Power Zones Agree?](https://joefrieltraining.com/should-heart-rate-and-power-zones-agree/)
- Zone calculations: `TrainingService.calculate_friel_hr_zones` (LTHR) and `calculate_friel_power_zones` (FTP) in `services/training_service.py`.
- Feedback rules: cycling-specific guidance in `prompts/feedback_prompt.txt` (Session Analysis → CYCLING).
