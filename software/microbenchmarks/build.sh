#!/usr/bin/env bash

set -ex

STARTDIR=$(pwd)

# re-gen ubmarks
python gen-primitive-tests.py
rm -rf primitive-benchmarks/*.riscv
rm -rf primitive-benchmarks/*.x86
time make -f Makefile -j64 all

python gen-primitive-tests-serializer.py
rm -rf primitive-benchmarks-serializer/*.riscv
rm -rf primitive-benchmarks-serializer/*.x86
time make -f Makefile-serializer -j64 all
