#!/usr/bin/env bash
# run_sweep.sh — sweep ProtoAcc hardware configs through the Verilator
# simulator and collect one CSV row per (config × bench × op).
#
# Sources env.sh ONCE (not per subprocess), pre-builds chipyard.jar serially
# to avoid concurrent-sbt races (the "Could not find or load main class
# chipyard.Generator" failures), then dispatches per-config work through GNU
# parallel. Each worker builds its config's Verilator sim, runs every
# pending (bench, op) pair against it, appends the results to the CSV under
# a file lock, and (unless --keep-artifacts) deletes the per-config
# generated-src tree + simulator binary to bound disk usage.
#
# Usage (see --help):
#   run_sweep.sh --output out.csv --side des --op des --workers 8

set -euo pipefail

CHIPYARD_ROOT=/home/ec2-user/hyperscale-grpc-chipyard
SWEEP_SCALA="$CHIPYARD_ROOT/generators/chipyard/src/main/scala/config/ProtoAccelSweepConfigs.scala"
VERILATOR_DIR="$CHIPYARD_ROOT/sims/verilator"
VERILATOR_OUTPUT_ROOT="$VERILATOR_DIR/output"
VERILATOR_GENERATED_SRC_ROOT="$VERILATOR_DIR/generated-src"
BENCH_BUILD_DIR="$CHIPYARD_ROOT/generators/protoacc/software/verilator-bench/build"
CLASSPATH_JAR="$CHIPYARD_ROOT/.classpath_cache/chipyard.jar"

DEFAULT_BENCHES=(bench0 bench1 bench2 bench3 bench4 bench5)

# Column order and defaults — kept in sync with gen_protoacc_sweep_configs.py.
DES_KEYS=(des_top_descriptor_reqs des_top_memloader_reqs des_cr_rocc_commands
          des_dth_l1_reqs des_dth_fd_reqs des_dth_fd_resps des_fw_l1_reqs
          des_ml_buf_info_q des_ml_load_info_q)
SER_KEYS=(ser_field_handlers ser_cr_rocc_commands ser_dth_hasbits_reqs
          ser_dth_descriptor_reqs ser_dth_reg_resps ser_dth_reqs_meta
          ser_dth_fh_outputs ser_mw_write_input ser_mw_write_inject
          ser_mw_write_ptrs)
PARAM_KEYS=("${DES_KEYS[@]}" "${SER_KEYS[@]}")

# key=default pairs (bash 4 assoc arrays).
declare -A DEFAULTS=(
  [des_top_descriptor_reqs]=4 [des_top_memloader_reqs]=64 [des_cr_rocc_commands]=2
  [des_dth_l1_reqs]=4 [des_dth_fd_reqs]=4 [des_dth_fd_resps]=4 [des_fw_l1_reqs]=4
  [des_ml_buf_info_q]=16 [des_ml_load_info_q]=256
  [ser_field_handlers]=6 [ser_cr_rocc_commands]=2 [ser_dth_hasbits_reqs]=4
  [ser_dth_descriptor_reqs]=4 [ser_dth_reg_resps]=10 [ser_dth_reqs_meta]=4
  [ser_dth_fh_outputs]=4 [ser_mw_write_input]=4 [ser_mw_write_inject]=4
  [ser_mw_write_ptrs]=10
)

# ShortLabel=FullKey, serialized so workers can rebuild the map cheaply.
SHORT_TO_FULL_TXT=$'DesTopDescr\tdes_top_descriptor_reqs
DesTopMemloader\tdes_top_memloader_reqs
DesCrRocc\tdes_cr_rocc_commands
DesDthL1\tdes_dth_l1_reqs
DesDthFdReqs\tdes_dth_fd_reqs
DesDthFdResps\tdes_dth_fd_resps
DesFwL1\tdes_fw_l1_reqs
DesMlBufInfo\tdes_ml_buf_info_q
DesMlLoadInfo\tdes_ml_load_info_q
SerFieldHandlers\tser_field_handlers
SerCrRocc\tser_cr_rocc_commands
SerDthHasbits\tser_dth_hasbits_reqs
SerDthDescr\tser_dth_descriptor_reqs
SerDthRegResps\tser_dth_reg_resps
SerDthReqsMeta\tser_dth_reqs_meta
SerDthFhOutputs\tser_dth_fh_outputs
SerMwWriteInput\tser_mw_write_input
SerMwWriteInject\tser_mw_write_inject
SerMwWritePtrs\tser_mw_write_ptrs'

