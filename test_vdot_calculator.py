#!/usr/bin/env python3
"""
Test script to verify VDOT calculator works with your CSV.

Usage:
    python test_vdot_calculator.py
"""
from utils.vdot_calculator import VDOTCalculator, get_vdot_from_race, validate_ai_vdot


def test_with_your_csv():
    """Test VDOT calculator with your actual CSV file"""
    
    print("="*70)
    print("VDOT Calculator Test - Using Your CSV")
    print("="*70)
    
    # Initialize calculator with your CSV (try both paths)
    import os
    csv_paths = [
        'data/vdot_table.csv',
        'VDOT_tables_-_VDOT_tables.csv',
        '../VDOT_tables_-_VDOT_tables.csv'
    ]
    
    csv_path = None
    for path in csv_paths:
        if os.path.exists(path):
            csv_path = path
            print(f"✅ Found CSV at: {path}\n")
            break
    
    if not csv_path:
        print("❌ Could not find VDOT CSV file!")
        print("\nSearched:")
        for path in csv_paths:
            print(f"   - {path}")
        print("\nPlease copy VDOT_tables_-_VDOT_tables.csv to data/vdot_table.csv")
        return
    
    calc = VDOTCalculator(csv_path=csv_path)
    
    if not calc.vdot_table:
        print("❌ Failed to load CSV!")
        return
    
    print(f"\n✅ Loaded {len(calc.vdot_table)} VDOT entries")
    print(f"   VDOT range: {min(calc.vdot_table.keys())} - {max(calc.vdot_table.keys())}")
    
    # Test 1: Your half marathon time (1:32:00)
    print("\n" + "="*70)
    print("Test 1: Your Half Marathon (1:32:00)")
    print("="*70)
    
    hm_seconds = 92 * 60  # 1 hour 32 minutes = 5520 seconds
    vdot_hm = get_vdot_from_race('HM', hm_seconds)
    
    if vdot_hm:
        print(f"✅ VDOT from 1:32:00 half marathon: {vdot_hm}")
        
        # Get equivalent times
        equiv_times = calc.get_equivalent_times(vdot_hm)
        print(f"\nEquivalent race times at VDOT {vdot_hm}:")
        for distance, time in equiv_times.items():
            print(f"   {distance:20s}: {time}")
        
        # Get training paces
        paces = calc.get_training_paces(vdot_hm)
        print(f"\nTraining paces from CSV:")
        for pace_type, pace in paces.items():
            print(f"   {pace_type:30s}: {pace}")
    
    # Test 2: Your 5K PB (19:34)
    print("\n" + "="*70)
    print("Test 2: Your 5K PB (19:34)")
    print("="*70)
    
    five_k_seconds = 19 * 60 + 34  # 19:34 = 1174 seconds
    vdot_5k = get_vdot_from_race('5K', five_k_seconds)
    
    if vdot_5k:
        print(f"✅ VDOT from 19:34 5K: {vdot_5k}")
        
        # Simplified training paces
        simple_paces = calc.suggest_training_paces(vdot_5k)
        print(f"\nSimplified training paces:")
        pace_names = {
            'E': 'Easy/Long',
            'M': 'Marathon',
            'T': 'Threshold',
            'I': 'Interval',
            'R': 'Repetition'
        }
        for code, pace in simple_paces.items():
            print(f"   {pace_names[code]:15s} ({code}): {pace}")
    
    # Test 3: Your 10K PB (39:53)
    print("\n" + "="*70)
    print("Test 3: Your 10K PB (39:53)")
    print("="*70)
    
    ten_k_seconds = 39 * 60 + 53  # 39:53 = 2393 seconds
    vdot_10k = get_vdot_from_race('10K', ten_k_seconds)
    
    if vdot_10k:
        print(f"✅ VDOT from 39:53 10K: {vdot_10k}")
    
    # Test 4: Your Marathon PB (3:26)
    print("\n" + "="*70)
    print("Test 4: Your Marathon PB (3:26)")
    print("="*70)
    
    marathon_seconds = (3 * 3600) + (26 * 60)  # 3:26:00 = 12360 seconds
    vdot_marathon = get_vdot_from_race('MARATHON', marathon_seconds)
    
    if vdot_marathon:
        print(f"✅ VDOT from 3:26:00 marathon: {vdot_marathon}")
    
    # Test 5: Validate AI calculation
    print("\n" + "="*70)
    print("Test 5: AI Validation (catching errors)")
    print("="*70)
    
    # Simulate AI saying VDOT is 48 for your half marathon
    is_valid, correct = validate_ai_vdot('HM', hm_seconds, ai_vdot=48.0, tolerance=2.0)
    
    if is_valid:
        print(f"✅ AI VDOT of 48.0 is within tolerance")
    else:
        print(f"⚠️  AI VDOT validation failed!")
        print(f"   AI said: 48.0")
        print(f"   Correct: {correct}")
        print(f"   This is the kind of error the validator catches!")
    
    # Test 6: Different distance formats
    print("\n" + "="*70)
    print("Test 6: Distance Format Variations")
    print("="*70)
    
    test_distances = [
        ('5K', 1174),
        ('5000M', 1174),
        ('5km', 1174),
        ('HM', 5520),
        ('Half Marathon', 5520),
        ('21K', 5520),
        ('MARATHON', 12360),
        ('42K', 12360),
    ]
    
    for distance, time_seconds in test_distances:
        vdot = get_vdot_from_race(distance, time_seconds)
        if vdot:
            print(f"   {distance:20s} → VDOT {vdot}")
    
    print("\n" + "="*70)
    print("All tests completed!")
    print("="*70)


if __name__ == '__main__':
    test_with_your_csv()