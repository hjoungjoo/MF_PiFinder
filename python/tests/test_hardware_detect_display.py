from PiFinder import hardware_detect


class _FakeI2C:
    def __init__(self, addresses):
        self.addresses = addresses
        self.unlocked = False

    def try_lock(self):
        return True

    def scan(self):
        return self.addresses

    def unlock(self):
        self.unlocked = True


class _FakeBoard:
    def __init__(self, addresses):
        self.addresses = addresses

    def I2C(self):
        return _FakeI2C(self.addresses)


def test_default_display_falls_back_without_board(monkeypatch):
    monkeypatch.setattr(hardware_detect, "board", None)
    assert hardware_detect.detect_ssd1333_display() is False
    assert hardware_detect.default_display_hardware() == "ssd1351"


def test_default_display_selects_ssd1333_when_marker_present(monkeypatch):
    monkeypatch.setattr(
        hardware_detect,
        "board",
        _FakeBoard([hardware_detect.BQ25895_ADDRESS]),
    )
    assert hardware_detect.detect_ssd1333_display() is True
    assert hardware_detect.default_display_hardware() == "ssd1333"


def test_default_display_uses_ssd1351_when_marker_absent(monkeypatch):
    monkeypatch.setattr(hardware_detect, "board", _FakeBoard([]))
    assert hardware_detect.detect_ssd1333_display() is False
    assert hardware_detect.default_display_hardware() == "ssd1351"
