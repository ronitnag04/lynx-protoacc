// bench_repro.c — minimal reproducer for the multi-field dispatch bug.
//
// Compile-time knob REPRO_CASE selects which field combination to build.
// Use this to bisect the failure: we know 1 field works, we want to find the
// smallest config that fails.

#include "accel_rocc.h"
#include "bench_common.h"
#include <stdio.h>
#include <string.h>

#ifndef REPRO_CASE
#define REPRO_CASE 1
// 1 = 1 int32
// 2 = 2 int32
// 3 = 2 strings
// 4 = int32 + string
// 5 = 3 int32
// 6 = fields {1, 3, 5} — 3 int32 with gaps at fn 2, 4 (span=5)
// 7 = field 1 only but min=3 max=17 (matches M1 span with 1 present field)
#endif

#define BENCH_NAME "repro"

// Each slot is 8-byte aligned. Field 1 at offset 24 (right after the hasbits
// area ending at 0x18). Subsequent fields +8.
#define OFF_HB 0x10
#define OFF_F1 0x18
#define OFF_F2 0x20
#define OFF_F3 0x28

#if REPRO_CASE == 1
#define N_FIELDS 1
#define MAX_FN  1
#define HASBITS 0x2    // rel_fn 1
#define OBJ_SZ  0x20
#endif

#if REPRO_CASE == 2
#define N_FIELDS 2
#define MAX_FN  2
#define HASBITS 0x6    // rel_fn 1+2
#define OBJ_SZ  0x28
#endif

#if REPRO_CASE == 3 || REPRO_CASE == 4
#define N_FIELDS 2
#define MAX_FN  2
#define HASBITS 0x6
#define OBJ_SZ  0x28
#endif

#if REPRO_CASE == 5
#define N_FIELDS 3
#define MAX_FN  3
#define HASBITS 0xE    // rel_fn 1+2+3
#define OBJ_SZ  0x30
#endif

#if REPRO_CASE == 6
// 3 int32 at fn {1, 3, 5} — span=5, rel_fns 1,3,5
#define N_FIELDS 3
#define MAX_FN  5
#define HASBITS 0x2A   // bits 1,3,5 → 2+8+32 = 0x2A
#define OBJ_SZ  0x30
#endif

#if REPRO_CASE == 7
// 1 int32 at fn 3, min=3 max=17 (matches M1 span)
#define N_FIELDS 1
#define MIN_FN  3
#define MAX_FN  17
#define HASBITS 0x2    // rel_fn 1 (= actual 3)
#define OBJ_SZ  0x30
#endif

#if REPRO_CASE == 8
// bench1 M1's shape: min=3 max=17, present fields at rel_fn 1,3,6,7,8,11,12,13,14,15
// simplified to JUST int32 primitives (no strings/floats/bools) to isolate the issue
#define N_FIELDS 10
#define MIN_FN  3
#define MAX_FN  17
#define HASBITS 0xF9CA   // exact M1 pattern
#define OBJ_SZ  0x78     // 120 bytes ≥ M1's 112
#endif

#if REPRO_CASE == 9
// Same shape as case 8, but ONLY 2 presents (rel_fn 14 and 15) — far from sentinel
// to test if iteration from high bits causes the issue.
#define N_FIELDS 2
#define MIN_FN  3
#define MAX_FN  17
#define HASBITS 0xC000   // bits 14,15
#define OBJ_SZ  0x78
#endif

#if REPRO_CASE == 10
// Same shape as case 8, but ONLY 2 presents at rel_fn 1 and 2 (lowest).
#define N_FIELDS 2
#define MIN_FN  3
#define MAX_FN  17
#define HASBITS 0x6      // bits 1,2
#define OBJ_SZ  0x78
#endif

#if REPRO_CASE == 11
// int32 + string where the string header+payload live IN THE SAME BUFFER as
// the cpp_obj (exactly matching what proto_to_accel.py emits). Goal: test if
// the intra-buffer string layout triggers the supportsGet fault.
#define N_FIELDS 2
#define MAX_FN  2
#define HASBITS 0x6
#define OBJ_SZ  0x50   // 80: space for obj + string header + payload + 16B tail cushion
#endif

