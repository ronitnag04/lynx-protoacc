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
# generated-src tree + simulator binary to bound disk usage. After a
# successful simulation-output upload to S3 (or if that zip already exists
# remotely), the matching output/ tree is removed too unless --keep-artifacts.
#
# By default the worker also zips and uploads the per-config build artifacts
# and simulation outputs to S3 before local cleanup:
#   generated-src + simulator   → s3://ronitnag04-lynx/verilator_build_files/<cls>.zip
#   output/<cls>/                → s3://ronitnag04-lynx/simulation_files/<cls>.zip
# This step is skipped (with a log line) if aws/zip are missing or if
# --skip-upload is passed (local output/ is kept when upload is skipped).
# Upload failures are non-fatal: they log and the sweep continues.
#
# With --pull-s3-builds, the worker reverses the build upload before
# building: it head-objects s3://.../verilator_build_files/<cls>.zip and,
# if present, downloads + unzips it under $VERILATOR_DIR (restoring
# generated-src/chipyard.harness.TestHarness.<cls> and the simulator
# binary). Only if the pull misses or fails does it fall back to a local
# Verilator build. Requires aws + unzip; otherwise the flag no-ops with a
# warning.
#
# Side (--side) vs op (--op):
#   --side filters which Scala config classes enter the sweep (DesSweep / SerSweep /
#   joint ProtoAccelSweep rows in ProtoAccelSweepConfigs.scala).
#   --op selects which benchmark ELFs run (*_des.riscv vs *_ser.riscv).
#   Default --op both: DesSweep configs run only des benches; SerSweep only ser;
#   joint configs run both. So --side des|ser with default --op is already
#   side-aligned; --op des|ser matters when --side both includes joint configs,
#   or to force one ELF flavor on every included row.
#
# Usage (see --help):
#   run_sweep.sh --output out.csv --side des --workers 8   # des configs + des ELFs (default op)
#   run_sweep.sh --output out.csv --side both --op des     # all configs, only *_des benches
#   run_sweep.sh --output out.csv --random-bench           # one random DEFAULT_BENCH per config
#   run_sweep.sh --output out.csv --iter-bench             # walk synth benches 1:1 with configs,
#                                                          # then random HPB for any remainder

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Chipyard repo root: .../generators/protoacc/software/verilator-bench → ../../../../
CONFIGS="${CONFIGS=-ProtoAccelSweepConfigs.scala}"
CHIPYARD_ROOT="${CHIPYARD_ROOT:-$(cd "$SCRIPT_DIR/../../../.." && pwd)}"
SWEEP_SCALA="$CHIPYARD_ROOT/generators/chipyard/src/main/scala/config/$CONFIGS"
VERILATOR_DIR="$CHIPYARD_ROOT/sims/verilator"
VERILATOR_OUTPUT_ROOT="$VERILATOR_DIR/output"
VERILATOR_GENERATED_SRC_ROOT="$VERILATOR_DIR/generated-src"
BENCH_BUILD_DIR="$CHIPYARD_ROOT/generators/protoacc/software/verilator-bench/build"
CLASSPATH_JAR="$CHIPYARD_ROOT/.classpath_cache/chipyard.jar"

# S3 destinations. Bucket/prefixes match what downstream Lynx training
# scripts expect; change both sides if you re-home the artifacts. The
# split bucket/prefix vars are there so the worker can use head-object
# (which requires them individually) without re-parsing the URI each call.
S3_BUCKET="ronitnag04-lynx"
S3_BUILD_KEY_PREFIX="verilator_build_files"
S3_SIM_KEY_PREFIX="simulation_files"
S3_BUILD_PREFIX="s3://${S3_BUCKET}/${S3_BUILD_KEY_PREFIX}"
S3_SIM_PREFIX="s3://${S3_BUCKET}/${S3_SIM_KEY_PREFIX}"

# AWS CLI profile used for every upload call. Matches the [lynx] stanza in
# ~/.aws/credentials, which is the team-scoped IAM user authorized to
# write these buckets (the default profile is read-only on this account).
AWS_PROFILE_NAME="${AWS_PROFILE_NAME:-lynx}"

