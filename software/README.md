- ``cd $CHIPYARD_DIR && source env.sh``. Chipyard Conda environment must be sourced
- ``build-all-sw.sh``. Builds all SW
- ``build-protobuf.sh``. Builds x86/RISC-V protobuf
- ``microbenchmarks/build-protobuf.sh``. Builds x86/RISC-V microbenchmarks

TODO:
- Use `mlockall`/`munlockall` to pin/unpin pages when running in Linux (see https://github.com/ucb-bar/caliptra-aes-acc/tree/change-to-cbc) for an example of this
- Protobuf SW changes for the accelerator doesn't seem to support repeated messages
    - Serialization fails due to incorrect `ByteSizeLong` calculation
        - Traced to `has_bits` modification (maybe hasbits not being set for repeated messages?)
