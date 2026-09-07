"""osu!catch star-rating and pp via rosu-pp-py.

There is no official Relax pp for osu!catch, so - exactly like Relaxation Vault
does for osu!standard - we compute the normal catch pp for the RX score.
"""
from __future__ import annotations

import os

import rosu_pp_py as rosu

from .mods import strings_to_rosu

try:
    from importlib.metadata import version as _pkg_version

    PP_VERSION = f"rosu-pp-py {_pkg_version('rosu-pp-py')}"
except Exception:  # pragma: no cover
    PP_VERSION = "rosu-pp-py"


def _load(cache_path: str, beatmap_id: int) -> rosu.Beatmap:
    path = os.path.join(cache_path, f"{beatmap_id}.osu")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    bm = rosu.Beatmap(path=path)
    try:
        bm.convert(rosu.GameMode.Catch, None)
    except Exception:
        pass  # already catch, or an inconvertible map (caller handles the failure)
    return bm


def star_rating(cache_path: str, beatmap_id: int, mods: list[str] | None = None) -> float:
    """Star rating with the given mods (defaults to just RX, matching RV)."""
    mods = mods if mods is not None else ["RX"]
    bm = _load(cache_path, beatmap_id)
    attrs = rosu.Difficulty(mods=strings_to_rosu(mods)).calculate(bm)
    return attrs.stars


def calculate_pp(
    cache_path: str,
    beatmap_id: int,
    *,
    mods: list[str],
    accuracy: float,          # 0..1
    combo: int,
    count_great: int,
    count_large_droplet: int,
    count_small_droplet: int,
    count_small_droplet_miss: int,
    count_miss: int,
) -> float:
    bm = _load(cache_path, beatmap_id)
    perf = rosu.Performance(
        mods=strings_to_rosu(mods),
        lazer=True,
        accuracy=accuracy * 100.0,
        combo=combo,
        n300=count_great,
        n100=count_large_droplet,
        n50=count_small_droplet,
        n_katu=count_small_droplet_miss,
        misses=count_miss,
    )
    return perf.calculate(bm).pp
