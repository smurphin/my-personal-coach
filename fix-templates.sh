#!/bin/bash
# Automated Template Fixer for Flask Blueprint Migration
# This script updates all url_for() calls in templates to use blueprint prefixes

set -e  # Exit on error

echo "🔧 Flask Blueprint Template Fixer"
echo "=================================="
echo ""

# Check if templates directory exists
if [ ! -d "templates" ]; then
    echo "❌ Error: templates/ directory not found"
    echo "Please run this script from your project root directory"
    exit 1
fi

# Backup templates
echo "📦 Creating backup..."
BACKUP_DIR="templates_backup_$(date +%Y%m%d_%H%M%S)"
cp -r templates "$BACKUP_DIR"
echo "✅ Backup created: $BACKUP_DIR"
echo ""

cd templates/

echo "🔍 Fixing url_for() calls..."

# Fix dashboard blueprint
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('dashboard')/url_for('dashboard.dashboard')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"dashboard\")/url_for(\"dashboard.dashboard\")/g" {} +

# Fix auth blueprint
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('login')/url_for('auth.login')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"login\")/url_for(\"auth.login\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('logout')/url_for('auth.logout')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"logout\")/url_for(\"auth.logout\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('callback')/url_for('auth.callback')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"callback\")/url_for(\"auth.callback\")/g" {} +

# Fix plan blueprint
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('onboarding')/url_for('plan.onboarding')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"onboarding\")/url_for(\"plan.onboarding\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('view_plan')/url_for('plan.view_plan')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"view_plan\")/url_for(\"plan.view_plan\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('generate_plan')/url_for('plan.generate_plan')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"generate_plan\")/url_for(\"plan.generate_plan\")/g" {} +

# Fix feedback blueprint
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('feedback')/url_for('feedback.feedback')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"feedback\")/url_for(\"feedback.feedback\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('coaching_log')/url_for('feedback.coaching_log')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"coaching_log\")/url_for(\"feedback.coaching_log\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('view_specific_feedback'/url_for('feedback.view_specific_feedback'/g" {} +

# Fix dashboard blueprint (other routes)
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('index')/url_for('dashboard.index')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"index\")/url_for(\"dashboard.index\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('chat')/url_for('dashboard.chat')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"chat\")/url_for(\"dashboard.chat\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('chat_log_list')/url_for('dashboard.chat_log_list')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"chat_log_list\")/url_for(\"dashboard.chat_log_list\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('clear_chat')/url_for('dashboard.clear_chat')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"clear_chat\")/url_for(\"dashboard.clear_chat\")/g" {} +

# Fix admin blueprint
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('connections')/url_for('admin.connections')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"connections\")/url_for(\"admin.connections\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('garmin_login')/url_for('admin.garmin_login')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"garmin_login\")/url_for(\"admin.garmin_login\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('garmin_disconnect')/url_for('admin.garmin_disconnect')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"garmin_disconnect\")/url_for(\"admin.garmin_disconnect\")/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for('delete_data')/url_for('admin.delete_data')/g" {} +
find . -type f -name "*.html" -exec sed -i.bak "s/url_for(\"delete_data\")/url_for(\"admin.delete_data\")/g" {} +

echo "✅ url_for() calls fixed"
echo ""

echo "🔍 Fixing hardcoded paths..."

# Fix hardcoded paths in href attributes
find . -type f -name "*.html" -exec sed -i.bak 's|href="/dashboard"|href="{{ url_for('\''dashboard.dashboard'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|href="/login"|href="{{ url_for('\''auth.login'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|href="/logout"|href="{{ url_for('\''auth.logout'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|href="/onboarding"|href="{{ url_for('\''plan.onboarding'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|href="/plan"|href="{{ url_for('\''plan.view_plan'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|href="/feedback"|href="{{ url_for('\''feedback.feedback'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|href="/log"|href="{{ url_for('\''feedback.coaching_log'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|href="/connections"|href="{{ url_for('\''admin.connections'\'') }}"|g' {} +

# Fix hardcoded paths in action attributes
find . -type f -name "*.html" -exec sed -i.bak 's|action="/chat"|action="{{ url_for('\''dashboard.chat'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|action="/clear_chat"|action="{{ url_for('\''dashboard.clear_chat'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|action="/garmin_login"|action="{{ url_for('\''admin.garmin_login'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|action="/garmin_disconnect"|action="{{ url_for('\''admin.garmin_disconnect'\'') }}"|g' {} +
find . -type f -name "*.html" -exec sed -i.bak 's|action="/generate_plan"|action="{{ url_for('\''plan.generate_plan'\'') }}"|g' {} +

echo "✅ Hardcoded paths fixed"
echo ""

# Clean up .bak files
echo "🧹 Cleaning up backup files..."
find . -name "*.bak" -delete
echo "✅ Cleanup complete"
echo ""

cd ..

echo "✅ All templates have been updated!"
echo ""
echo "📋 Summary:"
echo "  - Backup created: $BACKUP_DIR"
echo "  - All url_for() calls updated with blueprint prefixes"
echo "  - Hardcoded paths converted to url_for() calls"
echo ""
echo "⚠️  IMPORTANT: Test your application thoroughly!"
echo ""
echo "Testing checklist:"
echo "  [ ] Start app: python app.py"
echo "  [ ] Visit homepage"
echo "  [ ] Test login"
echo "  [ ] Check dashboard"
echo "  [ ] Test all navigation links"
echo "  [ ] Verify forms work"
echo ""
echo "If something goes wrong, restore from backup:"
echo "  rm -rf templates"
echo "  mv $BACKUP_DIR templates"