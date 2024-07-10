#!/usr/bin/env bash

set -ex

cp -f ../../microbenchmarks/primitive-tests/*.riscv overlay/root/ubmarks/
chmod +x overlay/root/ubmarks/run-all.sh
