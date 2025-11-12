#!/bin/bash

# GitHub Issues Creation Script for kAIzen Coach
# Prerequisites: GitHub CLI (gh) must be installed and authenticated
# Install: https://cli.github.com/
# Usage: chmod +x create-github-issues.sh && ./create-github-issues.sh

REPO="smurphin/my-personal-coach"

echo "================================================"
echo "Creating Labels for kAIzen Coach"
echo "================================================"

# Create labels (will skip if they already exist)
gh label create "security" --color "B60205" --description "Security-related issues" --repo $REPO 2>/dev/null || echo "✓ Label 'security' exists"
gh label create "critical" --color "D93F0B" --description "Critical priority" --repo $REPO 2>/dev/null || echo "✓ Label 'critical' exists"
gh label create "bug" --color "D73A4A" --description "Something isn't working" --repo $REPO 2>/dev/null || echo "✓ Label 'bug' exists"
gh label create "enhancement" --color "A2EEEF" --description "New feature or request" --repo $REPO 2>/dev/null || echo "✓ Label 'enhancement' exists"
gh label create "refactor" --color "FEF2C0" --description "Code refactoring" --repo $REPO 2>/dev/null || echo "✓ Label 'refactor' exists"
gh label create "testing" --color "BFD4F2" --description "Testing related" --repo $REPO 2>/dev/null || echo "✓ Label 'testing' exists"
gh label create "documentation" --color "0075CA" --description "Documentation improvements" --repo $REPO 2>/dev/null || echo "✓ Label 'documentation' exists"
gh label create "infrastructure" --color "D4C5F9" --description "Infrastructure and DevOps" --repo $REPO 2>/dev/null || echo "✓ Label 'infrastructure' exists"
gh label create "ux" --color "C5DEF5" --description "User experience" --repo $REPO 2>/dev/null || echo "✓ Label 'ux' exists"
gh label create "performance" --color "FBCA04" --description "Performance improvements" --repo $REPO 2>/dev/null || echo "✓ Label 'performance' exists"
gh label create "monitoring" --color "5319E7" --description "Monitoring and observability" --repo $REPO 2>/dev/null || echo "✓ Label 'monitoring' exists"
gh label create "analytics" --color "BFD4F2" --description "Analytics and tracking" --repo $REPO 2>/dev/null || echo "✓ Label 'analytics' exists"
gh label create "feature" --color "7057FF" --description "New feature" --repo $REPO 2>/dev/null || echo "✓ Label 'feature' exists"
gh label create "code-quality" --color "FEF2C0" --description "Code quality improvements" --repo $REPO 2>/dev/null || echo "✓ Label 'code-quality' exists"
gh label create "small" --color "C2E0C6" --description "1-2 days of work" --repo $REPO 2>/dev/null || echo "✓ Label 'small' exists"
gh label create "medium" --color "FFEB95" --description "3-5 days of work" --repo $REPO 2>/dev/null || echo "✓ Label 'medium' exists"
gh label create "large" --color "FFA07A" --description "1-2 weeks of work" --repo $REPO 2>/dev/null || echo "✓ Label 'large' exists"

echo ""
echo "================================================"
echo "Creating Milestones using GitHub API"
echo "================================================"

# Create milestones using GitHub API (suppress output, errors ok if exists)
gh api repos/$REPO/milestones -f title="v1.1 - Security & Stability" -f description="Critical security fixes and error handling improvements" 2>/dev/null > /dev/null || echo "✓ Milestone 'v1.1' exists"
gh api repos/$REPO/milestones -f title="v1.2 - Code Quality" -f description="Refactoring, testing, and code organization" 2>/dev/null > /dev/null || echo "✓ Milestone 'v1.2' exists"
gh api repos/$REPO/milestones -f title="v1.3 - Performance" -f description="Optimization and performance improvements" 2>/dev/null > /dev/null || echo "✓ Milestone 'v1.3' exists"
gh api repos/$REPO/milestones -f title="v1.4 - Features & UX" -f description="New features and user experience enhancements" 2>/dev/null > /dev/null || echo "✓ Milestone 'v1.4' exists"
gh api repos/$REPO/milestones -f title="v1.5 - Observability" -f description="Monitoring, logging, and documentation" 2>/dev/null > /dev/null || echo "✓ Milestone 'v1.5' exists"

