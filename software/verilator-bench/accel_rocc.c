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

// htif_nano has a tiny malloc heap; use static BSS regions instead so the
// deserializer has somewhere stable to dump output. The deserializer uses the
// array region as a bump allocator within a single DO_PROTO_PARSE. The bench
// driver re-issues MEM_SETUP between iterations to reset the bump pointer,
// so we size for ONE max-sized message's dest workspace, not 40 iters. 64 KB
// is comfortably above any single HPB top-level message's needs.
#ifndef ACCEL_STATIC_REGION_BYTES
#define ACCEL_STATIC_REGION_BYTES (64 * 1024)
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

// Serializer output regions use static BSS so we don't pound the small
// htif_nano malloc heap. Sized to absorb HPB-realistic payloads: 10 top-level
// messages × ITERS iters × up to ~2 KB each; 128 KB gives headroom
// without bloating the ELF.
#ifndef ACCEL_SER_DATA_BYTES
#define ACCEL_SER_DATA_BYTES  (128 * 1024)
#endif
#ifndef ACCEL_SER_PTR_COUNT
#define ACCEL_SER_PTR_COUNT   512
#endif

static __attribute__((aligned(4096))) char accel_ser_data_region[ACCEL_SER_DATA_BYTES];
static __attribute__((aligned(4096))) char *accel_ser_ptr_region[ACCEL_SER_PTR_COUNT];

volatile char **AccelSetupAllocRegionSerializer(size_t num_string_pointers,
                                                size_t total_string_data_bytes) {
    ROCC_INSTRUCTION(PROTOACC_SER_OPCODE, FUNCT_SER_SFENCE);

    if (num_string_pointers > ACCEL_SER_PTR_COUNT) {
        printf("AccelSetupSer: FATAL: need %lu ptrs, have %d\n",
               (uint64_t)num_string_pointers, ACCEL_SER_PTR_COUNT);
        return NULL;
    }
    if (total_string_data_bytes > ACCEL_SER_DATA_BYTES) {
        printf("AccelSetupSer: FATAL: need %lu data bytes, have %d\n",
               (uint64_t)total_string_data_bytes, ACCEL_SER_DATA_BYTES);
        return NULL;
    }

    touch_all_pages(accel_ser_data_region, ACCEL_SER_DATA_BYTES);
    touch_all_pages((char *)accel_ser_ptr_region, sizeof(accel_ser_ptr_region));

    uint64_t data_tail = (uint64_t)(uintptr_t)accel_ser_data_region + ACCEL_SER_DATA_BYTES;
    accel_ser_ptr_region[0] = (char *)data_tail;
    char **ret_ptrs = accel_ser_ptr_region + 1;

    uint64_t ptr_u = (uint64_t)(uintptr_t)ret_ptrs;
    ROCC_INSTRUCTION_SS(PROTOACC_SER_OPCODE, data_tail, ptr_u, FUNCT_SER_MEM_SETUP);
    printf("AccelSetupSer: data_tail=%p ptr=%p bytes=%d\n",
           (void *)data_tail, (void *)ptr_u, ACCEL_SER_DATA_BYTES);
    return (volatile char **)ret_ptrs;
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
