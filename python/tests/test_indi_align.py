from datetime import datetime, timezone

from PiFinder.indi_align import (
    BRIGHT_ALIGN_STARS,
    angular_separation_degrees,
    nearest_align_star,
    visible_align_stars,
)


def test_bright_align_stars_loads_extended_offline_catalog():
    assert len(BRIGHT_ALIGN_STARS) >= 70
    assert any(star["name"] == "Sirius" for star in BRIGHT_ALIGN_STARS)


def test_nearest_align_star_prefers_nearby_unused_star():
    star = nearest_align_star(101.0, -16.0)
    assert star["name"] == "Sirius"

    next_star = nearest_align_star(101.0, -16.0, completed=[star])
    assert next_star["name"] != "Sirius"


def test_angular_separation_is_zero_for_same_position():
    assert angular_separation_degrees(10.0, 20.0, 10.0, 20.0) < 0.000001


def test_visible_align_stars_filters_below_horizon_targets():
    visible = visible_align_stars(
        37.52704,
        127.10936,
        30.0,
        datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc),
    )
    names = {star["name"] for star in visible}

    assert "Sirius" not in names
    assert {"Vega", "Arcturus"} & names
    assert all(star["alt"] >= 20.0 for star in visible)
