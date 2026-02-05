#!/bin/bash
# Create GitHub issue: Review & refine plan archive / admin access for v1.0.0 (full public production)

REPO="smurphin/my-personal-coach"
TITLE="v1.0.0: Review and refine plan archive & admin access for full public production"
LABELS="enhancement"

BODY=$(cat <<'EOF'
## Context

Before full public production release (v1.0.0, once onboarded to Strava), review and refine the plan archive and admin-access behaviour added during the truncation/rollback work.

## Current behaviour (as of fix/plan-archive-investigation)

- **Dashboard safeguards:** Chat (JSON and markdown) and reparse now archive the current plan before applying updates, so the previous good state is never lost to overwrite.
- **Plan archive UI:** `/admin/plan_archive` lists snapshots and allows restore for the logged-in user. Visibility is gated by `ADMIN_ATHLETE_IDS` (if set, only those athlete IDs see the link).
- **Plan archive API:** `GET /admin/api/plan_archive` and `POST /admin/api/restore_plan_archive` allow listing/restoring any tenant by `athlete_id`, protected by `FEEDBACK_TRIGGER_SECRET`.

## Review / refinement for v1.0.0

- [ ] **Access model:** Confirm whether plan archive should remain admin-only (UI + API secret) or be exposed in a limited way to end users (e.g. “Restore previous plan” in settings).
- [ ] **ADMIN_ATHLETE_IDS:** Document and enforce for production; ensure all production envs set it so only designated admins see plan archive UI.
- [ ] **API vs UI:** Consider a simple admin “impersonation” or tenant switcher for support (or keep API-only for other tenants).
- [ ] **Audit / logging:** Consider logging plan restores (who, when, athlete_id, index) for support and safety.
- [ ] **Retention:** Review how many plan snapshots to keep and S3 vs DynamoDB retention for archive entries.

**Priority:** Before v1.0.0 release  
**Estimate:** Small (review + doc + optional logging)
EOF
)

echo "Creating issue in $REPO..."
gh issue create \
    --repo "$REPO" \
    --title "$TITLE" \
    --body "$BODY" \
    --label "$LABELS"

if [ $? -eq 0 ]; then
    echo "✅ Issue created successfully!"
else
    echo "❌ Failed to create issue (check 'gh auth' and repo)"
    exit 1
fi
