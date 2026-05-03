# ProtoAcc Verilator Benchmarks — Status

_Last updated: 2026-05-01_

A bare-metal RISC-V workload suite that runs HyperProtoBench serialization
schemas on the ProtoAcc RoCC accelerator under Chipyard's Verilator simulator,
producing cycle-count / throughput data for the Lynx ML throughput model.

## Goal

Match HyperProtoBench (HPB) workloads closely enough that:
1. The cycle-count data lines up with the analytical features in
   [/home/ec2-user/lynx/analytical_model/extracted_features.json](/home/ec2-user/lynx/analytical_model/extracted_features.json)
   (keyed on `bench0`..`bench5`).
2. Measured throughput falls in the same order of magnitude as the ProtoAcc
   MICRO 2021 paper results: **serializer 60–100 Gb/s @ 1.84 GHz**,
   **deserializer 25–40 Gb/s @ 1.95 GHz**.

## Current results (serializer only)

Full HPB multi-field workloads including primitives, strings/bytes (1024 B cap),
and nested submessages (up to depth 5). At the Verilator config's nominal 1 GHz
clock:

| Bench  | bytes  | cycles | MB/s   | Gb/s @ 1 GHz | Gb/s @ 1.84 GHz | vs. paper 60–100 |
|--------|-------:|-------:|-------:|-------------:|----------------:|:----------------:|
| bench0 | 80,180 | 39,178 | 2,047  | 16.4         | **30.1**        | 0.5×–0.3×        |
| bench1 | 37,960 | 20,847 | 1,821  | 14.6         | **26.8**        | 0.4×–0.3×        |
| bench2 | 20,800 | 63,516 |   327  |  2.6         |  4.8            | 0.08×            |
| bench3 | 65,392 | 29,239 | 2,236  | 17.9         | **32.9**        | 0.5×–0.3×        |
| bench4 | 44,560 | 27,978 | 1,593  | 12.7         | **23.4**        | 0.4×–0.2×        |
| bench5 | 18,428 | 32,129 |   574  |  4.6         |  8.4            | 0.1×             |

Four of six benches are within ~2–3× of the paper's serializer range — same order
of magnitude. Bench2 and bench5 are lower, likely because their workloads have
more small-payload messages that expose the fixed per-message dispatch overhead.

**Full current data**: [benchmark_results.json](benchmark_results.json)

## What's implemented

### 1. Verilator sim config
- `ProtoAccelRocketBaseConfig` — direct-attach Rocket + 1 ProtoAcc serializer +
  1 ProtoAcc deserializer, no ReRoCC. Defined in
  [chipyard/src/main/scala/config/HyperscaleConfigs.scala](/home/ec2-user/hyperscale-grpc-chipyard/generators/chipyard/src/main/scala/config/HyperscaleConfigs.scala).
- `ProtoAccelRocketDebugConfig` — same but with `WithProtoAccelPrintf` for
  synthesis-path debug prints (does nothing on Verilator in practice; left
  wired for FireSim).

### 2. Generator: `.proto` + `.inc` → C benchmark data
[gen/proto_to_accel.py](gen/proto_to_accel.py)

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
| [accel_rocc.{h,c}](accel_rocc.c)          | `AccelSetup()`, `AccelSerializeToString_Helper()`, `BlockOnSerializedValue()`, static 512 KB serializer output region. |
| [bench_common.h](bench_common.h)          | `read_mcycle()`, `print_iter()`, `print_summary()`. |
| [bench_tiny_ser.c](bench_tiny_ser.c)      | Hand-crafted `{int32, string}` serialize. Validation baseline. |
| [bench_tiny_des.c](bench_tiny_des.c)      | Hand-crafted deser. Reconstructs `f1=42 f2="hello"`. |
| [bench_hpb_ser.c](bench_hpb_ser.c)        | Main HPB bench driver. One ELF per bench via `BENCH_DESCRIPTORS_H` macro + include of `gen/bench<N>_descriptors.h`. |
| [bench_isolate.c](bench_isolate.c)        | Per-message isolator — serialize ONE top-level message from a bench. Debug tool. |
| [bench_repro.c](bench_repro.c)            | 14 hand-crafted reproducer cases covering every hardware state-machine combination we've tested. Cases 11/13/14 deliberately fail (documented bugs). |
| [gen/proto_to_accel.py](gen/proto_to_accel.py) | The generator. ~750 LOC. |
| [gen/bench<N>_{data.c,descriptors.h}](gen/) | Auto-generated per-bench. Ignored by git; regen via `make gen`. |
| [parse_results.py](parse_results.py)      | Scans `output/*.log` → `benchmark_results.json`. |
| [Makefile](Makefile)                      | Build targets: `all`, `gen`, `clean`. |

