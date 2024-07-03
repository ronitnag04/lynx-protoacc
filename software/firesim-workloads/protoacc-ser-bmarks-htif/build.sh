#!/usr/bin/env bash

./copy-bmarks.sh
WRKLD=protoacc-ser-bmarks-htif.json
marshal -v -d   clean $WRKLD
marshal -v -d   build $WRKLD
marshal -v -d install $WRKLD
