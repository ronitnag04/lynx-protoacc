#ifndef BENCH_COMMON_H
#define BENCH_COMMON_H

#include <stdint.h>
#include <stdio.h>

static inline uint64_t read_mcycle(void) {
    uint64_t v;
    asm volatile("csrr %0, mcycle" : "=r"(v));
    return v;
}

static inline void print_iter(const char *bench, int i, uint64_t cycles, uint64_t bytes) {
    printf("ACCEL_ITER: bench=%s i=%d cycles=%lu bytes=%lu\n", bench, i, cycles, bytes);
}

static inline void print_summary(const char *bench, const char *op,
                                 int iters, uint64_t total_cycles, uint64_t total_bytes) {
    printf("ACCEL_SUMMARY: bench=%s op=%s iters=%d total_cycles=%lu total_bytes=%lu\n",
           bench, op, iters, total_cycles, total_bytes);
}

#endif
