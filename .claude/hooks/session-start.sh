#!/usr/bin/env bash
# SessionStart bring-up for platform/ dev in Claude Code on the web.
#
# SHIPPED BUT NOT WIRED by default: auto-running code each session is opt-in (see
# .claude/hooks/README.md and AGENTS.md). To enable, add a SessionStart hook to
# .claude/settings.json that runs this script, then export PLATFORM_DEV_AUTOSTART=1.
#
# Idempotent; ALWAYS exits 0 so it can never block a session from starting.
set -u
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT" 2>/dev/null || exit 0

if [ "${PLATFORM_DEV_AUTOSTART:-0}" != "1" ]; then
  echo "platform/ dev env not auto-started (set PLATFORM_DEV_AUTOSTART=1 to enable; see .claude/hooks/README.md)."
  exit 0
fi

echo "platform/: preparing dev environment (PLATFORM_DEV_AUTOSTART=1)…"

# 1) Docker daemon — the web sandbox does not start it automatically.
if ! docker info >/dev/null 2>&1; then
  dockerd --storage-driver=vfs >/tmp/dockerd.log 2>&1 &
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi

# 2) PostGIS (pulled image; no build).
docker compose up -d model-db >/tmp/model-db.log 2>&1 \
  || echo "  (model-db failed to start; see /tmp/model-db.log)"

# 3) Host venv with core-api deps (the host trusts the proxy CA; in-container pip may not).
VENV="$HOME/.cache/cv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" \
    && "$VENV/bin/pip" -q install -r platform/core-api/requirements.txt \
    || echo "  (venv/deps setup failed)"
fi

echo "platform/: ready. Run tests with:"
echo "  cd platform/core-api && DATABASE_URL=postgresql+psycopg://model:model@localhost:5434/model \\"
echo "    $VENV/bin/python -m pytest -q tests"
exit 0