usage() {
  cat <<EOF
Usage: $0 [options] --output OUT.csv

  --bench NAME                single bench (e.g. bench1)
  --benches NAME1 NAME2 ...   multiple benches (terminates at next --flag)
  --op {ser,des,both}         default: both
  --side {des,ser,both}       default: both
  --limit N                   only run first N configs (0 = all)
  --output PATH               CSV output path (required)
  --jobs N                    make -j for each Verilator build (default: 1)
  --workers N                 parallel configs (default: nproc)
  --bench-timeout SEC         per-bench wall cap (default: 3600)
  --skip-build                don't build missing sims
  --keep-artifacts            don't delete generated-src/sim binary after use
  --dry-run                   print plan and exit
EOF
}

OUTPUT=""
BENCHES=()
OP=both
SIDE=both
LIMIT=0
JOBS=1
WORKERS=$(nproc)
BENCH_TIMEOUT=3600
SKIP_BUILD=0
KEEP_ARTIFACTS=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --bench)          BENCHES=("$2"); shift 2 ;;
    --benches)        shift
                      while [[ $# -gt 0 && $1 != --* ]]; do BENCHES+=("$1"); shift; done ;;
    --op)             OP=$2; shift 2 ;;
    --side)           SIDE=$2; shift 2 ;;
    --limit)          LIMIT=$2; shift 2 ;;
    --output)         OUTPUT=$2; shift 2 ;;
    --jobs)           JOBS=$2; shift 2 ;;
    --workers)        WORKERS=$2; shift 2 ;;
    --bench-timeout)  BENCH_TIMEOUT=$2; shift 2 ;;
    --skip-build)     SKIP_BUILD=1; shift ;;
    --keep-artifacts) KEEP_ARTIFACTS=1; shift ;;
    --dry-run)        DRY_RUN=1; shift ;;
    -h|--help)        usage; exit 0 ;;
    *)                echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -z "$OUTPUT" ]] && { echo "--output is required" >&2; exit 2; }
