"""Quick sanity check for osu!catch pp. Usage:

    python scripts/pp_probe.py <beatmap_id> [accuracy] [mods...]

Downloads the .osu into the beatmap cache if missing, prints SR + pp for a
perfect play and for the given accuracy.
"""
import os
import sys

import requests

from app import create_app
from app.pp import calculate_pp, star_rating

app = create_app()
bid = int(sys.argv[1])
acc = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
mods = sys.argv[3:] or ["RX"]

with app.app_context():
    cache = app.config["BEATMAP_CACHE"]
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, f"{bid}.osu")
    if not os.path.exists(path):
        r = requests.get(f"https://osu.ppy.sh/osu/{bid}", timeout=60)
        r.raise_for_status()
        open(path, "wb").write(r.content)
        print("downloaded", path, len(r.content), "bytes")

    import rosu_pp_py as rosu

    bm = rosu.Beatmap(path=path)
    try:
        bm.convert(rosu.GameMode.Catch, None)
    except Exception:
        pass
    diff = rosu.Difficulty(mods=[{"acronym": m} for m in mods]).calculate(bm)
    fruits = diff.n_fruits or 0
    droplets = diff.n_droplets or 0
    tiny = diff.n_tiny_droplets or 0
    print(f"SR({','.join(mods)}) = {diff.stars:.2f}  max_combo={diff.max_combo}")
    print(f"objects: fruits={fruits} droplets={droplets} tiny={tiny}")

    pp_fc = calculate_pp(cache, bid, mods=mods, accuracy=1.0, combo=diff.max_combo,
                         count_great=fruits, count_large_droplet=droplets,
                         count_small_droplet=tiny, count_small_droplet_miss=0, count_miss=0)
    print(f"perfect FC pp = {pp_fc:.2f}")

    if acc < 1.0:
        miss = max(1, round(fruits * (1 - acc)))
        pp = calculate_pp(cache, bid, mods=mods, accuracy=acc, combo=int(diff.max_combo * 0.9),
                          count_great=fruits - miss, count_large_droplet=droplets,
                          count_small_droplet=tiny, count_small_droplet_miss=0, count_miss=miss)
        print(f"{acc:.3f} acc, {miss} miss, 90% combo pp = {pp:.2f}")
