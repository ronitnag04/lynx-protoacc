CURRENT_DIR := $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))

MICROBENCHMARKS_DIR := $(CURRENT_DIR)
ACCELLIB_DIR := $(CURRENT_DIR)/../api
PROTOBUF_INSTALL_DIR := $(CURRENT_DIR)/../_install

include $(MICROBENCHMARKS_DIR)/common.mk
include $(MICROBENCHMARKS_DIR)/testsfrag.mk
include $(MICROBENCHMARKS_DIR)/rules.mk
