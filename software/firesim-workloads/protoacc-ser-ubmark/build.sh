#!/usr/bin/env bash

set -ex

./copy-bmarks.sh

CFG=protoacc-ser-ubmark.yaml

marshal -v clean $CFG
marshal -v build $CFG
marshal -v install $CFG
