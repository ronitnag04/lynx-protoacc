// bench_hpb_ser.c — HyperProtoBench serializer benchmark.
//
// Compile-time selector: -DBENCH_NAME_ID=N picks which generated bench the
// Makefile wires in via `gen/benchN_*.c` + `gen/benchN_descriptors.h`.
//
// Each pre-built instance in *_INSTANCE[] contains placeholder byte offsets
// for string/bytes fields (stored with a 0x1 marker in the low bits of the
// char* slot). We fix those up at startup so the tagged ArenaStringPtr
// points at real memory inside the instance buffer.

#include "accel_rocc.h"
#include "bench_common.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#ifndef BENCH_NAME
#define BENCH_NAME "hpb"
#endif

#ifndef ITERS
#define ITERS 4
#endif

#include BENCH_DESCRIPTORS_H

// Runtime fixup: for each top-level message, walk NESTED_SPECS and STRING_SPECS
// and patch cpp_obj slots with real addresses.
//
// Each top-level message has two pools of instances:
//   - TOP_MESSAGE_INSTANCE_PTRS[m]        : the top cpp_obj (instance 0)
//   - TOP_MESSAGE_NESTED_POOLS[m]         : concat of all nested cpp_objs
//                                            (instance 1, 2, ... at byte
//                                            offsets given by NESTED_SPECS)
//
// NESTED_SPECS (3 u32 per nested instance): {parent_instance, parent_slot_offset, nested_offset}
//   parent_instance == 0  → patch top cpp_obj
//   parent_instance == k  → patch the (k-1)'th earlier nested instance
//   (specs are emitted in a pre-order traversal so parents always come before children.)
// Writes (uint64_t)(nested_pool + nested_offset) into parent's slot.
//
// STRING_SPECS (5 u32 per string): {owner_instance, slot_offset, hdr_index, payload_offset, length}
//   owner_instance 0 = top, >0 = nested pool slot.
// Fills: HEADERS[hdr*32] = (char*)(payloads+pay_off), HEADERS[hdr*32+8] = len,
//        then owner_buf[slot_off] = ((uint64_t)&HEADERS[hdr*32]) | 0x3.

static uint8_t *instance_ptr(int m, uint32_t instance_id) {
    if (instance_id == 0) return TOP_MESSAGE_INSTANCE_PTRS[m];
    uint8_t *pool = TOP_MESSAGE_NESTED_POOLS[m];
    const uint32_t *nspecs = TOP_MESSAGE_NESTED_SPECS[m];
    // (instance_id - 1)'th nested instance.
    uint32_t nested_offset = nspecs[(instance_id - 1) * 3 + 2];
    return pool + nested_offset;
}

static void fixup_nested(int m) {
    const uint32_t *specs = TOP_MESSAGE_NESTED_SPECS[m];
    uint32_t n = TOP_MESSAGE_NESTED_COUNTS[m];
    uint8_t *pool = TOP_MESSAGE_NESTED_POOLS[m];
    for (uint32_t i = 0; i < n; i++) {
        uint32_t parent_inst = specs[i * 3 + 0];
        uint32_t parent_slot = specs[i * 3 + 1];
        uint32_t nested_off  = specs[i * 3 + 2];
        uint8_t *parent = instance_ptr(m, parent_inst);
        *(uint64_t *)(parent + parent_slot) = (uint64_t)(uintptr_t)(pool + nested_off);
    }
}

static void fixup_strings(int m) {
    const uint32_t *specs = TOP_MESSAGE_STRING_SPECS[m];
    uint32_t n = TOP_MESSAGE_STRING_COUNTS[m];
    uint8_t *hdrs = TOP_MESSAGE_STRING_HEADERS[m];
    uint8_t *pool = TOP_MESSAGE_STRING_PAYLOADS[m];
    for (uint32_t i = 0; i < n; i++) {
        uint32_t owner    = specs[i * 5 + 0];
        uint32_t slot_off = specs[i * 5 + 1];
        uint32_t hdr_idx  = specs[i * 5 + 2];
        uint32_t pay_off  = specs[i * 5 + 3];
        uint32_t length   = specs[i * 5 + 4];
        uint8_t *owner_buf = instance_ptr(m, owner);
        uint8_t *hdr = hdrs + hdr_idx * 32;
        *(uint64_t *)(hdr + 0) = (uint64_t)(uintptr_t)(pool + pay_off);
        *(uint64_t *)(hdr + 8) = length;
        *(uint64_t *)(owner_buf + slot_off) = ((uint64_t)(uintptr_t)hdr) | 0x3ULL;
    }
}

int main(void) {
    printf("%s: start, n_top=%d\n", BENCH_NAME, TOP_MESSAGE_COUNT);
    // Serializer output region: one ptr per (message × iter × warmup).
    const int n_top = TOP_MESSAGE_COUNT;
    const int total_ptrs = n_top * (ITERS + 1) + 1;  // +1 warmup ptr headroom
    // Data region sized to absorb 40 iters × up to ~2 KB per message. Matches
    // the static ACCEL_SER_DATA_BYTES region in accel_rocc.c (128 KB).
    volatile char **ptrs = AccelSetupAllocRegionSerializer(total_ptrs + 4, 128 * 1024);
    printf("%s: after SetupSer ptrs=%p\n", BENCH_NAME, ptrs);

    // Fix up nested-message pointers FIRST so strings can reference nested
    // instances by owner_id. Both operations are idempotent.
    for (int m = 0; m < n_top; m++) {
        fixup_nested(m);
        fixup_strings(m);
    }
    printf("%s: fixups done\n", BENCH_NAME);

    // Dump the first instance for debugging.
    uint8_t *d = TOP_MESSAGE_INSTANCE_PTRS[0];
    uint32_t ds = TOP_MESSAGE_INSTANCE_BYTES[0];
    printf("%s: inst0=%p size=%u\n", BENCH_NAME, d, ds);
    for (uint32_t i = 0; i < ds; i += 8) {
        uint64_t v = *(uint64_t*)(d + i);
        printf("  [%02x] 0x%016lx\n", i, v);
    }

    // Warm-up (not measured).
    printf("%s: dispatching warmup for %s\n", BENCH_NAME, TOP_MESSAGE_NAMES[0]);
    AccelSerializeToString_Helper(TOP_MESSAGE_DESCRIPTORS[0],
                                  TOP_MESSAGE_INSTANCE_PTRS[0]);
    (void)BlockOnSerializedValue(ptrs, 0);
    printf("%s: warmup done\n", BENCH_NAME);

    uint64_t total_cycles = 0;
    uint64_t total_bytes = 0;
    int idx = 1;

    for (int m = 0; m < n_top; m++) {
        const uint64_t *descr = TOP_MESSAGE_DESCRIPTORS[m];
        uint8_t *obj = TOP_MESSAGE_INSTANCE_PTRS[m];
        for (int i = 0; i < ITERS; i++, idx++) {
            uint64_t t0 = read_mcycle();
            AccelSerializeToString_Helper(descr, obj);
            (void)BlockOnSerializedValue(ptrs, idx);
            uint64_t t1 = read_mcycle();
            size_t len = GetSerializedLength(ptrs, idx);
            uint64_t cyc = t1 - t0;
            printf("ACCEL_ITER: bench=%s msg=%s i=%d cycles=%lu bytes=%lu\n",
                   BENCH_NAME, TOP_MESSAGE_NAMES[m], i, cyc, (uint64_t)len);
            total_cycles += cyc;
            total_bytes += len;
        }
    }

    print_summary(BENCH_NAME, "ser", n_top * ITERS, total_cycles, total_bytes);
    return 0;
}
