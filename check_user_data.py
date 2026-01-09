#!/usr/bin/env python3
"""
Diagnostic script to check what fields exist in user_data.json
Run this to see exactly what needs to be migrated/preserved
"""
import json
from pathlib import Path

def check_user_data():
    """Check what fields exist in the user data"""
    
    json_path = Path('users_data.json')
    
    if not json_path.exists():
        print("❌ users_data.json not found")
        return
    
    with open(json_path, 'r') as f:
        all_data = json.load(f)
    
    # Check for user 2117356
    if '2117356' not in all_data:
        print("❌ Athlete 2117356 not found in data")
        return
    
    user_data = all_data['2117356']
    
    print("="*60)
    print("USER DATA STRUCTURE ANALYSIS")
    print("="*60)
    
    # Check top-level fields
    print("\n📋 TOP-LEVEL FIELDS:")
    for key in sorted(user_data.keys()):
        value = user_data[key]
        if isinstance(value, str):
            print(f"  {key}: string ({len(value)} chars)")
        elif isinstance(value, dict):
            print(f"  {key}: dict ({len(value)} keys)")
        elif isinstance(value, list):
            print(f"  {key}: list ({len(value)} items)")
        else:
            print(f"  {key}: {type(value).__name__}")
    
    # Check for training metrics
    print("\n🏃 TRAINING METRICS:")
    if 'training_metrics' in user_data:
        print("  ✅ training_metrics exists")
        metrics = user_data['training_metrics']
        if isinstance(metrics, dict):
            for key in ['vdot', 'lthr', 'ftp']:
                if key in metrics:
                    metric_data = metrics[key]
                    if isinstance(metric_data, dict) and 'value' in metric_data:
                        print(f"    {key.upper()}: {metric_data['value']} (source: {metric_data.get('source', 'unknown')})")
                    else:
                        print(f"    {key.upper()}: {metric_data}")
                else:
                    print(f"    {key.upper()}: not set")
    else:
        print("  ❌ training_metrics does NOT exist")
        
        # Check plan_data structure
        if 'plan_data' in user_data:
            print("\n  Checking plan_data structure:")
            plan_data = user_data['plan_data']
            
            # Check for LTHR in friel_hr_zones
            if 'friel_hr_zones' in plan_data and 'zones' in plan_data['friel_hr_zones']:
                zones = plan_data['friel_hr_zones']['zones']
                if len(zones) >= 4 and 'max' in zones[3]:
                    lthr = zones[3]['max']
                    print(f"    ✅ Found LTHR in friel_hr_zones: {lthr} bpm (Zone 4 max)")
                else:
                    print(f"    ❌ friel_hr_zones exists but can't extract LTHR")
            else:
                print(f"    ❌ No friel_hr_zones")
            
            # Check for FTP in friel_power_zones
            if 'friel_power_zones' in plan_data:
                calc_method = plan_data['friel_power_zones'].get('calculation_method', '')
                import re
                ftp_match = re.search(r'FTP:\s*(\d+)\s*W', calc_method)
                if ftp_match:
                    ftp = ftp_match.group(1)
                    print(f"    ✅ Found FTP in friel_power_zones: {ftp} W")
                else:
                    print(f"    ⚠️  friel_power_zones exists but can't parse FTP")
            else:
                print(f"    ❌ No friel_power_zones")
            
            # Check for VDOT
            if 'vdot_data' in plan_data:
                vdot_data = plan_data['vdot_data']
                status = vdot_data.get('status', 'Unknown')
                current_vdot = vdot_data.get('current_vdot')
                print(f"    ℹ️  VDOT status: {status}")
                if current_vdot:
                    print(f"    ✅ Current VDOT: {current_vdot}")
                else:
                    print(f"    ⚠️  No current_vdot value (will auto-detect from races)")
            else:
                print(f"    ❌ No vdot_data")
        else:
            print("\n  ❌ No plan_data structure")
        
        # Check for old-style fields
        print("\n  Checking old-style root-level fields:")
        for field in ['vdot', 'lthr', 'ftp']:
            if field in user_data:
                print(f"    ✅ Found '{field}' at root level: {user_data[field]}")
            else:
                print(f"    ❌ No '{field}' field")
    
    # Check for lifestyle
    print("\n👤 LIFESTYLE CONTEXT:")
    if 'lifestyle' in user_data:
        print("  ✅ lifestyle exists")
        lifestyle = user_data['lifestyle']
        if isinstance(lifestyle, dict):
            for key, value in lifestyle.items():
                if isinstance(value, str) and len(value) > 50:
                    print(f"    {key}: {value[:50]}...")
                else:
                    print(f"    {key}: {value}")
    else:
        print("  ❌ lifestyle does NOT exist")
        
        # Check plan_data structure
        if 'plan_data' in user_data:
            print("\n  Checking plan_data structure:")
            plan_data = user_data['plan_data']
            
            if 'lifestyle_context' in plan_data and plan_data['lifestyle_context']:
                context_len = len(plan_data['lifestyle_context'])
                print(f"    ✅ Found lifestyle_context ({context_len} chars)")
                print(f"       Preview: {plan_data['lifestyle_context'][:80]}...")
            else:
                print(f"    ❌ No lifestyle_context")
            
            if 'athlete_type' in plan_data and plan_data['athlete_type']:
                print(f"    ✅ Found athlete_type: {plan_data['athlete_type']}")
            else:
                print(f"    ❌ No athlete_type")
        else:
            print("\n  ❌ No plan_data structure")
        
        # Check for individual fields
        print("\n  Checking individual root-level fields:")
        for field in ['work_pattern', 'family_commitments', 'training_constraints', 'athlete_type']:
            if field in user_data:
                value = user_data[field]
                if isinstance(value, str) and len(value) > 50:
                    print(f"    ✅ Found '{field}': {value[:50]}...")
                else:
                    print(f"    ✅ Found '{field}': {value}")
            else:
                print(f"    ❌ No '{field}' field")
    
    # Check for plan_v2
    print("\n📊 PLAN STATUS:")
    if 'plan_v2' in user_data:
        print("  ✅ plan_v2 exists")
        plan_v2 = user_data['plan_v2']
        if isinstance(plan_v2, dict):
            print(f"    version: {plan_v2.get('version', 'unknown')}")
            print(f"    weeks: {len(plan_v2.get('weeks', []))} weeks")
            if 'weeks' in plan_v2 and plan_v2['weeks']:
                total_sessions = sum(len(w.get('sessions', [])) for w in plan_v2['weeks'])
                print(f"    sessions: {total_sessions} total")
            else:
                print("    ⚠️  No weeks/sessions found in plan_v2!")
    else:
        print("  ❌ plan_v2 does NOT exist (migration needed)")
    
    # Check for old plan
    if 'plan' in user_data:
        print(f"  ✅ plan (markdown) exists ({len(user_data['plan'])} chars)")
    else:
        print("  ❌ plan (markdown) does NOT exist")
    
    if 'plan_structure' in user_data:
        plan_struct = user_data['plan_structure']
        if isinstance(plan_struct, dict) and 'weeks' in plan_struct:
            print(f"  ✅ plan_structure exists ({len(plan_struct['weeks'])} weeks)")
        else:
            print("  ⚠️  plan_structure exists but malformed")
    else:
        print("  ❌ plan_structure does NOT exist")
    
    print("\n" + "="*60)
    print("MIGRATION RECOMMENDATIONS:")
    print("="*60)
    
    recommendations = []
    
    if 'plan_v2' not in user_data:
        recommendations.append("✅ Run migration to create plan_v2")
    
    # Check what training metrics will be migrated
    if 'training_metrics' not in user_data:
        metrics_to_migrate = []
        
        # Check plan_data
        if 'plan_data' in user_data:
            plan_data = user_data['plan_data']
            
            if 'friel_hr_zones' in plan_data and 'zones' in plan_data['friel_hr_zones']:
                zones = plan_data['friel_hr_zones']['zones']
                if len(zones) >= 4 and 'max' in zones[3] and zones[3]['max'] > 0:
                    metrics_to_migrate.append(f"LTHR ({zones[3]['max']} bpm)")
            
            if 'friel_power_zones' in plan_data:
                calc_method = plan_data['friel_power_zones'].get('calculation_method', '')
                import re
                ftp_match = re.search(r'FTP:\s*(\d+)\s*W', calc_method)
                if ftp_match:
                    metrics_to_migrate.append(f"FTP ({ftp_match.group(1)} W)")
            
            if 'vdot_data' in plan_data and plan_data['vdot_data'].get('current_vdot'):
                vdot = plan_data['vdot_data']['current_vdot']
                metrics_to_migrate.append(f"VDOT ({vdot})")
        
        # Check root-level fields as fallback
        if 'lthr' in user_data and user_data['lthr'] and 'LTHR' not in str(metrics_to_migrate):
            metrics_to_migrate.append(f"LTHR ({user_data['lthr']} bpm)")
        if 'ftp' in user_data and user_data['ftp'] and 'FTP' not in str(metrics_to_migrate):
            metrics_to_migrate.append(f"FTP ({user_data['ftp']} W)")
        if 'vdot' in user_data and user_data['vdot'] and 'VDOT' not in str(metrics_to_migrate):
            metrics_to_migrate.append(f"VDOT ({user_data['vdot']})")
        
        if metrics_to_migrate:
            recommendations.append(f"✅ Migration will extract: {', '.join(metrics_to_migrate)}")
        else:
            recommendations.append("⚠️  No training metrics found - will need manual setup")
        
        # Check for VDOT auto-detect opportunity
        if 'vdot_data' in user_data.get('plan_data', {}):
            if user_data['plan_data']['vdot_data'].get('status') == 'VDOT Ready':
                if not user_data['plan_data']['vdot_data'].get('current_vdot'):
                    recommendations.append("ℹ️  VDOT will auto-detect from future races")
    
    # Check what lifestyle will be migrated
    if 'lifestyle' not in user_data:
        lifestyle_to_migrate = []
        
        if 'plan_data' in user_data:
            plan_data = user_data['plan_data']
            
            if 'lifestyle_context' in plan_data and plan_data['lifestyle_context']:
                context_len = len(plan_data['lifestyle_context'])
                lifestyle_to_migrate.append(f"lifestyle_context ({context_len} chars)")
            
            if 'athlete_type' in plan_data and plan_data['athlete_type']:
                lifestyle_to_migrate.append(f"athlete_type ({plan_data['athlete_type']})")
        
        # Check root-level fields
        for field in ['work_pattern', 'family_commitments', 'training_constraints', 'athlete_type']:
            if field in user_data and user_data[field]:
                lifestyle_to_migrate.append(field)
        
        if lifestyle_to_migrate:
            recommendations.append(f"✅ Migration will extract: {', '.join(lifestyle_to_migrate)}")
        else:
            recommendations.append("⚠️  No lifestyle context found - will need manual setup")
    
    if recommendations:
        for rec in recommendations:
            print(f"  {rec}")
    else:
        print("  ✅ All fields exist and are properly structured")
    
    print()

if __name__ == '__main__':
    check_user_data()