# Default bench list: auto-discover every bench<N>_{ser,des}.riscv built
# under $BENCH_BUILD_DIR. This keeps synthetic benches (produced by
# `make synth SYNTH_COUNT=N` then `make bench SYNTH_COUNT=N`) in the sweep
# by default without requiring a second source of truth. Falls back to the
# historical bench0..bench5 list if the build dir is empty (e.g. dry-run
# before the first `make bench`).
_discover_default_benches() {
  local -a benches=()
  if [[ -d "$BENCH_BUILD_DIR" ]]; then
    local f name id
    # Use ser ELFs as the canonical list; des ELFs mirror the same ids.
    while IFS= read -r -d '' f; do
      name="$(basename "$f")"
      name="${name%_ser.riscv}"
      # Only accept the bench<N> shape — skip bench_tiny, bench_repro, isolate_, etc.
      if [[ "$name" =~ ^bench[0-9]+$ ]]; then
        benches+=("$name")
      fi
    done < <(find "$BENCH_BUILD_DIR" -maxdepth 1 -type f -name 'bench*_ser.riscv' -print0 2>/dev/null)
  fi
  if [[ ${#benches[@]} -eq 0 ]]; then
    benches=(bench0 bench1 bench2 bench3 bench4 bench5)
  fi
  # Sort numerically by the <N> suffix so CSV rows come out in a stable order.
  printf '%s\n' "${benches[@]}" | sort -V
}
mapfile -t DEFAULT_BENCHES < <(_discover_default_benches)

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
  --side {des,ser,both}       which ProtoAccel*Sweep*Config rows to include (from
                              ProtoAccelSweepConfigs.scala): DesSweep-only,
                              SerSweep-only, or all rows including joint configs.
                              Default: both.
  --op {ser,des,both}         which benchmark ELFs to run per row (*_des.riscv /
                              *_ser.riscv). Default both: DesSweep rows run only
                              des benches, SerSweep rows only ser benches, joint
                              rows run both. Use --op des or --op ser to force that
                              ELF on every included row (e.g. with --side both).
                              Default: both.
  --limit N                   only run first N configs (0 = all)
  --output PATH               CSV output path (required)
  --jobs N                    make -j for each Verilator build (default: 1)
  --workers N                 parallel configs (default: nproc)
  --bench-timeout SEC         per-bench wall cap (default: 3600)
  --bench-parallel N          within a single config, run up to N (bench, op)
                              simulations in parallel AFTER the build has
                              finished (default: 1 = serial). Each bench still
                              invokes 'make run-binary-fast' with BREAK_SIM_PREREQ=1,
                              so no sim rebuild happens under the fan-out; the
                              only shared side effect is the idempotent output-dir
                              mkdir and each bench writes to its own
                              <bench>_<op>.log. Recommended for pareto-validation
                              sweeps and the default-config sweep (few configs,
                              many benches per config); not useful when the
                              sweep already has many configs saturating
                              --workers. Memory: each sim can take multiple GB —
                              the effective peak is up to WORKERS*BENCH_PARALLEL
                              concurrent sims, so budget accordingly.
  --skip-build                don't build missing sims
  --keep-artifacts            don't delete generated-src/sim binary after use
  --skip-upload               don't zip+upload per-config artifacts to S3
  --pull-s3-builds            before building, try to fetch prebuilt
                              generated-src+simulator zip from S3 and unzip
                              in place; fall back to a local build if the
                              object is missing or the pull fails
  --random-bench              per config, run one bench chosen at random from
                              DEFAULT_BENCHES (ignores --bench / --benches)
  --iter-bench                iterate through every synth bench (bench<N> with
                              N >= HPB_SYNTH_START, default 6) once, pairing
                              synth bench i with plan config i. Any remaining
                              configs (when len(synth) < len(configs)) draw a
                              random HPB bench (bench0..bench<HPB_SYNTH_START-1>).
                              Ignores --bench / --benches. Useful for sweeps
                              where synth benches are generated to roughly
                              match the number of configs.
  --hpb-synth-start N         bench-id boundary between HPB and synth benches
                              (default 6, matching Makefile SYNTH_START). Only
                              meaningful with --iter-bench.
  --dry-run                   print plan and exit

  --side selects hardware configs; --op selects workloads. With default --op,
  --side des or --side ser already restricts runs to matching *_des or *_ser ELFs;
  --op is redundant there unless you narrow joint runs when using --side both.
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
BENCH_PARALLEL=1
SKIP_BUILD=0
KEEP_ARTIFACTS=0
SKIP_UPLOAD=0
PULL_S3_BUILDS=0
DRY_RUN=0
RANDOM_BENCH=0
ITER_BENCH=0
HPB_SYNTH_START=6

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
    --bench-parallel) BENCH_PARALLEL=$2; shift 2 ;;
    --skip-build)     SKIP_BUILD=1; shift ;;
    --keep-artifacts) KEEP_ARTIFACTS=1; shift ;;
    --skip-upload)    SKIP_UPLOAD=1; shift ;;
    --pull-s3-builds) PULL_S3_BUILDS=1; shift ;;
    --random-bench)   RANDOM_BENCH=1; shift ;;
    --iter-bench)     ITER_BENCH=1; shift ;;
    --hpb-synth-start) HPB_SYNTH_START=$2; shift 2 ;;
    --dry-run)        DRY_RUN=1; shift ;;
    -h|--help)        usage; exit 0 ;;
    *)                echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -z "$OUTPUT" ]] && { echo "--output is required" >&2; exit 2; }
if ! [[ "$BENCH_PARALLEL" =~ ^[0-9]+$ ]] || (( BENCH_PARALLEL < 1 )); then
  echo "--bench-parallel must be a positive integer (got '$BENCH_PARALLEL')" >&2; exit 2
fi
if (( RANDOM_BENCH == 1 && ITER_BENCH == 1 )); then
  echo "--random-bench and --iter-bench are mutually exclusive" >&2; exit 2
