import numpy as np
from pipeline.calibrator import compute_scale_from_pixel, scale_from_door_prior


def test_compute_scale_from_pixel():
    # A4 长边 0.297m 在 2.0m 距离、焦距 600px 时，像素长度约 89px
    scale = compute_scale_from_pixel(pixel_len=89.1, physical_len=0.297, distance=2.0, focal=600.0)
    assert 0.9 < scale < 1.1  # 单位: 米/单位（此处相机单位=米）


def test_scale_from_door_prior():
    # 门高先验：点云中门框高度 1.6 单位，标准 2.0m → 尺度 1.25 m/unit
    s = scale_from_door_prior(door_height_units=1.6, standard_height=2.0)
    assert abs(s - 1.25) < 1e-6


def test_compute_scale_invalid_inputs():
    import pytest
    with pytest.raises(ValueError):
        compute_scale_from_pixel(pixel_len=0, physical_len=0.297, distance=2.0, focal=600.0)
    with pytest.raises(ValueError):
        scale_from_door_prior(door_height_units=0)
