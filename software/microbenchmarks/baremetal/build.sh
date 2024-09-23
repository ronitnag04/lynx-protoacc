#!/bin/bash

set -ex

ACCELLIB_DIR=$PWD

#riscv64-unknown-elf-gcc \
#	-std=gnu99 -static -g3 -O3 -DNDEBUG \
#	-I $ACCELLIB_DIR \
#        $ACCELLIB_DIR/accellib.c \
#	$ACCELLIB_DIR/custom.c \
#	-o custom.riscv \
VARS=

riscv64-unknown-elf-gcc -DACCEL_REGION_DEFAULT_SHIFT=13 -DACCEL_REGION_SER_SHIFT=13 -fno-common -fno-builtin-printf -specs=htif_nano.specs -c custom.c $VARS
riscv64-unknown-elf-gcc -DACCEL_REGION_DEFAULT_SHIFT=13 -DACCEL_REGION_SER_SHIFT=13 -fno-common -fno-builtin-printf -specs=htif_nano.specs -c accellib.c $VARS
riscv64-unknown-elf-gcc -DACCEL_REGION_DEFAULT_SHIFT=13 -DACCEL_REGION_SER_SHIFT=13 -static -specs=htif_nano.specs custom.o accellib.o -o custom.riscv $VARS