echo "✓ All milestones created"
echo ""
echo "================================================"
echo "Creating Issues"
echo "================================================"

# Define milestone names (gh issue create uses titles, not numbers)
M1="v1.1 - Security & Stability"
M2="v1.2 - Code Quality"
M3="v1.3 - Performance"
M4="v1.4 - Features & UX"
M5="v1.5 - Observability"

# ============================================
# SECURITY ISSUES (Critical Priority)
# ============================================

echo "Creating security issues..."

gh issue create --repo $REPO \
  --title "Add CSRF Protection" \
  --label "security,critical,small" \
  --milestone "$M1" \
  --body "## Description
Add Cross-Site Request Forgery (CSRF) protection to all forms and state-changing requests.

## Current Issue
Forms are vulnerable to CSRF attacks as there's no token validation.

## Proposed Solution
Install flask-wtf and add CSRF protection to Flask app

## Acceptance Criteria
- [ ] CSRF protection enabled
- [ ] All forms include tokens
- [ ] Tests verify protection"

gh issue create --repo $REPO \
  --title "Add Rate Limiting Per User/Endpoint" \
  --label "security,enhancement,medium" \
  --milestone "$M1" \
  --body "## Description
Implement rate limiting to prevent abuse and protect API endpoints from DoS attacks.

## Suggested Limits
- Chat endpoint: 10 per minute
- Plan generation: 3 per hour
- API endpoints: 100 per hour"

gh issue create --repo $REPO \
  --title "Add Input Validation and Sanitization" \
  --label "security,bug,medium" \
  --milestone "$M1" \
  --body "## Description
Add comprehensive input validation for all user inputs.

## Areas Needing Validation
- LTHR: 100-220 range
- FTP: 50-500 range
- Sessions per week: 1-14
- Hours per week: 1-40"

gh issue create --repo $REPO \
  --title "Implement Session Security Best Practices" \
  --label "security,enhancement,small" \
  --milestone "$M1" \
  --body "## Description
Improve session management security to prevent session fixation and hijacking.

## Changes Needed
- Regenerate session ID on login
- Add secure cookie flags
- Implement session timeout mechanism"

gh issue create --repo $REPO \
  --title "Add Content Security Policy Headers" \
  --label "security,enhancement,medium" \
  --milestone "$M1" \
  --body "## Description
Implement Content Security Policy headers to prevent XSS attacks.

## Solution
Use Flask-Talisman for security headers including CSP"

gh issue create --repo $REPO \
  --title "Add Database Backup and Recovery Strategy" \
  --label "infrastructure,critical,medium" \
  --milestone "$M1" \
  --body "## Description
Implement automated backups and recovery procedures for DynamoDB.

## Proposed Solution
- Enable Point-in-Time Recovery on DynamoDB
- Set up automated daily backups via Lambda
- Document and test recovery procedures quarterly"

gh issue create --repo $REPO \
  --title "Add Terraform State Backend and Locking" \
  --label "infrastructure,critical,small" \
  --milestone "$M1" \
  --body "## Description
Configure S3 and DynamoDB backend for Terraform state management.

## Benefits
- Enable team collaboration on infrastructure
- Prevent concurrent modification issues
- Add state versioning and history"

# ============================================
# CODE QUALITY
# ============================================

echo "Creating code quality issues..."

gh issue create --repo $REPO \
  --title "Implement Structured Logging" \
  --label "bug,enhancement,medium" \
  --milestone "$M2" \
  --body "## Description
Replace print statements with proper structured logging.

## Benefits
- Filter logs by severity level
- Better debugging in production
- CloudWatch integration for monitoring"

gh issue create --repo $REPO \
  --title "Add Retry Logic with Exponential Backoff" \
  --label "enhancement,medium" \
  --milestone "$M2" \
  --body "## Description
Implement retry logic with exponential backoff for external API calls.

## Apply To
- Strava API calls
- Garmin API calls
- Gemini AI requests
- DynamoDB operations"

gh issue create --repo $REPO \
  --title "Refactor app.py into Modular Structure" \
  --label "refactor,large" \
  --milestone "$M2" \
  --body "## Description
