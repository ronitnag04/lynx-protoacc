# Bare-metal (HTIF) build for ProtoAcc Verilator benchmarks.
# Source Chipyard env first: `source "$CHIPYARD_ROOT/env.sh"` (see README).
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

TINY_BENCHES     := bench_tiny_ser bench_tiny_des

# Bench-id partitioning. HPB benches are fixed at 0..5 (one per directory in
# the HyperProtoBench submodule). Synthetic benches are generated from
# fleetbench by gen_synth_proto.py and start at SYNTH_START (default 6) so
# the flat bench<N> namespace used by run_sweep.sh / feature extraction keeps
# working unmodified.
HPB_IDS          ?= 0 1 2 3 4 5
SYNTH_START      ?= 6
SYNTH_COUNT      ?= 0
SYNTH_N_MESSAGES ?= 5
SYNTH_SEED       ?= 0
# SYNTH_IDS = SYNTH_START..SYNTH_START+SYNTH_COUNT-1 (empty when SYNTH_COUNT=0).
SYNTH_IDS        := $(shell if [ $(SYNTH_COUNT) -gt 0 ]; then \
                      seq $(SYNTH_START) $$(($(SYNTH_START) + $(SYNTH_COUNT) - 1)); \
                    fi)
BENCH_IDS        := $(HPB_IDS) $(SYNTH_IDS)

SER_BENCHES      := $(addsuffix _ser,$(addprefix bench,$(BENCH_IDS)))
DES_BENCHES      := $(addsuffix _des,$(addprefix bench,$(BENCH_IDS)))
HPB_BENCHES      := $(SER_BENCHES) $(DES_BENCHES)
REPRO_CASES      := 1 2 3 4 5 6 7 8 9 10 11 12 13 14
REPRO_ELFS       := $(addprefix $(BUILD)/,$(addprefix bench_repro_c,$(addsuffix .riscv,$(REPRO_CASES))))

# Isolate benches: one ELF per (bench, msg_idx) pair. Pattern:
#   build/isolate_bench<B>_m<I>.riscv serializes TOP_MESSAGE_INSTANCE_PTRS[I]
#   from gen/bench<B>_*.
ISOLATE_BENCH ?= 1
ISOLATE_IDXS  := 0 1 2 3 4 5 6 7 8 9
ISOLATE_ELFS  := $(addprefix $(BUILD)/,$(addprefix isolate_bench$(ISOLATE_BENCH)_m,$(addsuffix .riscv,$(ISOLATE_IDXS))))
BENCHES      := $(TINY_BENCHES) $(HPB_BENCHES)
ELFS         := $(addprefix $(BUILD)/,$(addsuffix .riscv,$(BENCHES))) $(REPRO_ELFS)

# Target sets:
#   all   — every ELF (tiny, HPB+synth ser/des, repro debug cases). Default.
#   bench — just the bench<N>_{ser,des}.riscv ELFs that run_sweep.sh consumes
#           (HPB by default; add synth by setting SYNTH_COUNT=N on the cmdline).
#           Skips tiny validation + repro debug workloads.
HPB_ELFS := $(addprefix $(BUILD)/,$(addsuffix .riscv,$(HPB_BENCHES)))

.PHONY: all bench clean gen synth
all: $(ELFS)
bench: $(HPB_ELFS)

$(BUILD):
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
# Data objects live directly under $(BUILD) (no $(BUILD)/gen subdir).
$(BUILD)/bench%_data.o: gen/bench%_data.c gen/bench%_descriptors.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/bench%_ser.o: bench_hpb_ser.c gen/bench%_descriptors.h $(HEADERS) | $(BUILD)
	$(CC) $(CFLAGS) -DBENCH_NAME=\"bench$*\" \
	      -DBENCH_DESCRIPTORS_H=\"bench$*_descriptors.h\" \
	      -c $< -o $@

$(BUILD)/bench%_ser.riscv: $(BUILD)/bench%_ser.o $(BUILD)/bench%_data.o $(COMMON_OBJS) | $(BUILD)
	$(CC) $(LDFLAGS) $^ -o $@

# HyperProtoBench deserializer benches. Same pattern as ser but wires in
# bench_hpb_des.c instead.
$(BUILD)/bench%_des.o: bench_hpb_des.c gen/bench%_descriptors.h $(HEADERS) | $(BUILD)
	$(CC) $(CFLAGS) -DBENCH_NAME=\"bench$*\" \
	      -DBENCH_DESCRIPTORS_H=\"bench$*_descriptors.h\" \
	      -c $< -o $@

$(BUILD)/bench%_des.riscv: $(BUILD)/bench%_des.o $(BUILD)/bench%_data.o $(COMMON_OBJS) | $(BUILD)
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

$(BUILD)/isolate_bench$(ISOLATE_BENCH)_m%.riscv: $(BUILD)/isolate_bench$(ISOLATE_BENCH)_m%.o $(BUILD)/bench$(ISOLATE_BENCH)_data.o $(COMMON_OBJS) | $(BUILD)
	$(CC) $(LDFLAGS) $^ -o $@

.PHONY: isolate
isolate: $(ISOLATE_ELFS)

