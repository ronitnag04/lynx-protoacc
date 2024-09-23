#include <stdio.h>

#include "accellib.h"

void mark() {
    static int count = 0;
    count++;
    printf("S%d\n", count);
}

int main() {
    mark();

    /*alignas(16)*/ uint64_t msg_data[] = {
        0x0000000000f0f958, // -> start
        0x0000003fc5292940, //
        0x0000000000000002, // -> hasbits?
        0x000000000163c9a3, // -> ptr within taggedstrptr
        0x0000000000000000, //
    };

    /*alignas(16)*/ uint64_t str_data[] = {
        0x000000000163c9b0, // -> start - ptr to the string data?
        0x0000000000000005, // -> size?
        0x000000646c726f77, // -> string data
        0x0000000000000000, //
        0x0000000000000000, //
        0x0000000000000641, //
    };

    // change addrs
    str_data[0] = (uint64_t)&str_data[2];
    msg_data[3] = ((uint64_t)&str_data[0]) | 0x3; // need 0x3 for tag

    /*alignas(16)*/ uint64_t accel_desc[8] = {
        0x0000000000000000, //0x0000000000f0fa18, // vptr... leave alone?
        0x0000000000000020,
        0x0000000000000010,
        0x0000000100000001,
        0x2400000000000018,
        0x0000000000000000,
        0x0000000000000000,
    };

    mark();

    volatile char** serializeoutputs = AccelSetupAllocRegionSerializer(200, 200);
    printf("Region: %p\n", serializeoutputs);
    printf("Region: %p\n", &serializeoutputs[0]);

    mark();

    AccelSerializeToString_Helper(accel_desc, (void*)msg_data);
    volatile char * serres = BlockOnSerializedValue(serializeoutputs, 0);
    size_t serlen = GetSerializedLength(serializeoutputs, 0);

    printf("ACCEL: SERIALIZEDLENGTH: %d, SERPTR: %p\n", serlen, serres);
    for (int l = 0; l < serlen; l++) {
        printf("ACCELBYTE: %02x\n", serres[l]);
    }

    mark();

    return 0;
}
