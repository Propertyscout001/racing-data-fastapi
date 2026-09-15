#!/usr/bin/env bash
# Regenerate docs/output.txt IN FULL: keyless mode, keyed mode, the concurrency
# demo, the error-passthrough table and the Docker run.
#
#   tools/capture_output.sh > docs/output.txt
#
# It starts and stops every server it needs, so nothing has to be running first.
# What it captures depends on what is available:
#
#   PART 1  keyless demo mode          always
#   PART 2  keyed mode                 only when PE_API_KEY is set
#   PART 3  error passthrough          always (tools/stub_upstream.py, no account)
#   PART 4  the container              only when a Docker daemon answers
#
# A section that cannot be captured says so in the file rather than being
# silently omitted -- the previous version of this script emitted six of the
# twenty blocks in docs/output.txt and claimed to regenerate all of them, which
# meant running it truncated the repo's own evidence.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
PORT="${PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
PY="${PYTHON:-python3}"
[ -x "$ROOT/.venv/bin/python3" ] && PY="$ROOT/.venv/bin/python3"

say()  { printf '\n$ %s\n' "$*"; }
jqp()  { "$PY" -m json.tool 2>/dev/null || cat; }
rule() { printf '===============================================================\n'; }
banner() { printf '\n'; rule; printf ' %s\n' "$@"; rule; }

# cap N -- print at most N lines, then say how many were dropped. Keeps the
# file readable without pretending the elided lines were never there.
cap() {
  awk -v n="$1" 'NR<=n{print} END{ if (NR>n) printf "[... elided: %d more lines of this response ...]\n", NR-n }'
}

SERVER_PID=""
STUB_PID=""
cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  [ -n "$STUB_PID" ]   && kill "$STUB_PID" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT

# start_server <logfile> [VAR=VAL ...] -- waits until /health answers
start_server() {
  local logfile="$1"; shift
  env "$@" "$PY" -m uvicorn app.main:app --port "$PORT" >"$logfile" 2>&1 &
  SERVER_PID=$!
  # Poll a LOCAL route. Polling /health here would probe the upstream on every
  # attempt, burn the keyless tier's 30 requests/minute, and leave the first
  # /health in the capture reporting a cached probe it did not take.
  for _ in $(seq 1 60); do
    curl -s -o /dev/null "$BASE/openapi.json" && return 0
    sleep 0.5
  done
  echo "server did not come up; see $logfile" >&2
  return 1
}
stop_server() { [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null; SERVER_PID=""; }

LOGDIR="$(mktemp -d)"

# Trimmer scripts live in files, NOT in `curl ... | python3 - <<EOF`: a heredoc
# and a pipe both want stdin, the heredoc wins, and python reads the script
# where the JSON should have been. That silently produced two empty blocks in
# an earlier version of this capture.
cat > "$LOGDIR/trim_race.py" <<'PYEOF'
import json, sys
d = json.load(sys.stdin)
races = d.get("races") or []
if not races:
    print("no races in this response"); sys.exit(0)
r = dict(races[0])
runners = r.get("runners") or []
kept = runners[:1]
r["runners"] = kept
print(json.dumps({"source": d["source"], "count": d["count"], "races": [r]}, indent=2))
print(f"\n[... elided: {len(runners) - len(kept)} more runners in this race ...]")
if kept:
    print(f"[ the runner above is shown COMPLETE: "
          f"{len(kept[0].get('prices') or [])} bookmakers, nothing removed ]")
counts = sorted({len(x.get("prices") or []) for x in runners})
print(f"[ this race, this capture: {len(runners)} runners; "
      f"bookmakers per runner: {counts} ]")
PYEOF

cat > "$LOGDIR/trim_history.py" <<'PYEOF'
import json, sys
d = json.load(sys.stdin)
runners = d.get("runners") or []
d["runners"] = runners[:2]
print(json.dumps(d, indent=2))
print(f"\n[... elided: {max(0, len(runners) - 2)} more runners ...]")
PYEOF

cat > "$LOGDIR/first_race_id.py" <<'PYEOF'
import json, sys
rs = json.load(sys.stdin).get("races") or []
print(rs[0]["race_id"] if rs else "")
PYEOF

# --------------------------------------------------------------- exercise ----
# The request sequence that PART 1 and PART 2 both run.
exercise() {
  say "curl -s $BASE/health"
  curl -s "$BASE/health" | jqp

  say "curl -s '$BASE/races/next?country=AU&limit=2'"
  curl -s "$BASE/races/next?country=AU&limit=2" | jqp | cap 120

  say "curl -s '$BASE/races/next?country=AU&limit=2'   # second call, same 20s window"
  curl -s "$BASE/races/next?country=AU&limit=2" \
    | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("source", d), indent=2))'

  local rid
  rid="$(curl -s "$BASE/races/next?country=AU&limit=1" | "$PY" "$LOGDIR/first_race_id.py")"
  if [ -n "$rid" ]; then
    say "curl -s $BASE/races/$rid/prices   # out of the snapshot already paid for"
    curl -s "$BASE/races/$rid/prices" \
      | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["source"], indent=2))'
  fi

  say "curl -s $BASE/best-odds/afl"
  curl -s "$BASE/best-odds/afl" | jqp | cap 60

  say "curl -i -s $BASE/best-odds/horse-racing   # refused locally, with the valid keys"
  curl -i -s "$BASE/best-odds/horse-racing" | sed -n '1p;/^{/p'

  say "curl -i -s $BASE/races/does-not-exist/prices"
  curl -i -s "$BASE/races/does-not-exist/prices" | sed -n '1p;/^{/p'
}

