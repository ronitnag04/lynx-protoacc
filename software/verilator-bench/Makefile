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
           -specs=htif_nano.specs \
           -I. -Igen

LDFLAGS := -static -specs=htif_nano.specs

COMMON_SRCS := accel_rocc.c
COMMON_OBJS := $(addprefix $(BUILD)/,$(COMMON_SRCS:.c=.o))
HEADERS     := accel_rocc.h rocc.h bench_common.h

TINY_BENCHES := bench_tiny_ser bench_tiny_des
HPB_BENCHES  := bench0_ser bench1_ser bench2_ser bench3_ser bench4_ser bench5_ser
REPRO_CASES  := 1 2 3 4 5 6 7 8 9 10 11 12 13 14
REPRO_ELFS   := $(addprefix $(BUILD)/,$(addprefix bench_repro_c,$(addsuffix .riscv,$(REPRO_CASES))))

# Isolate benches: one ELF per (bench, msg_idx) pair. Pattern:
#   build/isolate_bench<B>_m<I>.riscv serializes TOP_MESSAGE_INSTANCE_PTRS[I]
#   from gen/bench<B>_*.
ISOLATE_BENCH ?= 1
ISOLATE_IDXS  := 0 1 2 3 4 5 6 7 8 9
ISOLATE_ELFS  := $(addprefix $(BUILD)/,$(addprefix isolate_bench$(ISOLATE_BENCH)_m,$(addsuffix .riscv,$(ISOLATE_IDXS))))
BENCHES      := $(TINY_BENCHES) $(HPB_BENCHES)
ELFS         := $(addprefix $(BUILD)/,$(addsuffix .riscv,$(BENCHES))) $(REPRO_ELFS)

.PHONY: all clean gen
all: $(ELFS)

$(BUILD) $(BUILD)/gen:
	mkdir -p $@

# Tiny benches
$(BUILD)/%.o: %.c $(HEADERS) | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/bench_tiny_ser.riscv: $(BUILD)/bench_tiny_ser.o $(COMMON_OBJS) | $(BUILD)
	$(CC) $(LDFLAGS) $^ -o $@

$(BUILD)/bench_tiny_des.riscv: $(BUILD)/bench_tiny_des.o $(COMMON_OBJS) | $(BUILD)
	$(CC) $(LDFLAGS) $^ -o $@

# HyperProtoBench serializer benches. For each bench<N>_ser we compile
# bench_hpb_ser.c with -DBENCH_NAME / -DBENCH_DESCRIPTORS_H selecting the
# generated header, plus the generated data.c providing the instance bytes.
$(BUILD)/gen/bench%_data.o: gen/bench%_data.c gen/bench%_descriptors.h | $(BUILD)/gen
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/bench%_ser.o: bench_hpb_ser.c gen/bench%_descriptors.h $(HEADERS) | $(BUILD)
	$(CC) $(CFLAGS) -DBENCH_NAME=\"bench$*\" \
	      -DBENCH_DESCRIPTORS_H=\"bench$*_descriptors.h\" \
	      -c $< -o $@

$(BUILD)/bench%_ser.riscv: $(BUILD)/bench%_ser.o $(BUILD)/gen/bench%_data.o $(COMMON_OBJS) | $(BUILD)
	$(CC) $(LDFLAGS) $^ -o $@

# Minimal reproducers for the multi-field dispatch bug.
$(BUILD)/bench_repro_c%.o: bench_repro.c $(HEADERS) | $(BUILD)
	$(CC) $(CFLAGS) -DREPRO_CASE=$* -c $< -o $@

$(BUILD)/bench_repro_c%.riscv: $(BUILD)/bench_repro_c%.o $(COMMON_OBJS) | $(BUILD)
	$(CC) $(LDFLAGS) $^ -o $@

# Isolate: build/isolate_benchB_mI.{o,riscv} — uses $(ISOLATE_BENCH) and the
# last component of the target name (mI) to pick which generated bench file
# and which top-level message index to compile in.
$(BUILD)/isolate_bench$(ISOLATE_BENCH)_m%.o: bench_isolate.c $(HEADERS) gen/bench$(ISOLATE_BENCH)_descriptors.h | $(BUILD)
	$(CC) $(CFLAGS) -DBENCH_NAME=\"isolate_b$(ISOLATE_BENCH)_m$*\" \
	      -DBENCH_DESCRIPTORS_H=\"bench$(ISOLATE_BENCH)_descriptors.h\" \
	      -DISOLATE_MSG_IDX=$* \
	      -c $< -o $@

$(BUILD)/isolate_bench$(ISOLATE_BENCH)_m%.riscv: $(BUILD)/isolate_bench$(ISOLATE_BENCH)_m%.o $(BUILD)/gen/bench$(ISOLATE_BENCH)_data.o $(COMMON_OBJS) | $(BUILD)
	$(CC) $(LDFLAGS) $^ -o $@

.PHONY: isolate
isolate: $(ISOLATE_ELFS)

# Regenerate descriptor + data files from .proto sources.
HPB_DIR ?= /home/ec2-user/lynx/HyperProtoBench
gen: $(foreach b,0 1 2 3 4 5,gen/bench$(b)_descriptors.h gen/bench$(b)_data.c)

gen/bench%_descriptors.h gen/bench%_data.c: $(HPB_DIR)/bench%/benchmark.proto gen/proto_to_accel.py
	python3 gen/proto_to_accel.py --proto $< \
	  --out-header gen/bench$*_descriptors.h \
	  --out-source gen/bench$*_data.c

clean:
	rm -rf $(BUILD)
