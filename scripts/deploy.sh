#!/usr/bin/env bash
#
# Versioned deployment script for my-personal-coach
# Builds and pushes Docker images with semantic version tags.
# Supports deploying to staging, prod, or beta (mark/shane/dom).
#
# Usage:
#   ./scripts/deploy.sh <target> [version]
#
# Targets: staging | prod | beta | mark | shane | dom | all
# Version: optional, defaults to VERSION file (e.g. v0.1.0)
#
# Examples:
#   ./scripts/deploy.sh staging              # Deploy VERSION file to staging
#   ./scripts/deploy.sh mark v0.1.1          # Deploy v0.1.1 to Mark only (e.g. hotfix)
#   ./scripts/deploy.sh beta v0.1.2          # Deploy to mark, shane, dom
#   ./scripts/deploy.sh all v0.1.2           # Deploy to all ECR repos
#
# After pushing, trigger App Runner deployment:
#   aws apprunner start-deployment --service-arn <ARN> --region eu-west-1

set -e

ECR_REGISTRY="321490400104.dkr.ecr.eu-west-1.amazonaws.com"
REGION="eu-west-1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ECR repo names per target
declare -A REPOS
REPOS[staging]="staging-kaizencoach-app"
REPOS[prod]="my-personal-coach-app"
REPOS[mark]="mark-kaizencoach-app"
REPOS[shane]="shane-kaizencoach-app"
REPOS[dom]="dom-kaizencoach-app"

usage() {
    echo "Usage: $0 <target> [version]"
    echo ""
    echo "Targets: staging | prod | beta | mark | shane | dom | all"
    echo "  staging  - staging-kaizencoach-app"
    echo "  prod     - my-personal-coach-app"
    echo "  beta     - mark, shane, dom repos"
    echo "  mark     - mark-kaizencoach-app (single tenant)"
    echo "  shane    - shane-kaizencoach-app (single tenant)"
    echo "  dom      - dom-kaizencoach-app (single tenant)"
    echo "  all      - all of the above"
    echo ""
    echo "Version: vX.Y.Z (default: from VERSION file)"
    exit 1
}

# Resolve version: arg > VERSION file > fallback
get_version() {
    if [[ -n "$1" ]]; then
        # Ensure v prefix
        [[ "$1" == v* ]] && echo "$1" || echo "v$1"
    elif [[ -f "$PROJECT_ROOT/VERSION" ]]; then
        local v
        v=$(trim "$(cat "$PROJECT_ROOT/VERSION")")
        [[ -n "$v" ]] && { [[ "$v" == v* ]] && echo "$v" || echo "v$v"; } || echo "v0.0.0"
    else
        echo "v0.0.0"
    fi
}

trim() {
    local var="$*"
    var="${var#"${var%%[![:space:]]*}"}"
    var="${var%"${var##*[![:space:]]}"}"
    echo "$var"
}

if [[ $# -lt 1 ]]; then
    usage
fi

TARGET="$1"
VERSION_ARG="${2:-}"
VERSION=$(get_version "$VERSION_ARG")

# Resolve repo list
REPO_LIST=()
case "$TARGET" in
    staging) REPO_LIST=("${REPOS[staging]}") ;;
    prod)    REPO_LIST=("${REPOS[prod]}") ;;
    beta)    REPO_LIST=("${REPOS[mark]}" "${REPOS[shane]}" "${REPOS[dom]}") ;;
    mark)    REPO_LIST=("${REPOS[mark]}") ;;
    shane)   REPO_LIST=("${REPOS[shane]}") ;;
    dom)     REPO_LIST=("${REPOS[dom]}") ;;
    all)     REPO_LIST=("${REPOS[staging]}" "${REPOS[prod]}" "${REPOS[mark]}" "${REPOS[shane]}" "${REPOS[dom]}") ;;
    *)       echo "Unknown target: $TARGET"; usage ;;
esac

echo "=============================================="
echo "Deploying version: $VERSION to: $TARGET"
echo "Repos: ${REPO_LIST[*]}"
echo "=============================================="

# ECR login
aws ecr get-login-password --region "$REGION" | sudo docker login --username AWS --password-stdin "$ECR_REGISTRY"

cd "$PROJECT_ROOT"

# Build with version injected
echo ""
echo "Building image with VERSION=$VERSION..."
sudo docker build \
    --build-arg VERSION="$VERSION" \
    -t "${ECR_REGISTRY}/placeholder:${VERSION}" \
    -t "${ECR_REGISTRY}/placeholder:latest" \
    .

# Push to each repo
for REPO in "${REPO_LIST[@]}"; do
    echo ""
    echo "Pushing to $REPO..."
    sudo docker tag "${ECR_REGISTRY}/placeholder:${VERSION}" "${ECR_REGISTRY}/${REPO}:${VERSION}"
    sudo docker tag "${ECR_REGISTRY}/placeholder:latest" "${ECR_REGISTRY}/${REPO}:latest"
    sudo docker push "${ECR_REGISTRY}/${REPO}:${VERSION}"
    sudo docker push "${ECR_REGISTRY}/${REPO}:latest"
    echo "  ✓ ${REPO}:${VERSION} and :latest pushed"
done

echo ""
echo "=============================================="
echo "Done. Trigger App Runner deployment (pulls new :latest):"
echo "  aws apprunner start-deployment --service-arn <ARN> --region eu-west-1"
echo ""
echo "Verify deployed version: curl https://<your-domain>/version"
echo "=============================================="
