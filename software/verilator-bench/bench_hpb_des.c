// bench_hpb_des.c — HyperProtoBench deserializer benchmark (analog of
// bench_hpb_ser.c). For each top-level message in the bench schema we:
//   1. Feed the generator-produced wire bytes (TOP_MESSAGE_WIRE[m]) to
//      ProtoAcc's deserializer via AccelParseFromString_Helper.
//   2. Block on completion.
//   3. Report cycles consumed.
//
// Compile-time selector: -DBENCH_NAME="bench<N>" plus
// -DBENCH_DESCRIPTORS_H="bench<N>_descriptors.h" (handled by the Makefile).
//
// The hardware hardcodes top-level hasbits_offset = 0x10 at
// des/fieldhandler.scala:66, so the dest cpp_obj layout the descriptor uses
// (and that proto_to_accel.py emits) places hasbits at offset 0x10 and real
// fields at >= 0x18. This matches what our serializer benches use for
// consistency.

#include "accel_rocc.h"
#include "bench_common.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#ifndef BENCH_NAME
#define BENCH_NAME "hpb_des"
#endif

#ifndef ITERS
#define ITERS 1
#endif

#include BENCH_DESCRIPTORS_H

int main(void) {
    printf("%s: start, n_top=%d\n", BENCH_NAME, TOP_MESSAGE_COUNT);
    const int n_top = TOP_MESSAGE_COUNT;

    // AccelSetup() issues PROTOACC_OPCODE SFENCE + MEM_SETUP for the fixed
    // and array alloc regions (nested-message dest pools).
    AccelSetup();
    printf("%s: after AccelSetup\n", BENCH_NAME);

    // Dump first wire buffer for debug visibility.
    {
        const uint8_t *w0 = TOP_MESSAGE_WIRE[0];
        uint32_t w0_len = TOP_MESSAGE_WIRE_LEN[0];
        printf("%s: wire0=%p len=%u first=", BENCH_NAME, w0, w0_len);
        for (uint32_t k = 0; k < w0_len && k < 16; k++) {
            printf("%02x ", (unsigned)(unsigned char)w0[k]);
        }
        printf("\n");
    }

    // No warmup: each message is measured cold on its first (and only)
    // iteration. The cache-miss premium is intentionally baked in so the
    // downstream ML model can regress it against schema features.

    uint64_t total_cycles = 0;
    uint64_t total_bytes = 0;

    for (int m = 0; m < n_top; m++) {
        const uint64_t *descr = TOP_MESSAGE_DESCRIPTORS[m];
        uint8_t *dest = TOP_MESSAGE_DES_DEST[m];
        uint32_t dest_sz = TOP_MESSAGE_SIZES[m];
        const uint8_t *wire = TOP_MESSAGE_WIRE[m];
        uint32_t wire_len = TOP_MESSAGE_WIRE_LEN[m];
        for (int i = 0; i < ITERS; i++) {
            // The accelerator's array-alloc region is a monotonic bump
            // allocator that grows on every DO_PROTO_PARSE (it holds the
            // nested-message dest cpp_objs plus string byte slabs produced
            // during parsing). Reset it between iterations by re-issuing
            // MEM_SETUP; otherwise we'd need a region sized for all
            // ITERS × TOP_COUNT parses at once, which exceeds the
            // htif_nano BSS budget on realistic HPB workloads.
            AccelSetup();
            memset(dest, 0, dest_sz);
            uint64_t t0 = read_mcycle();
            AccelParseFromString_Helper(descr, dest, wire, wire_len);
            (void)block_on_completion();
            uint64_t t1 = read_mcycle();
            asm volatile("fence");
            uint64_t cyc = t1 - t0;
            printf("ACCEL_MESSAGE: bench=%s msg=%s i=%d cycles=%lu bytes=%lu\n",
                   BENCH_NAME, TOP_MESSAGE_NAMES[m], i, cyc, (uint64_t)wire_len);
            total_cycles += cyc;
            total_bytes += wire_len;
        }
    }

    print_summary(BENCH_NAME, "des", ITERS, total_cycles, total_bytes);
    return 0;
}