Break down the 800+ line app.py into a modular structure.

## Proposed Structure
- routes/ folder for different route groups
- services/ for Strava, Garmin, AI logic
- utils/ for decorators and validators"

gh issue create --repo $REPO \
  --title "Add Comprehensive Unit Tests" \
  --label "testing,enhancement,large" \
  --milestone "$M2" \
  --body "## Description
Create comprehensive test suite with pytest covering critical application logic.

## Priority Test Areas
- Zone calculations (high priority)
- Activity analysis (high priority)
- Data manager CRUD operations (high priority)

## Target Coverage
Minimum 70% overall, 90% for critical business logic"

gh issue create --repo $REPO \
  --title "Add Integration Tests for Critical User Flows" \
  --label "testing,enhancement,medium" \
  --milestone "$M2" \
  --body "## Description
Create integration tests verifying end-to-end user journeys.

## Test Scenarios
- New user onboarding flow
- Activity feedback generation loop
- Chat interaction with plan modification"

gh issue create --repo $REPO \
  --title "Add Type Hints Throughout Codebase" \
  --label "enhancement,code-quality,medium" \
  --milestone "$M2" \
  --body "## Description
Add Python type hints to improve code clarity and catch type errors early.

## Tools
- Add mypy for static type checking
- Integrate into CI/CD pipeline"

gh issue create --repo $REPO \
  --title "Add Docstrings to All Functions" \
  --label "documentation,enhancement,medium" \
  --milestone "$M2" \
  --body "## Description
Add comprehensive docstrings following Google or NumPy style to all functions and classes.

## Priority Areas
- Complex logic functions (high)
- Public API functions (high)
- Helper functions (medium)"

gh issue create --repo $REPO \
  --title "Implement CI/CD Pipeline with GitHub Actions" \
  --label "infrastructure,enhancement,large" \
  --milestone "$M2" \
  --body "## Description
Automate testing, building, and deployment with GitHub Actions.

## Workflows Needed
- CI: Run tests and linting on every PR
- CD: Deploy to production on merge to main
- Scheduled: Weekly dependency updates and backups"

# ============================================
# PERFORMANCE
# ============================================

echo "Creating performance issues..."

gh issue create --repo $REPO \
  --title "Add Caching for Expensive Calculations" \
  --label "performance,enhancement,small" \
  --milestone "$M3" \
  --body "## Description
Cache expensive calculation results to improve response times.

## Implementation
- Use lru_cache for zone calculations
- Use Flask-Caching for API responses"

gh issue create --repo $REPO \
  --title "Optimize Activity Analysis with Parallel Processing" \
  --label "performance,enhancement,medium" \
  --milestone "$M3" \
  --body "## Description
Analyze multiple activities in parallel to reduce plan generation time.

## Expected Improvement
From 30+ seconds to 5-8 seconds for analyzing 20 activities"

gh issue create --repo $REPO \
  --title "Optimize DynamoDB Queries with Indexes" \
  --label "performance,infrastructure,small" \
  --milestone "$M3" \
  --body "## Description
Add appropriate indexes to DynamoDB for faster queries.

## Proposed Indexes
- Global Secondary Index on feedback date
- TTL policy on old feedback entries"

gh issue create --repo $REPO \
  --title "Implement Feature Flags System" \
  --label "enhancement,infrastructure,medium" \
  --milestone "$M3" \
  --body "## Description
Add feature flag system for gradual rollouts and A/B testing.

## Use Cases
- Dark launch new features safely
- A/B test different AI prompts
- Emergency kill switch for problematic features"

# ============================================
# FEATURES & UX
# ============================================

echo "Creating feature and UX issues..."

gh issue create --repo $REPO \
  --title "Add Training Progress Visualization" \
  --label "enhancement,ux,medium" \
  --milestone "$M4" \
  --body "## Description
Add visual indicators of training progress throughout the plan.

## Features
- Progress bar showing weeks completed vs total
- Weekly summary statistics comparison
- Trend charts using Chart.js"

gh issue create --repo $REPO \
  --title "Add Calendar View for Training Plan" \
  --label "enhancement,ux,medium" \
  --milestone "$M4" \
  --body "## Description
