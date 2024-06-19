CURRENT_DIR := $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))

X86INSTALLDIR=$(CURRENT_DIR)/_install/x86
RISCVINSTALLDIR=$(CURRENT_DIR)/_install/riscv

PROTOC = $(X86INSTALLDIR)/bin/protoc

RVCPP = riscv64-unknown-linux-gnu-g++
RVSTRIP = riscv64-unknown-linux-gnu-strip
X86CPP = g++
X86STRIP = strip
CPPFLAGS = -std=c++14 -static -g3 -O3 -DNDEBUG

include testsfrag.mk

ACCELCPP = accellib.cpp
ACCELH = rocc.h accellib.h

all: $(rvtests) $(x86tests)

%.pb.cc %.pb.h: %.proto
	$(PROTOC) --proto_path=$(@D) --cpp_out=. $<
	mv *.pb.h $(@D)/
	mv *.pb.cc $(@D)/

%.riscv : export PKG_CONFIG_PATH := $(RISCVINSTALLDIR)/lib/pkgconfig
%.riscv: %.cpp $(protos) $(ACCELCPP) $(ACCELH)
	$(RVCPP) \
		$(CPPFLAGS) \
		-o $@ \
		$< \
		$(protocc) \
		$(ACCELCPP) \
		`pkg-config --cflags --libs protobuf`
	$(RVSTRIP) $@

# note: pkg-config should be at the end
%.x86 : export PKG_CONFIG_PATH := $(X86INSTALLDIR)/lib/pkgconfig
%.x86 : %.cpp $(protos)
	$(X86CPP) \
		$(CPPFLAGS) \
		-o $@ \
		$< \
		$(protocc) \
		`pkg-config --cflags --libs protobuf`
	$(X86STRIP) $@

.PRECIOUS: %.pb.cc %.pb.h

.PHONY: clean
clean:
	cd primitive-tests && rm -rf *.riscv *.x86 *.pb.cc *.pb.h
