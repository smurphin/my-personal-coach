#!/usr/bin/env python3
"""
Test script to verify TrainingPlan.from_dict() fix

Run this to confirm sessions are properly loaded and the input dict is not mutated.
"""
import json
from models.training_plan import TrainingPlan

# Load your plan
with open('users_data.json') as f:
    data = json.load(f)

plan_dict = data['2117356']['plan_v2']

print("="*60)
print("TESTING TrainingPlan.from_dict() FIX")
print("="*60)

# Count sessions in original dict
original_weeks = len(plan_dict['weeks'])
original_sessions = sum(len(w.get('sessions', [])) for w in plan_dict['weeks'])
print(f"\n📊 BEFORE from_dict():")
print(f"   Dict has {original_weeks} weeks")
print(f"   Dict has {original_sessions} sessions")

# Load using from_dict()
print(f"\n🔄 Calling TrainingPlan.from_dict()...")
plan_obj = TrainingPlan.from_dict(plan_dict)

# Check object
obj_weeks = len(plan_obj.weeks)
obj_sessions = sum(len(w.sessions) for w in plan_obj.weeks)
print(f"\n📊 AFTER from_dict():")
print(f"   Object has {obj_weeks} weeks")
print(f"   Object has {obj_sessions} sessions")

# Check if original dict was mutated
after_weeks = len(plan_dict['weeks'])
after_sessions = sum(len(w.get('sessions', [])) for w in plan_dict['weeks'])
print(f"\n📊 Original dict status:")
print(f"   Dict still has {after_weeks} weeks")
print(f"   Dict still has {after_sessions} sessions")

# Results
print(f"\n" + "="*60)
print("RESULTS:")
print("="*60)

if obj_sessions == 0:
    print("❌ FAILED: Object has 0 sessions (bug still exists)")
elif obj_sessions != original_sessions:
    print(f"⚠️  WARNING: Object has {obj_sessions} sessions but dict had {original_sessions}")
else:
    print(f"✅ PASS: Object loaded {obj_sessions} sessions correctly")

if after_sessions != original_sessions:
    print(f"❌ FAILED: Original dict was mutated ({original_sessions} -> {after_sessions})")
else:
    print(f"✅ PASS: Original dict not mutated ({after_sessions} sessions intact)")

if obj_sessions > 0 and after_sessions == original_sessions:
    print(f"\n🎉 ALL TESTS PASSED! from_dict() is fixed!")
else:
    print(f"\n❌ TESTS FAILED - from_dict() still broken")

print()