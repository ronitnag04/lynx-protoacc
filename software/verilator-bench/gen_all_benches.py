#!/usr/bin/env python3
"""
gen_all_benches.py — run proto_to_accel.py over many benches in one Python
process using a worker pool.

Why this exists: ``make gen`` invokes ``python3 proto_to_accel.py`` once per
bench. At 4,000 synth benches the Python-startup cost alone dominates
runtime (164 s wall, 42 min user on a 16-core box — almost entirely
interpreter/import overhead). This driver stays resident, imports
``proto_to_accel`` once, and dispatches each bench's emission to a
``ProcessPoolExecutor`` worker. Same outputs, ~10× faster.

Inputs: a manifest listing bench id → (proto_path, runtime_lengths_path_or_empty).
The Makefile generates this manifest on the fly before calling us, so the
caller doesn't need to know HPB vs synth naming conventions.

Typical invocation (from the Makefile):

    python3 gen_all_benches.py \\
        --gen-dir gen \\
        --manifest - << EOF
    0 /path/to/hpb/bench0/benchmark.proto
    6 gen/synth/bench6/benchmark.proto gen/synth/bench6/runtime_lengths.json
    7 gen/synth/bench7/benchmark.proto gen/synth/bench7/runtime_lengths.json
    ...
    EOF

Each line is: ``<bench_id> <proto_path> [<runtime_lengths_json>]``. The
optional third field is passed as ``--runtime-lengths`` to proto_to_accel;
HPB benches omit it and fall back to the sibling .inc parser.
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Import proto_to_accel as a module once in the parent. Workers inherit
# (fork) the loaded module, so no per-bench import cost.
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
import proto_to_accel  # noqa: E402


@dataclass
class BenchJob:
    bench_id: int
    proto_path: Path
    runtime_lengths: Optional[Path]  # None → no --runtime-lengths (HPB path)
    out_header: Path
    out_source: Path


def _parse_manifest(lines: List[str], gen_dir: Path) -> List[BenchJob]:
    jobs: List[BenchJob] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"Bad manifest line (need id + proto_path): {line!r}")
        bench_id = int(parts[0])
        proto_path = Path(parts[1])
        runtime_lengths = Path(parts[2]) if len(parts) >= 3 else None
        jobs.append(BenchJob(
            bench_id=bench_id,
            proto_path=proto_path,
            runtime_lengths=runtime_lengths,
            out_header=gen_dir / f"bench{bench_id}_descriptors.h",
            out_source=gen_dir / f"bench{bench_id}_data.c",
        ))
    return jobs


def _run_one(job: BenchJob) -> int:
    """Invoke proto_to_accel with the job's args. Returns bench_id.

    Mutates sys.argv and calls proto_to_accel.main() rather than the
    subprocess route so we avoid the ~200ms per-call Python startup hit.
    ``main()`` parses argv and writes files; we restore argv afterward so
    each worker handles multiple jobs cleanly.
    """
    argv_saved = sys.argv
    try:
        args = [
            "proto_to_accel.py",
            "--proto", str(job.proto_path),
            "--out-header", str(job.out_header),
            "--out-source", str(job.out_source),
        ]
        if job.runtime_lengths is not None:
            args += ["--runtime-lengths", str(job.runtime_lengths)]
        sys.argv = args
        proto_to_accel.main()
    finally:
        sys.argv = argv_saved
    return job.bench_id


def _build_manifest_from_dirs(
    hpb_ids: List[int],
    synth_ids: List[int],
    hpb_dir: Path,
    synth_dir: Path,
    gen_dir: Path,
) -> List[BenchJob]:
    """Build bench jobs directly from id lists + source-tree roots.

    Avoids shelling out expanded ``echo ...`` lines per bench, which blows
    out ARG_MAX above a few thousand benches.
    """
    jobs: List[BenchJob] = []
    for b in hpb_ids:
        jobs.append(BenchJob(
            bench_id=b,
            proto_path=hpb_dir / f"bench{b}" / "benchmark.proto",
            runtime_lengths=None,
            out_header=gen_dir / f"bench{b}_descriptors.h",
            out_source=gen_dir / f"bench{b}_data.c",
        ))
    for b in synth_ids:
        bd = synth_dir / f"bench{b}"
        jobs.append(BenchJob(
            bench_id=b,
            proto_path=bd / "benchmark.proto",
            runtime_lengths=bd / "runtime_lengths.json",
            out_header=gen_dir / f"bench{b}_descriptors.h",
            out_source=gen_dir / f"bench{b}_data.c",
        ))
    return jobs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gen-dir", type=Path, default=_THIS_DIR / "gen",
                    help="Output dir for bench<N>_{descriptors.h,data.c}.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifest", type=str,
                     help="Path to manifest file, or '-' to read stdin. One "
                          "line per bench: '<id> <proto> [<runtime_json>]'.")
    src.add_argument("--from-dirs", action="store_true",
                     help="Build the job list from --hpb-ids / --synth-ids "
                          "+ --hpb-dir / --synth-dir. Avoids the ARG_MAX "
                          "limit when launching 1000s of benches from make.")
    ap.add_argument("--hpb-ids", type=str, default="",
                    help="Comma-separated HPB bench ids (with --from-dirs).")
    ap.add_argument("--synth-ids", type=str, default="",
                    help="Comma-separated synth bench ids (with --from-dirs).")
    ap.add_argument("--hpb-dir", type=Path,
                    help="Root dir for HPB bench<N>/benchmark.proto (required with --from-dirs).")
    ap.add_argument("--synth-dir", type=Path,
                    help="Root dir for synth bench<N>/ (required with --from-dirs).")
    ap.add_argument("--workers", type=int,
                    default=max(1, (os.cpu_count() or 2) // 2),
                    help="Parallel worker processes (default: half of nproc).")
    args = ap.parse_args()

    if args.from_dirs:
        if not args.hpb_dir or not args.synth_dir:
            sys.exit("--from-dirs requires --hpb-dir and --synth-dir")
        hpb_ids = [int(x) for x in args.hpb_ids.split(",") if x.strip()]
        synth_ids = [int(x) for x in args.synth_ids.split(",") if x.strip()]
        jobs = _build_manifest_from_dirs(
            hpb_ids, synth_ids, args.hpb_dir, args.synth_dir, args.gen_dir,
        )
    else:
        if args.manifest == "-":
            manifest_lines = sys.stdin.readlines()
        else:
            manifest_lines = Path(args.manifest).read_text().splitlines()
        jobs = _parse_manifest(manifest_lines, args.gen_dir)
    if not jobs:
        sys.exit("No jobs in manifest.")
    args.gen_dir.mkdir(parents=True, exist_ok=True)

    total = len(jobs)
    if args.workers == 1:
        for i, job in enumerate(jobs, start=1):
            _run_one(job)
            print(f"[{i}/{total}] bench{job.bench_id}", file=sys.stderr)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_run_one, j): j for j in jobs}
            for i, fut in enumerate(as_completed(futures), start=1):
                bid = fut.result()
                print(f"[{i}/{total}] bench{bid}", file=sys.stderr)

    print(f"Generated {total} bench descriptor + data pairs in {args.gen_dir}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
