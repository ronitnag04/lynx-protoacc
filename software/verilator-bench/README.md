# ProtoAcc Verilator Benchmarks

Bare-metal RISC-V workloads that exercise the ProtoAcc accelerator under Chipyard's
Verilator simulator. Each bench emits `ACCEL_ITER`/`ACCEL_SUMMARY` lines with `mcycle`
deltas so the post-run parser can compute throughput.

## Prerequisites

```bash
source /home/ec2-user/hyperscale-grpc-chipyard/env.sh
```

A Verilator simulator for `ProtoAccelRocketConfig` must already exist at
`sims/verilator/simulator-chipyard.harness-ProtoAccelRocketConfig`. Build it with:

```bash
cd /home/ec2-user/hyperscale-grpc-chipyard/sims/verilator
make CONFIG=ProtoAccelRocketConfig -j$(nproc)
```

## Build

```bash
make                  # Build all ELFs → build/*.riscv
make gen              # Regenerate gen/bench[0-5]_*.{h,c} from lynx/HyperProtoBench .proto files
make clean            # Remove build/
```

## Run

```bash
cd /home/ec2-user/hyperscale-grpc-chipyard/sims/verilator

# Fast path: LOADMEM=1 bypasses the TSI loader (saves ~2min/run on big ELFs)
BREAK_SIM_PREREQ=1 LOADMEM=1 make CONFIG=ProtoAccelRocketConfig run-binary-fast \
    BINARY=/home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/software/verilator-bench/build/bench_tiny_ser.riscv
```

Output logs land under `sims/verilator/output/chipyard.harness.TestHarness.ProtoAccelRocketConfig/`.

## Aggregate results

After all the bench ELFs have run:

```bash
python3 parse_results.py --output benchmark_results.json
```

The output matches the shape `sample_protoacc_model/protobuf_model.py` expects
in its `default_benchmark_results` dict:

```json
{
  "serializer":   { "bench0": {"throughput": <bytes/sec>, "cycles": ..., "bytes": ..., "iters": ...}, ... },
  "deserializer": { ... }
}
```

Default clock is 1 GHz; override with `--clock-hz` if the Chipyard config
targets a different rate.

## Status

| Bench                 | Status | Notes |
|-----------------------|--------|-------|
| `bench_tiny_ser.riscv`| WORKS  | Hand-crafted `{int32, string}`, produces correct 9-byte wire output |
| `bench_tiny_des.riscv`| WORKS  | Hand-crafted deser, reconstructs `f1=42 f2="hello"` |
| `bench[0-5]_ser.riscv`| WORKS  | 1 field per top-level message (`KEEP_FIRST_FIELD_ONLY=True`). All 6 benches serialize 40 messages each and exit with Verilog $finish. Throughput 31-45 MB/s at 1 GHz. |

### Known limitation

With `KEEP_FIRST_FIELD_ONLY=False` (multi-field per message), the serializer trips
a TileLink monitor assertion `'A' channel carries Get type which slave claims it
can't support` at `protoacc_serializer.scala:39` (the 3rd `mem_serfieldhandler`).
The bug appears only when the `FieldDispatchRouter` dispatches work to more than
one parallel handler. Single-field runs stay on handler #0 and succeed. Needs
RTL-level inspection with `ProtoAccelRocketDebugConfig` and a cycle-by-cycle
VCD trace.

## Layout

```
Makefile              # Bare-metal build (riscv64-unknown-elf-gcc + htif_nano.specs)
rocc.h                # RoCC inline-asm macros
accel_rocc.{h,c}      # AccelSetup / AccelSetupAllocRegionSerializer / helpers
bench_common.h        # read_mcycle(), print_iter/summary
bench_tiny_ser.c      # Minimal working serialize bench
bench_tiny_des.c      # Minimal working deserialize bench
bench_hpb_ser.c       # Generic HPB-style serialize bench (wires in gen/benchN_*.h)
gen/
  proto_to_accel.py   # Generator: .proto → {benchN_descriptors.h, benchN_data.c}
  bench[0-5]_*.{h,c}  # Generated descriptors + pre-initialized cpp_obj instances
build/                # Build artifacts (.gitignored)
```

## Generator caveats

`gen/proto_to_accel.py` parses schemas via [lynx/analytical_model/protobuf_analyzer.py](/home/ec2-user/lynx/analytical_model/protobuf_analyzer.py)
and emits per-message descriptors + pre-initialized top-level instances. Current
scope for MVP:

- Primitive scalars (int32/64, uint32/64, float, double, bool, fixed*, sfixed*, sint*) + string/bytes
- Enums are emitted as int32
- **Submessages are disabled** (set `DISABLE_SUBMESSAGES = False` in the generator to re-enable
  once the runtime issues are resolved)
- Repeated fields: skipped (emitted as zero placeholder + not-present hasbits)
- `oneof`, `map`, groups: not supported

cpp_obj layout is forced to match the hardware-assumed shape `des/fieldhandler.scala:66`:
```
[ 0..16]   vptr/cached_size placeholder (zero)
[16..]     hasbits chunks (one 4B chunk per 32 relative_fieldnos)
[24+]      fields, packed 8-byte aligned, offsets encoded in descriptor entries
```

String/bytes fields use a tagged-ArenaStringPtr convention: the 8B slot in cpp_obj
holds `(hdr_addr | 0x3)`, where the header is `{char* data_ptr; size_t length; payload...}`
appended to the instance buffer tail. The bench's `fixup_instance()` patches the
relocatable byte-offset markers emitted by the generator into absolute addresses
at startup.
