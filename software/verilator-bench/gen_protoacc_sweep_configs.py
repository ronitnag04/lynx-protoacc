#!/usr/bin/env python3
"""
Generate chipyard.ProtoAccelSweepConfigs.scala (and side-specific companions)
from parameter domains split between deserializer (``des_*``) and serializer
(``ser_*``).

Sweep types (``-t`` / ``--sweep-type``):
- random: sample N distinct combinations over the **active** parameter axes only
  (mixed-radix index decode). The inactive side stays at defaults.
- ofat: one-factor-at-a-time over active axes only; inactive side at defaults.
- default: a single full row matching merged defaults.
- tweak: one config per **active** parameter; first non-default value in that
  axis's value list.

Emit modes (``--emit``):
- both (default): ``ProtoAccelDesSweepConfigs`` + ``ProtoAccelSerSweepConfigs``
  with classes ``ProtoAccelDesSweepSample*`` / ``ProtoAccelSerSweepSample*``.
  Serializer parameters vary only in the ser block; deserializer only in the
  des block. ``-n`` applies **per side** for random sweeps.
- des / ser: emit only that side's object + classes.
- joint: legacy single object ``ProtoAccelSweepConfigs`` + ``ProtoAccelSweepSample*``
  varying all axes together (old joint space).

Mixed-radix axis order within a side is sorted keys for that side
(``des_*`` then ``ser_*`` when combined for fragment emission order).

Examples:
  python3 gen_protoacc_sweep_configs.py -t random -n 32 -s 42
  python3 gen_protoacc_sweep_configs.py --emit des -t ofat -o /tmp/out.scala
  python3 gen_protoacc_sweep_configs.py --emit joint -t random -n 16 -s 0
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = (
    SCRIPT_DIR.parent.parent.parent
    / "chipyard"
    / "src"
    / "main"
    / "scala"
    / "config"
    / "ProtoAccelSweepConfigs.scala"
)

# --- Deserializer queue / depth parameters (``des_*`` keys) -----------------

DES_PARAM_VALUES: Dict[str, List[int]] = {
    "des_top_descriptor_reqs": [2, 4, 8, 16],
    "des_top_memloader_reqs": [16, 32, 64, 128],
    "des_cr_rocc_commands": [2, 4, 8],
    "des_dth_l1_reqs": [2, 4, 8, 16],
    "des_dth_fd_reqs": [2, 4, 8, 16],
    "des_dth_fd_resps": [2, 4, 8, 16],
    "des_fw_l1_reqs": [2, 4, 8, 16],
    "des_ml_buf_info_q": [8, 16, 32, 64],
    "des_ml_load_info_q": [128, 256, 512, 1024],
}

DES_DEFAULT_PARAM_VALUES: Dict[str, int] = {
    "des_top_descriptor_reqs": 4,
    "des_top_memloader_reqs": 64,
    "des_cr_rocc_commands": 2,
    "des_dth_l1_reqs": 4,
    "des_dth_fd_reqs": 4,
    "des_dth_fd_resps": 4,
    "des_fw_l1_reqs": 4,
    "des_ml_buf_info_q": 16,
    "des_ml_load_info_q": 256,
}

DES_WITH_CLASS_BY_KEY: Dict[str, str] = {
    "des_top_descriptor_reqs": "WithProtoAccelDesDescrOutstanding",
    "des_top_memloader_reqs": "WithProtoAccelDesMemloaderOutstanding",
    "des_cr_rocc_commands": "WithProtoAccelDesCrRoccCommands",
    "des_dth_l1_reqs": "WithProtoAccelDesDthL1Reqs",
    "des_dth_fd_reqs": "WithProtoAccelDesDthFdReqs",
    "des_dth_fd_resps": "WithProtoAccelDesDthFdResps",
    "des_fw_l1_reqs": "WithProtoAccelDesFwL1Reqs",
    "des_ml_buf_info_q": "WithProtoAccelDesMlBufInfoQ",
    "des_ml_load_info_q": "WithProtoAccelDesMlLoadInfoQ",
}

DES_SHORT_LABEL_BY_KEY: Dict[str, str] = {
    "des_top_descriptor_reqs": "DesTopDescr",
    "des_top_memloader_reqs": "DesTopMemloader",
    "des_cr_rocc_commands": "DesCrRocc",
    "des_dth_l1_reqs": "DesDthL1",
    "des_dth_fd_reqs": "DesDthFdReqs",
    "des_dth_fd_resps": "DesDthFdResps",
    "des_fw_l1_reqs": "DesFwL1",
    "des_ml_buf_info_q": "DesMlBufInfo",
    "des_ml_load_info_q": "DesMlLoadInfo",
}

# --- Serializer queue / depth parameters (``ser_*`` keys) ---------------------

SER_PARAM_VALUES: Dict[str, List[int]] = {
    "ser_field_handlers": [2, 4, 6, 8],
    "ser_cr_rocc_commands": [2, 4, 6, 8],
    "ser_dth_hasbits_reqs": [2, 4, 8],
    "ser_dth_descriptor_reqs": [2, 4, 8, 16],
    "ser_dth_reg_resps": [5, 10, 20],
    "ser_dth_reqs_meta": [2, 4, 8, 16],
    "ser_dth_fh_outputs": [2, 4, 8, 16],
    "ser_mw_write_input": [2, 4, 8, 16],
    "ser_mw_write_inject": [2, 4, 8, 16],
    "ser_mw_write_ptrs": [5, 10, 20],
}

SER_DEFAULT_PARAM_VALUES: Dict[str, int] = {
    "ser_field_handlers": 6,
    "ser_cr_rocc_commands": 2,
    "ser_dth_hasbits_reqs": 4,
    "ser_dth_descriptor_reqs": 4,
    "ser_dth_reg_resps": 10,
    "ser_dth_reqs_meta": 4,
    "ser_dth_fh_outputs": 4,
    "ser_mw_write_input": 4,
    "ser_mw_write_inject": 4,
    "ser_mw_write_ptrs": 10,
}

SER_WITH_CLASS_BY_KEY: Dict[str, str] = {
    "ser_field_handlers": "WithProtoAccelSerFieldHandlers",
    "ser_cr_rocc_commands": "WithProtoAccelSerCrRoccCommands",
    "ser_dth_hasbits_reqs": "WithProtoAccelSerDthHasbitsReqs",
    "ser_dth_descriptor_reqs": "WithProtoAccelSerDthDescriptorReqs",
    "ser_dth_reg_resps": "WithProtoAccelSerDthRegResps",
    "ser_dth_reqs_meta": "WithProtoAccelSerDthReqsMeta",
    "ser_dth_fh_outputs": "WithProtoAccelSerDthFhOutputs",
    "ser_mw_write_input": "WithProtoAccelSerMwWriteInput",
    "ser_mw_write_inject": "WithProtoAccelSerMwWriteInject",
    "ser_mw_write_ptrs": "WithProtoAccelSerMwWritePtrs",
}

SER_SHORT_LABEL_BY_KEY: Dict[str, str] = {
    "ser_field_handlers": "SerFieldHandlers",
    "ser_cr_rocc_commands": "SerCrRocc",
    "ser_dth_hasbits_reqs": "SerDthHasbits",
    "ser_dth_descriptor_reqs": "SerDthDescr",
    "ser_dth_reg_resps": "SerDthRegResps",
    "ser_dth_reqs_meta": "SerDthReqsMeta",
    "ser_dth_fh_outputs": "SerDthFhOutputs",
    "ser_mw_write_input": "SerMwWriteInput",
    "ser_mw_write_inject": "SerMwWriteInject",
    "ser_mw_write_ptrs": "SerMwWritePtrs",
}

# Merged tables (must match generators/protoacc/src/main/scala/util.scala defaults).
PARAM_VALUES: Dict[str, List[int]] = {**DES_PARAM_VALUES, **SER_PARAM_VALUES}
DEFAULT_PARAM_VALUES: Dict[str, int] = {**DES_DEFAULT_PARAM_VALUES, **SER_DEFAULT_PARAM_VALUES}
WITH_CLASS_BY_KEY: Dict[str, str] = {**DES_WITH_CLASS_BY_KEY, **SER_WITH_CLASS_BY_KEY}
SHORT_LABEL_BY_KEY: Dict[str, str] = {**DES_SHORT_LABEL_BY_KEY, **SER_SHORT_LABEL_BY_KEY}

DES_KEYS: Tuple[str, ...] = tuple(sorted(DES_PARAM_VALUES.keys()))
SER_KEYS: Tuple[str, ...] = tuple(sorted(SER_PARAM_VALUES.keys()))
FULL_PARAM_KEYS: Tuple[str, ...] = tuple(sorted(PARAM_VALUES.keys()))

_MAX_INDICES_MATERIALIZE = 10**9

# Same stack as HyperscaleConfigs.scala `ProtoAccelRocketBaseConfig` (single reference).
BASE_FRAGMENTS = """  new ProtoAccelRocketBaseConfig)"""

DEBUG_PREFIX = "  new protoacc.WithProtoAccelPrintf ++\n"

# Second side random sweep seed offset (avoid duplicating the same index draw).
_SER_SEED_OFFSET = 1_000_003


def _validate_tables() -> None:
    des_k = set(DES_PARAM_VALUES)
    ser_k = set(SER_PARAM_VALUES)
    if des_k & ser_k:
        raise SystemExit(f"DES and SER parameter keys overlap: {des_k & ser_k!r}")
    if set(PARAM_VALUES) != des_k | ser_k:
        raise SystemExit("PARAM_VALUES must be the disjoint union of DES and SER keys")

    for side, keys, defaults, pvals in (
        ("DES", des_k, DES_DEFAULT_PARAM_VALUES, DES_PARAM_VALUES),
        ("SER", ser_k, SER_DEFAULT_PARAM_VALUES, SER_PARAM_VALUES),
    ):
        if set(defaults) != keys:
            raise SystemExit(f"{side} defaults keys {set(defaults)!r} != param keys {keys!r}")
        for k in keys:
            if defaults[k] not in pvals[k]:
                raise SystemExit(
                    f"{side} default for {k!r} is {defaults[k]!r} but must appear in sweep values"
                )

    merged_defaults = {**DES_DEFAULT_PARAM_VALUES, **SER_DEFAULT_PARAM_VALUES}
    if merged_defaults != DEFAULT_PARAM_VALUES:
        raise SystemExit("DEFAULT_PARAM_VALUES must merge DES and SER defaults")

    for label, d in (("WITH_CLASS_BY_KEY", WITH_CLASS_BY_KEY), ("SHORT_LABEL_BY_KEY", SHORT_LABEL_BY_KEY)):
        if set(d) != set(PARAM_VALUES):
            raise SystemExit(f"{label} keys must match PARAM_VALUES")


def first_non_default_value(
    key: str, *, defaults: Mapping[str, int], param_values: Mapping[str, Sequence[int]]
) -> int | None:
    d = defaults[key]
    for v in param_values[key]:
        if v != d:
            return v
    return None


def total_combinations(
    param_values: Mapping[str, Sequence[int]], keys: Sequence[str]
) -> int:
    n = 1
    for k in keys:
        n *= len(param_values[k])
    return n


def _compute_strides(
    param_values: Mapping[str, Sequence[int]], keys: Sequence[str]
) -> Tuple[List[int], List[int]]:
    sizes = [len(param_values[k]) for k in keys]
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    return sizes, strides


def index_to_combination(
    index: int,
    param_values: Mapping[str, Sequence[int]],
    keys: Sequence[str],
    *,
    sizes: List[int] | None = None,
    strides: List[int] | None = None,
) -> Dict[str, int]:
    """Decode linear index into a partial parameter dict (mixed-radix over ``keys``)."""
    value_lists = [param_values[k] for k in keys]
    if sizes is None or strides is None:
        sizes, strides = _compute_strides(param_values, keys)
    digits = [(index // strides[i]) % sizes[i] for i in range(len(sizes))]
    return dict(zip(keys, [value_lists[i][d] for i, d in enumerate(digits)]))


def sample_random_indices(total: int, n: int) -> List[int]:
    """
    Return n distinct random indices in [0, total) without materializing range(total).
    """
    if n >= total:
        if total <= _MAX_INDICES_MATERIALIZE:
            return list(range(total))
        raise SystemExit(
            f"Total combinations ({total}) is very large; "
            "num_configs must be less than total (use -n to sample a subset)."
        )
    chosen: List[int] = []
    seen: set[int] = set()
    while len(chosen) < n:
        idx = random.randrange(total)
        if idx not in seen:
            seen.add(idx)
            chosen.append(idx)
    return chosen


def merge_full_combo(partial: Mapping[str, int]) -> Dict[str, int]:
    """Fill a partial sweep (one side's axes) with defaults for the full config."""
    row = dict(DEFAULT_PARAM_VALUES)
    row.update(partial)
    return row


def _combo_comment(combo: Mapping[str, int]) -> str:
    parts = [f"{SHORT_LABEL_BY_KEY[k]}={combo[k]}" for k in FULL_PARAM_KEYS]
    return ", ".join(parts)


def _scala_fragments(combo: Mapping[str, int]) -> str:
    lines = [
        f"  new protoacc.{WITH_CLASS_BY_KEY[k]}({combo[k]}) ++" for k in FULL_PARAM_KEYS
    ]
    return "\n".join(lines)


def build_combinations(
    sweep_type: str,
    num_configs: int,
    seed: int,
    active_keys: Sequence[str],
) -> Tuple[List[Dict[str, int]], str]:
    """Return (list of **full** combo dicts, human-readable summary line)."""
    slice_pv = {k: PARAM_VALUES[k] for k in active_keys}
    side = "joint" if len(active_keys) == len(FULL_PARAM_KEYS) else (
        "deserializer" if set(active_keys) <= set(DES_KEYS) else (
            "serializer" if set(active_keys) <= set(SER_KEYS) else "custom"
        )
    )

    if sweep_type == "random":
        random.seed(seed)
        total = total_combinations(slice_pv, active_keys)
        n = min(num_configs, total)
        summary = (
            f"[{side}] Total combinations on active axes: {total:.2e}. "
            f"Sampling {n} random indices (seed={seed})."
        )
        if n >= total:
            if total > _MAX_INDICES_MATERIALIZE:
                raise SystemExit(
                    f"Active space has {total} combinations (> {_MAX_INDICES_MATERIALIZE}); "
                    "cannot enumerate. Use -n < total to sample."
                )
            random_indices = list(range(total))
        else:
            random_indices = sample_random_indices(total, n)
        sizes, strides = _compute_strides(slice_pv, active_keys)
        combinations = [
            merge_full_combo(
                index_to_combination(
                    k, slice_pv, active_keys, sizes=sizes, strides=strides
                )
            )
            for k in random_indices
        ]
        return combinations, summary

    if sweep_type == "ofat":
        combinations = []
        for key in active_keys:
            for value in PARAM_VALUES[key]:
                combo = dict(DEFAULT_PARAM_VALUES)
                combo[key] = value
                combinations.append(combo)
        summary = (
            f"[{side}] OFAT sweep over {len(active_keys)} parameters, "
            f"{len(combinations)} total rows (--num-configs and --seed ignored)."
        )
        return combinations, summary

    if sweep_type == "default":
        combinations = [dict(DEFAULT_PARAM_VALUES)]
        summary = (
            f"[{side}] Single default baseline (--num-configs and --seed ignored)."
        )
        return combinations, summary

    if sweep_type == "tweak":
        combinations = []
        skipped: List[str] = []
        defaults = {k: DEFAULT_PARAM_VALUES[k] for k in active_keys}
        for key in active_keys:
            alt = first_non_default_value(
                key, defaults=defaults, param_values=slice_pv
            )
            if alt is None:
                skipped.append(key)
                continue
            row = dict(DEFAULT_PARAM_VALUES)
            row[key] = alt
            combinations.append(row)
        summary = (
            f"[{side}] Tweak sweep: {len(combinations)} rows. "
            "--num-configs and --seed ignored."
        )
        if skipped:
            summary += (
                f" Skipped {len(skipped)} parameter(s) with no value != default: "
                + ", ".join(skipped)
            )
        return combinations, summary

    raise SystemExit(f"Unknown sweep type: {sweep_type}")


def _emit_side_block(
    *,
    lines: List[str],
    object_name: str,
    sample_prefix: str,
    debug_sample_prefix: str,
    combinations: Sequence[Mapping[str, int]],
    sweep_type: str,
    seed: int,
    num_configs_requested: int,
    generate_debug: bool,
) -> None:
    n = len(combinations)
    idx_width = max(3, int(math.ceil(math.log10(max(n, 10)))))

    lines.append(f"object {object_name} {{")
    lines.append("")
    lines.append(f'  val generationSweepType: String = "{sweep_type}"')
    lines.append(f"  val generationSeed: Long = {seed}L")
    lines.append(f"  val generationNumConfigsRequested: Int = {num_configs_requested}")
    lines.append(f"  val generationNumConfigsEmitted: Int = {n}")
    lines.append("")
    lines.append("  /** CONFIG= names for non-debug generated classes (basename only). */")
    lines.append("  val normalSweepConfigNames: Seq[String] = Seq(")
    for i in range(n):
        name = f"{sample_prefix}{i:0{idx_width}d}Config"
        lines.append(f'    "{name}",')
    lines.append("  )")

    if generate_debug:
        lines += [
            "",
            "  /** CONFIG= names for debug (printf) variants. */",
            "  val debugSweepConfigNames: Seq[String] = Seq(",
        ]
        for i in range(n):
            dname = f"{debug_sample_prefix}{i:0{idx_width}d}Config"
            lines.append(f'    "{dname}",')
        lines.append("  )")
    else:
        lines += [
            "",
            "  /** Populate with ``--debug``. */",
            "  val debugSweepConfigNames: Seq[String] = Seq.empty[String]",
        ]

    lines.append("}")
    lines.append("")

    for i, combo in enumerate(combinations):
        cmt = _combo_comment(combo)
        name = f"{sample_prefix}{i:0{idx_width}d}Config"
        lines.append(f"/** Sweep row {i + 1}/{n} ({sweep_type}): {cmt} */")
        lines.append(f"class {name} extends Config(")
        lines.append(_scala_fragments(combo))
        lines.append(BASE_FRAGMENTS)
        lines.append("")

        if generate_debug:
            dname = f"{debug_sample_prefix}{i:0{idx_width}d}Config"
            lines.append(f"/** Debug printf variant of `{name}`. */")
            lines.append(f"class {dname} extends Config(")
            lines.append(DEBUG_PREFIX.rstrip("\n"))
            lines.append(_scala_fragments(combo))
            lines.append(BASE_FRAGMENTS)
            lines.append("")


def render_file_joint(
    *,
    out_path: Path,
    combinations: Sequence[Mapping[str, int]],
    sweep_type: str,
    seed: int,
    num_configs_requested: int,
    generate_debug: bool,
    summaries: Sequence[str],
) -> int:
    """Emit legacy joint ``ProtoAccelSweepConfigs``. Returns total Scala class count."""
    lines: List[str] = [
        "// GENERATED FILE — do not edit by hand.",
        "// Regenerate:",
        "//   python3 generators/protoacc/software/verilator-bench/gen_protoacc_sweep_configs.py \\",
        "//       --emit joint -t random -n <N> -s <seed> [--debug]",
        "//",
        f"// emit=joint sweep-type={sweep_type} requested_n={num_configs_requested} seed={seed}",
        "// Composes on `ProtoAccelRocketBaseConfig` (HyperscaleConfigs.scala).",
        "",
        "package chipyard",
        "",
        "import org.chipsalliance.cde.config.Config",
        "",
    ]
    for s in summaries:
        lines.append(f"// {s}")
    lines.append("")

    _emit_side_block(
        lines=lines,
        object_name="ProtoAccelSweepConfigs",
        sample_prefix="ProtoAccelSweepSample",
        debug_sample_prefix="ProtoAccelSweepDebugSample",
        combinations=combinations,
        sweep_type=sweep_type,
        seed=seed,
        num_configs_requested=num_configs_requested,
        generate_debug=generate_debug,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    n = len(combinations)
    total_classes = n * (2 if generate_debug else 1)
    return total_classes


def render_file_split(
    *,
    out_path: Path,
    blocks: Sequence[Tuple[str, str, str, str, List[Dict[str, int]], str]],
    sweep_type: str,
    seed_des: int,
    seed_ser: int,
    num_configs_requested: int,
    generate_debug: bool,
    summaries: Sequence[str],
) -> int:
    """
    ``blocks``: (object_name, sample_prefix, debug_sample_prefix, side_label, combos, seed_for_block)
    """
    lines: List[str] = [
        "// GENERATED FILE — do not edit by hand.",
        "// Regenerate (deserializer and serializer sweeps are independent):",
        "//   python3 generators/protoacc/software/verilator-bench/gen_protoacc_sweep_configs.py \\",
        "//       --emit both -t random -n <N> -s <seed> [--debug]",
        "//",
        f"// sweep-type={sweep_type} requested_n_per_side={num_configs_requested} "
        f"seed_des={seed_des} seed_ser={seed_ser}",
        "// Composes on `ProtoAccelRocketBaseConfig` (HyperscaleConfigs.scala).",
        "",
        "package chipyard",
        "",
        "import org.chipsalliance.cde.config.Config",
        "",
    ]
    for s in summaries:
        lines.append(f"// {s}")
    lines.append("")

    total_classes = 0
    for object_name, sample_pfx, debug_pfx, _label, combos, block_seed in blocks:
        _emit_side_block(
            lines=lines,
            object_name=object_name,
            sample_prefix=sample_pfx,
            debug_sample_prefix=debug_pfx,
            combinations=combos,
            sweep_type=sweep_type,
            seed=block_seed,
            num_configs_requested=num_configs_requested,
            generate_debug=generate_debug,
        )
        total_classes += len(combos) * (2 if generate_debug else 1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return total_classes


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output Scala path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--emit",
        type=str,
        default="both",
        choices=["both", "des", "ser", "joint"],
        help=(
            "both: ProtoAccelDesSweepConfigs + ProtoAccelSerSweepConfigs; "
            "des/ser: one side only; joint: legacy single-object joint sweep."
        ),
    )
    parser.add_argument(
        "-t",
        "--sweep-type",
        type=str,
        default="random",
        choices=["random", "ofat", "default", "tweak"],
        help=(
            "random: sample N combos on active axes; "
            "ofat: every value of each active param; default: one baseline; "
            "tweak: one row per active param off-default."
        ),
    )
    parser.add_argument(
        "-n",
        "--num-configs",
        type=int,
        default=32,
        help=(
            "For random sweeps: sample count. With --emit both, this is **per side** "
            "(des and ser each get N samples). Default 32 keeps the generated Scala "
            "roughly the same size as the old 32-row joint sweep."
        ),
    )
    parser.add_argument(
        "-s",
        "--seed",
        type=int,
        default=262,
        help="Random seed for random sweeps (ser side uses seed+1000003 when --emit both).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Also emit WithProtoAccelPrintf twin for each config",
    )
    args = parser.parse_args()

    _validate_tables()

    emit = args.emit
    sweep_type = args.sweep_type
    n = args.num_configs
    seed = args.seed

    if emit == "joint":
        combos, summary = build_combinations(sweep_type, n, seed, FULL_PARAM_KEYS)
        summaries = [summary]
        total = render_file_joint(
            out_path=args.output,
            combinations=combos,
            sweep_type=sweep_type,
            seed=seed,
            num_configs_requested=n,
            generate_debug=args.debug,
            summaries=summaries,
        )
        print(summary)
        print(f"Wrote {args.output} ({len(combos)} configs, {total} Scala classes)")
        return

    seed_des = seed
    seed_ser = seed + _SER_SEED_OFFSET
    summaries: List[str] = []
    blocks: List[Tuple[str, str, str, str, List[Dict[str, int]], int]] = []

    if emit in ("both", "des"):
        c_des, s_des = build_combinations(sweep_type, n, seed_des, DES_KEYS)
        summaries.append(s_des)
        blocks.append(
            (
                "ProtoAccelDesSweepConfigs",
                "ProtoAccelDesSweepSample",
                "ProtoAccelDesSweepDebugSample",
                "deserializer",
                c_des,
                seed_des,
            )
        )
    if emit in ("both", "ser"):
        c_ser, s_ser = build_combinations(sweep_type, n, seed_ser, SER_KEYS)
        summaries.append(s_ser)
        blocks.append(
            (
                "ProtoAccelSerSweepConfigs",
                "ProtoAccelSerSweepSample",
                "ProtoAccelSerSweepDebugSample",
                "serializer",
                c_ser,
                seed_ser,
            )
        )

    for s in summaries:
        print(s)
    total = render_file_split(
        out_path=args.output,
        blocks=blocks,
        sweep_type=sweep_type,
        seed_des=seed_des,
        seed_ser=seed_ser,
        num_configs_requested=n,
        generate_debug=args.debug,
        summaries=summaries,
    )
    nrows = sum(len(b[4]) for b in blocks)
    print(f"Wrote {args.output} ({nrows} configs across {len(blocks)} side block(s), {total} Scala classes)")


if __name__ == "__main__":
    main()
