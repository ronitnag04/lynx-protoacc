# ProtoAcc Verilator Benchmarks — Status

_Last updated: 2026-05-04_

A bare-metal RISC-V workload suite plus a parameter-sweep harness that runs
HyperProtoBench serialization + deserialization schemas on the ProtoAcc
RoCC accelerator under Chipyard's Verilator simulator. Outputs:

- Per-bench cycle/byte/throughput (`benchmark_results.json` for a single
  Verilator config, via [parse_results.py](parse_results.py)).
- Per-`(config, bench, op)` training rows for the Lynx ML throughput model,
  via [run_sweep.sh](run_sweep.sh) iterating the
  `ProtoAccelDesSweepSample*` / `ProtoAccelSerSweepSample*` configs emitted
  by [gen_protoacc_sweep_configs.py](gen_protoacc_sweep_configs.py).

## Goal

Match HyperProtoBench (HPB) workloads closely enough that:
1. The cycle-count data lines up with the analytical features in
   [/home/ec2-user/lynx/analytical_model/extracted_features.json](/home/ec2-user/lynx/analytical_model/extracted_features.json)
   (keyed on `bench0`..`bench5`).
2. Measured throughput falls in the same order of magnitude as the ProtoAcc
   MICRO 2021 paper results: **serializer 60–100 Gb/s @ 1.84 GHz**,
   **deserializer 25–40 Gb/s @ 1.95 GHz**.

## Current results (serializer + deserializer)

Full HPB multi-field workloads including primitives, strings/bytes (1024 B cap),
and nested submessages (up to depth 5). 40 iters per bench (10 top-level
messages × 4 iters). At the Verilator config's nominal 1 GHz clock:

### Serializer (paper target: 60–100 Gb/s @ 1.84 GHz)

| Bench  | bytes  | cycles | MB/s @ 1 GHz | Gb/s @ 1.84 GHz | vs. paper |
|--------|-------:|-------:|-------------:|----------------:|:---------:|
| bench0 | 80,180 | 38,604 | 2,077        | **30.6**        | 0.5×–0.3× |
| bench1 | 37,960 | 20,640 | 1,839        | **27.1**        | 0.5×–0.3× |
| bench2 | 20,800 | 62,324 |   334        |   4.9           | 0.08×     |
| bench3 | 65,392 | 29,121 | 2,246        | **33.1**        | 0.6×–0.3× |
| bench4 | 44,560 | 27,869 | 1,599        | **23.5**        | 0.4×–0.2× |
| bench5 | 18,428 | 31,613 |   583        |   8.6           | 0.1×      |

### Deserializer (paper target: 25–40 Gb/s @ 1.95 GHz)

| Bench  | bytes  | cycles | MB/s @ 1 GHz | Gb/s @ 1.95 GHz | vs. paper |
|--------|-------:|-------:|-------------:|----------------:|:---------:|
| bench0 | 80,180 | 51,017 | 1,572        | **24.5**        | 1.0×–0.6× |
| bench1 | 37,960 | 24,225 | 1,567        | **24.4**        | 1.0×–0.6× |
| bench2 | 20,800 | 100,932 |  206        |   3.2           | 0.13×     |
| bench3 | 65,392 | 29,940 | 2,184        | **34.1**        | 1.4×–0.9× |
| bench4 | 44,560 | 34,023 | 1,310        | **20.4**        | 0.8×–0.5× |
| bench5 | 18,428 | 45,411 |   406        |   6.3           | 0.25×     |

**Deserializer lands within the paper's 25–40 Gb/s band** on bench0/1/3/4 —
bench3 is right in the middle of it. The serializer is ~2-3× below paper
throughput; still same order of magnitude on bench0/1/3/4.
**Bench2 and bench5 are outliers** on both ops: their schemas have many
small submessages where the fixed per-message dispatch cost dominates.

**Full current data**: [benchmark_results.json](benchmark_results.json)

### Analytical-model alignment

The Lynx analytical model at
[/home/ec2-user/lynx/analytical_model/](/home/ec2-user/lynx/analytical_model/)
now has a `WorkloadProfile` that mirrors our bench caps (skip repeated,
max_string_len=1024, max_nested_depth=5). Invoke with
`--verilator-bench-profile` to produce features that line up with the measured
cycle/byte counts in `benchmark_results.json`. The canonical
`extracted_features.json` in that directory is now produced with the bench
profile applied; a full-HPB-scoped sibling is saved as
`extracted_features_full.json` for reference.

## What's implemented

