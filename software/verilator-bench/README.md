# ProtoAcc Verilator Benchmarks

Bare-metal RISC-V workloads + sweep harness that exercise the ProtoAcc
accelerator under Chipyard's Verilator simulator. Each bench ELF emits
`ACCEL_ITER` / `ACCEL_SUMMARY` lines carrying `mcycle` deltas, byte counts,
and iteration counts so throughput can be derived from the bench log.

The per-config sweep driver ([run_sweep.sh](run_sweep.sh)) iterates every
emitted `ProtoAccelDesSweep{acronyms}Config` /
`ProtoAccelSerSweep{acronyms}Config` class (the class name encodes every
varied parameter as `<acronym><value>` pairs — see the
`DES_ACRONYM_LABEL_BY_KEY` / `SER_ACRONYM_LABEL_BY_KEY` tables in
`gen_protoacc_sweep_configs.py`; only the sweep's active side is encoded,
so CSV rows from separate des/ser runs join cleanly on `config_name`),
builds a Verilator simulator for each, runs all six HPB benches against it,
appends one row per `(config, bench, op)` to a CSV, and reclaims the
per-config `generated-src` tree + simulator binary between configs.

## Directory layout

```
Makefile                       # Bare-metal build (riscv64-unknown-elf-gcc + htif_nano.specs)
proto_to_accel.py              # .proto + runtime data → gen/bench<N>_{descriptors.h,data.c}
                               #   HPB: reads sibling benchmark.inc
                               #   SYNTH: consumes --runtime-lengths JSON from gen_synth_proto
gen_synth_proto.py             # Combines 5 random fleetbench Message<N>.proto files per
                               #   synthetic bench + extracts per-field string lengths via
                               #   lynx/fleetbench_runtime.py (output under gen/synth/). Parallel.
gen_all_benches.py             # Batch driver: runs proto_to_accel.py over many benches in
                               #   one process pool (`make gen_batch`). Avoids per-bench
                               #   Python startup overhead at scale.
gen_protoacc_sweep_configs.py  # Emits ProtoAccelSweepConfigs.scala (des/ser/joint)
run_sweep.sh                   # Parallel sweep driver (auto-discovers bench ids from build/)
parse_results.py               # Aggregate bench logs → benchmark_results.json
rocc.h                         # RoCC custom-2/custom-3 inline-asm macros
accel_rocc.{h,c}               # AccelSetup + static serializer/deserializer regions
bench_common.h                 # read_mcycle, print_iter/summary
bench_hpb_{ser,des}.c          # HPB/synth ser/des bench drivers (one ELF per bench via -D)
bench_tiny_{ser,des}.c         # Minimal hand-crafted validation benches
bench_repro.c                  # 14 minimal reproducers for state-machine bugs
bench_isolate.c                # Per-message isolator (debug tool)
gen/                           # Generated descriptors + data (.gitignored)
  bench[0-5]_descriptors.h     #   emitted by `make gen` (HPB)
  bench[0-5]_data.c            #
  bench[6-N]_descriptors.h     #   emitted by `make gen SYNTH_COUNT=N` (synthetic)
  bench[6-N]_data.c            #
  synth/bench<N>/              # Source artifacts for synthetic benches:
    benchmark.proto            #     combined fleetbench schemas
    runtime_lengths.json       #     per-field string/bytes lengths from access_message<N>.cc
    manifest.json              #     {seed, chosen_messages, …}
  synth/synth_manifest.csv     #   index over all synthetic benches
build/                         # Build artifacts (.gitignored)
  bench<N>_{ser,des}.riscv     #   emitted by `make bench` (HPB + synth)
```

## Prerequisites

Layout inside Chipyard: this directory lives at
`generators/protoacc/software/verilator-bench`, next to the **lynx** submodule
at `generators/protoacc/software/lynx` (`Makefile` / `proto_to_accel.py` use
that sibling path; override with `LYNX_ROOT` if your checkout differs).

Environment variables (optional overrides):

