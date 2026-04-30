# Bare-metal (HTIF) build for ProtoAcc Verilator benchmarks.
# Run `source /home/ec2-user/hyperscale-grpc-chipyard/env.sh` first.
#
# Build artifacts go under build/. Pass BUILD=<dir> to override.

TARGET  := riscv64-unknown-elf
CC      := $(TARGET)-gcc
ARCH    := rv64imafdc
ABI     := lp64d

BUILD   ?= build

CFLAGS  := -march=$(ARCH) -mabi=$(ABI) \
           -std=gnu99 -O2 -Wall -Wno-unused-variable \
           -fno-common -fno-builtin-printf \
           -DACCEL_REGION_DEFAULT_SHIFT=13 -DACCEL_REGION_SER_SHIFT=13 \
           -specs=htif_nano.specs

LDFLAGS := -static -specs=htif_nano.specs

COMMON_SRCS := accel_rocc.c
COMMON_OBJS := $(addprefix $(BUILD)/,$(COMMON_SRCS:.c=.o))
HEADERS     := accel_rocc.h rocc.h bench_common.h

BENCHES := bench_tiny_ser bench_tiny_des
ELFS    := $(addprefix $(BUILD)/,$(addsuffix .riscv,$(BENCHES)))

.PHONY: all clean
all: $(ELFS)

$(BUILD):
	mkdir -p $@

$(BUILD)/%.o: %.c $(HEADERS) | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/bench_tiny_ser.riscv: $(BUILD)/bench_tiny_ser.o $(COMMON_OBJS) | $(BUILD)
	$(CC) $(LDFLAGS) $^ -o $@

$(BUILD)/bench_tiny_des.riscv: $(BUILD)/bench_tiny_des.o $(COMMON_OBJS) | $(BUILD)
	$(CC) $(LDFLAGS) $^ -o $@

clean:
	rm -rf $(BUILD)
