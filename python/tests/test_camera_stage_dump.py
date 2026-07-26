#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
Unit tests for camera_stage_dump.py - lossless pipeline stage dumps.

Each dtype must round-trip bit-exact: the whole point of the dump is
inspecting what each processing stage did to the pixels, so any lossy
save would defeat it.
"""

import json

import numpy as np
import pytest
from PIL import Image

from PiFinder import camera_stage_dump


@pytest.mark.unit
class TestSaveStage:
    def test_uint16_roundtrips_through_16bit_png(self, tmp_path):
        arr = np.array([[0, 1000], [4095, 65535]], dtype=np.uint16)
        stats = camera_stage_dump.save_stage(tmp_path, 0, "raw_cropped", arr)
        assert stats["file"] == "00_raw_cropped.png"
        loaded = np.asarray(Image.open(tmp_path / stats["file"]))
        assert loaded.dtype == np.uint16 or loaded.dtype == np.int32
        assert np.array_equal(loaded.astype(np.uint16), arr)

    def test_uint8_roundtrips_through_png(self, tmp_path):
        arr = np.arange(256, dtype=np.uint8).reshape(16, 16)
        stats = camera_stage_dump.save_stage(tmp_path, 3, "stretched_8bit", arr)
        loaded = np.asarray(Image.open(tmp_path / stats["file"]))
        assert np.array_equal(loaded, arr)

    def test_float_roundtrips_through_npy(self, tmp_path):
        arr = np.array([[0.5, -3.75], [1e6, 0.0]], dtype=np.float32)
        stats = camera_stage_dump.save_stage(tmp_path, 1, "bias_subtracted", arr)
        assert stats["file"] == "01_bias_subtracted.npy"
        loaded = np.load(tmp_path / stats["file"])
        assert loaded.dtype == np.float32
        assert np.array_equal(loaded, arr)

    def test_stats_fields(self, tmp_path):
        arr = np.full((4, 4), 7, dtype=np.uint8)
        stats = camera_stage_dump.save_stage(tmp_path, 0, "x", arr)
        assert stats["shape"] == [4, 4]
        assert stats["mean"] == 7.0
        assert stats["min"] == 7.0
        assert stats["max"] == 7.0


@pytest.mark.unit
class TestFinalize:
    def test_writes_stats_json(self, tmp_path):
        arr = np.zeros((2, 2), dtype=np.uint8)
        stats = [camera_stage_dump.save_stage(tmp_path, 0, "solver_input", arr)]
        camera_stage_dump.finalize(
            tmp_path, stats, {"exposure_us": 200000, "gain": 30.0}
        )
        payload = json.loads((tmp_path / "stats.json").read_text())
        assert payload["metadata"]["exposure_us"] == 200000
        assert payload["stages"][0]["stage"] == "solver_input"
