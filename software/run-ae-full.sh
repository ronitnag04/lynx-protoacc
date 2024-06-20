#!/usr/bin/env bash

set -ex

SCRIPTDIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

cd $SCRIPTDIR

# generate all software components
./build-all-sw.sh

echo "ERROR(abegonzalez): Only tested recently up to here."

# generate host-side firesim driver
./build-driver-only.sh

# run all simulations
./sims-run-all.sh

# plot results from sim runs
./gen-all-plots.sh

echo "run-ae-full.sh complete."
