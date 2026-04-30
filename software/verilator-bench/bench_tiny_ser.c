// bench_tiny_ser.c — smallest end-to-end ProtoAcc serializer test.
//
// Message shape (protobuf):
//   message Tiny {
//     optional int32  f1 = 1 = 42;
//     optional string f2 = 2 = "hello";
//   }
// Expected wire bytes: 08 2a 12 05 68 65 6c 6c 6f (9 bytes).
//
// cpp_obj layout — the serializer reads desc[2] (hasbits_offset) from the descriptor,
// so we can place hasbits wherever the descriptor says. We keep it at offset 8
// for a compact layout:
//   [ 0..8]  vptr/cached_size placeholder (zero)
//   [ 8..12] hasbits (bit 1 + bit 2 = 0x6)
//   [16..20] int32 f1 value (cpp_size_log2=2 → 4-byte slot)
//   [24..32] tagged ArenaStringPtr for f2: (addr of strobj) | 0x3
//
// strobj layout (16 bytes): [0..8] data_ptr, [8..16] length.

#include "accel_rocc.h"
#include "bench_common.h"
#include <stdio.h>
#include <string.h>

#ifndef ITERS
#define ITERS 4
#endif

#define BENCH_NAME "tiny_ser"

// Field slot offsets within cpp_obj. Serializer reads hasbits_offset from desc[2],
// so we keep the compact layout from the proven-working first run.
#define OFFS_HASBITS 8
#define OFFS_F1      16
#define OFFS_F2      24
#define OBJ_SIZE     32

// Descriptor table — 16B-aligned, covers 2 fields (min=1, max=2) plus is_submessage bitfield.
__attribute__((aligned(16)))
static const uint64_t TINY_DESC[] = {
    /* [0] vptr              */ 0ULL,
    /* [1] cpp_obj size      */ (uint64_t)OBJ_SIZE,
    /* [2] hasbits offset    */ (uint64_t)OFFS_HASBITS,
    /* [3] (min<<32) | max   */ ((uint64_t)1 << 32) | 2ULL,
    /* [4] field 1 entry: type=INT32(5), offset=OFFS_F1, not repeated */
    (((uint64_t)0) << 63) | (((uint64_t)5 & 0x1F) << 58) | ((uint64_t)OFFS_F1),
    /* [5] submessage ptr    */ 0ULL,
    /* [6] field 2 entry: type=STRING(9), offset=OFFS_F2 */
    (((uint64_t)0) << 63) | (((uint64_t)9 & 0x1F) << 58) | ((uint64_t)OFFS_F2),
    /* [7] submessage ptr    */ 0ULL,
    /* [8] is_submessage_bitfield (neither field is a submessage) */ 0ULL,
};

int main(void) {
    // 16B-aligned cpp_obj
    __attribute__((aligned(16))) static uint64_t obj_words[OBJ_SIZE / 8];
    memset(obj_words, 0, sizeof(obj_words));

    // String-object header + inline payload for f2.
    __attribute__((aligned(16))) static uint64_t strobj[4];
    static const char payload[] = "hello"; // length 5 (no null)
    strobj[0] = (uint64_t)payload; // data_ptr
    strobj[1] = 5;                 // length
    strobj[2] = 0; strobj[3] = 0;  // unused slack

    // Populate cpp_obj
    char *obj = (char *)obj_words;
    *(uint32_t *)(obj + OFFS_HASBITS) = 0x6; // bits 1 and 2 present
    *(int32_t *)(obj + OFFS_F1) = 42;
    *(uint64_t *)(obj + OFFS_F2) = ((uint64_t)strobj) | 0x3ULL;

    printf("bench_tiny_ser: obj=%p strobj=%p payload=%p desc=%p\n",
           (void *)obj, (void *)strobj, (void *)payload, (void *)TINY_DESC);

    // Setup — 8 output pointers, 256 bytes of data region is plenty.
    volatile char **ptrs = AccelSetupAllocRegionSerializer(8, 256);

    // Warm-up (not measured) — same payload to prime TLB/cache
    AccelSerializeToString_Helper(TINY_DESC, obj);
    volatile char *warm = BlockOnSerializedValue(ptrs, 0);
    size_t warm_len = GetSerializedLength(ptrs, 0);
    printf("bench_tiny_ser: warmup len=%lu ptr=%p\n", (uint64_t)warm_len, warm);

    // Measured iterations
    uint64_t total_cycles = 0;
    uint64_t total_bytes = 0;
    for (int i = 0; i < ITERS; i++) {
        int idx = i + 1; // ptrs[0] held warmup
        uint64_t t0 = read_mcycle();
        AccelSerializeToString_Helper(TINY_DESC, obj);
        volatile char *res = BlockOnSerializedValue(ptrs, idx);
        uint64_t t1 = read_mcycle();
        size_t len = GetSerializedLength(ptrs, idx);
        uint64_t cyc = t1 - t0;
        print_iter(BENCH_NAME, i, cyc, (uint64_t)len);
        total_cycles += cyc;
        total_bytes += len;

        if (i == 0) {
            printf("ACCEL: SERIALIZEDLENGTH: %lu SERPTR: %p\n", (uint64_t)len, res);
            for (size_t k = 0; k < len; k++) {
                printf("ACCELBYTE: %02x\n", (unsigned)(unsigned char)res[k]);
            }
        }
    }

    print_summary(BENCH_NAME, "ser", ITERS, total_cycles, total_bytes);
    return 0;
}
