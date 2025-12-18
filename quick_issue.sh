#!/bin/bash
# Quick GitHub Issue Creator - Interactive
# Usage: ./quick_issue.sh

REPO="smurphin/my-personal-coach"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}   Quick GitHub Issue Creator${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo ""

# Prompt for title
echo -e "${GREEN}Title:${NC}"
read -p "> " TITLE

if [ -z "$TITLE" ]; then
    echo "Error: Title is required"
    exit 1
fi

# Prompt for description
echo ""
echo -e "${GREEN}Description (press Enter twice when done):${NC}"
DESCRIPTION=""
while IFS= read -r line; do
    [ -z "$line" ] && break
    DESCRIPTION+="$line"$'\n'
done

# Prompt for issue type
echo ""
echo -e "${GREEN}Type:${NC}"
echo "  1) Bug"
echo "  2) Feature"
echo "  3) Enhancement"
echo "  4) Question"
read -p "> " TYPE_CHOICE

case $TYPE_CHOICE in
    1) LABEL="bug" ;;
    2) LABEL="feature" ;;
    3) LABEL="enhancement" ;;
    4) LABEL="question" ;;
    *) LABEL="bug" ;;
esac

# Prompt for priority
echo ""
echo -e "${GREEN}Priority (optional):${NC}"
echo "  1) Critical"
echo "  2) High"
echo "  3) Medium (default)"
echo "  4) Low"
echo "  5) Skip"
read -p "> " PRIORITY_CHOICE

case $PRIORITY_CHOICE in
    1) PRIORITY_LABEL="critical" ;;
    2) PRIORITY_LABEL="high-priority" ;;
    3) PRIORITY_LABEL="medium-priority" ;;  
    4) PRIORITY_LABEL="low-priority" ;;
    5) PRIORITY_LABEL="" ;;
    *) PRIORITY_LABEL="" ;;
esac

# Prompt for size estimate
echo ""
echo -e "${GREEN}Size estimate (optional):${NC}"
echo "  1) Small (< 1 hour)"
echo "  2) Medium (1-4 hours)"
echo "  3) Large (> 4 hours)"
echo "  4) Skip"
read -p "> " SIZE_CHOICE

case $SIZE_CHOICE in
    1) SIZE_LABEL="small" ;;
    2) SIZE_LABEL="medium" ;;
    3) SIZE_LABEL="large" ;;
    4) SIZE_LABEL="break" ;;
    *) SIZE_LABEL="" ;;
esac

# Build labels string
LABELS="$LABEL"
[ -n "$PRIORITY_LABEL" ] && LABELS="$LABELS,$PRIORITY_LABEL"
[ -n "$SIZE_LABEL" ] && LABELS="$LABELS,$SIZE_LABEL"

# Show summary
echo ""
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${YELLOW}Summary:${NC}"
echo -e "  Title: $TITLE"
echo -e "  Type: $LABEL"
[ -n "$PRIORITY_LABEL" ] && echo -e "  Priority: $PRIORITY_LABEL"
[ -n "$SIZE_LABEL" ] && echo -e "  Size: $SIZE_LABEL"
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo ""

# Confirm
read -p "Create this issue? (y/n) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

# Create the issue
echo ""
echo "Creating issue..."

gh issue create \
    --repo "$REPO" \
    --title "$TITLE" \
    --body "$DESCRIPTION" \
    --label "$LABELS"

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✅ Issue created successfully!${NC}"
    echo "View at: https://github.com/$REPO/issues"
else
    echo ""
    echo "❌ Failed to create issue"
    #exit 1
fi