import pytest

from pipeline.scene_contract import validate_metric_calibration


def test_metric_apriltag_contract_requires_applied_scale():
    metadata = validate_metric_calibration({
        "status": "metric_apriltag",
        "coordinate_unit": "meters",
        "scale_factor": 0.42,
        "scale_applied_by": "vid2scene",
    })
    assert metadata["coordinate_unit"] == "meters"


def test_metric_apriltag_contract_rejects_double_scale_ambiguous_state():
    with pytest.raises(ValueError, match="缩放凭据"):
        validate_metric_calibration({
            "status": "metric_apriltag",
            "coordinate_unit": "meters",
            "scale_factor": 0.42,
            "scale_applied_by": None,
        })


def test_relative_contract_uses_model_units():
    metadata = validate_metric_calibration({
        "status": "relative", "coordinate_unit": "model_units"
    })
    assert metadata["status"] == "relative"


def test_calibration_failure_cannot_claim_meter_units():
    with pytest.raises(ValueError, match="model_units"):
        validate_metric_calibration({
            "status": "calibration_failed", "coordinate_unit": "meters"
        })