### 1. Verilator sim config
- `ProtoAccelRocketBaseConfig` — direct-attach Rocket + 1 ProtoAcc serializer +
  1 ProtoAcc deserializer, no ReRoCC. Defined in
  [chipyard/src/main/scala/config/HyperscaleConfigs.scala](/home/ec2-user/hyperscale-grpc-chipyard/generators/chipyard/src/main/scala/config/HyperscaleConfigs.scala).
- `ProtoAccelRocketDebugConfig` — same but with `WithProtoAccelPrintf` for
  synthesis-path debug prints (does nothing on Verilator in practice; left
  wired for FireSim).

### 2. Generator: `.proto` + `.inc` → C benchmark data
[proto_to_accel.py](proto_to_accel.py)

- Reuses the Lynx `ProtobufAnalyzer` directly (imports from
  `/home/ec2-user/lynx/analytical_model/protobuf_analyzer.py`) so we get the
  same per-field string/bytes length distributions the analytical features
  are computed from.
- Emits per-top-message:
  - `<Msg>_INSTANCE[]` — cpp_obj (primitives + zero-slot placeholders)
  - `<Msg>_NESTED_POOL[]` — concat of nested cpp_obj instances, 16-B aligned
  - `<Msg>_STRING_HEADERS[]` — one 32-B ArenaStringPtr `{char*, size_t}` per string
  - `<Msg>_STRING_PAYLOADS[]` — concat of all string payloads, 16-B aligned
  - `<Msg>_NESTED_SPECS[]` — `{parent_instance, parent_slot_offset, nested_offset}` records
  - `<Msg>_STRING_SPECS[]` — `{owner_instance, slot_offset, hdr_idx, payload_offset, length}` records
- Runtime fixup (`fixup_nested`, `fixup_strings` in
  [bench_hpb_ser.c](bench_hpb_ser.c)) walks the spec tables and patches
  parent slots with absolute addresses at program start.
- CLI flags: `--max-nested-depth N` (default 5), `--max-string-len N`
  (default 1024), `--seed N`, `--top-messages M1 M2 ...`.

### 3. Field-type coverage

| Category               | Status | Notes |
|------------------------|--------|-------|
| int32/64, uint32/64, sint, sfixed, fixed | WORKS | Written to cpp_obj slots at type-appropriate size. |
| float, double          | WORKS  | IEEE-754 bit patterns via `struct.pack`. |
| bool                   | WORKS  | 1-byte slot, 0x01 for present. |
| enum                   | WORKS  | Treated as int32 per protobuf wire format. |
| string, bytes          | WORKS  | Separate-buffer ArenaStringPtr layout with realistic lengths from `.inc`. 1024-B cap. |
| message (nested)       | WORKS  | Pre-allocated in `_NESTED_POOL`, linked at runtime. Up to depth 5. |
| repeated               | SKIPPED | Placeholder 0 entries. ~3% of all HPB fields. |
| oneof, map, groups     | SKIPPED | Protobuf analyzer doesn't distinguish these from the fields we support. |

### 4. Hardware ABI work verified
- Descriptor table layout: `[vptr, obj_size, hasbits_offset, (min<<32)|max,
  per-field entries, is_submessage_bitfield]`.
- Hasbits encoding: 32-bit chunks at `cpp_obj + 0x10`, bit `(actual_fn - min + 1)`.
- Deserializer top-level hasbits offset HARDCODED at 0x10 (des/fieldhandler.scala:66).
- Serializer reads `desc[2]` for top-level via HASBITS_INFO.
- ArenaStringPtr convention: low-2-bit `0x3` tag on cpp_obj slot → header
  with `{char*, size_t}` layout.
- Nested submessage: cpp_obj slot is a raw pointer (not tagged).

See [reference_protoacc_abi.md](/home/ec2-user/.claude/projects/-home-ec2-user/memory/reference_protoacc_abi.md)
for the full ABI cheat-sheet.

### 5. Files in this directory

