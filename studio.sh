#!/usr/bin/env bash
# Run the whole studio from this Mac and give a friend a link to it.
#
#   ./studio.sh
#
# Starts three things and stops them all on Ctrl-C:
#   1. the web app          (a production build, on localhost)
#   2. producer sync --watch (checks uploads, renders, publishes)
#   3. a Cloudflare tunnel   (a public https link to the web app)
#
# Everything stays on this machine: the store is the folder web/.data, so
# there is no hosted storage and no quota. The link only works while this is
# running, and it is a new link each time -- that is what a free tunnel is.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${STUDIO_PORT:-3400}"
LOGS="$ROOT/web/.data/.logs"

# Force the local store, whatever credentials web/.env.local holds.
export MIXMAX_DATA_DIR="$ROOT/web/.data"

# The link is public, so the passcode is not optional.
if [ -z "${MIXMAX_PASSCODE:-}" ] && [ -f "$ROOT/web/.env.local" ]; then
  MIXMAX_PASSCODE="$(grep '^MIXMAX_PASSCODE=' "$ROOT/web/.env.local" | head -1 | cut -d= -f2- | tr -d '"' || true)"
fi
if [ -z "${MIXMAX_PASSCODE:-}" ]; then
  echo "No passcode. Run it as:  MIXMAX_PASSCODE=something ./studio.sh" >&2
  exit 1
fi
export MIXMAX_PASSCODE

for tool in cloudflared node; do
  command -v "$tool" >/dev/null || { echo "Missing $tool (brew install $tool)." >&2; exit 1; }
done
[ -x "$ROOT/.venv/bin/producer" ] || { echo "Missing .venv/bin/producer — see README, Install." >&2; exit 1; }

mkdir -p "$LOGS"
PIDS=()
cleanup() {
  echo
  echo "Stopping the studio."
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' INT TERM

echo "Building the web app…"
(cd "$ROOT/web" && npx next build >"$LOGS/build.log" 2>&1) || {
  echo "The build failed. See $LOGS/build.log" >&2; exit 1; }

(cd "$ROOT/web" && exec npx next start -p "$PORT" >"$LOGS/web.log" 2>&1) &
PIDS+=($!)

for _ in $(seq 1 60); do
  curl -s -o /dev/null "http://127.0.0.1:$PORT/login" && break
  sleep 0.5
done

cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >"$LOGS/tunnel.log" 2>&1 &
PIDS+=($!)

LINK=""
for _ in $(seq 1 60); do
  LINK="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOGS/tunnel.log" | head -1 || true)"
  [ -n "$LINK" ] && break
  sleep 0.5
done

echo
echo "  Studio is up."
echo "  You:          http://localhost:$PORT"
if [ -n "$LINK" ]; then
  echo "  Your friend:  $LINK"
else
  echo "  The tunnel did not come up — see $LOGS/tunnel.log. Local use still works."
fi
echo "  Passcode:     $MIXMAX_PASSCODE"
echo
echo "  Leave this running. Ctrl-C stops everything and ends the link."
echo

# The worker runs in the foreground so its output is what you watch.
cd "$ROOT"
.venv/bin/producer sync --watch --interval 10 --store "$MIXMAX_DATA_DIR" &
PIDS+=($!)
wait "${PIDS[2]}"
