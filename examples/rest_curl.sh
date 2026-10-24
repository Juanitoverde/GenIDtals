#!/usr/bin/env bash
# REST API from the shell. Any language with HTTP + JSON + base64 works the same way.
#
#   export GENIDTALS_API_KEYS="$(genidtals keygen --api-key)"
#   genidtals serve &                         # listens on 127.0.0.1:8080
#   genidtals synth --identity 1 --count 3 --out samples
#   GENIDTALS_API_KEY="$GENIDTALS_API_KEYS" ./examples/rest_curl.sh
set -euo pipefail
URL="${GENIDTALS_URL:-http://127.0.0.1:8080}"
AUTH="Authorization: Bearer ${GENIDTALS_API_KEY:?set GENIDTALS_API_KEY}"
b64() { base64 < "$1" | tr -d '\n'; }
# Bodies go through stdin: base64 images exceed the command-line length limit.
post() { curl -sf -X POST "$URL$1" -H "$AUTH" -H 'Content-Type: application/json' --data-binary @-; echo; }

printf '{"images": ["%s", "%s"], "overwrite": true}' \
  "$(b64 samples/specimen1_1.png)" "$(b64 samples/specimen1_2.png)" | post /v1/users/alice/enroll

printf '{"image": "%s"}' "$(b64 samples/specimen1_3.png)" | post /v1/users/alice/verify
