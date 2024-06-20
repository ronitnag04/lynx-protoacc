#!/usr/bin/env bash

set -ex

SCRIPTDIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

cd $SCRIPTDIR

# build the modified protobuf library
./build-protobuf.sh

# re-gen ubmarks
cd microbenchmarks
./build.sh

cd $SCRIPTDIR/firesim-workloads
cd hyperproto
./buildall.sh
./copy.sh $SCRIPTDIR/firesim-workloads

# build images
cd $SCRIPTDIR/firesim-workloads
cd boom-plain-bmarks && ./build.sh
cd ../protoacc-des-bmarks && ./build.sh
cd ../protoacc-ser-bmarks && ./build.sh