fi
# RANDOM_BENCH samples one bench per config from DEFAULT_BENCHES. ITER_BENCH
# pairs plan config i with synth bench i; configs past the synth pool fall
# back to a random HPB bench. Neither mode uses --bench / --benches.
if (( RANDOM_BENCH == 0 && ITER_BENCH == 0 )); then
  [[ ${#BENCHES[@]} -eq 0 ]] && BENCHES=("${DEFAULT_BENCHES[@]}")
fi

# Split DEFAULT_BENCHES on HPB_SYNTH_START: ids < start are HPB, >= start are
# synth. Matches the Makefile's HPB_IDS / SYNTH_START partition so we don't
# duplicate that knowledge here.
SYNTH_BENCHES=()
HPB_BENCHES_POOL=()
for _b in "${DEFAULT_BENCHES[@]}"; do
  _id=${_b#bench}
  if [[ "$_id" =~ ^[0-9]+$ ]]; then
    if (( _id >= HPB_SYNTH_START )); then
      SYNTH_BENCHES+=("$_b")
    else
      HPB_BENCHES_POOL+=("$_b")
    fi
  fi
done
unset _b _id

if (( ITER_BENCH == 1 )); then
  if (( ${#SYNTH_BENCHES[@]} == 0 )); then
    echo "[iter-bench] no synth benches found (expected bench<N>_ser.riscv with N >= $HPB_SYNTH_START under $BENCH_BUILD_DIR); run 'make synth SYNTH_COUNT=<N> && make bench SYNTH_COUNT=<N>' first." >&2
    exit 2
  fi
  if (( ${#HPB_BENCHES_POOL[@]} == 0 )); then
    echo "[iter-bench] warning: no HPB benches (bench0..bench$((HPB_SYNTH_START-1))) discovered; configs past the synth pool will be skipped." >&2
  fi
fi

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

# S3 upload is optional: disable it automatically (with a warning) if the
# required tools aren't on PATH. The worker still logs per-config upload
# failures individually.
if (( SKIP_UPLOAD == 0 )); then
  if ! command -v zip >/dev/null; then
    echo "[upload] 'zip' not on PATH; disabling uploads." >&2
    SKIP_UPLOAD=1
  elif ! command -v aws >/dev/null; then
    echo "[upload] 'aws' CLI not on PATH; disabling uploads." >&2
    SKIP_UPLOAD=1
  fi
fi

# S3 build pull requires aws + unzip. Disable with a warning if either is
# missing, same pattern as --skip-upload so the sweep still makes forward
# progress (falling back to local builds).
if (( PULL_S3_BUILDS == 1 )); then
  if ! command -v unzip >/dev/null; then
    echo "[pull] 'unzip' not on PATH; disabling --pull-s3-builds." >&2
    PULL_S3_BUILDS=0
  elif ! command -v aws >/dev/null; then
    echo "[pull] 'aws' CLI not on PATH; disabling --pull-s3-builds." >&2
    PULL_S3_BUILDS=0
  fi
fi

# Serialize the sbt assembly step. With multiple workers each discovering a
# stale chipyard.jar, concurrent `sbt assembly` invocations overwrite the jar
# mid-read in the downstream `java -cp ... chipyard.Generator` call, producing
# "Could not find or load main class chipyard.Generator". Building it once
# upfront (even if already fresh) eliminates that race.
echo "[prebuild] ensuring $CLASSPATH_JAR is up to date..." >&2
make -C "$VERILATOR_DIR" CONFIG=ProtoAccelRocketConfig "$CLASSPATH_JAR" >/dev/null

# Parse ProtoAccelSweepConfigs.scala: one TSV record per class, fields are
# (cls, side, side_idx, short=N short=N ...). side_idx is a 1-based per-side
# counter so --iter-bench can pair config i-of-side with synth bench i
# independently for des and ser (otherwise ser rows, which follow all des
# rows in the plan, would exhaust the synth pool and always fall back to
# HPB). awk collapses the /** Sweep row ... */ comment and the following
# `class` declaration into one record.
PLAN_FILE=$(mktemp -t protoacc_sweep.XXXXXX)

# On Ctrl+C / SIGTERM, tear down before exiting. Sending one wave of signals to
# every descendant at once is unsafe: killing a worker bash before cc1plus /
# verilator / chipyard.Generator exits orphans those processes (they reparent
# to PID 1 and keep running). Kill deepest descendants first (bottom-up), then
# TERM → KILL with a short pause. Any orphans still tied to this repo get a
# final UID-scoped sweep (_pkill_chipyard_stragglers).
_kill_descendants_bottom_up() {
  local sig=$1 parent=$2 child
  for child in $(pgrep -P "$parent" 2>/dev/null || true); do
    [[ "$child" == "$$" ]] && continue
    _kill_descendants_bottom_up "$sig" "$child"
    kill -"$sig" "$child" 2>/dev/null || true
  done
}

# Processes no longer under $$ (e.g. orphaned make children). Match only when
# cmdline suggests this Chipyard checkout or Verilator sim dir; same UID only.
_pkill_chipyard_stragglers() {
  local sig=$1 u=${EUID:-$(id -u)} pid rest
  pkill -"$sig" -u "$u" -f 'chipyard\.Generator' 2>/dev/null || true
  while read -r pid rest; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    case "$rest" in
      *"$CHIPYARD_ROOT"*|"*$VERILATOR_DIR"*)
        case "$rest" in
          *verilator*|*cc1plus*|*"/cc1 "*|*simulator-chipyard*|*V[A-Za-z0-9_]*__ALL*)
            kill -"$sig" "$pid" 2>/dev/null || true
            ;;
        esac
        ;;
    esac
  done < <(pgrep -u "$u" -af . 2>/dev/null || true)
}
cleanup_tmp() {
  rm -f "$PLAN_FILE" "$PLAN_FILE".tmp 2>/dev/null || true
  [[ -n "${CSV_LOCK:-}" ]] && rm -f "$CSV_LOCK" 2>/dev/null || true
}
on_interrupt() {
  trap '' INT TERM  # ignore further signals while we tear down
  echo "" >&2
  echo "[abort] interrupt received — terminating sweep subprocesses..." >&2
  _kill_descendants_bottom_up TERM $$
  sleep 3
  _kill_descendants_bottom_up KILL $$
  _pkill_chipyard_stragglers TERM
  sleep 1
  _pkill_chipyard_stragglers KILL
  cleanup_tmp
  exit 130
}
trap on_interrupt INT TERM
trap cleanup_tmp EXIT

# Class-name scheme emitted by gen_protoacc_sweep_configs.py (updated):
#   ProtoAccelDesSweep<AcronymValuePairs>Config         e.g. ...SweepDC4DDL2...Config
#   ProtoAccelSerSweep<AcronymValuePairs>Config
#   ProtoAccelSweep<AcronymValuePairs>Config             (joint)
#   ProtoAcc{Des,Ser,}SweepDebug<AcronymValuePairs>Config (with --debug)
# Side is determined by the prefix; debug variants reuse the preceding
# non-debug row's parameter list.
gawk -v side_filter="$SIDE" '
  /\/\*\* Sweep row/ {
    # strip the /** ... */ wrapper, keep "ShortLabel=N, ShortLabel=N, ..."
    params = $0
    sub(/.*\):[[:space:]]*/, "", params)
    sub(/[[:space:]]*\*\/.*/, "", params)
    gsub(/,/, " ", params)
    next
  }
  /^class ProtoAccel[A-Za-z0-9]*Sweep[A-Za-z0-9]+Config/ {
    if (match($0, /class (ProtoAccel[A-Za-z0-9]*Sweep[A-Za-z0-9]+Config)/, a)) {
      cls = a[1]
      side = (cls ~ /DesSweep/) ? "des" :
             (cls ~ /SerSweep/) ? "ser" : "joint"
      if (side_filter == "both" || side == side_filter) {
        side_idx[side]++
        printf "%s\t%s\t%d\t%s\n", cls, side, side_idx[side], params
      }
      # intentionally do NOT reset params — a /** Debug printf variant of ... */
      # class immediately follows its non-debug sibling and should inherit its
      # parameter list.
    }
  }
' "$SWEEP_SCALA" > "$PLAN_FILE"

if (( LIMIT > 0 )); then
  head -n "$LIMIT" "$PLAN_FILE" > "$PLAN_FILE.tmp"
  mv "$PLAN_FILE.tmp" "$PLAN_FILE"
fi

TOTAL_CONFIGS=$(wc -l < "$PLAN_FILE")
(( TOTAL_CONFIGS == 0 )) && { echo "No ProtoAccel*Sweep*Config classes found." >&2; exit 1; }

if (( RANDOM_BENCH == 1 )); then
  echo "Sweep plan: $TOTAL_CONFIGS configs × 1 random bench (from DEFAULT_BENCHES) × op=$OP" >&2
elif (( ITER_BENCH == 1 )); then
  echo "Sweep plan: $TOTAL_CONFIGS configs × 1 bench (synth pool ${#SYNTH_BENCHES[@]}, HPB pool ${#HPB_BENCHES_POOL[@]}) × op=$OP" >&2
else
  echo "Sweep plan: $TOTAL_CONFIGS configs × ${#BENCHES[@]} bench(es) × op=$OP" >&2
fi

if (( DRY_RUN == 1 )); then
  while IFS=$'\t' read -r cls side side_idx _; do
    if (( RANDOM_BENCH == 1 )); then
      rb_idx=$((RANDOM % ${#DEFAULT_BENCHES[@]}))
      echo "  would run $cls [$side] bench=${DEFAULT_BENCHES[$rb_idx]} op=$OP"
    elif (( ITER_BENCH == 1 )); then
      # side_idx is 1-based per-side; shift to 0-based for the synth pool.
      dr_idx0=$((side_idx - 1))
      if (( dr_idx0 < ${#SYNTH_BENCHES[@]} )); then
        echo "  would run $cls [$side] bench=${SYNTH_BENCHES[$dr_idx0]} op=$OP (synth iter)"
      elif (( ${#HPB_BENCHES_POOL[@]} > 0 )); then
        hpb_idx=$((RANDOM % ${#HPB_BENCHES_POOL[@]}))
        echo "  would run $cls [$side] bench=${HPB_BENCHES_POOL[$hpb_idx]} op=$OP (hpb random)"
      else
        echo "  would SKIP $cls [$side]: synth pool exhausted and no HPB pool"
      fi
    else
      for bench in "${BENCHES[@]}"; do
        echo "  would run $cls [$side] bench=$bench op=$OP"
      done
    fi
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
export BENCH_BUILD_DIR OUTPUT OP JOBS BENCH_TIMEOUT BENCH_PARALLEL SKIP_BUILD KEEP_ARTIFACTS
export SKIP_UPLOAD PULL_S3_BUILDS S3_BUCKET S3_BUILD_KEY_PREFIX S3_SIM_KEY_PREFIX S3_BUILD_PREFIX S3_SIM_PREFIX AWS_PROFILE_NAME
export BENCHES_STR="${BENCHES[*]}"
export DEFAULT_BENCHES_STR="${DEFAULT_BENCHES[*]}"
export SYNTH_BENCHES_STR="${SYNTH_BENCHES[*]}"
export HPB_BENCHES_POOL_STR="${HPB_BENCHES_POOL[*]}"
export RANDOM_BENCH ITER_BENCH
export PARAM_KEYS_STR="${PARAM_KEYS[*]}"
export DEFAULTS_STR
DEFAULTS_STR=""
for k in "${PARAM_KEYS[@]}"; do DEFAULTS_STR+="$k=${DEFAULTS[$k]} "; done
export SHORT_TO_FULL_TXT
export CSV_LOCK="${OUTPUT}.lock"

worker() {
  set -u
  # $1 is the plan line. It carries a 1-based per-side index (side_idx) that
  # --iter-bench uses to pair config i-of-side with synth bench i, keeping
  # des and ser runs from sharing one counter (otherwise ser configs, which
  # follow all des configs in the plan, would always blow past the synth
  # pool size and fall back to random HPB).
  local line=$1
  local cls side side_idx params
  IFS=$'\t' read -r cls side side_idx params <<< "$line"

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
  if (( RANDOM_BENCH == 1 )); then
    local -a rb_benches=()
    read -ra rb_benches <<< "$DEFAULT_BENCHES_STR"
    bench=${rb_benches[$((RANDOM % ${#rb_benches[@]}))]}
    for op in "${ops[@]}"; do
      if ! grep -q "^${cls},${side},${bench},${op}," "$OUTPUT" 2>/dev/null; then
        pending+=("${bench}:${op}")
      fi
    done
  elif (( ITER_BENCH == 1 )); then
    # side_idx is 1-based per side; shift to 0-based. Config i-of-side gets
    # synth[i] if i < len(synth); otherwise draw a random HPB bench.
    local -a ib_synth=() ib_hpb=()
    read -ra ib_synth <<< "$SYNTH_BENCHES_STR"
    read -ra ib_hpb   <<< "$HPB_BENCHES_POOL_STR"
    local idx0=$((side_idx - 1))
    if (( idx0 < ${#ib_synth[@]} )); then
      bench=${ib_synth[$idx0]}
    elif (( ${#ib_hpb[@]} > 0 )); then
      bench=${ib_hpb[$((RANDOM % ${#ib_hpb[@]}))]}
    else
      echo "[iter-bench-skip] $cls: synth pool exhausted at idx=$idx0, no HPB pool" >&2
      upload_artifacts "$cls"
      maybe_cleanup "$cls"
      return 0
    fi
    for op in "${ops[@]}"; do
      if ! grep -q "^${cls},${side},${bench},${op}," "$OUTPUT" 2>/dev/null; then
        pending+=("${bench}:${op}")
      fi
    done
  else
    for op in "${ops[@]}"; do
      for bench in $BENCHES_STR; do
        if ! grep -q "^${cls},${side},${bench},${op}," "$OUTPUT" 2>/dev/null; then
          pending+=("${bench}:${op}")
        fi
      done
    done
  fi
  if (( ${#pending[@]} == 0 )); then
    echo "[skip-done] $cls" >&2
    # Still try to upload: a prior run may have produced CSV rows but
    # crashed before uploading, and head-object makes this idempotent.
    upload_artifacts "$cls"
    maybe_cleanup "$cls"
    return 0
  fi

  # Build the Verilator sim (or reuse if already present).
  local sim_bin="$VERILATOR_DIR/simulator-chipyard.harness-$cls"
  local build_wall_s=0 was_cached=0
  if [[ -x "$sim_bin" ]]; then
    was_cached=1
  elif (( PULL_S3_BUILDS == 1 )) && _s3_pull_build "$cls"; then
    # Treat S3-pulled builds as cached: build_wall_s stays 0 and
    # build_was_cached=1 in the CSV, matching how on-disk reuse is reported.
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

  # Param-value CSV suffix is identical for every (bench, op) of this config;
  # precompute it once so _run_bench_pair stays a pure per-bench function.
  local csv_suffix="" pk
  for pk in $PARAM_KEYS_STR; do csv_suffix+=",${combo[$pk]}"; done

  # Attribute the build cost to exactly one CSV row (first bench in the
  # pending list). Every other row records this_build_wall=0,
  # build_was_cached=1 so downstream readers see a single non-zero
  # build_wall_s per config and don't double-count. This is the same
  # bookkeeping the old serial loop used via build_attributed; doing it
  # up front lets us fan the loop out in parallel without coordinating
  # which child "won" the attribution.
  local -a bench_list=() op_list=() bw_list=() cached_list=()
  local i=0
  for p in "${pending[@]}"; do
    bench_list+=("${p%%:*}")
    op_list+=("${p##*:}")
    if (( i == 0 )); then
      bw_list+=("$build_wall_s")
      cached_list+=("$was_cached")
    else
      bw_list+=(0)
      cached_list+=(1)
    fi
    i=$((i+1))
  done

  if (( BENCH_PARALLEL > 1 )) && (( ${#pending[@]} > 1 )); then
    # Parallel path: fan out each bench into a background subshell, capped
    # at BENCH_PARALLEL concurrent. Needs bash 4.3+ for `wait -n`. Each
    # subshell uses its own rt0, its own log, and CSV appends are already
    # flock-serialized inside _run_bench_pair.
    echo "[run-p]    $cls × ${#pending[@]} benches (parallel=$BENCH_PARALLEL)" >&2
    local running=0 idx
    for (( idx=0; idx<${#bench_list[@]}; idx++ )); do
      while (( running >= BENCH_PARALLEL )); do
        wait -n 2>/dev/null || true
        running=$((running - 1))
      done
      _run_bench_pair "$cls" "$side" \
        "${bench_list[$idx]}" "${op_list[$idx]}" \
        "${bw_list[$idx]}" "${cached_list[$idx]}" \
        "$csv_suffix" &
      running=$((running + 1))
    done
    wait
  else
    local idx
    for (( idx=0; idx<${#bench_list[@]}; idx++ )); do
      _run_bench_pair "$cls" "$side" \
        "${bench_list[$idx]}" "${op_list[$idx]}" \
        "${bw_list[$idx]}" "${cached_list[$idx]}" \
        "$csv_suffix"
    done
  fi

  upload_artifacts "$cls"
  maybe_cleanup "$cls"
}

# Run one (bench, op) against an already-built simulator and append its CSV
# row. Safe to call concurrently for distinct BINARYs under the same CONFIG:
# %.run.fast in common.mk only writes to $output_dir/<BINARY>.log (unique per
# bench) and its one shared prereq — mkdir -p of the output dir — is
# idempotent. SIM_PREREQ is suppressed via BREAK_SIM_PREREQ=1 so make won't
# try to rebuild the sim.
#
# Args: cls side bench op build_wall_s cached_col csv_suffix
# csv_suffix is the leading "," + comma-joined param values for this config.
_run_bench_pair() {
  set -u
  local cls=$1 side=$2 bench=$3 op=$4
  local this_build_wall=$5 cached_col=$6 csv_suffix=$7

  local elf="$BENCH_BUILD_DIR/${bench}_${op}.riscv"
  if [[ ! -f $elf ]]; then
    echo "[elf-missing] $elf" >&2
    return 0
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
    return 0
  fi
  local summary
  summary=$(grep 'ACCEL_SUMMARY:' "$log" | tail -1 || true)
  if [[ -z $summary ]]; then
    echo "[no-summary] $log (wall=${wall}s)" >&2
    return 0
  fi

  local iters cycles bytes
  iters=$(grep -oP 'iters=\K[0-9]+'        <<<"$summary" || echo 0)
  cycles=$(grep -oP 'total_cycles=\K[0-9]+' <<<"$summary" || echo 0)
  bytes=$(grep -oP 'total_bytes=\K[0-9]+'   <<<"$summary" || echo 0)

  local tput=0
  if (( cycles > 0 )); then
    tput=$(awk -v b="$bytes" -v c="$cycles" 'BEGIN{printf "%.6f", b*1e9/c}')
  fi

  local row="$cls,$side,$bench,$op,$iters,$cycles,$bytes,$tput,$wall,$this_build_wall,$cached_col$csv_suffix"

  (
    flock -w 60 9
    echo "$row" >> "$OUTPUT"
  ) 9>"$CSV_LOCK"

  echo "[done]     $cls × ${bench}_${op}: ${wall}s, ${bytes}B / ${cycles}cyc" >&2
}

# Zip build artifacts (generated-src + simulator binary) and simulation
# output, then upload each zip to its S3 prefix. Idempotent: a pre-existing
# object at the destination key is left alone (the sweep is resume-friendly
# and we don't want to re-upload identical builds). Any failure only logs;
# it doesn't abort the worker, since losing an upload shouldn't void the
# CSV row we just committed.
upload_artifacts() {
  (( SKIP_UPLOAD == 1 )) && return 0
  local cls=$1
  local gen_src="$VERILATOR_GENERATED_SRC_ROOT/chipyard.harness.TestHarness.$cls"
  local sim_bin="$VERILATOR_DIR/simulator-chipyard.harness-$cls"
  local sim_bin_debug="$VERILATOR_DIR/simulator-chipyard.harness-$cls.debug"
  local sim_out="$VERILATOR_OUTPUT_ROOT/chipyard.harness.TestHarness.$cls"

  _s3_upload_build "$cls" "$gen_src" "$sim_bin" "$sim_bin_debug"
  _s3_upload_sim   "$cls" "$sim_out"
}

# $1 cls, $2 generated-src dir, $3 sim bin, $4 sim bin (debug variant)
_s3_upload_build() {
  local cls=$1 gen_src=$2 sim_bin=$3 sim_bin_debug=$4
  local key="$S3_BUILD_KEY_PREFIX/$cls.zip"

  # Need at least one of the artifacts to exist. If neither does, this is
  # almost certainly a resumed sweep where --keep-artifacts wasn't set on
  # the prior run — nothing to do.
  if [[ ! -d $gen_src && ! -x $sim_bin && ! -f $sim_bin_debug ]]; then
    return 0
  fi

  if aws --profile "$AWS_PROFILE_NAME" s3api head-object \
      --bucket "$S3_BUCKET" --key "$key" >/dev/null 2>&1; then
    echo "[upload-skip] $S3_BUILD_PREFIX/$cls.zip already exists" >&2
    return 0
  fi

  local zip_tmp
  zip_tmp=$(mktemp -t "build_${cls}.XXXXXX.zip")
  rm -f "$zip_tmp"  # zip wants to create, not append
  local zip_log
  zip_log=$(mktemp -t "build_${cls}.XXXXXX.ziplog")

  # Run zip from a stable cwd so archive paths are predictable (relative to
  # $VERILATOR_DIR).
  local -a zip_inputs=()
  [[ -d $gen_src       ]] && zip_inputs+=("generated-src/chipyard.harness.TestHarness.$cls")
  [[ -x $sim_bin       ]] && zip_inputs+=("simulator-chipyard.harness-$cls")
  [[ -f $sim_bin_debug ]] && zip_inputs+=("simulator-chipyard.harness-$cls.debug")

  if (( ${#zip_inputs[@]} == 0 )); then
    rm -f "$zip_tmp" "$zip_log"
    return 0
  fi

  if ! (cd "$VERILATOR_DIR" && zip -r -q "$zip_tmp" "${zip_inputs[@]}") \
      >"$zip_log" 2>&1; then
    echo "[upload-fail] $cls zip (build): see tail" >&2
    tail -c 400 "$zip_log" >&2
    rm -f "$zip_tmp" "$zip_log"
    return 0
  fi

  if aws --profile "$AWS_PROFILE_NAME" s3 cp --only-show-errors \
      "$zip_tmp" "$S3_BUILD_PREFIX/$cls.zip" >"$zip_log" 2>&1; then
    local sz
    sz=$(stat -c %s "$zip_tmp" 2>/dev/null || echo ?)
    echo "[upload-ok] $S3_BUILD_PREFIX/$cls.zip (${sz}B)" >&2
  else
    echo "[upload-fail] $S3_BUILD_PREFIX/$cls.zip: see tail" >&2
    tail -c 400 "$zip_log" >&2
  fi
  rm -f "$zip_tmp" "$zip_log"
}

# Reverse of _s3_upload_build: try to fetch s3://.../<S3_BUILD_KEY_PREFIX>/<cls>.zip
# and unzip it under $VERILATOR_DIR so that generated-src/chipyard.harness.TestHarness.<cls>
# and simulator-chipyard.harness-<cls>[.debug] reappear in the exact layout a
# local `make` would produce. Returns 0 on a usable restore (simulator binary
# present and executable), nonzero otherwise — caller falls back to a local
# build on failure.
#
# Idempotent with the on-disk check in the caller: if a fresh sim_bin already
# exists the caller short-circuits before ever calling us.
_s3_pull_build() {
  local cls=$1
  local key="$S3_BUILD_KEY_PREFIX/$cls.zip"
  local sim_bin="$VERILATOR_DIR/simulator-chipyard.harness-$cls"

  # Cheap existence probe first — avoids spawning a download on misses.
  if ! aws --profile "$AWS_PROFILE_NAME" s3api head-object \
      --bucket "$S3_BUCKET" --key "$key" >/dev/null 2>&1; then
    echo "[pull-miss] $S3_BUILD_PREFIX/$cls.zip" >&2
    return 1
  fi

  local zip_tmp pull_log
  zip_tmp=$(mktemp -t "pull_${cls}.XXXXXX.zip")
  pull_log=$(mktemp -t "pull_${cls}.XXXXXX.log")

  if ! aws --profile "$AWS_PROFILE_NAME" s3 cp --only-show-errors \
      "$S3_BUILD_PREFIX/$cls.zip" "$zip_tmp" >"$pull_log" 2>&1; then
    echo "[pull-fail] $S3_BUILD_PREFIX/$cls.zip download: see tail" >&2
    tail -c 400 "$pull_log" >&2
    rm -f "$zip_tmp" "$pull_log"
    return 1
  fi

  # Matching _s3_upload_build: archive paths are relative to $VERILATOR_DIR,
  # so unzip from that cwd restores them to the expected locations. -o
  # overwrites any stale partial artifacts rather than prompting.
  if ! (cd "$VERILATOR_DIR" && unzip -o -q "$zip_tmp") >"$pull_log" 2>&1; then
    echo "[pull-fail] $cls unzip: see tail" >&2
    tail -c 400 "$pull_log" >&2
    rm -f "$zip_tmp" "$pull_log"
    return 1
  fi

  rm -f "$zip_tmp" "$pull_log"

  # zip archives don't preserve +x on all filesystems; re-mark the simulator
  # executable so the subsequent run-binary-fast target accepts it.
  [[ -f $sim_bin ]] && chmod +x "$sim_bin" 2>/dev/null || true
  [[ -f "${sim_bin}.debug" ]] && chmod +x "${sim_bin}.debug" 2>/dev/null || true

  if [[ ! -x $sim_bin ]]; then
    echo "[pull-fail] $cls: simulator binary missing after unzip" >&2
    return 1
  fi

  local sz
  sz=$(stat -c %s "$sim_bin" 2>/dev/null || echo ?)
  echo "[pull-ok]  $S3_BUILD_PREFIX/$cls.zip → sim=${sz}B" >&2
  return 0
}

# Remove local simulation logs after S3 has (or already had) the zip.
# Honors KEEP_ARTIFACTS like maybe_cleanup.
_cleanup_sim_out_after_s3_ok() {
  local cls=$1 sim_out=$2
  (( KEEP_ARTIFACTS == 1 )) && return 0
  [[ -d $sim_out ]] || return 0
  rm -rf -- "$sim_out"
  echo "[cleanup-sim-out] $cls" >&2
}

# $1 cls, $2 output dir
_s3_upload_sim() {
  local cls=$1 sim_out=$2
  local key="$S3_SIM_KEY_PREFIX/$cls.zip"

  if [[ ! -d $sim_out ]]; then
    return 0
  fi

  if aws --profile "$AWS_PROFILE_NAME" s3api head-object \
      --bucket "$S3_BUCKET" --key "$key" >/dev/null 2>&1; then
    echo "[upload-skip] $S3_SIM_PREFIX/$cls.zip already exists" >&2
    _cleanup_sim_out_after_s3_ok "$cls" "$sim_out"
    return 0
  fi

  local zip_tmp zip_log
  zip_tmp=$(mktemp -t "sim_${cls}.XXXXXX.zip")
  rm -f "$zip_tmp"
  zip_log=$(mktemp -t "sim_${cls}.XXXXXX.ziplog")

  if ! (cd "$VERILATOR_OUTPUT_ROOT" && \
        zip -r -q "$zip_tmp" "chipyard.harness.TestHarness.$cls") \
      >"$zip_log" 2>&1; then
    echo "[upload-fail] $cls zip (sim): see tail" >&2
    tail -c 400 "$zip_log" >&2
    rm -f "$zip_tmp" "$zip_log"
    return 0
  fi

  if aws --profile "$AWS_PROFILE_NAME" s3 cp --only-show-errors \
      "$zip_tmp" "$S3_SIM_PREFIX/$cls.zip" >"$zip_log" 2>&1; then
    local sz
    sz=$(stat -c %s "$zip_tmp" 2>/dev/null || echo ?)
    echo "[upload-ok] $S3_SIM_PREFIX/$cls.zip (${sz}B)" >&2
    _cleanup_sim_out_after_s3_ok "$cls" "$sim_out"
  else
    echo "[upload-fail] $S3_SIM_PREFIX/$cls.zip: see tail" >&2
    tail -c 400 "$zip_log" >&2
  fi
  rm -f "$zip_tmp" "$zip_log"
}

maybe_cleanup() {
  (( KEEP_ARTIFACTS == 1 )) && return 0
  local cls=$1
  rm -rf -- "$VERILATOR_GENERATED_SRC_ROOT/chipyard.harness.TestHarness.$cls"
  rm -f  -- "$VERILATOR_DIR/simulator-chipyard.harness-$cls" \
            "$VERILATOR_DIR/simulator-chipyard.harness-$cls.debug"
  echo "[cleanup]  $cls" >&2
}

export -f worker _run_bench_pair upload_artifacts _s3_upload_build _s3_upload_sim \
  _s3_pull_build maybe_cleanup _cleanup_sim_out_after_s3_ok

echo "[parallel] $WORKERS workers, make -j$JOBS per build, bench-parallel=$BENCH_PARALLEL" >&2
parallel -j "$WORKERS" --line-buffer --halt soon,fail=1 \
  --termseq INT,1000,TERM,2000,KILL,25 \
  worker '{}' :::: "$PLAN_FILE"

echo "Wrote $OUTPUT" >&2