| Variable | Purpose |
|----------|---------|
| `CHIPYARD_ROOT` | Chipyard repo root. `run_sweep.sh` and `parse_results.py` default it by walking up from this directory. |
| `CHIPYARD` | Alias read by `parse_results.py` if `CHIPYARD_ROOT` is unset. |
| `LYNX_ROOT` | Root of the Lynx repo when not using `../lynx` next to this folder. |

Source the Chipyard env (puts `riscv64-unknown-elf-gcc`, Verilator, and the
sbt/firtool toolchain on PATH):

```bash
source "${CHIPYARD_ROOT:?}/env.sh"
```

For the sweep, `run_sweep.sh` also needs GNU parallel — install via
`conda install -p $CONDA_PREFIX -c conda-forge parallel` if missing.

## From-scratch workflow

### 1. Generate bench descriptors + data

Reads each HPB `.proto` + `.inc`, samples per-field string/bytes lengths from
the `.inc` runtime data, and emits `gen/bench<N>_{descriptors.h,data.c}`.

```bash
cd generators/protoacc/software/verilator-bench   # from Chipyard root
make gen
```

Re-run only when a `.proto` schema changes, the generator changes, or you
want a different RNG seed / cap (see
`proto_to_accel.py --help` for `--max-nested-depth`, `--max-string-len`,
`--seed`).

#### 1b. (Optional) Add synthetic fleetbench-derived workloads

HPB only ships 6 distinct schemas, which is too few for an ML model to
generalize to out-of-distribution schemas. To expand the catalog, combine
random subsets of the 20 fleetbench `Message<N>.proto` schemas into
additional `bench<N>` workloads (ids 6..N, so HPB's 0..5 stay intact):

```bash
# Generate 20 synthetic benches (5 fleetbench messages each), then the
# usual descriptor + data .c/.h pair for every id:
make synth     SYNTH_COUNT=20 SYNTH_SEED=42   # parallel, ~0.3s at 20, ~0.9s at 4000
make gen_batch SYNTH_COUNT=20 SYNTH_SEED=42   # parallel, ~10s at 20, ~3m at 4000
```

Use `make gen` (per-bench pattern rules with `-j$(nproc)`) instead of
`make gen_batch` when you only want to re-emit a handful of changed benches —
the pattern rules track per-bench dependencies but pay ~40 ms Python startup
each, so they're only faster for small rebuilds. `gen_batch` stays in one
Python process and farms each bench out to a worker pool (`--workers`
defaults to `nproc/2` inside `gen_all_benches.py`); it always rebuilds
everything in `BENCH_IDS`.

`gen_synth_proto.py` writes each synthetic bench to
`gen/synth/bench<N>/{benchmark.proto,runtime_lengths.json,manifest.json}`
and a summary `gen/synth/synth_manifest.csv`. The runtime JSON is parsed
from fleetbench's `access_message<N>.cc` by `lynx/fleetbench_runtime.py`
and fed to `proto_to_accel.py` via `--runtime-lengths`. Override knobs via
the Makefile variables: `SYNTH_N_MESSAGES` (default 5),
`SYNTH_START` (default 6), `SYNTH_DIR` (default `gen/synth`).

To wire the new benches into the analytical-features pipeline, append them
to the verilator-bench analysis JSON and re-run the feature extractor:

```bash
cd ../lynx/analytical_model
python3 analyze_synth.py --synth-root ../../verilator-bench/gen/synth
python3 extract_features.py   # updates extracted_features.json in place
```

### 2. Build the bench ELFs

```bash
make bench       # just the 12 HPB ELFs (build/bench[0-5]_{ser,des}.riscv) ← what run_sweep.sh needs
make             # everything: HPB + tiny validation + 14 repro debug cases
make clean       # wipe build/ and gen/
```

### 3. Emit sweep configs

`gen_protoacc_sweep_configs.py` writes `ProtoAccelSweepConfigs.scala` into
`generators/chipyard/src/main/scala/config/`. Each emitted class name
encodes the **active** side's parameter values as `<acronym><value>` pairs
(acronyms come from `DES_ACRONYM_LABEL_BY_KEY` /
`SER_ACRONYM_LABEL_BY_KEY`). For example, a des-side sample with
`des_cr_rocc_commands=4, des_dth_fd_reqs=8, …` emits
`ProtoAccelDesSweepDC4DDFQ8…Config`, and a ser-side sample with
`ser_field_handlers=6, ser_cr_rocc_commands=4, …` emits
`ProtoAccelSerSweepSF6SC4…Config`. Because the inactive side stays at
defaults, two independent `--emit des` and `--emit ser` runs can be
concatenated into one training CSV and joined on `config_name` without
collision. Debug variants (`--debug`) get a `Debug` infix:
`ProtoAccelDesSweepDebug…Config`.

```bash
# 32 random samples per side (64 total configs), seed 42:
python3 gen_protoacc_sweep_configs.py --emit both -t random -n 32 -s 42

# Or a one-factor-at-a-time sweep over active axes only:
python3 gen_protoacc_sweep_configs.py --emit both -t ofat

# See --help for tweak / joint / default / ser-only / des-only emit modes.
```

Each emitted class is preceded by a `/** Sweep row N/M (random): Key=val, … */`
comment, which `run_sweep.sh` parses to recover the parameter vector for
each CSV row.

### 4. Run the sweep

Defaults match a many-core machine: one worker per CPU, single-threaded
Verilator builds inside each worker (`make -j1`), and per-config cleanup of
`sims/verilator/generated-src/chipyard.harness.TestHarness.<cls>` +
`sims/verilator/simulator-chipyard.harness-<cls>` after the config's benches
finish. Cleanup keeps disk usage bounded across hundreds of configs.

```bash
bash run_sweep.sh --output sweep.csv
```

What this does per config, in parallel:
1. Build the Verilator simulator (`make CONFIG=<cls>`, cached between runs
   only if `--keep-artifacts`).
2. Run every `bench<N>_{op}.riscv` (auto-discovered from `build/`: all HPB
   ids plus any synth ids produced via `make synth SYNTH_COUNT=…`) where
   `op` matches the config's side (des-side configs → des benches, ser-side →
   ser) via `make run-binary-fast BREAK_SIM_PREREQ=1 LOADMEM=1`.