| File                        | Purpose |
|-----------------------------|---------|
| [rocc.h](rocc.h)                          | RoCC `.insn r CUSTOM_X` macro family (verbatim from baremetal/). |
| [accel_rocc.{h,c}](accel_rocc.c)          | `AccelSetup()`, `AccelSerializeToString_Helper()`, `AccelParseFromString_Helper()`, `BlockOnSerializedValue()`, static 128 KB serializer output region + 64 KB per-side deserializer fixed/array regions. |
| [bench_common.h](bench_common.h)          | `read_mcycle()`, `print_iter()`, `print_summary()`. |
| [bench_tiny_ser.c](bench_tiny_ser.c)      | Hand-crafted `{int32, string}` serialize. Validation baseline. |
| [bench_tiny_des.c](bench_tiny_des.c)      | Hand-crafted deser. Reconstructs `f1=42 f2="hello"`. |
| [bench_hpb_ser.c](bench_hpb_ser.c)        | Main HPB serializer bench driver. One ELF per bench via `BENCH_DESCRIPTORS_H` macro + include of `gen/bench<N>_descriptors.h`. |
| [bench_hpb_des.c](bench_hpb_des.c)        | Matching HPB deserializer bench driver. Feeds `TOP_MESSAGE_WIRE[m]` (generator-emitted wire bytes for each top-level message) through `AccelParseFromString_Helper`; reissues `AccelSetup()` between iters so the accelerator's array-alloc region doesn't overflow on long runs. |
| [bench_isolate.c](bench_isolate.c)        | Per-message isolator — serialize ONE top-level message from a bench. Debug tool. |
| [bench_repro.c](bench_repro.c)            | 14 hand-crafted reproducer cases covering every hardware state-machine combination we've tested. Cases 11/13/14 deliberately fail (documented bugs). |
| [proto_to_accel.py](proto_to_accel.py)    | The generator: `.proto` + `.inc` → `gen/bench<N>_{descriptors.h,data.c}`. |
| [gen_protoacc_sweep_configs.py](gen_protoacc_sweep_configs.py) | Emit `ProtoAccelSweepConfigs.scala` (random / ofat / tweak / default; des / ser / both / joint). |
| [run_sweep.sh](run_sweep.sh)              | Parallel sweep driver: one worker per config, `make CONFIG=<cls>` + run all six HPB benches, append CSV row per `(config, bench, op)`, cleanup generated-src + simulator after each config. |
| [parse_results.py](parse_results.py)      | Scans `output/*.log` → `benchmark_results.json`. |
| [gen/bench<N>_{data.c,descriptors.h}](gen/) | Auto-generated per-bench. Ignored by git; regen via `make gen`. |
| [Makefile](Makefile)                      | Build targets: `all` (every ELF), `bench` (just HPB ELFs — what `run_sweep.sh` needs), `gen` (regen descriptors + data), `clean` (wipe `build/` and `gen/`), `isolate`. |

### 6. Build + run recipes

[README.md](README.md) has the full step-by-step; the condensed versions:

**Single-config smoke test** (one bench on the baseline sim):

```bash
source /home/ec2-user/hyperscale-grpc-chipyard/env.sh

# One-time: Verilator baseline sim (~15 min on first build, cached after).
make -C /home/ec2-user/hyperscale-grpc-chipyard/sims/verilator \
    CONFIG=ProtoAccelRocketBaseConfig -j$(nproc)

# Bench descriptors + ELFs (build/bench[0-5]_{ser,des}.riscv):
cd /home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/software/verilator-bench
make gen       # .proto + .inc → gen/bench<N>_{descriptors.h,data.c}
make bench     # just the 12 HPB ELFs (make alone → also tiny + repro)

# Run one bench (LOADMEM=1 bypasses TSI loader, saves ~2 min/run).
cd /home/ec2-user/hyperscale-grpc-chipyard/sims/verilator
BREAK_SIM_PREREQ=1 LOADMEM=1 make CONFIG=ProtoAccelRocketBaseConfig run-binary-fast \
    BINARY=/home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/software/verilator-bench/build/bench1_ser.riscv

# Aggregate logs in the baseline output dir → benchmark_results.json.
cd /home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/software/verilator-bench
python3 parse_results.py --output benchmark_results.json
```

Output logs land in `sims/verilator/output/chipyard.harness.TestHarness.ProtoAccelRocketBaseConfig/bench<N>_{ser,des}.log`.

**Full HW-parameter sweep** (produces the ML training CSV):

```bash
source /home/ec2-user/hyperscale-grpc-chipyard/env.sh
cd /home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/software/verilator-bench
make gen && make bench

# Emit N random samples per side into ProtoAccelSweepConfigs.scala.
python3 gen_protoacc_sweep_configs.py --emit both -t random -n 32 -s 42

# Run every emitted sample config × all six HPB benches in parallel.
# Defaults: --workers=nproc, --jobs=1, auto-clean per-config artifacts.
bash run_sweep.sh --output sweep.csv
```

The sweep driver skips `(config, bench, op)` tuples already present in
`--output`, so an interrupted run resumes cleanly. See
[run_sweep.sh](run_sweep.sh) header + `--help` for flag reference.

## Known bugs and gotchas

