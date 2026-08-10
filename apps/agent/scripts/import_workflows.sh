#!/usr/bin/env bash
# Import n8n workflow definitions into a running n8n instance.
# Usage: ./scripts/import_workflows.sh [N8N_URL] [N8N_API_KEY]
#
# Defaults to the local Docker stack values.
# Run AFTER "make dev" once n8n is healthy.

set -euo pipefail

N8N_URL="${1:-http://localhost:5678}"
N8N_API_KEY="${2:-${N8N_API_KEY:-}}"
WORKFLOWS_DIR="$(cd "$(dirname "$0")/../../.." && pwd)/infra/n8n/workflows"

# ── helpers ──────────────────────────────────────────────────────────────────

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

require_cmd() { command -v "$1" &>/dev/null || { red "ERROR: '$1' not found — install it first"; exit 1; }; }
require_cmd curl
require_cmd jq

# ── wait for n8n ─────────────────────────────────────────────────────────────

blue "Waiting for n8n at ${N8N_URL}…"
for i in $(seq 1 30); do
  if curl -sf "${N8N_URL}/healthz" &>/dev/null; then
    break
  fi
  sleep 2
  if [[ $i -eq 30 ]]; then
    red "ERROR: n8n did not become ready after 60 s"
    exit 1
  fi
done
green "n8n is ready"

# ── resolve API key ───────────────────────────────────────────────────────────

if [[ -z "${N8N_API_KEY}" ]]; then
  echo ""
  echo "No N8N_API_KEY provided. To create one:"
  echo "  1. Open ${N8N_URL} in your browser"
  echo "  2. Log in (admin / admin)"
  echo "  3. Go to Settings → API → Create an API Key"
  echo "  4. Re-run:  N8N_API_KEY=<key> ./scripts/import_workflows.sh"
  echo ""
  exit 1
fi

AUTH_HEADER="X-N8N-API-KEY: ${N8N_API_KEY}"

# ── fetch existing workflow names ─────────────────────────────────────────────

existing=$(curl -sf "${N8N_URL}/api/v1/workflows" \
  -H "${AUTH_HEADER}" | jq -r '.data[].name' 2>/dev/null || echo "")

# ── import each workflow file ─────────────────────────────────────────────────

imported=0
skipped=0

for file in "${WORKFLOWS_DIR}"/*.json; do
  name=$(jq -r '.name' "$file")

  if echo "${existing}" | grep -qx "${name}"; then
    echo "  skip  ${name} (already exists)"
    skipped=$((skipped + 1))
    continue
  fi

  # n8n rejects read-only fields on create. `active` is one of them — it is set
  # through /activate below, not in the body.
  payload=$(jq 'del(.active, .tags, .id)' "$file")

  response=$(curl -sf -X POST "${N8N_URL}/api/v1/workflows" \
    -H "${AUTH_HEADER}" \
    -H "Content-Type: application/json" \
    -d "${payload}")

  workflow_id=$(echo "${response}" | jq -r '.id // empty')
  if [[ -z "${workflow_id}" ]]; then
    red "  ERROR importing ${name}: ${response}"
    continue
  fi

  green "  import ${name} → id=${workflow_id}"
  imported=$((imported + 1))

  # An imported-but-inactive schedule trigger never fires, which looks exactly
  # like a working install until the first report fails to arrive.
  if curl -sf -X POST "${N8N_URL}/api/v1/workflows/${workflow_id}/activate" \
      -H "${AUTH_HEADER}" >/dev/null; then
    green "  active ${name}"
  else
    red "  WARN  ${name} imported but could not be activated — enable it in the n8n UI"
  fi
done

echo ""
green "Done — imported ${imported}, skipped ${skipped}"
echo ""
echo "Next: set N8N_API_KEY=${N8N_API_KEY} in apps/agent/.env so the agent can trigger workflows."
