#!/usr/bin/env bash

set -ex

SCRIPTDIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

cd $SCRIPTDIR

# build the modified protobuf library
./build-protobuf.sh

# re-gen ubmarks
cd microbenchmarks
./build.sh

# build hyperprotobench
cd $SCRIPTDIR/firesim-workloads/hyperproto
./buildall.sh

# build images
cd $SCRIPTDIR/firesim-workloads
pushd hyperproto
./copy.sh $SCRIPTDIR/firesim-workloads
popd
cd boom-plain-bmarks && ./build.sh
cd ../protoacc-des-bmarks && ./build.sh
cd ../protoacc-ser-bmarks && ./build.sh
