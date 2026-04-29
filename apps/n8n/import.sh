#!/usr/bin/env bash
# Import all workflow JSONs into n8n via the REST API.
# Usage: N8N_BASE_URL=http://localhost:5678 N8N_API_KEY=your-key ./apps/n8n/import.sh

set -euo pipefail

N8N_BASE_URL="${N8N_BASE_URL:-http://localhost:5678}"
N8N_API_KEY="${N8N_API_KEY:-}"
WORKFLOWS_DIR="$(dirname "$0")/workflows"

if [[ -z "$N8N_API_KEY" ]]; then
  echo "Error: N8N_API_KEY is not set." >&2
  exit 1
fi

echo "Importing workflows into $N8N_BASE_URL ..."

for file in "$WORKFLOWS_DIR"/*.json; do
  name=$(basename "$file" .json)

  # Check if workflow with this name already exists
  existing=$(curl -sf \
    -H "X-N8N-API-KEY: $N8N_API_KEY" \
    "$N8N_BASE_URL/api/v1/workflows" | \
    python3 -c "import sys,json; ws=json.load(sys.stdin)['data']; print(next((w['id'] for w in ws if w['name']=='$name'), ''))" 2>/dev/null || echo "")

  if [[ -n "$existing" ]]; then
    echo "  [$name] already exists (id=$existing) — skipping"
    continue
  fi

  response=$(curl -sf \
    -X POST \
    -H "X-N8N-API-KEY: $N8N_API_KEY" \
    -H "Content-Type: application/json" \
    -d @"$file" \
    "$N8N_BASE_URL/api/v1/workflows")

  id=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "unknown")
  echo "  [$name] imported (id=$id)"
done

echo "Done."
