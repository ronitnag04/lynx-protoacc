.PHONY: all
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
