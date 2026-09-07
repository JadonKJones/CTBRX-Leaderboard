from app.mods import (
    has_relax,
    is_allowed,
    mods_to_strings,
    strings_to_rosu,
)


def test_mods_to_strings_rate():
    api = [{"acronym": "RX"}, {"acronym": "DT", "settings": {"speed_change": 1.3}}]
    assert mods_to_strings(api) == ["RX", "DTx1.3"]


def test_roundtrip_rate():
    assert strings_to_rosu(["DTx1.3"]) == [{"acronym": "DT", "settings": {"speed_change": 1.3}}]
    assert strings_to_rosu(["RX"]) == [{"acronym": "RX"}]


def test_has_relax():
    assert has_relax([{"acronym": "RX"}])
    assert not has_relax([{"acronym": "HD"}])


def test_allowlist():
    assert is_allowed([{"acronym": "RX"}, {"acronym": "HD"}])
    assert not is_allowed([{"acronym": "RX"}, {"acronym": "TP"}])  # target practice: not allowed
    assert not is_allowed([{"acronym": "DT", "settings": {"weird": 1}}])
