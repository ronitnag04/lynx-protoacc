#!/usr/bin/env bash

set -ex

SCRIPTDIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

cd $SCRIPTDIR

# setup protobuf repo
PROTOBUFREPO=$SCRIPTDIR/protobuf
pushd $PROTOBUFREPO
git submodule update --init --recursive

# use _build/ since protobuf repo already gitignores it
X86BUILDDIR=$SCRIPTDIR/_build/x86
X86INSTALLDIR=$SCRIPTDIR/_install/x86
rm -rf $X86BUILDDIR
mkdir -p $X86BUILDDIR
rm -rf $X86INSTALLDIR
mkdir -p $X86INSTALLDIR

RISCVBUILDDIR=$SCRIPTDIR/_build/riscv
RISCVINSTALLDIR=$SCRIPTDIR/_install/riscv
rm -rf $RISCVBUILDDIR
mkdir -p $RISCVBUILDDIR
rm -rf $RISCVINSTALLDIR
mkdir -p $RISCVINSTALLDIR

ARGS="--clean-first --parallel 40"

# build for x86
pushd $X86BUILDDIR
cmake -DCMAKE_INSTALL_PREFIX=$X86INSTALLDIR \
	-Dprotobuf_BUILD_TESTS=OFF \
	-DABSL_BUILD_TESTING=OFF \
	-DABSL_PROPAGATE_CXX_STD=ON \
	-DCMAKE_CXX_STANDARD=14 \
	$PROTOBUFREPO
cmake --build . $ARGS --target install

# build for riscv64
pushd $RISCVBUILDDIR
cmake -DCMAKE_INSTALL_PREFIX=$RISCVINSTALLDIR \
	-Dprotobuf_BUILD_TESTS=OFF \
	-DABSL_BUILD_TESTING=OFF \
	-DABSL_PROPAGATE_CXX_STD=ON \
	-DCMAKE_CXX_STANDARD=14 \
	-DCMAKE_TOOLCHAIN_FILE=$PROTOBUFREPO/RISCV.cmake \
	-DCMAKE_CROSSCOMPILING=ON \
	$PROTOBUFREPO
cmake --build . $ARGS --target install
