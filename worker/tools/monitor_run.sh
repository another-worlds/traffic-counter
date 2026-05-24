#!/usr/bin/env bash
# Periodic execution telemetry for a traffic-counter GPU run.
#
# Mixes RAM, disk, nvidia-smi (GPU utilisation + which PIDs hold VRAM),
# `docker compose ps`, per-container `docker stats`, and per-container health
# (status, restart count, OOMKilled, exit code) into ONE timestamped log so a
# run can be diagnosed after the fact and the log shared verbatim.
#
# Read-only: it never touches the containers, only observes them.
#
# Usage:
#   worker/tools/monitor_run.sh [-i interval_s] [-d duration_s] [-o outfile] \
#                               [-f compose_file] [-p project_dir]
#   -i  seconds between samples            (default 5)
#   -d  total seconds to run; 0 = forever  (default 0, stop with Ctrl-C)
#   -o  log file path                      (default ./run_monitor_<ts>.log)
#   -f  docker compose file (passed to -f) (optional)
#   -p  dir to cd into for compose context (optional; defaults to CWD)
#
# Stop with Ctrl-C; the log is flushed after every sample so a partial run is
# still usable.
set -u

INTERVAL=5; DURATION=0; OUTFILE=""; COMPOSE_FILE=""; PROJECT_DIR=""
while getopts "i:d:o:f:p:h" opt; do
  case "$opt" in
    i) INTERVAL=$OPTARG;; d) DURATION=$OPTARG;; o) OUTFILE=$OPTARG;;
    f) COMPOSE_FILE=$OPTARG;; p) PROJECT_DIR=$OPTARG;;
    h) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "bad option; -h for help" >&2; exit 1;;
  esac
done

[ -n "$PROJECT_DIR" ] && cd "$PROJECT_DIR"
[ -z "$OUTFILE" ] && OUTFILE="./run_monitor_$(date +%Y%m%d_%H%M%S).log"

have() { command -v "$1" >/dev/null 2>&1; }

# docker compose: v2 plugin ("docker compose") or v1 binary ("docker-compose").
DC=()
if have docker && docker compose version >/dev/null 2>&1; then DC=(docker compose)
elif have docker-compose; then DC=(docker-compose); fi
[ -n "$COMPOSE_FILE" ] && [ ${#DC[@]} -gt 0 ] && DC+=(-f "$COMPOSE_FILE")

log()  { printf '%s\n' "$*" >>"$OUTFILE"; }
run()  { # run() "<label cmd>" -> appends "$ <cmd>" then its stdout+stderr
  log "\$ $*"; eval "$*" >>"$OUTFILE" 2>&1 || log "  (command failed: exit $?)"; }

container_ids() {
  local ids=""
  [ ${#DC[@]} -gt 0 ] && ids="$("${DC[@]}" ps -q 2>/dev/null)"
  [ -z "$ids" ] && have docker && ids="$(docker ps -q 2>/dev/null)"
  printf '%s' "$ids"
}

write_header() {
  log "################################################################"
  log "# traffic-counter run monitor"
  log "# started   : $(date -u +%Y-%m-%dT%H:%M:%SZ) (epoch $(date +%s))"
  log "# host       : $(hostname 2>/dev/null)"
  log "# kernel     : $(uname -a 2>/dev/null)"
  log "# cwd        : $(pwd)"
  log "# interval_s : $INTERVAL   duration_s: $DURATION (0=until Ctrl-C)"
  log "# compose    : ${DC[*]:-<none found>}"
  have docker && run "docker version --format '{{.Server.Version}} (client {{.Client.Version}})'"
  if have nvidia-smi; then
    run "nvidia-smi --query-gpu=driver_version,name,memory.total --format=csv,noheader"
  else
    log "# nvidia-smi : NOT FOUND (no GPU telemetry will be captured)"
  fi
  [ ${#DC[@]} -gt 0 ] && run "${DC[*]} config --services"
  log "################################################################"
  log ""
}

sample() {
  local n=$1 now epoch elapsed cids
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; epoch="$(date +%s)"
  elapsed=$(( epoch - START_EPOCH ))
  log "================ SAMPLE $n | $now | +${elapsed}s ================"

  log "-- MEMORY (MB) --"
  run "free -m"
  [ -r /proc/pressure/memory ] && run "cat /proc/pressure/memory"

  log "-- DISK --"
  run "df -h --output=source,fstype,size,used,avail,pcent,target 2>/dev/null || df -h"

  if have nvidia-smi; then
    log "-- GPU --"
    run "nvidia-smi --query-gpu=index,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit --format=csv,noheader,nounits"
    log "-- GPU COMPUTE PROCESSES (pid / name / VRAM MB) --"
    run "nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits"
  fi

  # Space-separate the IDs: they reach docker stats/inspect via eval, where
  # embedded newlines would otherwise be parsed as separate commands.
  cids="$(container_ids | tr '\n' ' ')"
  log "-- DOCKER COMPOSE PS --"
  if [ ${#DC[@]} -gt 0 ]; then run "${DC[*]} ps"; else log "  (no compose command found)"; fi

  if [ -n "$cids" ]; then
    log "-- CONTAINER STATS --"
    run "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}' $cids"
    log "-- CONTAINER HEALTH (status / health / restarts / OOMKilled / exit) --"
    run "docker inspect --format '{{.Name}}: status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}} restarts={{.RestartCount}} oomkilled={{.State.OOMKilled}} exit={{.State.ExitCode}} started={{.State.StartedAt}}' $cids"
  else
    log "-- CONTAINER STATS / HEALTH --"
    log "  (no running containers found)"
  fi
  log ""

  # Compact console heartbeat so the operator sees it's alive.
  local memline gpuline ncont
  memline="$(free -m | awk '/^Mem:/{printf "%d/%dMB", $3, $2}')"
  if have nvidia-smi; then
    gpuline="gpu $(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' | awk -F, '{printf "%s%% %s/%sMB", $1, $2, $3}')"
  else gpuline="gpu n/a"; fi
  ncont="$(printf '%s' "$cids" | wc -w)"
  printf '[%s +%ss] mem %s | %s | containers %s -> %s\n' \
    "$n" "$elapsed" "$memline" "$gpuline" "$ncont" "$OUTFILE"
}

trap 'echo; echo "stopped. log saved to: $OUTFILE"; exit 0' INT TERM

write_header
START_EPOCH="$(date +%s)"
echo "logging to $OUTFILE (Ctrl-C to stop)"
n=0
while :; do
  n=$((n + 1))
  sample "$n"
  if [ "$DURATION" -gt 0 ] && [ $(( $(date +%s) - START_EPOCH )) -ge "$DURATION" ]; then
    break
  fi
  sleep "$INTERVAL"
done
echo "done after $n sample(s). log saved to: $OUTFILE"
