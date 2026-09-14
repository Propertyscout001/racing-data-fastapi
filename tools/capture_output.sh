#!/usr/bin/env bash
# Exercise a running instance and print what it actually returns.
# Usage:  tools/capture_output.sh [base_url]
# The output of this script is what lives in docs/output.txt.
set -u
BASE="${1:-http://127.0.0.1:8000}"

say() { printf '\n$ %s\n' "$*"; }
jqp() { python3 -m json.tool 2>/dev/null || cat; }

say "curl -s $BASE/health"
curl -s "$BASE/health" | jqp

say "curl -s '$BASE/races/next?country=AU&limit=2'"
curl -s "$BASE/races/next?country=AU&limit=2" | jqp

say "curl -s '$BASE/races/next?country=AU&limit=2'   # second call, same 20s window"
curl -s "$BASE/races/next?country=AU&limit=2" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("source", d), indent=2))'

say "curl -s $BASE/best-odds/afl"
curl -s "$BASE/best-odds/afl" | jqp

say "curl -i -s $BASE/best-odds/horse-racing   # refused locally, costs nothing"
curl -i -s "$BASE/best-odds/horse-racing" | sed -n '1p;/^{/p'

say "curl -i -s $BASE/races/does-not-exist/prices"
curl -i -s "$BASE/races/does-not-exist/prices" | sed -n '1p;/^{/p'
