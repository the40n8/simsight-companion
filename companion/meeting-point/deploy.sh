#!/bin/zsh
# Deploy the meeting point on your own domain, with its workers.dev address
# turned off (so protections set on the domain cannot be skipped):
#   ./deploy.sh simsight-app.example.com
# Without a domain, plain `npx wrangler deploy` uses the workers.dev address.
# Build the phone app first (mac/build.sh): the meeting point serves it.
set -euo pipefail
HERE="${0:A:h}"
DOMAIN="${1:?usage: ./deploy.sh <domain>}"
[[ -f "$HERE/../web/build/index.html" ]] || { echo "Run mac/build.sh first."; exit 1; }
CFG="$HERE/.deploy.jsonc"
trap 'rm -f "$CFG"' EXIT
# The shared config, plus this deployment's address.
sed "s#\"name\": \"simsight\",#\"name\": \"simsight\",\n  \"workers_dev\": false,\n  \"routes\": [{ \"pattern\": \"$DOMAIN\", \"custom_domain\": true }],#" \
  "$HERE/wrangler.jsonc" > "$CFG"
grep -q '"workers_dev": false' "$CFG"
cd "$HERE" && npx wrangler deploy --config "$CFG"