3. Parse the last `ACCEL_SUMMARY:` line from
   `sims/verilator/output/chipyard.harness.TestHarness.<cls>/bench<N>_{op}.log`.
4. Append one CSV row per bench under a file lock.
5. Zip + upload the per-config artifacts to S3 (idempotent; skipped if the
   object already exists or if `--skip-upload` / `zip` / `aws` are missing):
   - `generated-src/chipyard.harness.TestHarness.<cls>` + `simulator-chipyard.harness-<cls>`
     → `s3://ronitnag04-lynx/verilator_build_files/<cls>.zip`
   - `output/chipyard.harness.TestHarness.<cls>/` → `s3://ronitnag04-lynx/simulation_files/<cls>.zip`
6. Delete the per-config `generated-src` tree and simulator binary.

Selected useful flags (see `bash run_sweep.sh --help`):

| Flag                 | Purpose                                                      |
|----------------------|--------------------------------------------------------------|
| `--benches b0 b1 …`  | Restrict to a subset of benches (default: every `bench<N>_ser.riscv` present in `build/`, HPB + synth). |
| `--op {ser,des,both}`| Override per-config op selection.                            |
| `--side {des,ser,both}` | Filter which sample classes run.                          |
| `--limit N`          | Only the first N matching configs (smoke test).              |
| `--workers N`        | Parallel configs (default: `nproc`).                         |
| `--jobs N`           | `make -j` per Verilator build (default: 1).                  |
| `--keep-artifacts`   | Skip cleanup, useful when debugging a single config.         |
| `--skip-upload`      | Skip the per-config S3 upload (default: enabled).            |
| `--dry-run`          | Print the plan without building.                             |

**Resume**: re-running with the same `--output` skips any
`(config_name, bench, op)` already present in the CSV. An interrupted sweep
picks up where it left off with no wasted Verilator build cost.

### 5. Join with analytical features → training dataset

This lives in the Lynx repo, not here:

```bash
python3 ../lynx/build_training_dataset.py \
    --sweep-csv /tmp/sweep.csv \
    --output-csv /tmp/training_data.csv
```