# Regenerate descriptor + data files from .proto sources.
VERILATOR_BENCH_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
LYNX_ROOT ?= $(abspath $(VERILATOR_BENCH_DIR)/../lynx)
HPB_DIR   ?= $(LYNX_ROOT)/HyperProtoBench
SYNTH_DIR ?= gen/synth

# Per-bench-id source lookup. HPB benches sit at $(HPB_DIR)/bench<N>/
# benchmark.proto and supply their runtime data via a sibling benchmark.inc,
# which proto_to_accel picks up automatically. Synthetic benches live under
# $(SYNTH_DIR)/bench<N>/ with a precomputed runtime_lengths.json — we pass
# that to proto_to_accel via --runtime-lengths.
#
# Invariant: every synth bench dir must exist before `make gen` runs
# (created by `make synth` below). This keeps the pattern rules a one-liner:
# HPB ids look up via HPB_DIR, synth ids look up via SYNTH_DIR.
HPB_ID_LIST   := $(HPB_IDS)
SYNTH_ID_LIST := $(SYNTH_IDS)

# Per-bench pattern rules (below). Good for incremental rebuilds — touch one
# proto and only that bench regenerates. Scales poorly with BENCH_IDS count
# because each invocation pays ~200 ms Python startup; see gen_batch for the
# pooled alternative used when regenerating 100s+ of synth benches.
gen: $(foreach b,$(BENCH_IDS),gen/bench$(b)_descriptors.h gen/bench$(b)_data.c)

# Batch-gen target: one Python process + worker pool, no per-bench startup
# overhead. ~10× faster than `gen` on 4000 synth benches (2m44s → ~10s on a
# 16-core box). Always regenerates every bench in BENCH_IDS — no incremental
# dependency tracking, so use this after `make clean` or when you know every
# bench needs rebuilding (e.g. after changing proto_to_accel.py).
#
# Passes bench ids + source roots via CLI flags (not inline shell expansion)
# so the rule stays under ARG_MAX at SYNTH_COUNT=4000+.
empty   :=
space   := $(empty) $(empty)
comma   := ,
HPB_IDS_CSV   := $(subst $(space),$(comma),$(HPB_ID_LIST))
SYNTH_IDS_CSV := $(subst $(space),$(comma),$(SYNTH_ID_LIST))

.PHONY: gen_batch
gen_batch:
	@mkdir -p gen
	python3 gen_all_benches.py \
	  --from-dirs \
	  --gen-dir gen \
	  --hpb-dir $(HPB_DIR) \
	  --synth-dir $(SYNTH_DIR) \
	  --hpb-ids "$(HPB_IDS_CSV)" \
	  --synth-ids "$(SYNTH_IDS_CSV)"

# Generate the synthetic benches (protos + runtime JSON). Idempotent: rerun
# with different SYNTH_COUNT / SYNTH_SEED to produce a different catalog.
# Skipped with a warning when SYNTH_COUNT=0 so `make gen` still works in
# HPB-only mode.
synth:
ifeq ($(SYNTH_COUNT),0)
	@echo "make synth: SYNTH_COUNT=0, nothing to do. "\
	      "Rerun with SYNTH_COUNT=<N> SYNTH_SEED=<S> to generate synthetic benches."
else
	mkdir -p $(SYNTH_DIR)
	python3 gen_synth_proto.py \
	  --count $(SYNTH_COUNT) \
	  --n-messages $(SYNTH_N_MESSAGES) \
	  --start-id $(SYNTH_START) \
	  --seed $(SYNTH_SEED) \
	  --out-root $(SYNTH_DIR)
endif

# HPB proto → descriptors + data. Use grouped targets (`&:`, GNU Make 4.3+)
# so a single invocation of proto_to_accel.py produces both outputs instead
# of firing once per .h / .c (which would happen with `a b: deps`).
define HPB_GEN_RULE
gen/bench$(1)_descriptors.h gen/bench$(1)_data.c &: \
    $(HPB_DIR)/bench$(1)/benchmark.proto proto_to_accel.py
	mkdir -p gen
	python3 proto_to_accel.py --proto $$< \
	  --out-header gen/bench$(1)_descriptors.h \
	  --out-source gen/bench$(1)_data.c
endef
$(foreach b,$(HPB_ID_LIST),$(eval $(call HPB_GEN_RULE,$(b))))

# Synthetic proto → descriptors + data. Depends on the synthetic bench dir
# already existing (produced by `make synth`).
define SYNTH_GEN_RULE
gen/bench$(1)_descriptors.h gen/bench$(1)_data.c &: \
    $(SYNTH_DIR)/bench$(1)/benchmark.proto \
    $(SYNTH_DIR)/bench$(1)/runtime_lengths.json \
    proto_to_accel.py
	mkdir -p gen
	python3 proto_to_accel.py --proto $$< \
	  --runtime-lengths $(SYNTH_DIR)/bench$(1)/runtime_lengths.json \
	  --out-header gen/bench$(1)_descriptors.h \
	  --out-source gen/bench$(1)_data.c
endef
$(foreach b,$(SYNTH_ID_LIST),$(eval $(call SYNTH_GEN_RULE,$(b))))

clean:
	rm -rf $(BUILD) gen
