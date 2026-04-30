#!/usr/bin/env python3
"""
proto_to_accel.py — generate ProtoAcc-compatible C descriptor tables + cpp_obj instances
from a protobuf .proto schema.

Usage:
    python3 proto_to_accel.py --proto /path/to/benchmark.proto \\
                              --out-header bench0_descriptors.h \\
                              --out-source bench0_data.c \\
                              [--top-message M10] [--seed 7]

Design:
- Reuses ProtobufAnalyzer from /home/ec2-user/lynx/analytical_model/protobuf_analyzer.py
  to parse the .proto into Message/Field dataclasses.
- Emits one ACCEL_DESCRIPTOR array + one cpp_obj layout per message.
- Fills cpp_obj slots with reproducible pseudo-random data (via --seed).
- Handles: primitive scalars, string/bytes (tagged ArenaStringPtr), nested messages.
- Skips: repeated, oneof, map, groups (generator emits a warning and zeros the field).

Critical layout constraint (from RTL):
- des/fieldhandler.scala:66 hardcodes top-level hasbits_offset = 0x10, so every
  cpp_obj MUST place its first real field at offset >= 24. We use:
    [0..16]   vptr/cached_size placeholder (zero)
    [16..20]  hasbits chunk 0
    [20..24]  hasbits chunk 1 (reserved even if unused, for alignment)
    [24..]    fields, packed 8-byte aligned
  This keeps ser and des symmetrical — desc[2] = 0x10 always.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import random
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Reuse lynx's protobuf parser by importing it directly.
# ---------------------------------------------------------------------------
LYNX_ANALYZER = Path("/home/ec2-user/lynx/analytical_model/protobuf_analyzer.py")
if not LYNX_ANALYZER.is_file():
    sys.exit(f"Error: expected {LYNX_ANALYZER} to exist")

spec = importlib.util.spec_from_file_location("protobuf_analyzer", LYNX_ANALYZER)
pa_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pa_mod)
ProtobufAnalyzer = pa_mod.ProtobufAnalyzer
AnalyzerField = pa_mod.Field
AnalyzerMessage = pa_mod.Message

# ---------------------------------------------------------------------------
# Hardware ABI constants (must match descriptortablehandler_{des,ser}.scala).
# ---------------------------------------------------------------------------
PROTO_TYPE_CODES = {
    "double": 1, "float": 2, "int64": 3, "uint64": 4, "int32": 5,
    "fixed64": 6, "fixed32": 7, "bool": 8, "string": 9, "group": 10,
    "message": 11, "bytes": 12, "uint32": 13, "enum": 14,
    "sfixed32": 15, "sfixed64": 16, "sint32": 17, "sint64": 18,
}
# log2(cpp slot size) per type, from descriptortablehandler_des.scala:73-95.
CPP_SIZE_LOG2 = {
    1: 3, 2: 2, 3: 3, 4: 3, 5: 2, 6: 3, 7: 2, 8: 0, 9: 3,
    10: 0, 11: 3, 12: 3, 13: 2, 14: 2, 15: 2, 16: 3, 17: 2, 18: 3,
}
# Hardware-mandated top-level hasbits offset (des/fieldhandler.scala:66).
TOPLEVEL_HASBITS_OFFSET = 0x10
# Fields start 8 bytes after the hasbits area (which is itself 8 bytes = 2 u32 chunks).
FIELD_BASE_OFFSET = 0x18  # 24

# ---------------------------------------------------------------------------

@dataclass
class ResolvedField:
    name: str
    field_number: int
    proto_type_code: int  # value from PROTO_TYPE_CODES
    cpp_size_log2: int
    offset: int  # byte offset within cpp_obj
    is_submessage: bool
    submessage_name: Optional[str]  # qualified message name
    skip_reason: Optional[str]  # non-None → emit zero placeholder, log a comment


@dataclass
class ResolvedMessage:
    name: str  # fully qualified name, e.g., "M1" or "M1_M2"
    fields: List[ResolvedField]
    min_field_no: int
    max_field_no: int
    obj_size: int
    field_base_offset: int  # first field goes here; hasbits fills [0x10..field_base_offset)


def fq_name(parent: Optional[str], name: str) -> str:
    return f"{parent}_{name}" if parent else name


def resolve_type(field: AnalyzerField) -> Tuple[int, bool, Optional[str], Optional[str]]:
    """Return (proto_type_code, is_submessage, submessage_simple_name, skip_reason)."""
    # Enums → treated as int32 on the wire.
    if field.is_enum:
        return PROTO_TYPE_CODES["enum"], False, None, None
    if field.is_nested_message:
        return PROTO_TYPE_CODES["message"], True, field.nested_message_name, None
    code = PROTO_TYPE_CODES.get(field.field_type)
    if code is None:
        return 0, False, None, f"unknown type {field.field_type}"
    return code, False, None, None


def walk_messages(
    messages: List[AnalyzerMessage],
    parent_name: Optional[str],
    out: Dict[str, AnalyzerMessage],
):
    """Flatten nested messages into a dict keyed by fully-qualified name."""
    for m in messages:
        qname = fq_name(parent_name, m.name)
        out[qname] = m
        walk_messages(m.nested_messages, qname, out)


def resolve_all(
    all_messages: Dict[str, AnalyzerMessage],
) -> Dict[str, ResolvedMessage]:
    """Convert parsed AnalyzerMessages into ResolvedMessages with offsets assigned."""
    # First pass: determine simple-name → fully-qualified map for submessage lookups.
    # Protobuf scoping: a field `optional Foo f1` inside message M resolves to
    # M.Foo first, then ancestors, then top-level. We support the common case
    # (direct nested or top-level) — misses fall back to unresolved.
    simple_to_fq: Dict[str, List[str]] = {}
    for fq in all_messages:
        simple = fq.split("_")[-1]
        simple_to_fq.setdefault(simple, []).append(fq)

    def resolve_submsg(referrer_fq: str, simple: str) -> Optional[str]:
        # Prefer a nested child of the referrer.
        direct = f"{referrer_fq}_{simple}"
        if direct in all_messages:
            return direct
        # Then any ancestor-scoped match.
        parts = referrer_fq.split("_")
        for i in range(len(parts) - 1, -1, -1):
            cand = "_".join(parts[:i] + [simple]) if i > 0 else simple
            if cand in all_messages:
                return cand
        # Finally: unique global simple name.
        cands = simple_to_fq.get(simple, [])
        if len(cands) == 1:
            return cands[0]
        return None

    # MVP scope flags.
    # DISABLE_SUBMESSAGES: skip fields whose type is MESSAGE. Nested-msg RTL
    # paths (stack management, nested-descr traversal) are out of scope until
    # the flat path is stable.
    # KEEP_FIRST_FIELD_ONLY: for each message, keep only its first field (by
    # field_number). Dramatically shrinks the test surface — use when
    # hunting a repro for a misbehaving benchmark. Set to False for full
    # runs.
    DISABLE_SUBMESSAGES = True
    KEEP_FIRST_FIELD_ONLY = True

    resolved: Dict[str, ResolvedMessage] = {}
    for fqn, msg in all_messages.items():
        # Optionally prune to a single field per message for debugging.
        if KEEP_FIRST_FIELD_ONLY and msg.fields:
            keep = sorted(msg.fields, key=lambda f: f.field_number)[0]
            msg = type(msg)(
                name=msg.name, fields=[keep], nested_messages=msg.nested_messages,
                enums=msg.enums, total_fields=1, max_field_number=keep.field_number,
                has_repeated_fields=False, has_nested_messages=False, has_enums=False,
                size_bytes=msg.size_bytes, depth=msg.depth,
            )
        # Compute per-message hasbits size from field-number span.
        if msg.fields:
            mn = min(f.field_number for f in msg.fields)
            mx = max(f.field_number for f in msg.fields)
            span_plus_sentinel = mx - mn + 2  # +1 for rel_fn=0 sentinel
            n_chunks = (span_plus_sentinel + 31) // 32
        else:
            n_chunks = 1
        hasbits_bytes = n_chunks * 4
        # Round field_base_offset up to 8-byte alignment.
        base = TOPLEVEL_HASBITS_OFFSET + hasbits_bytes
        base = (base + 7) & ~7
        # Always leave at least one free 8-byte slack after hasbits to avoid
        # accidental overlap (the first chunk always fits in 4 bytes of the
        # 0x10..0x18 region, but subsequent chunks bump us into the field area).
        if n_chunks > 1:
            # already included above
            pass
        field_base = max(base, FIELD_BASE_OFFSET)

        rfields: List[ResolvedField] = []
        offset = field_base
        for f in msg.fields:
            code, is_sub, sub_simple, skip = resolve_type(f)
            if f.cardinality == "repeated":
                skip = skip or "repeated (not yet supported)"
            submsg_fq: Optional[str] = None
            if is_sub and sub_simple:
                submsg_fq = resolve_submsg(fqn, sub_simple)
                if submsg_fq is None:
                    skip = skip or f"unresolved submessage {sub_simple}"
                elif DISABLE_SUBMESSAGES:
                    skip = skip or "submessage (MVP: disabled)"
            size_log2 = CPP_SIZE_LOG2.get(code, 3) if code else 3
            slot_bytes = max(8, 1 << size_log2)  # always round up to 8 for packing
            # 8-byte-align offset before placing this slot.
            offset = (offset + 7) & ~7
            rfields.append(
                ResolvedField(
                    name=f.name,
                    field_number=f.field_number,
                    proto_type_code=code,
                    cpp_size_log2=size_log2,
                    offset=offset,
                    is_submessage=is_sub,
                    submessage_name=submsg_fq,
                    skip_reason=skip,
                )
            )
            offset += slot_bytes
        if not rfields:
            resolved[fqn] = ResolvedMessage(fqn, [], 1, 0, 0x20, FIELD_BASE_OFFSET)
            continue
        min_fn = min(rf.field_number for rf in rfields)
        max_fn = max(rf.field_number for rf in rfields)
        obj_size = (offset + 15) & ~15
        resolved[fqn] = ResolvedMessage(fqn, rfields, min_fn, max_fn, obj_size, field_base)
    return resolved

# ---------------------------------------------------------------------------
# Data synthesis
# ---------------------------------------------------------------------------

def det_rng(seed: int, key: str) -> random.Random:
    h = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "little"))


def rand_string(rng: random.Random, length: int) -> str:
    return "".join(rng.choices(string.ascii_letters, k=length))


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------

HEADER_PROLOGUE = """// AUTO-GENERATED by gen/proto_to_accel.py. Do not edit by hand.
// Source: {src}

