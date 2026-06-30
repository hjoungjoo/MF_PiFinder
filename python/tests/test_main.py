import pytest

from PiFinder import main


@pytest.mark.smoke
def test_smoke():
    """
    If we get here, all the imports in main work
    """
    assert True


class _Location:
    def __init__(self, source="None", error_in_m=0):
        self.source = source
        self.error_in_m = error_in_m


def test_user_location_can_replace_previous_web_location():
    assert main.should_apply_location_fix(
        _Location(source="WEB", error_in_m=0),
        {"source": "WEB", "error_in_m": 0},
    )


def test_gps_does_not_replace_user_loaded_location():
    assert not main.should_apply_location_fix(
        _Location(source="WEB", error_in_m=0),
        {"source": "GPS", "error_in_m": 1},
    )


def test_config_location_can_replace_previous_manual_location():
    assert main.should_apply_location_fix(
        _Location(source="MANUAL", error_in_m=0),
        {"source": "CONFIG: Home", "error_in_m": 0},
    )
