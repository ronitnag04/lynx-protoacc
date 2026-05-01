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

# Cap the submessage instantiation depth so cyclic schemas or very deep
# hierarchies don't blow up the generated buffers. HPB max nested depths:
#   bench0=2, bench1=2, bench2=13, bench3=1, bench4=3, bench5=5.
# Depth 5 covers every bench except bench2 (which has unusually deep nesting —
# depth 13 — and would produce multi-MB buffers).
DEFAULT_MAX_NESTED_DEPTH = 5

# Cap per-string length for Verilator feasibility. Real HPB strings go up to
# 3 MB, which would balloon binary size and simulation time without adding ML
# signal. At 1024 bytes we still see realistic multi-chunk tail reads through
# S_STRING_LOADDATA, and keep binaries under a few MB.
DEFAULT_MAX_STRING_LEN = 1024

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
    DISABLE_SUBMESSAGES = False  # nested message instances are pre-allocated + linked at runtime
    DISABLE_STRINGS_BYTES = False  # strings now work via separate-buffer layout
    KEEP_FIRST_FIELD_ONLY = False

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
            if code in (PROTO_TYPE_CODES["string"], PROTO_TYPE_CODES["bytes"]) and DISABLE_STRINGS_BYTES:
                skip = skip or "string/bytes (MVP: disabled)"
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
        # String support tables. Runtime fixup uses these to set up
        # ArenaStringPtr headers + patch the cpp_obj tagged-pointer slots.
        # String specs are 5-element records: {owner_instance, slot_offset,
        # hdr_index, payload_offset, length}. owner_instance==0 means the
        # top-level cpp_obj; >0 means the (owner-1)'th nested instance.
        f.write("extern uint8_t* const TOP_MESSAGE_STRING_HEADERS[TOP_MESSAGE_COUNT];\n")
        f.write("extern uint8_t* const TOP_MESSAGE_STRING_PAYLOADS[TOP_MESSAGE_COUNT];\n")
        f.write("extern const uint32_t* const TOP_MESSAGE_STRING_SPECS[TOP_MESSAGE_COUNT];\n")
        f.write("extern const uint32_t TOP_MESSAGE_STRING_COUNTS[TOP_MESSAGE_COUNT];\n")
        # Nested-message tables. NESTED_POOL holds all nested instances
        # concatenated (16-byte aligned). NESTED_SPECS is 3-element records:
        # {parent_instance, parent_slot_offset, nested_offset} — runtime
        # fixup patches the parent's slot with (nested_pool + nested_offset).
        f.write("extern uint8_t* const TOP_MESSAGE_NESTED_POOLS[TOP_MESSAGE_COUNT];\n")
        f.write("extern const uint32_t* const TOP_MESSAGE_NESTED_SPECS[TOP_MESSAGE_COUNT];\n")
        f.write("extern const uint32_t TOP_MESSAGE_NESTED_COUNTS[TOP_MESSAGE_COUNT];\n")
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
    max_nested_depth: int = DEFAULT_MAX_NESTED_DEPTH,
    runtime_lengths: Optional[Dict[str, Dict[str, List[int]]]] = None,
    max_string_len: int = DEFAULT_MAX_STRING_LEN,
):
    if runtime_lengths is None:
        runtime_lengths = {}
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

        # Pre-init top-level instances. cpp_obj holds primitives only; strings
        # live in separate arrays. Nested submessage instances live in another
        # separate pool. Runtime fixup patches the parent's cpp_obj tagged-ptr
        # slots and nested-message pointer slots with absolute addresses.
        instance_sizes: Dict[str, int] = {}
        per_msg_string_specs: Dict[str, List[StringSpec]] = {}
        per_msg_nested_specs: Dict[str, List[NestedSpec]] = {}
        for top in top_messages:
            rm = resolved[top]
            inst_bytes, nested_bytes, str_specs, payloads, n_specs = \
                build_instance_bytes(rm, resolved, seed, top, max_nested_depth,
                                     runtime_lengths, max_string_len)
            instance_sizes[top] = len(inst_bytes)
            per_msg_string_specs[top] = str_specs
            per_msg_nested_specs[top] = n_specs

            # INSTANCE buffer (top cpp_obj only).
            f.write(f"uint8_t {top}_INSTANCE[{len(inst_bytes)}] __attribute__((aligned(16))) = {{\n")
            for i in range(0, len(inst_bytes), 16):
                row = inst_bytes[i:i + 16]
                f.write("  " + ", ".join(f"0x{b:02x}" for b in row) + ",\n")
            f.write("};\n\n")

            # Nested instances pool.
            if len(nested_bytes) > 0:
                f.write(f"uint8_t {top}_NESTED_POOL[{len(nested_bytes)}] "
                        f"__attribute__((aligned(16))) = {{\n")
                for i in range(0, len(nested_bytes), 16):
                    row = nested_bytes[i:i + 16]
                    f.write("  " + ", ".join(f"0x{b:02x}" for b in row) + ",\n")
                f.write("};\n\n")
            else:
                f.write(f"uint8_t {top}_NESTED_POOL[1] __attribute__((aligned(16))) = {{0}};\n\n")

            # Strings.
            n_strings = len(str_specs)
            if n_strings > 0:
                f.write(f"uint8_t {top}_STRING_HEADERS[{n_strings * 32}] "
                        f"__attribute__((aligned(32))) = {{0}};\n")
                f.write(f"uint8_t {top}_STRING_PAYLOADS[{len(payloads)}] "
                        f"__attribute__((aligned(16))) = {{\n")
                for i in range(0, len(payloads), 16):
                    row = payloads[i:i + 16]
                    f.write("  " + ", ".join(f"0x{b:02x}" for b in row) + ",\n")
                f.write("};\n\n")
            else:
                f.write(f"uint8_t {top}_STRING_HEADERS[1] __attribute__((aligned(32))) = {{0}};\n")
                f.write(f"uint8_t {top}_STRING_PAYLOADS[1] __attribute__((aligned(16))) = {{0}};\n\n")

            # String specs: {owner_instance, slot_offset, hdr_index, payload_offset, length}.
            if n_strings > 0:
                f.write(f"const uint32_t {top}_STRING_SPECS[{n_strings * 5}] = {{\n")
                for s in str_specs:
                    f.write(f"  {s.owner_instance}, {s.slot_offset}, "
                            f"{s.hdr_index}, {s.payload_offset}, {s.length},\n")
                f.write("};\n\n")
            else:
                f.write(f"const uint32_t {top}_STRING_SPECS[1] = {{0}};\n\n")

            # Nested specs: {parent_instance, parent_slot_offset, nested_offset}.
            n_nested = len(n_specs)
            if n_nested > 0:
                f.write(f"const uint32_t {top}_NESTED_SPECS[{n_nested * 3}] = {{\n")
                for ns in n_specs:
                    f.write(f"  {ns.parent_instance}, {ns.parent_slot_offset}, "
                            f"{ns.nested_offset},\n")
                f.write("};\n\n")
            else:
                f.write(f"const uint32_t {top}_NESTED_SPECS[1] = {{0}};\n\n")

        # Top-level descriptor/name/size/instance tables.
        f.write("const uint64_t* const TOP_MESSAGE_DESCRIPTORS[TOP_MESSAGE_COUNT] = {\n")
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
            f.write(f"  {instance_sizes[n]},\n")
        f.write("};\n\n")
        # String tables.
        f.write("uint8_t* const TOP_MESSAGE_STRING_HEADERS[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f"  {n}_STRING_HEADERS,\n")
        f.write("};\n\n")
        f.write("uint8_t* const TOP_MESSAGE_STRING_PAYLOADS[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f"  {n}_STRING_PAYLOADS,\n")
        f.write("};\n\n")
        f.write("const uint32_t* const TOP_MESSAGE_STRING_SPECS[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f"  {n}_STRING_SPECS,\n")
        f.write("};\n\n")
        f.write("const uint32_t TOP_MESSAGE_STRING_COUNTS[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f"  {len(per_msg_string_specs[n])},\n")
        f.write("};\n\n")
        # Nested-message tables.
        f.write("uint8_t* const TOP_MESSAGE_NESTED_POOLS[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f"  {n}_NESTED_POOL,\n")
        f.write("};\n\n")
        f.write("const uint32_t* const TOP_MESSAGE_NESTED_SPECS[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f"  {n}_NESTED_SPECS,\n")
        f.write("};\n\n")
        f.write("const uint32_t TOP_MESSAGE_NESTED_COUNTS[TOP_MESSAGE_COUNT] = {\n")
        for n in top_messages:
            f.write(f"  {len(per_msg_nested_specs[n])},\n")
        f.write("};\n")


@dataclass
class StringSpec:
    """Emitted string field plan: where in the cpp_obj slot to write the tagged
    pointer, which global header array element to point at, and the payload.

    owner_instance = 0 means the top-level cpp_obj; >0 means the (owner-1)'th
    nested instance in the per-top pool. slot_offset is the offset within
    that instance's buffer.
    """
    owner_instance: int
    slot_offset: int
    hdr_index: int  # index into the per-top STRING_HEADERS array
    payload_offset: int  # byte offset within <Msg>_STRING_PAYLOADS
    length: int


@dataclass
class NestedSpec:
    """A nested message instance placed in the per-top NESTED_INSTANCES pool.

    parent_instance = 0 means the top-level cpp_obj; >0 means the
    (parent-1)'th nested instance. parent_slot_offset is where in the parent's
    buffer to patch the pointer. nested_offset is this instance's byte offset
    within the per-top NESTED_INSTANCES pool.
    """
    parent_instance: int
    parent_slot_offset: int
    nested_offset: int


def _pick_string_length(
    rng: random.Random,
    field_name: str,
    rm: ResolvedMessage,
    runtime_lengths: Dict[str, Dict[str, List[int]]],
    max_string_len: int,
) -> int:
    """Pick a realistic length for the given string/bytes field.

    1. If we have HPB runtime data (from .inc) for this message's field, sample
       one of the observed lengths (then cap at max_string_len).
    2. Otherwise fall back to a bounded random length (1..16).
    """
    msg_simple = rm.name.split("_")[-1]
    per_msg = runtime_lengths.get(msg_simple, {})
    lengths = per_msg.get(field_name, [])
    if lengths:
        return min(max_string_len, rng.choice(lengths))
    return rng.randint(1, 16)


def _fill_primitive_fields(rm: ResolvedMessage, buf: bytearray, rng: random.Random,
                            payloads: bytearray, string_specs: List[StringSpec],
                            owner_instance: int,
                            runtime_lengths: Dict[str, Dict[str, List[int]]],
                            max_string_len: int) -> int:
    """Populate primitive fields + emit string specs. Returns hasbits value.

    - buf: the cpp_obj bytes being built (rm.obj_size long).
    - payloads: shared pool for this top-level message's string payloads.
    - string_specs: shared list; string specs are appended with owner_instance.
    - owner_instance: 0 for top-level cpp_obj; 1..N for nested instances.
    - runtime_lengths: {msg_simple_name: {field_name: [observed lengths...]}}
      from the HPB .inc file, via ProtobufAnalyzer.
    """
    hasbits = 0
    for rf in rm.fields:
        rel_fn = rf.field_number - rm.min_field_no + 1
        if rf.skip_reason:
            continue
        if rf.is_submessage:
            continue  # handled separately in the recursive builder

        present = True
        if rf.proto_type_code in (PROTO_TYPE_CODES["string"], PROTO_TYPE_CODES["bytes"]):
            length = _pick_string_length(rng, rf.name, rm, runtime_lengths, max_string_len)
            payload = rand_string(rng, length).encode("ascii")
            while len(payloads) % 16:
                payloads += b"\x00"
            payload_offset = len(payloads)
            payloads += payload
            string_specs.append(StringSpec(
                owner_instance=owner_instance,
                slot_offset=rf.offset,
                hdr_index=len(string_specs),
                payload_offset=payload_offset,
                length=length,
            ))
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
    return hasbits


def _write_hasbits(rm: ResolvedMessage, buf: bytearray, hasbits: int) -> None:
    n_rel = rm.max_field_no - rm.min_field_no + 2
    n_chunks = (n_rel + 31) // 32
    for c in range(n_chunks):
        chunk_val = (hasbits >> (32 * c)) & 0xFFFFFFFF
        off = TOPLEVEL_HASBITS_OFFSET + 4 * c
        buf[off:off + 4] = chunk_val.to_bytes(4, "little")


def build_instance_bytes(
    rm: ResolvedMessage,
    resolved: Dict[str, ResolvedMessage],
    seed: int,
    top_name: str,
    max_nested_depth: int,
    runtime_lengths: Dict[str, Dict[str, List[int]]],
    max_string_len: int,
) -> Tuple[bytes, bytes, List[StringSpec], bytes, List[NestedSpec]]:
    """Return (top_cpp_obj_bytes, nested_pool_bytes, string_specs, payloads, nested_specs).

    Top cpp_obj holds primitives only. Strings live in a separate pool (see
    StringSpec). Nested cpp_obj instances live in another separate pool; for
    each nested slot in the parent we emit a NestedSpec that tells the runtime
    fixup code to patch the parent's slot with the absolute address of the
    nested instance.

    Cycles / very-deep nesting are capped by max_nested_depth. A submessage
    field at depth >= max_nested_depth becomes a zero-present slot (no
    instance allocated, not marked in hasbits).
    """
    rng = det_rng(seed, top_name)
    # Fill the top-level cpp_obj.
    buf = bytearray(rm.obj_size)
    string_specs: List[StringSpec] = []
    payloads = bytearray()
    nested_specs: List[NestedSpec] = []
    # Pool of nested instance bytes, padded to 16B between instances.
    nested_pool = bytearray()

    # Returns the hasbits word value for the given instance's submessage
    # fields (using the rel_fn-based indexing), filling nested_pool and
    # nested_specs recursively.
    def emit_instance(
        current_rm: ResolvedMessage,
        current_buf: bytearray,
        current_instance_id: int,
        depth: int,
        rng_local: random.Random,
    ) -> int:
        # Primitives + strings.
        hb = _fill_primitive_fields(
            current_rm, current_buf, rng_local, payloads, string_specs,
            owner_instance=current_instance_id,
            runtime_lengths=runtime_lengths,
            max_string_len=max_string_len,
        )
        # Nested submessage fields.
        if depth >= max_nested_depth:
            _write_hasbits(current_rm, current_buf, hb)
            return hb
        for rf in current_rm.fields:
            if rf.skip_reason or not rf.is_submessage:
                continue
            if rf.submessage_name is None or rf.submessage_name not in resolved:
                continue
            child_rm = resolved[rf.submessage_name]
            # Allocate child instance slot in nested_pool, 16-byte aligned.
            while len(nested_pool) % 16:
                nested_pool.append(0)
            child_offset = len(nested_pool)
            # Reserve child bytes; fill later so we can mutate.
            nested_pool.extend(b"\x00" * child_rm.obj_size)
            child_buf = bytearray(child_rm.obj_size)
            # Deterministic RNG per (parent, field, depth).
            child_rng = det_rng(seed, f"{top_name}:{current_instance_id}:{rf.field_number}:{depth}")
            child_id = len(nested_specs) + 1  # 1-based; 0 = top
            # Assign an ID first so nested_specs entries match up.
            nested_specs.append(NestedSpec(
                parent_instance=current_instance_id,
                parent_slot_offset=rf.offset,
                nested_offset=child_offset,
            ))
            # Recurse.
            emit_instance(child_rm, child_buf, child_id, depth + 1, child_rng)
            # Copy filled child bytes back into nested_pool.
            nested_pool[child_offset:child_offset + child_rm.obj_size] = child_buf
            rel_fn = rf.field_number - current_rm.min_field_no + 1
            hb |= 1 << rel_fn
        _write_hasbits(current_rm, current_buf, hb)
        return hb

    emit_instance(rm, buf, 0, 0, rng)

    # Tail cushion for payloads so trailing 16B reads don't run off.
    while len(payloads) % 16:
        payloads += b"\x00"
    payloads += b"\x00" * 16

    return bytes(buf), bytes(nested_pool), string_specs, bytes(payloads), nested_specs


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
    ap.add_argument("--max-nested-depth", type=int, default=DEFAULT_MAX_NESTED_DEPTH,
                    help=f"Max submessage nesting depth (default: {DEFAULT_MAX_NESTED_DEPTH}). "
                         f"Submessages deeper than this get zero slots.")
    ap.add_argument("--max-string-len", type=int, default=DEFAULT_MAX_STRING_LEN,
                    help=f"Cap on individual string/bytes payload length (default: "
                         f"{DEFAULT_MAX_STRING_LEN}). Real HPB strings reach MBs which "
                         f"bloat Verilator binaries.")
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

    # Extract HPB runtime data (.inc file) so we can use real per-field string
    # and bytes lengths. This matches what the Lynx analytical model sees when
    # computing feature vectors from the same .proto + .inc.
    runtime_lengths: Dict[str, Dict[str, List[int]]] = {}
    rd = analysis.runtime_data or {}
    for msg_name, rd_entry in rd.items():
        per_field: Dict[str, List[int]] = {}
        for fname, lengths in (rd_entry.field_string_lengths or {}).items():
            per_field[fname] = list(lengths)
        for fname, lengths in (rd_entry.field_bytes_lengths or {}).items():
            per_field.setdefault(fname, []).extend(lengths)
        if per_field:
            runtime_lengths[msg_name] = per_field

    emit_header(resolved, out_h, args.proto, top)
    emit_source(resolved, out_c, out_h, top, args.seed, args.max_nested_depth,
                runtime_lengths=runtime_lengths,
                max_string_len=args.max_string_len)

    n_fields_with_rt = sum(len(v) for v in runtime_lengths.values())
    print(f"Wrote {out_h} ({len(resolved)} descriptors).", file=sys.stderr)
    print(f"Wrote {out_c} (top-level: {top}, .inc runtime fields: {n_fields_with_rt}).",
          file=sys.stderr)


if __name__ == "__main__":
    main()
