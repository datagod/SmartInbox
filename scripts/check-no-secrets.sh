#!/usr/bin/env bash
# Fail if sensitive files are staged for commit.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BLOCKED=(
  .env
  config.yaml
  credentials.json
  token.json
)

STAGED=$(git diff --cached --name-only 2>/dev/null || true)
FAIL=0

for path in $STAGED; do
  base=$(basename "$path")
  for blocked in "${BLOCKED[@]}"; do
    if [[ "$path" == "$blocked" || "$base" == "$blocked" ]]; then
      echo "ERROR: Refusing to commit sensitive file: $path" >&2
      FAIL=1
    fi
  done
  if [[ "$path" == data/* || "$path" == localrecordings/* ]]; then
    echo "ERROR: Refusing to commit runtime data: $path" >&2
    FAIL=1
  fi
  if [[ "$path" == .env.* && "$path" != .env.example ]]; then
    echo "ERROR: Refusing to commit env file: $path" >&2
    FAIL=1
  fi
done

if git diff --cached -G 'GOCSPX-[a-zA-Z0-9_-]{8,}' --quiet 2>/dev/null; then
  :
else
  echo "ERROR: Staged diff may contain a Google client secret (GOCSPX-...)" >&2
  FAIL=1
fi

exit $FAIL