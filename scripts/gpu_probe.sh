#!/usr/bin/env bash
# Measure the GPU and recommend how many worker replicas to launch.
#
# 1. Confirms nvidia-smi + docker compose are usable, refuses to run
#    while any worker is already up (would inflate "free VRAM").
# 2. Reads total/free VRAM via nvidia-smi.
# 3. Spawns one ephemeral worker container that loads YOLOv8m on
#    cuda:0 in fp16 and prints torch.cuda.max_memory_allocated() after
#    a warm-up inference — this is the real per-worker footprint.
# 4. Recommends N = floor((free_mib - safety_margin) / (per_worker * 1.15)).
#
# Flags:
#   --no-measure          skip the docker probe, use a 800 MiB estimate
#   --safety-margin <MiB> override the default 512 MiB headroom
#   --json                machine-readable output
#
# Env:
#   GPU_SAFETY_MARGIN_MIB   alternate way to set the safety margin
set -euo pipefail

# ── arg parsing ──────────────────────────────────────────────────────────────
SAFETY_MARGIN_MIB="${GPU_SAFETY_MARGIN_MIB:-512}"
MEASURE=1
JSON=0
ESTIMATE_FALLBACK_MIB=800
PAD_NUMERATOR=115     # 1.15× as integer math (×100)
PAD_DENOMINATOR=100

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-measure)        MEASURE=0; shift ;;
        --safety-margin)     SAFETY_MARGIN_MIB="$2"; shift 2 ;;
        --safety-margin=*)   SAFETY_MARGIN_MIB="${1#*=}"; shift ;;
        --json)              JSON=1; shift ;;
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf 'unknown flag: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

# ── pre-flight ───────────────────────────────────────────────────────────────
die() { printf 'gpu_probe: %s\n' "$*" >&2; exit "${2:-1}"; }

command -v nvidia-smi >/dev/null 2>&1 \
    || die "nvidia-smi not found on PATH — is the NVIDIA driver installed?" 2
command -v docker >/dev/null 2>&1 \
    || die "docker not found on PATH"
docker compose version >/dev/null 2>&1 \
    || die "docker compose plugin not available"

# Resolve repo root so the script works from any cwd.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Refuse to run while workers are up — their VRAM occupancy hides the
# true free-VRAM number and the recommendation comes out too low.
if RUNNING="$(docker compose ps -q worker 2>/dev/null)" && [[ -n "$RUNNING" ]]; then
    die "worker container(s) are running — stop them first:
        docker compose stop worker
    then re-run this probe."
fi

# ── GPU snapshot ─────────────────────────────────────────────────────────────
if ! GPU_LINE="$(nvidia-smi --query-gpu=name,memory.total,memory.free \
                            --format=csv,noheader,nounits 2>/dev/null \
                  | head -n1)" || [[ -z "$GPU_LINE" ]]; then
    die "nvidia-smi returned no GPU" 2
fi
GPU_NAME="$(printf '%s' "$GPU_LINE" | awk -F', ' '{print $1}')"
TOTAL_MIB="$(printf '%s' "$GPU_LINE" | awk -F', ' '{print $2}')"
FREE_MIB="$(printf '%s' "$GPU_LINE" | awk -F', ' '{print $3}')"

[[ "$TOTAL_MIB" =~ ^[0-9]+$ ]] || die "could not parse total VRAM from nvidia-smi"
[[ "$FREE_MIB"  =~ ^[0-9]+$ ]] || die "could not parse free VRAM from nvidia-smi"

# ── per-worker measurement ───────────────────────────────────────────────────
PROBE_SOURCE=measured
if (( MEASURE )); then
    PROBE_SCRIPT='
import os, sys, torch
from ultralytics import YOLO
m = YOLO(os.environ["MODEL_NAME"])
dummy = torch.zeros(1, 3, 640, 640, device="cuda:0", dtype=torch.float16)
m.predict(dummy, device="cuda:0", half=True, verbose=False)
torch.cuda.synchronize()
sys.stdout.write(str(int(torch.cuda.max_memory_allocated() / 1024 / 1024)))
'
    if ! PROBE_OUT="$(docker compose run --rm --no-deps -T worker \
                          python -c "$PROBE_SCRIPT" 2>/tmp/gpu_probe.err)"; then
        printf 'gpu_probe: docker measurement failed; falling back to %s MiB estimate.\n' \
               "$ESTIMATE_FALLBACK_MIB" >&2
        printf '  (probe stderr follows — usually image build needed or driver mismatch)\n' >&2
        sed 's/^/    /' /tmp/gpu_probe.err >&2 || true
        PER_WORKER_MIB="$ESTIMATE_FALLBACK_MIB"
        PROBE_SOURCE=estimated
    else
        # docker compose run may prepend log noise; take the last numeric line.
        PER_WORKER_MIB="$(printf '%s\n' "$PROBE_OUT" | tail -n1 | tr -dc '0-9')"
        [[ -n "$PER_WORKER_MIB" ]] || die "probe produced no number; got: $PROBE_OUT"
    fi
else
    PER_WORKER_MIB="$ESTIMATE_FALLBACK_MIB"
    PROBE_SOURCE=estimated
fi

# ── compute N ────────────────────────────────────────────────────────────────
# Pad per-worker by 1.15× for ByteTrack buffers + larger-frame spikes.
PADDED_MIB=$(( (PER_WORKER_MIB * PAD_NUMERATOR + PAD_DENOMINATOR - 1) / PAD_DENOMINATOR ))
USABLE_MIB=$(( FREE_MIB - SAFETY_MARGIN_MIB ))
if (( USABLE_MIB <= 0 )); then
    RECOMMENDED=1
else
    RECOMMENDED=$(( USABLE_MIB / PADDED_MIB ))
    (( RECOMMENDED < 1 )) && RECOMMENDED=1
fi

# ── output ───────────────────────────────────────────────────────────────────
if (( JSON )); then
    printf '{"gpu_name":"%s","total_mib":%s,"free_mib":%s,"per_worker_mib":%s,"per_worker_mib_padded":%s,"safety_margin_mib":%s,"recommended":%s,"source":"%s"}\n' \
        "$GPU_NAME" "$TOTAL_MIB" "$FREE_MIB" \
        "$PER_WORKER_MIB" "$PADDED_MIB" "$SAFETY_MARGIN_MIB" \
        "$RECOMMENDED" "$PROBE_SOURCE"
    exit 0
fi

cat <<EOF
GPU:                              ${GPU_NAME}
VRAM total:                       ${TOTAL_MIB} MiB
VRAM free:                        ${FREE_MIB} MiB
Per-worker (${PROBE_SOURCE}, +15% pad):  ${PADDED_MIB} MiB
Safety margin:                    ${SAFETY_MARGIN_MIB} MiB
───────────────────────────────────────────────
Recommended replica count:        ${RECOMMENDED}

To launch:
    ./scripts/scale_workers.sh ${RECOMMENDED}

To shrink back to 1:
    ./scripts/scale_workers.sh 1
EOF
