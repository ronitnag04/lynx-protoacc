#ifndef ACCEL_ROCC_H
#define ACCEL_ROCC_H

#include "rocc.h"
#include <stdint.h>
#include <stddef.h>

#define PROTOACC_OPCODE         2
#define FUNCT_SFENCE            0
#define FUNCT_PROTO_PARSE_INFO  1
#define FUNCT_DO_PROTO_PARSE    2
#define FUNCT_MEM_SETUP         3
#define FUNCT_CHECK_COMPLETION  4

#define PROTOACC_SER_OPCODE        3
#define FUNCT_SER_SFENCE           0
#define FUNCT_HASBITS_INFO         1
#define FUNCT_DO_PROTO_SERIALIZE   2
#define FUNCT_SER_MEM_SETUP        3
#define FUNCT_SER_CHECK_COMPLETION 4

// Deserializer: allocates fixed and array regions and issues FUNCT_MEM_SETUP.
void AccelSetup(void);

// Serializer: allocates string-data + string-pointer regions and issues FUNCT_SER_MEM_SETUP.
// Returns the base of the string pointer array (ptrs[0] = tail of data region before call,
// returned pointer is advanced by 1 — so ptrs[i] for i>=0 receives serialized outputs).
volatile char **AccelSetupAllocRegionSerializer(size_t num_string_pointers,
                                                size_t total_string_data_bytes);

// Issue HASBITS_INFO + DO_PROTO_SERIALIZE for one message.
// descriptor_table_ptr must point to the ACCEL_DESCRIPTOR array (words [2] hasbits_offset,
// [3] min|max are read by this helper). src_base_addr is the cpp_obj pointer.
void AccelSerializeToString_Helper(const void *descriptor_table_ptr,
                                   void *src_base_addr);

// Issue PROTO_PARSE_INFO + DO_PROTO_PARSE for one message.
// Resurrected from the commented-out C++ helper in
// generators/protoacc/software/microbenchmarks/baremetal/accellib.c:159-180.
void AccelParseFromString_Helper(const void *descriptor_table_ptr,
                                 void *dest_base_addr,
                                 const void *wire_bytes_ptr,
                                 uint64_t wire_bytes_length);

// Spin on string_ptr_region[index] until the hardware writes the output pointer.
// Returns the output pointer (start of serialized bytes).
volatile char *BlockOnSerializedValue(volatile char **ptrs, int index);

// Length = ptrs[index-1] - ptrs[index] (bytes are laid out tail-growing).
size_t GetSerializedLength(volatile char **ptrs, int index);

// Issue FUNCT_CHECK_COMPLETION on the deserializer and fence. Returns completed count.
uint64_t block_on_completion(void);

#endif
