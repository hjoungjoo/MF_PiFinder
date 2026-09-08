"""MF scalar projection must stay identical for Align and target markers."""

import numpy as np
import pandas
import pytest

from PiFinder import plot

pytestmark = pytest.mark.unit


def old_scalar_projection(field, ra, dec):
    """The pre-port scalar implementation, kept as an independent oracle."""
    dataframe = pandas.DataFrame(
        {
            "ra_hours": [plot.Angle(degrees=ra)._hours],
            "dec_degrees": [dec],
            "epoch_year": 1991.25,
        }
    )
    position = field.earth.observe(plot.Star.from_dataframe(dataframe))
    x_array, y_array = field.projection(position)
    x, y = float(x_array[0]), float(y_array[0])
    roll_rad = field.roll * (np.pi / 180.0)
    xr = x * np.cos(roll_rad) - y * np.sin(roll_rad)
    yr = x * np.sin(roll_rad) + y * np.cos(roll_rad)
    return (
        xr * field.pixel_scale + field.render_center[0],
        -yr * field.pixel_scale + field.render_center[1],
    )


@pytest.mark.parametrize("roll", (0, 37, 180, -92))
@pytest.mark.parametrize("dec_center", (-70, 0, 80))
def test_batch_and_scalar_match_prior_mf_projection(roll, dec_center):
    field = object.__new__(plot.Starfield)
    field.earth = plot.sf_utils.earth.at(plot.sf_utils.ts.tt_jd(2460000.0))
    center = field.earth.observe(plot.Star(ra_hours=0, dec_degrees=dec_center))
    field.projection = plot.build_stereographic_projection(center)
    field.roll = roll
    field.pixel_scale = 173.0
    field.render_center = (88, 88)
    ras = [359.0, 0.0, 1.0, 3.0]
    decs = [dec_center - 1, dec_center, dec_center + 1, dec_center + 2]
    xs, ys = field.radec_to_xy_many(ras, decs)
    for ra, dec, x, y in zip(ras, decs, xs, ys):
        expected = old_scalar_projection(field, ra, dec)
        assert (x, y) == pytest.approx(expected, abs=1e-10)
        assert field.radec_to_xy(ra, dec) == pytest.approx(expected, abs=1e-10)


def test_empty_projection_never_touches_skyfield():
    assert object.__new__(plot.Starfield).radec_to_xy_many([], []) == ([], [])