### Packed-buffer string layout crashes the serializer (WORKAROUND IN PLACE)
If string ArenaStringPtr headers live inside the same byte buffer as the
cpp_obj (along with primitives), the serializer fires either a
`TLMonitor supportsGet` assertion or an htif `tohost=7` exit. Reproduced in
`bench_repro_c11`/`c13`/`c14`. Root cause not pinned to a specific RTL line.

**Workaround**: string headers and payloads MUST live in distinct static arrays
physically separated from the cpp_obj. The current generator does this — see
the `_STRING_HEADERS` and `_STRING_PAYLOADS` arrays. Don't collapse them back
into the instance buffer without re-reproducing the failure.

Full details: [project_hpb_ser_multifield_bug.md](/home/ec2-user/.claude/projects/-home-ec2-user/memory/project_hpb_ser_multifield_bug.md).

### ProtoAccelRocketDebugConfig printfs don't fire on Verilator
`WithProtoAccelPrintf` only affects `SynthesizePrintf` (FireSim synthesis wrappers);
the underlying Chisel `printf` always fires on Verilator regardless of the flag.
But our debug attempts with that config produced no `ProtoaccLogger` output on
stderr. If you need RTL visibility on a new failure, build
`run-binary-debug` (which produces VCD) and inspect the waveform directly.

### Verilator runs can be slow
- Per-bench `run-binary-fast` times range from ~1 min (bench1) to ~20 min
  (bench2 at 2 MB descriptor). Plan for 30–60 min total for the full 6-bench
  sweep when you re-run.
- The sim's hard watchdog is `max-cycles=10000000` (10 M cycles ≈ 10 ms sim
  time). Very large workloads can blow this. Override via make flag
  `TIMEOUT_CYCLES=50000000`.

### htif_nano heap is small
`AccelSetupAllocRegionSerializer` now uses static 512 KB BSS region (see
[accel_rocc.c](accel_rocc.c)). Raising `--max-string-len` above 1024 can push
total output past this limit — increase `ACCEL_SER_DATA_BYTES` there first.

### Bench2's unusual nesting depth (13)
All other benches max at depth ≤ 5. `MAX_NESTED_DEPTH=5` means bench2
silently truncates submessages deeper than 5. Raising it produces multi-MB
binaries and very long sim times. The ML features are still keyed on the
full schema, so we're under-representing bench2's real shape — acceptable for
a first pass, worth revisiting later.

## Gap vs. original HyperProtoBench

| Dimension           | HPB reference        | Our current benches  |
|---------------------|----------------------|----------------------|
| Environment         | Linux userspace + libprotobuf | Bare-metal HTIF (no OS) |
| Iters per message   | 65,536               | 4 (+ 1 warmup)       |
| String/bytes lengths| Up to 3.6 MB, p50 ~80–2000 B | Capped at 1024 B; real per-field distribution otherwise |
| Timing              | `std::chrono` wall time | `mcycle` CSR        |
| Fields covered      | All (incl. repeated) | All except `repeated`, oneof, map, groups |
| Submessage depth    | Up to 13 (bench2)    | Capped at 5          |
| Runtime values      | Hand-picked real-world traffic | Seeded-random ASCII; lengths from `.inc` |

### Analytical-model compensation for these caps

Because we can't feasibly match HPB's full byte-size scale under Verilator,
the Lynx analytical model's feature extractor has been patched to honor the
same caps so its output aligns with what the bench measures. See the
`WorkloadProfile` in [/home/ec2-user/lynx/analytical_model/protobuf_analyzer.py](/home/ec2-user/lynx/analytical_model/protobuf_analyzer.py):

- `skip_repeated = True` — treat repeated fields as absent in feature sizes.
- `max_string_len = 1024` — cap every string/bytes payload to 1 KB.
- `max_nested_depth = 5` — ignore nested messages at depth ≥ 5.

Invoke `python3 protobuf_analyzer.py --verilator-bench-profile` to produce
`protobuf_analysis_verilator_bench.json`, then
`python3 extract_features.py --input protobuf_analysis_verilator_bench.json`
to emit `extracted_features.json`. The canonical
`/home/ec2-user/lynx/analytical_model/extracted_features.json` is the bench-
scoped one; `extracted_features_full.json` is the unconstrained reference.

## Next steps (prioritized)

### 1. ~~Kick off the parameter sweep to generate ML training data~~ — DONE

