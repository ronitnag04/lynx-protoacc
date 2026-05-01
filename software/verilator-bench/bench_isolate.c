// bench_isolate.c — serialize a SINGLE specific top-level message from a
// generated bench file, once. Useful for bisecting which message triggers
// the multi-field dispatch bug.
//
// Compile-time knobs:
//   -DBENCH_DESCRIPTORS_H=\"benchN_descriptors.h\"
//   -DISOLATE_MSG_NAME=\"M1\"     - the top-level message's name
//   -DISOLATE_MSG_IDX=0           - index into TOP_MESSAGE_* arrays

#include "accel_rocc.h"
#include "bench_common.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#ifndef BENCH_NAME
#define BENCH_NAME "isolate"
#endif
#ifndef ISOLATE_MSG_IDX
#define ISOLATE_MSG_IDX 0
#endif

#include BENCH_DESCRIPTORS_H

static uint8_t *instance_ptr(int m, uint32_t instance_id) {
    if (instance_id == 0) return TOP_MESSAGE_INSTANCE_PTRS[m];
    uint8_t *pool = TOP_MESSAGE_NESTED_POOLS[m];
    const uint32_t *nspecs = TOP_MESSAGE_NESTED_SPECS[m];
    return pool + nspecs[(instance_id - 1) * 3 + 2];
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
    int idx = ISOLATE_MSG_IDX;
    printf("isolate: msg=%s idx=%d\n", TOP_MESSAGE_NAMES[idx], idx);

    volatile char **ptrs = AccelSetupAllocRegionSerializer(8, 4096);

    uint8_t *obj = TOP_MESSAGE_INSTANCE_PTRS[idx];
    fixup_nested(idx);
    fixup_strings(idx);

    uint32_t sz = TOP_MESSAGE_SIZES[idx];
    uint32_t isz = TOP_MESSAGE_INSTANCE_BYTES[idx];
    printf("isolate: obj=%p obj_size=%u instance_bytes=%u\n", obj, sz, isz);
    for (uint32_t i = 0; i < isz; i += 8) {
        printf("  [%02x] 0x%016lx\n", i, *(uint64_t *)(obj + i));
    }

    const uint64_t *descr = TOP_MESSAGE_DESCRIPTORS[idx];
    printf("isolate: descr=%p\n", descr);

    uint64_t t0 = read_mcycle();
    AccelSerializeToString_Helper(descr, obj);
    volatile char *r = BlockOnSerializedValue(ptrs, 0);
    uint64_t t1 = read_mcycle();
    size_t len = GetSerializedLength(ptrs, 0);
    printf("ACCEL_ITER: bench=%s msg=%s cycles=%lu bytes=%lu\n",
           BENCH_NAME, TOP_MESSAGE_NAMES[idx], t1 - t0, (uint64_t)len);
    for (size_t k = 0; k < len && k < 64; k++) {
        printf("ACCELBYTE: %02x\n", (unsigned)(unsigned char)r[k]);
    }
    print_summary(BENCH_NAME, "ser", 1, t1 - t0, (uint64_t)len);
    return 0;
}