### 6. Build + run recipe

```bash
source /home/ec2-user/hyperscale-grpc-chipyard/env.sh

# One-time: build the Verilator sim (~15 min on first build, cached after).
cd /home/ec2-user/hyperscale-grpc-chipyard/sims/verilator
make CONFIG=ProtoAccelRocketBaseConfig -j$(nproc)

# Generate bench data + build ELFs.
cd /home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/software/verilator-bench
make gen   # .proto + .inc → gen/bench<N>_*.{c,h}
make       # → build/bench<N>_ser.riscv

# Run one bench (LOADMEM=1 bypasses TSI loader, saves ~2 min/run).
cd /home/ec2-user/hyperscale-grpc-chipyard/sims/verilator
BREAK_SIM_PREREQ=1 LOADMEM=1 make CONFIG=ProtoAccelRocketBaseConfig run-binary-fast \
    BINARY=/home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/software/verilator-bench/build/bench1_ser.riscv

# Aggregate all bench logs → JSON matching Lynx's consumer schema.
cd /home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/software/verilator-bench
python3 parse_results.py --output benchmark_results.json
```

Output logs land in `sims/verilator/output/chipyard.harness.TestHarness.ProtoAccelRocketBaseConfig/bench<N>_ser.log`.

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
| `avg_size_bytes` vs HPB | (target) | bench1: 45% of target, bench0/3/4: 2-5%, bench2/5: <2% |

## Next steps (prioritized)

### 1. Hit the paper's throughput targets (highest priority)
Current serializer is 23–33 Gb/s on bench0/1/3/4 at 1.84 GHz — paper reports
60–100 Gb/s. The gap is mostly because we amortize less work per RoCC
dispatch. Things to try:

- **Raise `--max-string-len`** from 1024 to 4096 or 16384. Real HPB p50 is
  ~1856 B for bench0 — we're still capping below that. This is the single
  biggest lever on throughput and workload realism. Memory budget: each
  top-level message × 4096 B × (num string fields) × (ITERS+1 iters) has to
  fit in the 512 KB serializer output region — may require bumping
  `ACCEL_SER_DATA_BYTES` too.
- **Raise `ITERS`** from 4 to 16 or 32. The first iteration eats setup
  latency; steady-state iterations are much faster. Longer measurement
  windows will push throughput closer to amortized-steady-state.
- **Profile which states dominate bench2/5 cycles**. They're 5–10× slower per
  byte than bench0/1/3/4. Likely they have many small messages where the
  HASBITS_INFO + DO_PROTO_SERIALIZE dispatch cost dominates. Could switch
  those benches to mostly-large top-level messages.

### 2. Deserializer side
We have `bench_tiny_des.c` validating the deserializer path for
`{int32, string}`. Need to extend the generator to emit bench<N>_des ELFs
that feed wire bytes into the deserializer and measure cycles. The wire bytes
would come from running our serializer first, OR from the `.inc` runtime
data fed through a software-only protobuf encoder. The hardware has a 0x10
hardcoded hasbits_offset at the top level — we already use that, so the
layout is compatible. Expected throughput from the paper: 25–40 Gb/s @
1.95 GHz.

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

### 4. Raise bench2 `MAX_NESTED_DEPTH` selectively
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

1. [README.md](README.md) — basic usage
2. This file — project state and roadmap
3. [gen/proto_to_accel.py](gen/proto_to_accel.py) — the data model
4. [bench_hpb_ser.c](bench_hpb_ser.c) — the runtime driver
5. `~/.claude/projects/-home-ec2-user/memory/reference_protoacc_abi.md` — hardware ABI
6. `~/.claude/projects/-home-ec2-user/memory/project_hpb_*.md` — the three bug/feature memory notes
