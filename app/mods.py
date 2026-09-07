"""Mod parsing / filtering, ported from Relaxation Vault's Utils.cs."""

# osu!catch-relevant subset of RV's allow-list. A score using anything outside
# this set is ignored entirely (not stored).
ALLOWED_MODS = {
    "HD", "DT", "NC", "HT", "DC", "HR", "EZ", "FL",
    "MR", "NF", "SD", "PF", "CL", "AC", "RX",
}

# Mod settings we tolerate. Anything else -> score ignored.
ALLOWED_MOD_SETTINGS = {"speed_change", "adjust_pitch"}

_RATE_KEY = "speed_change"


def mod_to_string(mod: dict) -> str:
    """{'acronym': 'DT', 'settings': {'speed_change': 1.3}} -> 'DTx1.3'."""
    acronym = mod.get("acronym", "")
    settings = mod.get("settings") or {}
    if _RATE_KEY in settings:
        return f"{acronym}x{settings[_RATE_KEY]}"
    return acronym


def mods_to_strings(api_mods: list[dict]) -> list[str]:
    return [mod_to_string(m) for m in api_mods]


def is_allowed(api_mods: list[dict]) -> bool:
    for m in api_mods:
        if m.get("acronym") not in ALLOWED_MODS:
            return False
        for key in (m.get("settings") or {}):
            if key not in ALLOWED_MOD_SETTINGS:
                return False
    return True


def has_relax(api_mods: list[dict]) -> bool:
    return any(m.get("acronym") == "RX" for m in api_mods)


def string_to_rosu(mod_string: str) -> dict:
    """'DTx1.3' -> {'acronym': 'DT', 'settings': {'speed_change': 1.3}}."""
    if "x" in mod_string:
        acronym, _, rate = mod_string.partition("x")
        try:
            return {"acronym": acronym, "settings": {_RATE_KEY: float(rate)}}
        except ValueError:
            return {"acronym": acronym}
    return {"acronym": mod_string}


def strings_to_rosu(mod_strings: list[str]) -> list[dict]:
    return [string_to_rosu(s) for s in mod_strings if s]