# ------------------------------------------------------------------ header ---
printf 'racing-data-fastapi -- real captured output\n'
printf 'Generated by tools/capture_output.sh (this whole file, top to bottom)\n'
printf 'Captured: %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ UTC')"
printf 'Host: %s, %s, uvicorn 127.0.0.1:%s, upstream %s\n' \
  "$(uname -sr)" "$("$PY" --version 2>&1)" "$PORT" "$(grep -o 'https://[^"]*' <<<"${PE_UPSTREAM_BASE:-https://api.puntersedge.online/v1}")"

# ------------------------------------------------------------------ PART 1 ---
banner "PART 1  KEYLESS DEMO MODE -- no PE_API_KEY, zero credits"
say "uvicorn app.main:app --port $PORT"
start_server "$LOGDIR/keyless.log" PE_API_KEY= PE_ENV_FILE=/nonexistent || exit 1
grep -E 'mode=|no PE_API_KEY|Application startup|Uvicorn running' "$LOGDIR/keyless.log"
exercise

say "python3 tools/demo_single_flight.py 25   # keyless: one shared cache key"
"$PY" tools/demo_single_flight.py 25 "$BASE"

printf '\n--- what the upstream actually saw for the whole of PART 1 ---\n'
grep -o 'HTTP Request: GET https://[^ ]* "[^"]*"' "$LOGDIR/keyless.log" | sort | uniq -c | sed 's/^ */  /'
stop_server

# ------------------------------------------------------------------ PART 2 ---
banner "PART 2  KEYED MODE -- PE_API_KEY set, real credits spent"
if [ -z "${PE_API_KEY:-}" ]; then
  printf '\nSKIPPED: PE_API_KEY is not set in this environment.\n'
  printf 'Free key: https://puntersedge.online/api?utm_source=racing-data-fastapi&utm_medium=code\n'