Create a calendar view to visualize the entire training plan at a glance.

## Implementation Options
- Use FullCalendar.js library
- Or custom implementation with Tailwind CSS"

gh issue create --repo $REPO \
  --title "Add Email Notifications for Key Sessions" \
  --label "enhancement,feature,large" \
  --milestone "$M4" \
  --body "## Description
Send email reminders for upcoming key sessions to keep users accountable.

## Notification Types
- Key session reminder (day before)
- Weekly plan summary (Sunday evening)
- Missed session nudge
- Milestone celebrations"

gh issue create --repo $REPO \
  --title "Add Zone Distribution Charts" \
  --label "enhancement,ux,medium" \
  --milestone "$M4" \
  --body "## Description
Visualize time spent in each training zone over time.

## Proposed Visualizations
- Pie chart for single activities
- Line chart showing weekly trends
- Bar chart comparing target vs actual zone time"

gh issue create --repo $REPO \
  --title "Add Progressive Web App (PWA) Support" \
  --label "enhancement,feature,large" \
  --milestone "$M4" \
  --body "## Description
Make kAIzen Coach installable as a Progressive Web App.

## Benefits
- Install on home screen
- Offline access to cached pages
- Native-like experience
- No app store approval needed"

gh issue create --repo $REPO \
  --title "Improve Mobile Responsiveness" \
  --label "enhancement,ux,medium" \
  --milestone "$M4" \
  --body "## Description
Optimize the UI for mobile devices to improve user experience.

## Improvements Needed
- Responsive navigation with hamburger menu
- Touch-friendly button sizes (minimum 44px)
- Horizontal scrolling for tables
- Optimized form inputs for mobile"

# ============================================
# OBSERVABILITY
# ============================================

echo "Creating observability issues..."

gh issue create --repo $REPO \
  --title "Add Health Check Endpoint" \
  --label "monitoring,enhancement,small" \
  --milestone "$M5" \
  --body "## Description
Create health check endpoint for monitoring and load balancer status checks.

## Checks
- Database connectivity test
- AI service availability
- Strava API reachability"

gh issue create --repo $REPO \
  --title "Set Up CloudWatch Dashboards and Alarms" \
  --label "monitoring,infrastructure,medium" \
  --milestone "$M5" \
  --body "## Description
Create comprehensive monitoring with CloudWatch dashboards and alarms.

## Key Metrics
- Request rate and error rate
- Response time percentiles
- Active users (daily/weekly)
- Plans generated per day"

gh issue create --repo $REPO \
  --title "Create API Documentation with Swagger" \
  --label "documentation,enhancement,medium" \
  --milestone "$M5" \
  --body "## Description
Document all API endpoints using OpenAPI/Swagger specification.

## Features
- Interactive API documentation
- Request/response examples
- Authentication flow documentation"

gh issue create --repo $REPO \
  --title "Create User Documentation and Help System" \
  --label "documentation,enhancement,large" \
  --milestone "$M5" \
  --body "## Description
Create comprehensive user documentation to help athletes get the most from kAIzen Coach.

## Sections Needed
- Getting started guide
- Features documentation
- Training philosophy explanations
- FAQ and troubleshooting"

gh issue create --repo $REPO \
  --title "Add Privacy-Focused Analytics" \
  --label "enhancement,analytics,medium" \
  --milestone "$M5" \
  --body "## Description
Implement privacy-focused analytics to understand user behavior.

## Events to Track
- User journey milestones
- Feature usage patterns
- Performance metrics
- Business KPIs (DAU, retention)"

echo ""
echo "================================================"
echo "✅ All issues created successfully!"
echo "================================================"
echo ""
echo "Summary:"
echo "- Labels: 17 created/verified"
echo "- Milestones: 5 created/verified"
echo "- Issues: 25 created"
echo ""
echo "View issues: https://github.com/$REPO/issues"
echo "View milestones: https://github.com/$REPO/milestones"
echo ""
echo "Next steps:"
echo "1. Review issues at the link above"
echo "2. Start with v1.1 Security & Stability"
echo "3. Set up GitHub Projects board for tracking"
echo ""