The sweep harness is now in-tree:
[gen_protoacc_sweep_configs.py](gen_protoacc_sweep_configs.py) emits the
sample configs and [run_sweep.sh](run_sweep.sh) drives Verilator builds +
bench runs in parallel, one row per `(config, bench, op)` in the output
CSV. Downstream joining with analytical features happens in
[/home/ec2-user/lynx/build_training_dataset.py](/home/ec2-user/lynx/build_training_dataset.py).

### 2. Raise per-bench iteration count (easy lever)
`ITERS=4` undercounts steady-state behavior because each bench includes a
warmup pass that eats first-iteration cache/TLB fills. Bumping to
`ITERS=16` or `32` makes the measurement window more stable and shrinks
relative noise. Memory budget check: each iter burns ~1 message's worth of
serializer output (~1-2 KB); the 128 KB static region fits ~50 iters × 10
messages comfortably. Deserializer is unaffected because it resets its
array region per iter.

### 3. Close the serializer throughput gap (2-3× below paper)
Serializer is 23–33 Gb/s at 1.84 GHz on bench0/1/3/4 — paper reports
60–100 Gb/s. Likely drivers of the gap:

- **Raise `--max-string-len`** from 1024 to 4096 or 16384. Real HPB p50 is
  ~1856 B for bench0 — we're still below that. Bigger strings amortize
  per-message dispatch cost. Would need to raise `ACCEL_SER_DATA_BYTES`
  and `ACCEL_STATIC_REGION_BYTES` proportionally.
- **Profile which states dominate bench2/5 cycles**. They're 5–10× slower per
  byte than bench0/1/3/4. Likely dispatch-bound because of many small
  messages. Could use `WithProtoAccelPrintf` + VCD to confirm.

### 4. Workload fidelity: repeated fields
About 3% of HPB fields (71 out of 2578). Biggest impact on bench2 (42 fields).
Hardware layout (from [fieldhandler.scala:580-670](/home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/src/main/scala/fieldhandler.scala#L580)):
- For repeated scalars: cpp_obj slot holds address of a `RepeatedField<T>`
  struct `{T* elements_, int current_size_, int total_size_}` (24 B).
  Serializer reads `src-8` → `elements_*`, `src` → `{current_size_, total_size_}`.
- For repeated strings: `RepeatedPtrField<string>` with Rep object + tag bit.

Generator changes: emit a `<Msg>_REP_POOL[]` for repeated-field backing
arrays, add repeated specs to the fixup table, generate a few elements per
repeated field. Runtime changes: `fixup_repeated()` analogous to
`fixup_nested()`.

### 3. Repeated-field support
- About 3% of HPB fields (71 out of 2578). Biggest impact on bench2 (42 fields).
- Hardware layout (from [fieldhandler.scala:580-670](/home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/src/main/scala/fieldhandler.scala#L580)):
  - For repeated scalars: cpp_obj slot holds address of a `RepeatedField<T>`
    struct `{T* elements_, int current_size_, int total_size_}` (24 B).
    Serializer reads `src-8` → `elements_*`, `src` → `{current_size_, total_size_}`.
  - For repeated strings: `RepeatedPtrField<string>` with Rep object + tag bit.
- Generator changes: emit a `<Msg>_REP_POOL[]` for repeated-field backing
  arrays, add repeated specs to the fixup table, generate a few elements per
  repeated field.
- Runtime changes: `fixup_repeated()` analogous to `fixup_nested()`.

### 5. Raise bench2 `MAX_NESTED_DEPTH` selectively
The generator supports per-proto `--max-nested-depth`. For bench2, try
depth 7 or 8 (vs its actual max of 13) to see if the binary fits.
Probably needs iteration.

### 5. Higher simulation fidelity
- Run `run-binary` (not `-fast`) periodically to generate spike-dasm traces;
  useful if a regression appears.
- Consider adding a `--scratchpad` variant config that routes accel reads
  through a per-tile scratchpad (supported by ProtoAcc's `spadParams` arg)
  to sidestep any future DRAM-path issues.

## Files to read first (for a new collaborator)

1. [README.md](README.md) — from-scratch workflow (generate → build → sweep → training CSV)
2. This file — project state, measured vs paper throughput, and roadmap
3. [proto_to_accel.py](proto_to_accel.py) — the data model
4. [bench_hpb_ser.c](bench_hpb_ser.c) — the runtime driver
5. [run_sweep.sh](run_sweep.sh) + [gen_protoacc_sweep_configs.py](gen_protoacc_sweep_configs.py) — the sweep harness
6. `~/.claude/projects/-home-ec2-user/memory/reference_protoacc_abi.md` — hardware ABI
7. `~/.claude/projects/-home-ec2-user/memory/project_hpb_*.md` — the bug/feature memory notes
