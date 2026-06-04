#!/usr/bin/env bash
set -euo pipefail

if ! command -v fly >/dev/null 2>&1; then
  echo "fly CLI is not installed or not on PATH." >&2
  exit 1
fi

secret_names="$(fly secrets list --config fly.toml | awk 'NR > 1 {print $1}')"
missing=()

for name in GOOGLE_API_KEY PHOENIX_API_KEY PHOENIX_COLLECTOR_ENDPOINT ALLOWED_ORIGINS; do
  if ! printf '%s\n' "$secret_names" | grep -qx "$name"; then
    missing+=("$name")
  fi
done

if ! printf '%s\n' "$secret_names" | grep -Eq '^(SERPAPI_API_KEY|SERPAPI_KEY)$'; then
  missing+=("SERPAPI_API_KEY")
fi

if [ "${#missing[@]}" -gt 0 ]; then
  echo "Missing Fly secrets: ${missing[*]}" >&2
  echo "Set them with: fly secrets set NAME=value --config fly.toml" >&2
  exit 1
fi

fly deploy --config fly.toml "$@"
