# Versioning & Deployment Guide

This guide describes the versioning strategy for my-personal-coach, enabling:

<!-- toc -->

  * [Quick Reference](#quick-reference)
  * [Versioning Strategy](#versioning-strategy)
    + [Single Source of Truth: VERSION file](#single-source-of-truth-version-file)
    + [Docker Image Tags](#docker-image-tags)
  * [Changelog](#changelog)
  * [Recommended Deployment Flow](#recommended-deployment-flow)
  * [Single-Tenant Deployments (Beta Hotfixes)](#single-tenant-deployments-beta-hotfixes)
  * [Verifying Deployed Version](#verifying-deployed-version)
  * [Future: Multi-User / Strava Onboarding](#future-multi-user--strava-onboarding)

<!-- tocstop -->

- **Traceability**: Know exactly what version is deployed in ECR and App Runner
- **Rollbacks**: Switch to a previous version quickly if needed
- **Controlled releases**: Promote specific versions across staging → prod → beta

## Quick Reference

| Action | Command |
|--------|---------|
| Deploy to staging | `./scripts/deploy.sh staging` |
| Deploy to prod | `./scripts/deploy.sh prod` |
| Deploy to all beta | `./scripts/deploy.sh beta` |
| Deploy to single tenant | `./scripts/deploy.sh mark` or `shane` or `dom` |
| Verify deployed version | `curl https://staging.kaizencoach.training/version` |
| List ECR images | `aws ecr describe-images --repository-name staging-kaizencoach-app --region eu-west-1` |

## Versioning Strategy

### Single Source of Truth: `VERSION` file

- **Major.Feature.Patch** (e.g. 0.1.0)
- **v0.x.x**: Beta (pre-Strava onboarded)
- **v1.x.x**: Production (Strava onboarded)
- Bump before deploy:
  - **PATCH** (0.1.0 → 0.1.1): Bug fixes, minor tweaks
  - **FEATURE** (0.1.1 → 0.2.0): New features, non-breaking
  - **MAJOR** (0.2.0 → 1.0.0): Strava onboarded; breaking changes

### Docker Image Tags

Images are tagged with **both**:

- `:vX.Y.Z` — immutable, for rollbacks and traceability (example: `:v1.2.3`)
- `:latest` — convenience for "current" (mutable)

> **Note:** Any specific tags like `v1.2.3` in this document are **examples only**. The **actual** version for the app is always taken from the `VERSION` file at the repo root and mirrored in `CHANGELOG.md`.

App Runner pulls `:latest` from ECR. Versioned tags (`:vX.Y.Z`) are for ECR traceability and the `/version` endpoint.

**Runtime config:** AI model, temperature, webhook delay etc. are configurable per environment via Secrets Manager—no code deploy needed. See `docs/secrets-guide.md` §9.

## Changelog

`CHANGELOG.md` at the repo root records what changed in each release. It follows [Keep a Changelog](https://keepachangelog.com/) and is the source for "What's new" / release notes (e.g. in a help section).

**Workflow:** When you bump `VERSION`, add a matching section in `CHANGELOG.md`:
1. Move items from `[Unreleased]` into a new `[X.Y.Z]` section with the release date
2. Add new entries under `[Unreleased]` as you work

**Sections:** `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`—use what fits.

Git history (commits, PRs) remains the full technical record; the changelog is a curated summary for users and future you.

## Recommended Deployment Flow

1. **Develop** on a feature branch, test locally
2. **Merge** to main (or your release branch)
3. **Bump** `VERSION` file
4. **Update** `CHANGELOG.md` (move Unreleased items to new version section, add date)
5. **Deploy to staging**:
   ```bash
   ./scripts/deploy.sh staging
   aws apprunner start-deployment --service-arn <staging-arn> --region eu-west-1
   ```
6. **Smoke test** staging (chat, plan, feedback, etc.)
7. **Deploy to prod**:
   ```bash
   ./scripts/deploy.sh prod
   aws apprunner start-deployment --service-arn <prod-arn> --region eu-west-1
   ```
8. **Deploy to beta** (mark, shane, dom) when stable
9. **Tag in Git** for release tracking: `git tag v0.1.0 && git push origin v0.1.0`

## Single-Tenant Deployments (Beta Hotfixes)

You can deploy to one beta tester without touching the others—useful when fixing an issue reported by a specific user:

```bash
# Deploy only to Mark's tenant
./scripts/deploy.sh mark
aws apprunner start-deployment --service-arn <mark-service-arn> --region eu-west-1

# Or with explicit version
./scripts/deploy.sh mark v0.1.1
aws apprunner start-deployment --service-arn <mark-service-arn> --region eu-west-1
```

Same for `shane` or `dom`. Bump the VERSION file first if the fix warrants a new patch.

## Verifying Deployed Version

1. **HTTP endpoint** (no auth):
   ```bash
   curl https://staging.kaizencoach.training/version
   # {"version":"v1.2.3","environment":"staging"}
   ```

2. **ECR** — list images in a repo:
   ```bash
   aws ecr describe-images --repository-name staging-kaizencoach-app --region eu-west-1 --query 'imageDetails[*].[imageTags[0],imagePushedAt]' --output table
   ```

3. **App Runner** — in AWS Console: Service → Configuration → Source (shows `:latest`)

## Future: Multi-User / Strava Onboarding

When you move to staging + prod only:

- Same versioning applies
- Consider GitHub Actions to automate: merge to main → bump → build → push → deploy staging
- Add a manual approval gate before prod

## Documentation Maintenance Checklist

When you change anything related to deployments, environments, or AI configuration, also:

- Update the `VERSION` file and `CHANGELOG.md` as needed.
- Reflect any changes to deployment flow in `docs/versioning.md` and, if relevant, `deploy.txt`.
- Adjust environment/bootstrap details in `docs/multi_env_setup.md` or `docs/new_env_deployment.md` if Terraform, DNS, or secrets flows change.
- Keep `docs/secrets-guide.md` in sync with how secrets are actually loaded (see `config.py` and `infra/secrets.tf`).
