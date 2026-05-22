#!/usr/bin/env bash
# Set the worker replica count to N.
#
#   ./scripts/scale_workers.sh 6
#
# Persists WORKER_REPLICAS=N into .env so the next plain
# `docker compose up -d` can re-apply the same scale, and immediately
# brings the worker service up at the requested count.
set -euo pipefail

N="${1:?usage: scale_workers.sh <replica-count>}"
[[ "$N" =~ ^[0-9]+$ ]] \
    || { printf 'scale_workers: N must be a non-negative integer (got %q)\n' "$N" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Persist for the next plain `docker compose up -d`. Compose itself
# only honors --scale when explicitly passed, but this keeps the
# intended scale in one obvious place for operators.
touch .env
if grep -q '^WORKER_REPLICAS=' .env; then
    sed -i.bak "s/^WORKER_REPLICAS=.*/WORKER_REPLICAS=${N}/" .env
    rm -f .env.bak
else
    printf 'WORKER_REPLICAS=%s\n' "${N}" >> .env
fi

printf 'scale_workers: bringing worker replicas to %s …\n' "${N}"
docker compose up -d --scale worker="${N}" worker
docker compose ps worker