else
  say "PE_API_KEY=... uvicorn app.main:app --port $PORT"
  start_server "$LOGDIR/keyed.log" "PE_API_KEY=$PE_API_KEY" PE_ENV_FILE=/nonexistent || exit 1
  grep -E 'mode=|Application startup' "$LOGDIR/keyed.log"
  exercise

  say "curl -s '$BASE/races/next?country=AU&limit=1'   # ONE full runner, every book it carries"
  curl -s "$BASE/races/next?country=AU&limit=1" | "$PY" "$LOGDIR/trim_race.py"

  say "python3 tools/demo_single_flight.py 25   # keyed: a cache key nothing has asked for"
  "$PY" tools/demo_single_flight.py 25 "$BASE"

  say "curl -s '$BASE/races/<a race that has already run>/history'"
  HIST_ID="${PE_HISTORY_RACE_ID:-}"
  if [ -n "$HIST_ID" ]; then
    curl -s "$BASE/races/$HIST_ID/history" | "$PY" "$LOGDIR/trim_history.py"
  else
    printf 'set PE_HISTORY_RACE_ID to a race that has already run to capture this block\n'
  fi

  say "curl -i -s '$BASE/races/<a race that has NOT jumped>/history'"
  NOTRUN="$(curl -s "$BASE/races/next?country=AU&limit=1" | "$PY" "$LOGDIR/first_race_id.py")"
  [ -n "$NOTRUN" ] && curl -i -s "$BASE/races/$NOTRUN/history" | sed -n '1p;/^{/p'

  say "curl -s $BASE/health   # cache accounting after the above"
  curl -s "$BASE/health" | "$PY" -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["cache"], indent=2))'
  stop_server
fi

# ------------------------------------------------------------------ PART 3 ---
banner "PART 3  ERROR PASSTHROUGH -- tools/stub_upstream.py" \
       "402 and 429 are both 'upstream said no'. Only one is worth retrying."
for code in 402 429 401 503; do
  printf '\n--- upstream returns %s -------------------------------------\n' "$code"
  "$PY" tools/stub_upstream.py "$code" >"$LOGDIR/stub.log" 2>&1 &
  STUB_PID=$!
  sleep 1
  start_server "$LOGDIR/stub-server-$code.log" \
    PE_API_KEY=not-a-real-key PE_ENV_FILE=/nonexistent \
    PE_UPSTREAM_BASE=http://127.0.0.1:8099/v1 >/dev/null 2>&1
  say "curl -i -s '$BASE/races/next?limit=2'"
  curl -i -s "$BASE/races/next?limit=2" | sed -n '1p;/^retry-after/Ip;/^content-type/Ip;/^{/p'
  stop_server
  kill "$STUB_PID" 2>/dev/null; wait "$STUB_PID" 2>/dev/null; STUB_PID=""
done

# ------------------------------------------------------------------ PART 4 ---
banner "PART 4  THE CONTAINER -- docker compose up --build"
if ! docker info >/dev/null 2>&1; then
  printf '\nSKIPPED: no Docker daemon answered on this machine.\n'
else
  printf '\nThe container reads PE_API_KEY from your shell (compose passes the bare\n'
  printf 'name through) or from .env (env_file). Unset in both: keyless demo mode.\n'
  say "docker compose up --build -d"
  docker compose up --build -d 2>&1 | tail -6
  CID="$(docker compose ps -q racing-data-fastapi)"
  for _ in $(seq 1 60); do
    [ "$(docker inspect --format '{{.State.Health.Status}}' "$CID" 2>/dev/null)" = "healthy" ] && break
    sleep 2
  done
  say "docker inspect --format '{{.State.Health.Status}}' \$CID"
  docker inspect --format '{{.State.Health.Status}}' "$CID"
  say "docker compose exec -T racing-data-fastapi id -u   # runs unprivileged"
  docker compose exec -T racing-data-fastapi id -u 2>&1 | tail -1
  say "curl -s 'localhost:8000/races/next?country=AU&limit=2'   # against the container"
  curl -s "http://127.0.0.1:8000/races/next?country=AU&limit=2" \
    | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"source": d["source"], "count": d["count"], "first_venue": (d["races"] or [{}])[0].get("venue")}, indent=2))'
  say "docker compose down"
  docker compose down 2>&1 | tail -3
fi

printf '\n=== end of capture ===\n'