[[ ${#BENCHES[@]} -eq 0 ]] && BENCHES=("${DEFAULT_BENCHES[@]}")

# Source the Chipyard env once — every downstream sbt/make/java call inherits.
# env.sh triggers conda activate hooks that reference unset vars (e.g. $RISCV
# in activate-riscv-tools.sh), so relax nounset just for the source.
set +u
# shellcheck disable=SC1091
source "$CHIPYARD_ROOT/env.sh"
set -u

if ! command -v parallel >/dev/null; then
  echo "GNU parallel not on PATH — install via: conda install -p $CONDA_PREFIX -c conda-forge parallel" >&2
  exit 1
fi

# Serialize the sbt assembly step. With multiple workers each discovering a
# stale chipyard.jar, concurrent `sbt assembly` invocations overwrite the jar
# mid-read in the downstream `java -cp ... chipyard.Generator` call, producing
# "Could not find or load main class chipyard.Generator". Building it once
# upfront (even if already fresh) eliminates that race.
echo "[prebuild] ensuring $CLASSPATH_JAR is up to date..." >&2
make -C "$VERILATOR_DIR" CONFIG=ProtoAccelRocketConfig "$CLASSPATH_JAR" >/dev/null

# Parse ProtoAccelSweepConfigs.scala: one TSV record per class, fields are
# (cls, side, short=N short=N ...). awk collapses the /** Sweep row ... */
# comment and the following `class` declaration into one record.
PLAN_FILE=$(mktemp -t protoacc_sweep.XXXXXX)
trap 'rm -f "$PLAN_FILE" "$PLAN_FILE".tmp' EXIT

gawk -v side_filter="$SIDE" '
  /\/\*\* Sweep row/ {
    # strip the /** ... */ wrapper, keep "ShortLabel=N, ShortLabel=N, ..."
    params = $0
    sub(/.*\):[[:space:]]*/, "", params)
    sub(/[[:space:]]*\*\/.*/, "", params)
    gsub(/,/, " ", params)
    next
  }
  /^class Proto.*SweepSample[0-9]+Config/ {
    if (match($0, /class (Proto[A-Za-z]+SweepSample[0-9]+Config)/, a)) {
      cls = a[1]
      side = (cls ~ /DesSweepSample/) ? "des" :
             (cls ~ /SerSweepSample/) ? "ser" : "joint"
      if (side_filter == "both" || side == side_filter) {
        printf "%s\t%s\t%s\n", cls, side, params
      }
      params = ""
    }
  }
' "$SWEEP_SCALA" > "$PLAN_FILE"

if (( LIMIT > 0 )); then
  head -n "$LIMIT" "$PLAN_FILE" > "$PLAN_FILE.tmp"
  mv "$PLAN_FILE.tmp" "$PLAN_FILE"
fi

TOTAL_CONFIGS=$(wc -l < "$PLAN_FILE")
(( TOTAL_CONFIGS == 0 )) && { echo "No sweep-sample classes found." >&2; exit 1; }

echo "Sweep plan: $TOTAL_CONFIGS configs × ${#BENCHES[@]} bench(es) × op=$OP" >&2

if (( DRY_RUN == 1 )); then
  while IFS=$'\t' read -r cls side _; do
    for bench in "${BENCHES[@]}"; do
      echo "  would run $cls [$side] bench=$bench op=$OP"
    done
  done < "$PLAN_FILE"
  exit 0
fi

# CSV header — only if file doesn't yet exist (resume-friendly).
CSV_HEADER="config_name,side,bench,op,iters,cycles,bytes,throughput_bytes_per_sec,wall_s,build_wall_s,build_was_cached"
for k in "${PARAM_KEYS[@]}"; do CSV_HEADER+=",$k"; done
mkdir -p "$(dirname "$OUTPUT")"
if [[ ! -f "$OUTPUT" ]]; then echo "$CSV_HEADER" > "$OUTPUT"; fi

# Export state the worker needs (parallel spawns fresh shells per job).
export CHIPYARD_ROOT VERILATOR_DIR VERILATOR_OUTPUT_ROOT VERILATOR_GENERATED_SRC_ROOT
export BENCH_BUILD_DIR OUTPUT OP JOBS BENCH_TIMEOUT SKIP_BUILD KEEP_ARTIFACTS
export BENCHES_STR="${BENCHES[*]}"
export PARAM_KEYS_STR="${PARAM_KEYS[*]}"
export DEFAULTS_STR
DEFAULTS_STR=""
for k in "${PARAM_KEYS[@]}"; do DEFAULTS_STR+="$k=${DEFAULTS[$k]} "; done
export SHORT_TO_FULL_TXT
export CSV_LOCK="${OUTPUT}.lock"

worker() {
  set -u
  local line=$1
  local cls side params
  IFS=$'\t' read -r cls side params <<< "$line"

  # Rebuild DEFAULTS / SHORT_TO_FULL assoc arrays from exported strings.
  declare -A combo short_to_full
  local kv k v
  for kv in $DEFAULTS_STR; do
    k=${kv%%=*}; v=${kv##*=}
    combo[$k]=$v
  done
  local sl
  while IFS=$'\t' read -r sl k; do
    [[ -n $sl ]] && short_to_full[$sl]=$k
  done <<< "$SHORT_TO_FULL_TXT"

  # Overlay this config's actual values.
  local tok
  for tok in $params; do
    [[ $tok != *=* ]] && continue
    local short=${tok%%=*} val=${tok##*=}
    local full=${short_to_full[$short]:-}
    if [[ -n $full ]]; then
      combo[$full]=$val
    else
      combo[unknown_$short]=$val
    fi
  done

  # Decide which ops apply based on side / OP flag.
  local -a ops=()
  case $OP in
    ser) ops=(ser) ;;
    des) ops=(des) ;;
    both)
      case $side in
        des)   ops=(des) ;;
        ser)   ops=(ser) ;;
        *)     ops=(des ser) ;;
      esac ;;
  esac

  # Figure out which (bench, op) pairs still need a row in the CSV.
  local -a pending=()
  local op bench
  for op in "${ops[@]}"; do
    for bench in $BENCHES_STR; do
      if ! grep -q "^${cls},${side},${bench},${op}," "$OUTPUT" 2>/dev/null; then
        pending+=("${bench}:${op}")
      fi
    done
  done
  if (( ${#pending[@]} == 0 )); then
    echo "[skip-done] $cls" >&2
    maybe_cleanup "$cls"
    return 0
  fi

  # Build the Verilator sim (or reuse if already present).
  local sim_bin="$VERILATOR_DIR/simulator-chipyard.harness-$cls"
  local build_wall_s=0 was_cached=0
  if [[ -x "$sim_bin" ]]; then
    was_cached=1
  elif (( SKIP_BUILD == 1 )); then
    echo "[skip-build] $cls" >&2
    return 0
  else
    local build_log
    build_log=$(mktemp -t "build_${cls}.XXXXXX")
    echo "[build]    $cls" >&2
    local t0=$SECONDS
    if ! make -C "$VERILATOR_DIR" CONFIG="$cls" -j"$JOBS" >"$build_log" 2>&1; then
      build_wall_s=$((SECONDS - t0))
      echo "[build-fail] $cls wall=${build_wall_s}s tail:" >&2
      tail -c 600 "$build_log" >&2
      rm -f "$build_log"
      return 0
    fi
    build_wall_s=$((SECONDS - t0))
    rm -f "$build_log"
    echo "[build-ok] $cls wall=${build_wall_s}s" >&2
  fi

  # Run each pending bench and append a row per success.
  local build_attributed=0
  for p in "${pending[@]}"; do
    bench=${p%%:*}
    op=${p##*:}
    local elf="$BENCH_BUILD_DIR/${bench}_${op}.riscv"
    if [[ ! -f $elf ]]; then
      echo "[elf-missing] $elf" >&2
      continue
    fi

    echo "[run]      $cls × ${bench}_${op}" >&2
    local rt0=$SECONDS
    timeout "$BENCH_TIMEOUT" \
      make -C "$VERILATOR_DIR" \
        CONFIG="$cls" BREAK_SIM_PREREQ=1 LOADMEM=1 \
        run-binary-fast BINARY="$elf" >/dev/null 2>&1 || true
    local wall=$((SECONDS - rt0))

    local log="$VERILATOR_OUTPUT_ROOT/chipyard.harness.TestHarness.$cls/${bench}_${op}.log"
    if [[ ! -f $log ]]; then
      echo "[no-log]   $log" >&2
      continue
    fi
    # Match Python: take the LAST ACCEL_SUMMARY line in the log.
    local summary
    summary=$(grep 'ACCEL_SUMMARY:' "$log" | tail -1 || true)
    if [[ -z $summary ]]; then
      echo "[no-summary] $log (wall=${wall}s)" >&2
      continue
    fi

    local iters cycles bytes
    iters=$(grep -oP 'iters=\K[0-9]+' <<<"$summary" || echo 0)
    cycles=$(grep -oP 'total_cycles=\K[0-9]+' <<<"$summary" || echo 0)
    bytes=$(grep -oP 'total_bytes=\K[0-9]+' <<<"$summary" || echo 0)

    local tput=0
    if (( cycles > 0 )); then
      tput=$(awk -v b="$bytes" -v c="$cycles" 'BEGIN{printf "%.6f", b*1e9/c}')
    fi

    local this_build_wall=0
    local cached_col=$was_cached
    if (( build_attributed == 0 )); then
      this_build_wall=$build_wall_s
      build_attributed=1
    else
      cached_col=1
    fi

    # Assemble the CSV row in the declared column order.
    local row="$cls,$side,$bench,$op,$iters,$cycles,$bytes,$tput,$wall,$this_build_wall,$cached_col"
    local pk
    for pk in $PARAM_KEYS_STR; do row+=",${combo[$pk]}"; done

    # Lock-serialized append so concurrent workers don't interleave lines.
    (
      flock -w 60 9
      echo "$row" >> "$OUTPUT"
    ) 9>"$CSV_LOCK"

    echo "[done]     $cls × ${bench}_${op}: ${wall}s, ${bytes}B / ${cycles}cyc" >&2
  done

  maybe_cleanup "$cls"
}

maybe_cleanup() {
  (( KEEP_ARTIFACTS == 1 )) && return 0
  local cls=$1
  rm -rf -- "$VERILATOR_GENERATED_SRC_ROOT/chipyard.harness.TestHarness.$cls"
  rm -f  -- "$VERILATOR_DIR/simulator-chipyard.harness-$cls" \
            "$VERILATOR_DIR/simulator-chipyard.harness-$cls.debug"
  echo "[cleanup]  $cls" >&2
}

export -f worker maybe_cleanup

echo "[parallel] $WORKERS workers, make -j$JOBS per build" >&2
parallel -j "$WORKERS" --line-buffer --halt soon,fail=1 worker :::: "$PLAN_FILE"

echo "Wrote $OUTPUT" >&2