#if REPRO_CASE == 12
// ONE string field in same-buffer layout. No int32. Same obj structure as case 11
// but only field 1 (string) is present. Tests if the SAME-BUFFER layout alone
// (independent of multi-field interaction) is the trigger.
#define N_FIELDS 1
#define MAX_FN  1
#define HASBITS 0x2
#define OBJ_SZ  0x50
#endif

#if REPRO_CASE == 13
// Same as case 11 (int32+string same-buffer) but the int32 at a HIGHER offset
// than the string, to see if the ORDER matters.
#define N_FIELDS 2
#define MAX_FN  2
#define HASBITS 0x6
#define OBJ_SZ  0x50
#endif

#if REPRO_CASE == 14
// Same as case 11 but BOTH fields are strings (test 2 same-buffer strings).
#define N_FIELDS 2
#define MAX_FN  2
#define HASBITS 0x6
#define OBJ_SZ  0x60
#endif

#ifndef MIN_FN
#define MIN_FN 1
#endif

// Build a descriptor with N_FIELDS primitive fields (int32 by default).
__attribute__((aligned(16)))
static const uint64_t REPRO_DESC[] = {
    0ULL,
    OBJ_SZ,
    OFF_HB,
    ((uint64_t)MIN_FN << 32) | (uint64_t)MAX_FN,
#if REPRO_CASE == 3
    // 2 strings
    (((uint64_t)9 & 0x1F) << 58) | OFF_F1, 0ULL,
    (((uint64_t)9 & 0x1F) << 58) | OFF_F2, 0ULL,
#elif REPRO_CASE == 4
    // int32 + string
    (((uint64_t)5 & 0x1F) << 58) | OFF_F1, 0ULL,
    (((uint64_t)9 & 0x1F) << 58) | OFF_F2, 0ULL,
#elif REPRO_CASE == 5
    (((uint64_t)5 & 0x1F) << 58) | OFF_F1, 0ULL,
    (((uint64_t)5 & 0x1F) << 58) | OFF_F2, 0ULL,
    (((uint64_t)5 & 0x1F) << 58) | OFF_F3, 0ULL,
#elif REPRO_CASE == 6
    // fn1, fn2=gap, fn3, fn4=gap, fn5
    (((uint64_t)5 & 0x1F) << 58) | OFF_F1, 0ULL,
    0ULL, 0ULL,
    (((uint64_t)5 & 0x1F) << 58) | OFF_F2, 0ULL,
    0ULL, 0ULL,
    (((uint64_t)5 & 0x1F) << 58) | OFF_F3, 0ULL,
#elif REPRO_CASE == 7
    // fn3 int32; fn4..17 all gaps (14 gaps). min=3 max=17.
    (((uint64_t)5 & 0x1F) << 58) | OFF_F1, 0ULL,
    0ULL, 0ULL, 0ULL, 0ULL, 0ULL, 0ULL, 0ULL, 0ULL,  // fn 4..7
    0ULL, 0ULL, 0ULL, 0ULL, 0ULL, 0ULL, 0ULL, 0ULL,  // fn 8..11
    0ULL, 0ULL, 0ULL, 0ULL, 0ULL, 0ULL, 0ULL, 0ULL,  // fn 12..15
    0ULL, 0ULL, 0ULL, 0ULL,                           // fn 16..17
#elif REPRO_CASE == 11
    // f1: int32 at 0x18; f2: string at 0x20. String header + payload inside obj.
    (((uint64_t)5 & 0x1F) << 58) | 0x18, 0ULL,
    (((uint64_t)9 & 0x1F) << 58) | 0x20, 0ULL,
#elif REPRO_CASE == 12
    // f1: string at 0x18. Header + payload inside obj.
    (((uint64_t)9 & 0x1F) << 58) | 0x18, 0ULL,
#elif REPRO_CASE == 13
    // f1 string at 0x18, f2 int32 at 0x20 (reverse of case 11)
    (((uint64_t)9 & 0x1F) << 58) | 0x18, 0ULL,
    (((uint64_t)5 & 0x1F) << 58) | 0x20, 0ULL,
#elif REPRO_CASE == 14
    // f1 string at 0x18, f2 string at 0x20
    (((uint64_t)9 & 0x1F) << 58) | 0x18, 0ULL,
    (((uint64_t)9 & 0x1F) << 58) | 0x20, 0ULL,
#elif REPRO_CASE == 8 || REPRO_CASE == 9 || REPRO_CASE == 10
    // All fields are int32 at increasing offsets. Every entry is a primitive
    // int32 even if that rel_fn isn't "present"; hasbits controls whether
    // hw processes it.
    (((uint64_t)5 & 0x1F) << 58) | 0x18, 0ULL,  // rel_fn 1 @ 0x18
    (((uint64_t)5 & 0x1F) << 58) | 0x20, 0ULL,  // rel_fn 2 @ 0x20
    (((uint64_t)5 & 0x1F) << 58) | 0x28, 0ULL,  // rel_fn 3 @ 0x28
    (((uint64_t)5 & 0x1F) << 58) | 0x30, 0ULL,  // rel_fn 4 @ 0x30
    (((uint64_t)5 & 0x1F) << 58) | 0x38, 0ULL,  // rel_fn 5 @ 0x38
    (((uint64_t)5 & 0x1F) << 58) | 0x40, 0ULL,  // rel_fn 6 @ 0x40
    (((uint64_t)5 & 0x1F) << 58) | 0x48, 0ULL,  // rel_fn 7 @ 0x48
    (((uint64_t)5 & 0x1F) << 58) | 0x50, 0ULL,  // rel_fn 8 @ 0x50
    (((uint64_t)5 & 0x1F) << 58) | 0x58, 0ULL,  // rel_fn 9 @ 0x58
    (((uint64_t)5 & 0x1F) << 58) | 0x60, 0ULL,  // rel_fn 10 @ 0x60
    (((uint64_t)5 & 0x1F) << 58) | 0x68, 0ULL,  // rel_fn 11 @ 0x68
    (((uint64_t)5 & 0x1F) << 58) | 0x70, 0ULL,  // rel_fn 12 @ 0x70
    // remaining 3 rel_fns to reach span 15 (max-min+1)
    0ULL, 0ULL,
    0ULL, 0ULL,
    0ULL, 0ULL,
#else
    (((uint64_t)5 & 0x1F) << 58) | OFF_F1, 0ULL,
  #if N_FIELDS >= 2
    (((uint64_t)5 & 0x1F) << 58) | OFF_F2, 0ULL,
  #endif
#endif
    0ULL, // is_submessage_bitfield
};

