#!/usr/bin/env bash

set -ex

./copy-bmarks.sh

CFG=protoacc-des-ubmark.yaml

# use initramfs so that you can use checkpointing
marshal -v -d clean $CFG
marshal -v -d build $CFG
marshal -v -d install $CFG