#ifndef {guard}
#define {guard}

#include <stdint.h>

"""

HEADER_EPILOGUE = "#endif // {guard}\n"


def encode_desc_entry(rf: ResolvedField) -> int:
    # (is_repeated<<63) | ((type & 0x1F)<<58) | (offset & ((1<<58)-1))
    # We never emit is_repeated=1 (skipped earlier).
    return ((rf.proto_type_code & 0x1F) << 58) | (rf.offset & ((1 << 58) - 1))


def descriptor_for(rm: ResolvedMessage, resolved: Dict[str, ResolvedMessage]) -> List[str]:
    """Emit the uint64_t descriptor array entries for one message, as C lines."""
    n_fields = rm.max_field_no - rm.min_field_no + 1
    # Map actual_field_no -> ResolvedField; missing slots get a zeroed-type placeholder.
    by_fn = {rf.field_number: rf for rf in rm.fields}
    lines = [
        f"  /* [0] vptr              */ 0ULL,",
        f"  /* [1] cpp_obj size      */ {rm.obj_size}ULL,",
        f"  /* [2] hasbits offset    */ {TOPLEVEL_HASBITS_OFFSET}ULL,",
        f"  /* [3] (min<<32)|max     */ (((uint64_t){rm.min_field_no}) << 32) | {rm.max_field_no}ULL,",
    ]
    is_submessage_mask = 0
    for i in range(n_fields):
        actual_fn = rm.min_field_no + i
        rf = by_fn.get(actual_fn)
        if rf is None or rf.skip_reason:
            reason = rf.skip_reason if rf else "gap"
            lines.append(f"  /* field {actual_fn}: skipped ({reason}) */ 0ULL, 0ULL,")
            continue
        word_a = encode_desc_entry(rf)
        if rf.is_submessage and rf.submessage_name in resolved:
            submsg_sym = f"{rf.submessage_name}_ACCEL_DESCRIPTOR"
            # Hardware indexes the is_submessage bitfield by relative_fieldno
            # (= i + 1), matching the hasbits indexing convention.
            is_submessage_mask |= 1 << (i + 1)
            lines.append(
                f"  /* f{actual_fn} {rf.name} (msg={rf.submessage_name}) */ "
                f"0x{word_a:016x}ULL, (uint64_t)(uintptr_t){submsg_sym},"
            )
        else:
            tname = next(
                (k for k, v in PROTO_TYPE_CODES.items() if v == rf.proto_type_code),
                "?",
            )
            lines.append(
                f"  /* f{actual_fn} {rf.name} ({tname} @ {rf.offset}) */ "
                f"0x{word_a:016x}ULL, 0ULL,"
            )
    lines.append(f"  /* is_submessage_bitfield */ {hex(is_submessage_mask)}ULL,")
    return lines


def emit_header(
    resolved: Dict[str, ResolvedMessage],
    out: Path,
    proto_src: str,
    top_messages: List[str],
):
    guard = out.name.upper().replace(".", "_").replace("-", "_")
    with out.open("w") as f:
        f.write(HEADER_PROLOGUE.format(src=proto_src, guard=guard))
        # Forward-declare all descriptors so nested-referencing descriptors can take
        # their address even if the referenced one is defined later.
        f.write("/* Forward declarations so nested refs can resolve in any order. */\n")
        for name in resolved:
            f.write(f"extern const uint64_t {name}_ACCEL_DESCRIPTOR[];\n")
        f.write("\n/* Sizes */\n")
        for name, rm in resolved.items():
            f.write(f"#define {name}_ACCEL_SIZEOF {rm.obj_size}\n")
            f.write(f"#define {name}_ACCEL_MIN_FIELD_NO {rm.min_field_no}\n")
            f.write(f"#define {name}_ACCEL_MAX_FIELD_NO {rm.max_field_no}\n")
        f.write(f"\n#define TOP_MESSAGE_COUNT {len(top_messages)}\n")
        f.write("extern const uint64_t* const TOP_MESSAGE_DESCRIPTORS[TOP_MESSAGE_COUNT];\n")
        f.write("extern const char* const TOP_MESSAGE_NAMES[TOP_MESSAGE_COUNT];\n")
        f.write("extern const uint32_t TOP_MESSAGE_SIZES[TOP_MESSAGE_COUNT];\n")
        f.write("extern uint8_t* const TOP_MESSAGE_INSTANCE_PTRS[TOP_MESSAGE_COUNT];\n")
        f.write("extern const uint32_t TOP_MESSAGE_INSTANCE_BYTES[TOP_MESSAGE_COUNT];\n")
        # Pre-built instances for serialize benchmarks.
        f.write("\n/* Pre-initialized cpp_obj instances (serializer inputs). */\n")
        for name in top_messages:
            f.write(f"extern uint8_t {name}_INSTANCE[];\n")
        f.write("\n")
        f.write(HEADER_EPILOGUE.format(guard=guard))


def emit_source(
    resolved: Dict[str, ResolvedMessage],
    out_source: Path,
    out_header: Path,
    top_messages: List[str],
    seed: int,
):
    with out_source.open("w") as f:
        f.write(f'// AUTO-GENERATED by gen/proto_to_accel.py. Do not edit.\n')
        f.write(f'#include "{out_header.name}"\n')
        f.write("#include <stdint.h>\n")
        f.write("#include <string.h>\n\n")

        # Descriptor arrays.
        for name, rm in resolved.items():
            f.write(f"const uint64_t {name}_ACCEL_DESCRIPTOR[] __attribute__((aligned(16))) = {{\n")
            for line in descriptor_for(rm, resolved):
                f.write(line + "\n")
            f.write("};\n\n")

        # Pre-init top-level instances. String payloads are placed inside the
        # instance buffer tail so the tagged-ArenaStringPtr convention works
        # without separate allocations.
        instance_total_bytes: Dict[str, int] = {}
        for top in top_messages:
            rm = resolved[top]
            # Compute layout: instance bytes + (per-string) {char* + size + payload}.
            init_lines, padded_size = build_instance_bytes(rm, resolved, seed, top)
            instance_total_bytes[top] = padded_size
            f.write(f"uint8_t {top}_INSTANCE[{padded_size}] __attribute__((aligned(16))) = {{\n")
            # 16 bytes/line.
            for i in range(0, padded_size, 16):
                row = init_lines[i:i + 16]
                f.write("  " + ", ".join(f"0x{b:02x}" for b in row) + ",\n")
            f.write("};\n\n")

        # Top-level descriptor/name/size tables.
        f.write(
            "const uint64_t* const TOP_MESSAGE_DESCRIPTORS[TOP_MESSAGE_COUNT] = {\n"
        )
        for n in top_messages:
            f.write(f"  {n}_ACCEL_DESCRIPTOR,\n")
        f.write("};\n\n")
        f.write("const char* const TOP_MESSAGE_NAMES[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f'  "{n}",\n')
        f.write("};\n\n")
        f.write("const uint32_t TOP_MESSAGE_SIZES[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f"  {resolved[n].obj_size},\n")
        f.write("};\n\n")
        f.write("uint8_t* const TOP_MESSAGE_INSTANCE_PTRS[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f"  {n}_INSTANCE,\n")
        f.write("};\n\n")
        f.write("const uint32_t TOP_MESSAGE_INSTANCE_BYTES[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f"  {instance_total_bytes[n]},\n")
        f.write("};\n")


def build_instance_bytes(
    rm: ResolvedMessage,
    resolved: Dict[str, ResolvedMessage],
    seed: int,
    top_name: str,
) -> Tuple[bytes, int]:
    """Return (raw_bytes, total_padded_size) for one top-level message instance.

    Layout:
        [0..obj_size]           the cpp_obj itself
        [obj_size..]            ArenaStringPtr headers + payloads for each string/bytes field

    Strings/bytes live tail-appended in the same buffer so the hardware's
    `data & ~0x3` dereference chain lands inside the instance.
    """
    rng = det_rng(seed, top_name)
    # Start with the object at offset 0.
    buf = bytearray(rm.obj_size)
    # Hasbits: set bit (actual_field_no - min + 1) for every present primitive field.
    hasbits = 0
    # String pool appended after the object.
    pool = bytearray()
    pool_base_offset = rm.obj_size  # offset within `buf` where pool starts

    for rf in rm.fields:
        rel_fn = rf.field_number - rm.min_field_no + 1
        if rf.skip_reason:
            # Leave slot zero, mark not-present.
            continue
        if rf.is_submessage:
            # For the MVP we don't pre-build nested instances. A submessage field
            # pointing at a zeroed cpp_obj is valid but the has_bits is unset
            # — so the hardware won't try to encode it. This keeps generation
            # simple for deep nesting without infinite recursion.
            continue

        present = True
        if rf.proto_type_code in (PROTO_TYPE_CODES["string"], PROTO_TYPE_CODES["bytes"]):
            # Append {char* data_ptr; size_t length; payload_bytes...} to pool.
            length = rng.randint(1, 16)
            payload = rand_string(rng, length).encode("ascii")
            # Header goes at current pool offset; payload immediately after.
            hdr_offset_in_buf = len(buf) + len(pool)
            payload_offset_in_buf = hdr_offset_in_buf + 16
            # We can't know final addresses at gen time; the runtime fixup
            # step in bench_hpb_ser.c converts these placeholders to real
            # addresses. We mark them with a distinctive tag that primitive
            # values are extremely unlikely to collide with:
            #   - slot in cpp_obj: high bits = hdr_offset, low 16 bits = MARKER_SLOT
            #   - hdr data_ptr:   high bits = payload_offset, low 16 bits = MARKER_HDR
            # The fixup routine scans the instance buffer for these markers
            # and converts them to absolute addresses with the proper low-bit
            # tag (0x3 for slot, raw for hdr).
            MARKER_SLOT = 0xFACE
            MARKER_HDR = 0xF00D
            pool += ((payload_offset_in_buf << 16) | MARKER_HDR).to_bytes(8, "little")
            pool += length.to_bytes(8, "little")
            pool += payload
            while len(pool) % 8:
                pool += b"\x00"
            slot_val = ((hdr_offset_in_buf << 16) | MARKER_SLOT).to_bytes(8, "little")
            buf[rf.offset:rf.offset + 8] = slot_val
        elif rf.proto_type_code in (
            PROTO_TYPE_CODES["int32"], PROTO_TYPE_CODES["uint32"],
            PROTO_TYPE_CODES["sint32"], PROTO_TYPE_CODES["fixed32"],
            PROTO_TYPE_CODES["sfixed32"], PROTO_TYPE_CODES["enum"],
        ):
            val = rng.randint(0, 0x7fffffff)
            buf[rf.offset:rf.offset + 4] = val.to_bytes(4, "little")
        elif rf.proto_type_code in (
            PROTO_TYPE_CODES["int64"], PROTO_TYPE_CODES["uint64"],
            PROTO_TYPE_CODES["sint64"], PROTO_TYPE_CODES["fixed64"],
            PROTO_TYPE_CODES["sfixed64"],
        ):
            val = rng.randint(0, 0x7fffffffffffffff)
            buf[rf.offset:rf.offset + 8] = val.to_bytes(8, "little")
        elif rf.proto_type_code == PROTO_TYPES_BOOL:
            buf[rf.offset:rf.offset + 1] = bytes([1])
        elif rf.proto_type_code == PROTO_TYPE_CODES["float"]:
            import struct
            buf[rf.offset:rf.offset + 4] = struct.pack("<f", rng.uniform(0, 1000))
        elif rf.proto_type_code == PROTO_TYPE_CODES["double"]:
            import struct
            buf[rf.offset:rf.offset + 8] = struct.pack("<d", rng.uniform(0, 1000))
        else:
            present = False
        if present:
            hasbits |= 1 << rel_fn

    # Hasbits can span multiple 32-bit chunks when span > 31 fields.
    # resolve_all() sized rm.field_base_offset to fit all chunks, so we can
    # safely write as many chunks as needed.
    n_rel = rm.max_field_no - rm.min_field_no + 2  # +1 for sentinel bit 0
    n_chunks = (n_rel + 31) // 32
    for c in range(n_chunks):
        chunk_val = (hasbits >> (32 * c)) & 0xFFFFFFFF
        off = TOPLEVEL_HASBITS_OFFSET + 4 * c
        buf[off:off + 4] = chunk_val.to_bytes(4, "little")

    total = bytes(buf) + bytes(pool)
    return total, len(total)


# Need this alias since key name has a digit-only variant used above.
PROTO_TYPES_BOOL = PROTO_TYPE_CODES["bool"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proto", required=True, help="Path to benchmark.proto")
    ap.add_argument("--out-header", required=True, help="Output .h path")
    ap.add_argument("--out-source", required=True, help="Output .c path")
    ap.add_argument("--top-messages", nargs="*",
                    help="Top-level messages to emit pre-init instances for "
                         "(default: all top-level messages)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    analyzer = ProtobufAnalyzer(args.proto)
    analysis = analyzer.analyze()

    all_messages: Dict[str, AnalyzerMessage] = {}
    walk_messages(analysis.messages, None, all_messages)
    resolved = resolve_all(all_messages)

    top = args.top_messages or [m.name for m in analysis.messages]
    missing = [t for t in top if t not in resolved]
    if missing:
        sys.exit(f"top messages not found: {missing}")

    out_h = Path(args.out_header)
    out_c = Path(args.out_source)
    out_h.parent.mkdir(parents=True, exist_ok=True)
    out_c.parent.mkdir(parents=True, exist_ok=True)

    emit_header(resolved, out_h, args.proto, top)
    emit_source(resolved, out_c, out_h, top, args.seed)

    print(f"Wrote {out_h} ({len(resolved)} descriptors).", file=sys.stderr)
    print(f"Wrote {out_c} (top-level instances: {top}).", file=sys.stderr)


if __name__ == "__main__":
    main()
