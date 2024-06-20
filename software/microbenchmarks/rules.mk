# Requires that ACCELLIB_DIR is set

ifndef ACCELLIB_DIR
	$(error Unset ACCELLIB_DIR)
endif

.PHONY: all
all: $(rvtests) $(x86tests)

%.pb.cc %.pb.h: %.proto
	$(PROTOC) --proto_path=$(@D) --cpp_out=. $<
	mv *.pb.h $(@D)/
	mv *.pb.cc $(@D)/

.PRECIOUS: %.pb.cc %.pb.h

%.riscv: export PKG_CONFIG_PATH := $(RISCVINSTALLDIR)/lib/pkgconfig
%.riscv: %.cpp $(protos) $(ACCELCPP) $(ACCELH)
	$(RVCPP) \
		$(CPPFLAGS) \
		-o $@ \
		$< \
		$(protocc) \
		$(ACCELCPP) \
		-I $(ACCELLIB_DIR) \
		-DACCEL_REGION_DEFAULT_SHIFT=13 \
		-DACCEL_REGION_SER_SHIFT=13 \
		`pkg-config --cflags --libs protobuf`
	$(RVSTRIP) $@

# note: pkg-config should be at the end
%.x86: export PKG_CONFIG_PATH := $(X86INSTALLDIR)/lib/pkgconfig
%.x86: %.cpp $(protos)
	$(X86CPP) \
		$(CPPFLAGS) \
		-o $@ \
		$< \
		$(protocc) \
		`pkg-config --cflags --libs protobuf`
	$(X86STRIP) $@

.PHONY: check-pkgconfig-riscv
check-pkgconfig-riscv: export PKG_CONFIG_PATH := $(RISCVINSTALLDIR)/lib/pkgconfig
check-pkgconfig-riscv:
	echo `pkg-config --cflags --libs protobuf`

.PHONY: check-pkgconfig-x86
check-pkgconfig-x86: export PKG_CONFIG_PATH := $(X86INSTALLDIR)/lib/pkgconfig
check-pkgconfig-x86:
	echo `pkg-config --cflags --libs protobuf`

.PHONY: clean
clean:
	cd primitive-tests && rm -rf *.riscv *.x86 *.pb.cc *.pb.h
