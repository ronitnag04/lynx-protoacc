#!/usr/bin/env python3
"""
parse_results.py — aggregate ACCEL_SUMMARY lines from Verilator bench logs.

Reads logs under $CHIPYARD/sims/verilator/output/chipyard.harness.TestHarness.*Config/bench*_ser.log
and emits a JSON file shaped like sample_protoacc_model/protobuf_model.py's
default_benchmark_results:

    {
      "serializer":   { "bench0": {"throughput": ..., "cycles": ..., "bytes": ...}, ... },
      "deserializer": { ... }
    }

Throughput is bytes / (cycles / clock_hz). We default clock_hz to 1e9 (1 GHz) but
accept --clock-hz to override when the target config uses a different rate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

SUMMARY_RE = re.compile(
    r"ACCEL_SUMMARY:\s+"
    r"bench=(?P<bench>\S+)\s+"
    r"op=(?P<op>\S+)\s+"
    r"iters=(?P<iters>\d+)\s+"
    r"total_cycles=(?P<cycles>\d+)\s+"
    r"total_bytes=(?P<bytes>\d+)"
)


def parse_log(path: Path) -> Optional[Dict]:
    """Return the last ACCEL_SUMMARY line parsed as a dict, or None."""
    last = None
    with path.open() as f:
        for line in f:
            m = SUMMARY_RE.search(line)
            if m:
                last = {
                    "bench": m.group("bench"),
                    "op": m.group("op"),
                    "iters": int(m.group("iters")),
                    "cycles": int(m.group("cycles")),
                    "bytes": int(m.group("bytes")),
                }
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--log-dir",
        type=Path,
        default=Path(
            "/home/ec2-user/hyperscale-grpc-chipyard/sims/verilator/output/"
            "chipyard.harness.TestHarness.ProtoAccelRocketConfig"
        ),
        help="Directory containing bench*_ser.log / bench*_des.log files.",
    )
    ap.add_argument("--clock-hz", type=float, default=1e9)
    ap.add_argument("--output", type=Path, default=Path("benchmark_results.json"))
    args = ap.parse_args()

    if not args.log_dir.is_dir():
        sys.exit(f"Log dir not found: {args.log_dir}")

    results: Dict[str, Dict[str, Dict[str, float]]] = {
        "serializer": {},
        "deserializer": {},
    }

    log_paths = sorted(args.log_dir.glob("bench*.log"))
    if not log_paths:
        sys.exit(f"No bench*.log files in {args.log_dir}")

    for path in log_paths:
        summary = parse_log(path)
        if summary is None:
            print(f"[skip] {path.name}: no ACCEL_SUMMARY line", file=sys.stderr)
            continue
        op = summary["op"]
        bench = summary["bench"]
        op_key = {"ser": "serializer", "des": "deserializer"}.get(op)
        if op_key is None:
            print(f"[skip] {path.name}: unknown op={op}", file=sys.stderr)
            continue
        cycles = summary["cycles"]
        bytes_ = summary["bytes"]
        throughput = 0.0
        if cycles > 0:
            throughput = bytes_ / (cycles / args.clock_hz)  # bytes/sec
        results[op_key][bench] = {
            "throughput": throughput,
            "cycles": cycles,
            "bytes": bytes_,
            "iters": summary["iters"],
        }
        print(
            f"[ok] {path.name}: {bench} {op} iters={summary['iters']} "
            f"cycles={cycles} bytes={bytes_} "
            f"throughput={throughput / 1e6:.2f} MB/s",
            file=sys.stderr,
        )

    args.output.write_text(json.dumps(results, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