See [../lynx/README.md](../lynx/README.md) for the downstream training pipeline.

## CSV schema (sweep output)

```
config_name, side, bench, op, iters, cycles, bytes, throughput_bytes_per_sec,
wall_s, build_wall_s, build_was_cached,
des_top_descriptor_reqs, des_top_memloader_reqs, … (9 des_* knobs),
ser_field_handlers, ser_cr_rocc_commands, … (10 ser_* knobs)
```

`throughput_bytes_per_sec` assumes the Verilator config's nominal 1 GHz
clock; rescale if your config targets a different rate.

Inactive-side knobs are recorded at their generator defaults (see
`DES_DEFAULTS` / `SER_DEFAULTS` in
[gen_protoacc_sweep_configs.py](gen_protoacc_sweep_configs.py)), so every
row has a complete 19-column parameter vector.

## Single-bench smoke test (no sweep)

For debugging a single bench on the baseline config without going through
`run_sweep.sh`:

```bash
# Build the baseline sim (cached after first time):
cd sims/verilator    # from Chipyard root
make CONFIG=ProtoAccelRocketBaseConfig -j$(nproc)

# Run bench1 serialize on it:
BREAK_SIM_PREREQ=1 LOADMEM=1 make CONFIG=ProtoAccelRocketBaseConfig run-binary-fast \
    BINARY="$(pwd)/../../generators/protoacc/software/verilator-bench/build/bench1_ser.riscv"

# Aggregate bench logs in the baseline output dir → JSON:
cd ../../generators/protoacc/software/verilator-bench
python3 parse_results.py --output benchmark_results.json
```

`parse_results.py` is the quick way to produce the `benchmark_results.json`
shape `sample_protoacc_model/protobuf_model.py` expects — use the sweep for
ML training data, this for a single-config sanity check.

## Status + known caveats

| Bench                      | Status  | Notes |
|----------------------------|---------|-------|
| `bench[0-5]_{ser,des}.riscv` | WORKS  | Full HPB multi-field workloads; primitives + strings/bytes (1024 B cap) + nested submessages (depth ≤ 5). |
| `bench_tiny_{ser,des}.riscv` | WORKS  | Hand-crafted `{int32, string}` — validation baseline. |
| `bench_repro_c*.riscv`       | Debug  | Minimal reproducers; cases 11/13/14 deliberately fail (packed-buffer string bug). |
| `bench_isolate_bN_mI.riscv`  | Debug  | Per-message isolator. `make isolate ISOLATE_BENCH=<N>`. |

See [STATUS.md](STATUS.md) for measured throughput, paper-target comparison,
ABI details, and the roadmap.

## Generator notes (`proto_to_accel.py`)

Parses `.proto` + `.inc` via [../lynx/analytical_model/protobuf_analyzer.py](../lynx/analytical_model/protobuf_analyzer.py).
Emits per top-level message M:

```
M_INSTANCE[obj_size]             # top cpp_obj (primitives + zero slots)
M_NESTED_POOL[nested_bytes]      # concat of nested cpp_objs, 16-B aligned
M_STRING_HEADERS[K*32]           # K ArenaStringPtr {char*, size_t} blocks, 32-B aligned
M_STRING_PAYLOADS[payload_bytes] # concat of all string payloads, 16-B aligned + cushion
M_NESTED_SPECS[N*3]              # {parent_instance, parent_slot_offset, nested_offset}
M_STRING_SPECS[K*5]              # {owner_instance, slot_offset, hdr_idx, payload_offset, length}
```

At runtime, `fixup_nested()` + `fixup_strings()` walk the spec tables and
patch parent cpp_obj slots with absolute addresses of the nested/string
records. Why three pools (not one): packing strings or nested instances
inside the top cpp_obj buffer triggers a hardware fault in the ProtoAcc
serializer — see the `project_hpb_ser_multifield_bug.md` memory note.

Coverage: primitive scalars (int32/64, uint, sint, sfixed, fixed, float,
double, bool), enums (as int32), string/bytes, nested messages up to depth
5. **Skipped**: `repeated` (3% of HPB fields), `oneof`, `map`, groups.
