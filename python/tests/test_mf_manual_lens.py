"""Manual one-decimal focal-length override coverage."""

import pytest

from PiFinder.mf_manual_lens import normalise_manual_focal_length
from PiFinder.optics import OpticalTrainResolver, build_optical_train


pytestmark = pytest.mark.unit


def test_manual_focal_length_is_normalised_to_one_decimal_place():
    assert normalise_manual_focal_length("7.64") == 7.6
    assert normalise_manual_focal_length(7.65) == 7.7
    assert normalise_manual_focal_length("") is None


def test_manual_focal_length_overrides_selected_lens_then_clears_cleanly():
    resolver = OpticalTrainResolver()
    selected = resolver.resolve("imx462", "16mm")
    manual = resolver.resolve("imx462", "16mm", 7.5)
    cleared = resolver.resolve("imx462", "16mm", None)

    assert manual.lens.key == "manual"
    assert manual.lens.nominal_focal_length_mm == 7.5
    assert manual.fov_degrees > selected.fov_degrees
    assert cleared.fov_degrees == pytest.approx(selected.fov_degrees)


def test_manual_focal_length_does_not_mutate_the_registry_selection():
    train = build_optical_train("imx462", "4mm", 6.2)
    assert train.lens.key == "manual"
    assert train.lens.effective_focal_length_mm == 6.2


@pytest.mark.parametrize("value", ("bad", 0, 100.0, -1.0))
def test_manual_focal_length_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        normalise_manual_focal_length(value)
