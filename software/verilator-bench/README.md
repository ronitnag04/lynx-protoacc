# ProtoAcc Verilator Benchmarks

Bare-metal RISC-V workloads that exercise the ProtoAcc accelerator under Chipyard's
Verilator simulator. Each bench emits `ACCEL_ITER`/`ACCEL_SUMMARY` lines with `mcycle`
deltas so the post-run parser can compute throughput.

## Prerequisites

```bash
source /home/ec2-user/hyperscale-grpc-chipyard/env.sh
```

A Verilator simulator for `ProtoAccelRocketBaseConfig` must already exist at
`sims/verilator/simulator-chipyard.harness-ProtoAccelRocketBaseConfig`. Build it with:

```bash
cd /home/ec2-user/hyperscale-grpc-chipyard/sims/verilator
make CONFIG=ProtoAccelRocketBaseConfig -j$(nproc)
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
BREAK_SIM_PREREQ=1 LOADMEM=1 make CONFIG=ProtoAccelRocketBaseConfig run-binary-fast \
    BINARY=/home/ec2-user/hyperscale-grpc-chipyard/generators/protoacc/software/verilator-bench/build/bench_tiny_ser.riscv
```

Output logs land under `sims/verilator/output/chipyard.harness.TestHarness.ProtoAccelRocketBaseConfig/`.

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
| `bench[0-5]_ser.riscv`| WORKS  | Full multi-field with primitives, strings/bytes, and nested submessages (up to 3 levels deep). All 6 HyperProtoBench schemas exercised end-to-end. At 1 GHz: bench0=454, bench1=319, bench2=278, bench3=308, bench4=323, bench5=268 MB/s. |
| `bench_repro_c*.riscv` | Debug | Minimal reproducers used during bug bisection. Cases 11/13/14 still deliberately fail (they demonstrate the packed-buffer string bug). |
| `bench_isolate_bN_mI.riscv` | Debug | Per-message isolator that compiles ONE top-level message from generated bench data. Useful for bisecting which specific message breaks when regressions appear. |

### Design notes

Per top-level message M the generator emits four separate static arrays, plus
small spec tables the runtime walks to stitch them together:

```
M_INSTANCE[obj_size]             # top cpp_obj (primitives + zero slots)
M_NESTED_POOL[nested_bytes]      # concat of nested cpp_objs, 16-B aligned
M_STRING_HEADERS[K*32]           # K ArenaStringPtr {char*, size_t} blocks, 32-B aligned
M_STRING_PAYLOADS[payload_bytes] # concat of all string payloads, 16-B aligned + cushion
M_NESTED_SPECS[N*3]              # {parent_instance, parent_slot_offset, nested_offset}
M_STRING_SPECS[K*5]              # {owner_instance, slot_offset, hdr_idx, payload_offset, length}
```

At runtime, `fixup_nested()` + `fixup_strings()` walk the spec tables and patch:
- parent cpp_obj slot for each nested submessage → absolute address of the nested
  instance in `NESTED_POOL`
- parent cpp_obj slot for each string → `((uint64_t)&STRING_HEADERS[i]) | 0x3`
- `STRING_HEADERS[i].data = &STRING_PAYLOADS[p]; .length = L`

Why three pools (not one): packing strings or nested instances inside the top
cpp_obj buffer triggers a hardware fault in the ProtoAcc serializer (see
`project_hpb_ser_multifield_bug.md` memory note for the reproducers). Keeping
them in distinct static arrays at distinct `.data` addresses avoids the bug.

- **Submessages**: supported. Nested cpp_objs are pre-allocated recursively up
  to `MAX_NESTED_DEPTH=3`. Each nested instance gets its own primitives and
  possibly further nested children.
- **Repeated / map / oneof / groups**: skipped; the generator emits a zero
  placeholder entry and doesn't set the presence bit.

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
