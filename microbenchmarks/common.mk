X86INSTALLDIR=$(CURRENT_DIR)/_install/x86
RISCVINSTALLDIR=$(CURRENT_DIR)/_install/riscv

PROTOC = $(X86INSTALLDIR)/bin/protoc

RVPREFIX = riscv64-unknown-linux-gnu-
RVCPP = $(RVPREFIX)-g++
RVSTRIP = $(RVPREFIX)-strip
X86CPP = g++
X86STRIP = strip
CPPFLAGS = -std=c++14 -static -g3 -O3 -DNDEBUG

ACCELCPP = accellib.cpp
ACCELH = rocc.h accellib.h
