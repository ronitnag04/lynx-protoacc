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

// Walk every top-level instance and patch relocatable pointers the generator
// emitted as byte-offset placeholders. See proto_to_accel.py's
// build_instance_bytes() for the encoding:
//   - Slot marker (in cpp_obj): ((hdr_offset << 16) | 0xFACE)
//       → replace with (instance_base + hdr_offset) | 0x3  (tagged ArenaStringPtr)
//   - Header marker (inside the string pool, first 8B of each string header):
//       ((payload_offset << 16) | 0xF00D)
//       → replace with (instance_base + payload_offset)     (untagged char*)
// The markers are arbitrary 16-bit sentinels chosen to make accidental
// collisions with primitive values vanishingly unlikely.
#define MARKER_SLOT 0xFACEULL
#define MARKER_HDR  0xF00DULL
#define MARKER_MASK 0xFFFFULL

static void fixup_instance(uint8_t *base, uint32_t size) {
    for (uint32_t off = 0; off + 8 <= size; off += 8) {
        uint64_t *slot = (uint64_t *)(base + off);
        uint64_t v = *slot;
        uint64_t marker = v & MARKER_MASK;
        if (marker == MARKER_SLOT) {
            uint64_t hdr_off = v >> 16;
            if (hdr_off < size) {
                *slot = ((uint64_t)(uintptr_t)(base + hdr_off)) | 0x3ULL;
            }
        } else if (marker == MARKER_HDR) {
            uint64_t payload_off = v >> 16;
            if (payload_off < size) {
                *slot = (uint64_t)(uintptr_t)(base + payload_off);
            }
        }
    }
}

int main(void) {
    printf("%s: start, n_top=%d\n", BENCH_NAME, TOP_MESSAGE_COUNT);
    // Serializer output region: one ptr per (message × iter × warmup).
    const int n_top = TOP_MESSAGE_COUNT;
    const int total_ptrs = n_top * (ITERS + 1) + 1;  // +1 warmup ptr headroom
    volatile char **ptrs = AccelSetupAllocRegionSerializer(total_ptrs + 4, 8192);
    printf("%s: after SetupSer ptrs=%p\n", BENCH_NAME, ptrs);

    // Fix up string pointers once. We walk the FULL instance buffer
    // (includes the string pool appended after the cpp_obj), not just the
    // cpp_obj portion, so headers inside the pool get their `char* data`
    // pointers patched too.
    for (int m = 0; m < n_top; m++) {
        uint8_t *inst = TOP_MESSAGE_INSTANCE_PTRS[m];
        fixup_instance(inst, TOP_MESSAGE_INSTANCE_BYTES[m]);
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