int main(void) {
    printf("repro: case=%d\n", REPRO_CASE);

    __attribute__((aligned(16))) static uint64_t obj[OBJ_SZ / 8];
    memset(obj, 0, sizeof(obj));
    *(uint32_t *)((char *)obj + OFF_HB) = HASBITS;

#if REPRO_CASE == 3
    static __attribute__((aligned(16))) uint64_t s1[4] = {0, 5, 0, 0};
    static __attribute__((aligned(16))) uint64_t s2[4] = {0, 5, 0, 0};
    static const char p1[] = "hello";
    static const char p2[] = "world";
    s1[0] = (uint64_t)p1; s2[0] = (uint64_t)p2;
    *(uint64_t *)((char *)obj + OFF_F1) = ((uint64_t)s1) | 0x3;
    *(uint64_t *)((char *)obj + OFF_F2) = ((uint64_t)s2) | 0x3;
#elif REPRO_CASE == 4
    *(int32_t *)((char *)obj + OFF_F1) = 42;
    static __attribute__((aligned(16))) uint64_t s1[4] = {0, 5, 0, 0};
    static const char p1[] = "hello";
    s1[0] = (uint64_t)p1;
    *(uint64_t *)((char *)obj + OFF_F2) = ((uint64_t)s1) | 0x3;
#elif REPRO_CASE == 5 || REPRO_CASE == 6
    *(int32_t *)((char *)obj + OFF_F1) = 1;
    *(int32_t *)((char *)obj + OFF_F2) = 2;
    *(int32_t *)((char *)obj + OFF_F3) = 3;
#elif REPRO_CASE == 7
    *(int32_t *)((char *)obj + OFF_F1) = 42;
#elif REPRO_CASE == 8 || REPRO_CASE == 9 || REPRO_CASE == 10
    // Fill all 12 int32 slots with distinct values.
    for (int i = 0; i < 12; i++) {
        *(int32_t *)((char *)obj + 0x18 + i * 8) = 0x100 + i;
    }
#elif REPRO_CASE == 11
    // int32 at 0x18; string tagged-ptr at 0x20 points to header at 0x28; hdr char*
    // points to payload at 0x38 (inside same obj buffer, mirroring generator).
    {
        char *p = (char *)obj;
        *(int32_t *)(p + 0x18) = 42;
        // header: char*=obj+0x38, len=5
        *(uint64_t *)(p + 0x28) = (uint64_t)(p + 0x38);
        *(uint64_t *)(p + 0x30) = 5;
        memcpy(p + 0x38, "hello", 5);
        // tagged slot
        *(uint64_t *)(p + 0x20) = ((uint64_t)(p + 0x28)) | 0x3;
    }
#elif REPRO_CASE == 12
    // String at 0x18, header at 0x20, payload at 0x30.
    {
        char *p = (char *)obj;
        *(uint64_t *)(p + 0x20) = (uint64_t)(p + 0x30);
        *(uint64_t *)(p + 0x28) = 5;
        memcpy(p + 0x30, "hello", 5);
        *(uint64_t *)(p + 0x18) = ((uint64_t)(p + 0x20)) | 0x3;
    }
#elif REPRO_CASE == 13
    // f1 string at 0x18, f2 int32 at 0x20 — string header + payload at tail.
    {
        char *p = (char *)obj;
        *(int32_t *)(p + 0x20) = 42;
        // string hdr at 0x28, payload at 0x38
        *(uint64_t *)(p + 0x28) = (uint64_t)(p + 0x38);
        *(uint64_t *)(p + 0x30) = 5;
        memcpy(p + 0x38, "hello", 5);
        *(uint64_t *)(p + 0x18) = ((uint64_t)(p + 0x28)) | 0x3;
    }
#elif REPRO_CASE == 14
    // Two strings, both same-buffer layout.
    {
        char *p = (char *)obj;
        // s1 hdr at 0x28, payload at 0x40
        *(uint64_t *)(p + 0x28) = (uint64_t)(p + 0x40);
        *(uint64_t *)(p + 0x30) = 5;
        memcpy(p + 0x40, "hello", 5);
        // s2 hdr at 0x50, payload at 0x58  (pay attention: 0x58 offset-1 from end of s1 hdr)
        *(uint64_t *)(p + 0x50) = (uint64_t)(p + 0x58);
        *(uint64_t *)(p + 0x50 + 8) = 5;
        memcpy(p + 0x58, "world", 5);
        // tagged slots
        *(uint64_t *)(p + 0x18) = ((uint64_t)(p + 0x28)) | 0x3;
        *(uint64_t *)(p + 0x20) = ((uint64_t)(p + 0x50)) | 0x3;
    }
#else
    *(int32_t *)((char *)obj + OFF_F1) = 42;
  #if N_FIELDS >= 2
    *(int32_t *)((char *)obj + OFF_F2) = 100;
  #endif
#endif

    volatile char **ptrs = AccelSetupAllocRegionSerializer(8, 256);

    // Warmup
    AccelSerializeToString_Helper(REPRO_DESC, obj);
    volatile char *w = BlockOnSerializedValue(ptrs, 0);
    size_t wl = GetSerializedLength(ptrs, 0);
    printf("repro: warmup len=%lu\n", (uint64_t)wl);

    // One measured iter with byte dump.
    uint64_t t0 = read_mcycle();
    AccelSerializeToString_Helper(REPRO_DESC, obj);
    volatile char *r = BlockOnSerializedValue(ptrs, 1);
    uint64_t t1 = read_mcycle();
    size_t l = GetSerializedLength(ptrs, 1);
    printf("ACCEL_ITER: bench=repro i=0 cycles=%lu bytes=%lu\n", t1 - t0, (uint64_t)l);
    for (size_t k = 0; k < l && k < 64; k++) {
        printf("ACCELBYTE: %02x\n", (unsigned)(unsigned char)r[k]);
    }
    print_summary(BENCH_NAME, "ser", 1, t1 - t0, (uint64_t)l);
    return 0;
}
