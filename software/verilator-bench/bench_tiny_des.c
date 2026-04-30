// bench_tiny_des.c — smallest end-to-end ProtoAcc deserializer test.
//
// Input wire bytes: 08 2a 12 05 68 65 6c 6c 6f (int32 f1=42, string f2="hello").
// Same descriptor table as bench_tiny_ser.c.
//
// The deserializer writes fixed-size fields directly into dest cpp_obj slots.
// For strings (TYPE_STRING), the output goes through the fixed/array alloc regions
// set up by AccelSetup — the string slot in cpp_obj receives a tagged-pointer form
// analogous to the serializer input convention.

#include "accel_rocc.h"
#include "bench_common.h"
#include <stdio.h>
#include <string.h>

#ifndef ITERS
#define ITERS 4
#endif

#define BENCH_NAME "tiny_des"

// Hardware bakes top-level hasbits_offset = 0x10 (16) in des/fieldhandler.scala:66.
// desc[2] in the descriptor table is ONLY consulted for nested messages.
// So our cpp_obj layout must reserve [0..16] for vptr/cached_size, [16..20] for hasbits,
// and put real fields at offsets >= 24 (8-byte aligned).
#define OFFS_HASBITS 16
#define OFFS_F1      24   // int32, 4-byte slot (cpp_size_log2 = 2)
#define OFFS_F2      32   // string, 8-byte tagged-ptr slot
#define OBJ_SIZE     40

__attribute__((aligned(16)))
static const uint64_t TINY_DESC[] = {
    0ULL,                                      // [0] vptr
    (uint64_t)OBJ_SIZE,                        // [1] cpp-obj size
    (uint64_t)OFFS_HASBITS,                    // [2] hasbits offset (nested only; top-level ignored)
    ((uint64_t)1 << 32) | 2ULL,                // [3] (min<<32)|max
    (((uint64_t)5 & 0x1F) << 58) | ((uint64_t)OFFS_F1),  // [4] INT32 at OFFS_F1
    0ULL,                                      // [5] no submessage
    (((uint64_t)9 & 0x1F) << 58) | ((uint64_t)OFFS_F2),  // [6] STRING at OFFS_F2
    0ULL,                                      // [7] no submessage
    0ULL,                                      // [8] is_submessage bitfield
};

__attribute__((aligned(16)))
static const uint8_t WIRE[] = {
    0x08, 0x2a,                         // f1 = 42 (varint)
    0x12, 0x05, 'h','e','l','l','o',    // f2 = "hello" (length-delimited)
};

int main(void) {
    __attribute__((aligned(16))) static uint64_t dest_words[OBJ_SIZE / 8];

    // AccelSetup issues PROTOACC_OPCODE SFENCE + MEM_SETUP (two alloc regions).
    AccelSetup();

    // Warm-up
    memset(dest_words, 0, sizeof(dest_words));
    AccelParseFromString_Helper(TINY_DESC, dest_words, WIRE, sizeof(WIRE));
    (void)block_on_completion();
    asm volatile("fence");
    printf("bench_tiny_des: warmup f1=%d\n", *(int32_t *)((char *)dest_words + OFFS_F1));

    uint64_t total_cycles = 0;
    uint64_t total_bytes = 0;
    for (int i = 0; i < ITERS; i++) {
        memset(dest_words, 0, sizeof(dest_words));
        uint64_t t0 = read_mcycle();
        AccelParseFromString_Helper(TINY_DESC, dest_words, WIRE, sizeof(WIRE));
        (void)block_on_completion();
        uint64_t t1 = read_mcycle();
        asm volatile("fence");

        uint64_t cyc = t1 - t0;
        print_iter(BENCH_NAME, i, cyc, (uint64_t)sizeof(WIRE));
        total_cycles += cyc;
        total_bytes += sizeof(WIRE);

        if (i == 0) {
            // Dump dest words so we can see where values actually landed.
            for (int w = 0; w < OBJ_SIZE / 8; w++) {
                printf("DEST[%d] @ off %d = 0x%016lx\n", w, w * 8, dest_words[w]);
            }
            int32_t f1 = *(int32_t *)((char *)dest_words + OFFS_F1);
            uint64_t tagged = *(uint64_t *)((char *)dest_words + OFFS_F2);
            uint64_t strobj = tagged & ~(uint64_t)0x7;
            if (strobj == 0) {
                printf("ACCEL_DES: f1=%d f2=<null strobj> tagged=0x%lx\n", f1, tagged);
            } else {
                const char *data = (const char *)((uint64_t *)strobj)[0];
                uint64_t len = ((uint64_t *)strobj)[1];
                printf("ACCEL_DES: f1=%d f2_len=%lu f2=", f1, len);
                for (uint64_t k = 0; k < len && k < 32; k++) putchar(data[k]);
                putchar('\n');
            }
        }
    }

    print_summary(BENCH_NAME, "des", ITERS, total_cycles, total_bytes);
    return 0;
}
