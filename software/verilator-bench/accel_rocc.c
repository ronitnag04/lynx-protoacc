#include "accel_rocc.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>

#ifndef ACCEL_REGION_DEFAULT_SHIFT
#define ACCEL_REGION_DEFAULT_SHIFT 13
#endif

#define PAGESIZE_BYTES 4096

static inline void touch_all_pages(char *region, size_t max_bytes) {
    for (uint64_t i = 0; i < max_bytes; i += PAGESIZE_BYTES) {
        region[i] = 0;
    }
}

// htif_nano has a tiny malloc heap; use static BSS regions instead so the first bench
// (9-byte wire input) doesn't exhaust it. Sized for small benchmarks; grow via
// ACCEL_STATIC_REGION_BYTES if ported to larger workloads.
#ifndef ACCEL_STATIC_REGION_BYTES
#define ACCEL_STATIC_REGION_BYTES (32 * 1024)
#endif

static __attribute__((aligned(4096))) char accel_fixed_region[ACCEL_STATIC_REGION_BYTES];
static __attribute__((aligned(4096))) char accel_array_region[ACCEL_STATIC_REGION_BYTES];

void AccelSetup(void) {
    ROCC_INSTRUCTION(PROTOACC_OPCODE, FUNCT_SFENCE);

    // Touch every page so they're resident before the accel issues loads through L1.
    touch_all_pages(accel_fixed_region, sizeof(accel_fixed_region));
    touch_all_pages(accel_array_region, sizeof(accel_array_region));

    uint64_t fixed_u = (uint64_t)(uintptr_t)accel_fixed_region;
    uint64_t array_u = (uint64_t)(uintptr_t)accel_array_region;
    ROCC_INSTRUCTION_SS(PROTOACC_OPCODE, fixed_u, array_u, FUNCT_MEM_SETUP);
    printf("AccelSetup: fixed=%p array=%p size=%lu\n",
           (void *)fixed_u, (void *)array_u, (uint64_t)sizeof(accel_fixed_region));
}

volatile char **AccelSetupAllocRegionSerializer(size_t num_string_pointers,
                                                size_t total_string_data_bytes) {
    ROCC_INSTRUCTION(PROTOACC_SER_OPCODE, FUNCT_SER_SFENCE);

    size_t data_sz = ((total_string_data_bytes + 63) / 64) * 64;
    char *data_region = (char *)memalign(PAGESIZE_BYTES, data_sz);
    touch_all_pages(data_region, data_sz);

    uint64_t data_base = (uint64_t)data_region;
    uint64_t data_tail = data_base + (uint64_t)data_sz;

    size_t ptr_sz = num_string_pointers * sizeof(char *);
    char **ptr_region = (char **)memalign(PAGESIZE_BYTES, ptr_sz);
    touch_all_pages((char *)ptr_region, ptr_sz);

    // Initialize ptrs[0] to the tail, then return &ptrs[1] so ptrs[index>=0] receives outputs
    // (see baremetal/accellib.c:82-83 for the same convention).
    ptr_region[0] = (char *)data_tail;
    ptr_region += 1;

    uint64_t ptr_u = (uint64_t)ptr_region;
    ROCC_INSTRUCTION_SS(PROTOACC_SER_OPCODE, data_tail, ptr_u, FUNCT_SER_MEM_SETUP);
    printf("AccelSetupSer: data_tail=%p ptr=%p\n", (void *)data_tail, (void *)ptr_u);
    return (volatile char **)ptr_region;
}

void AccelSerializeToString_Helper(const void *descriptor_table_ptr,
                                   void *src_base_addr) {
    const uint64_t *d = (const uint64_t *)descriptor_table_ptr;
    uint64_t hasbits_offset = d[2];
    uint64_t min_max = d[3];

    ROCC_INSTRUCTION_SS(PROTOACC_SER_OPCODE, hasbits_offset, min_max, FUNCT_HASBITS_INFO);
    ROCC_INSTRUCTION_SS(PROTOACC_SER_OPCODE, descriptor_table_ptr, src_base_addr,
                        FUNCT_DO_PROTO_SERIALIZE);
}

void AccelParseFromString_Helper(const void *descriptor_table_ptr,
                                 void *dest_base_addr,
                                 const void *wire_bytes_ptr,
                                 uint64_t wire_bytes_length) {
    if (wire_bytes_length == 0) return;
    const uint64_t *d = (const uint64_t *)descriptor_table_ptr;
    uint64_t min_fieldno = d[3] >> 32;
    uint64_t rs2 = (min_fieldno << 32) | (wire_bytes_length & 0xFFFFFFFFull);

    ROCC_INSTRUCTION_SS(PROTOACC_OPCODE, descriptor_table_ptr, dest_base_addr,
                        FUNCT_PROTO_PARSE_INFO);
    ROCC_INSTRUCTION_SS(PROTOACC_OPCODE, wire_bytes_ptr, rs2, FUNCT_DO_PROTO_PARSE);
}

volatile char *BlockOnSerializedValue(volatile char **ptrs, int index) {
    uint64_t retval;
    ROCC_INSTRUCTION_D(PROTOACC_SER_OPCODE, retval, FUNCT_SER_CHECK_COMPLETION);
    asm volatile("fence");
    (void)retval;
    while (ptrs[index] == 0) {
        asm volatile("fence");
    }
    return ptrs[index];
}

size_t GetSerializedLength(volatile char **ptrs, int index) {
    return (size_t)(ptrs[index - 1] - ptrs[index]);
}

uint64_t block_on_completion(void) {
    uint64_t retval;
    ROCC_INSTRUCTION_D(PROTOACC_OPCODE, retval, FUNCT_CHECK_COMPLETION);
    asm volatile("fence");
    return retval;
}
