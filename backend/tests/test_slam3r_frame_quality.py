from pipeline.slam3r_runner import _select_quality_indices


def test_quality_selection_drops_only_bounded_extreme_frames():
    sharpness = [100.0] * 20
    sharpness[5] = 1.0
    sharpness[6] = 2.0
    selected = _select_quality_indices(
        sharpness, [0.0] * 20, [0.0] * 20, fps=4.0, max_drop_fraction=0.15,
    )
    assert 5 not in selected and 6 not in selected
    assert len(selected) >= 17
    assert selected[0] == 0 and selected[-1] == 19


def test_quality_selection_preserves_timeline_coverage():
    selected = _select_quality_indices(
        [1.0] * 16, [0.9] * 16, [0.0] * 16, fps=4.0, max_drop_fraction=0.3,
    )
    for start in range(0, 16, 4):
        assert any(index in selected for index in range(start, start + 4))


def test_quality_selection_caps_long_video_across_full_timeline():
    sharpness = [20.0 + index % 7 for index in range(1200)]
    selected = _select_quality_indices(
        sharpness, [0.0] * 1200, [0.0] * 1200,
        fps=4.0, max_drop_fraction=0.15, max_frames=900,
    )
    assert len(selected) <= 900
    assert selected[0] == 0 and selected[-1] == 1199
    assert any(index > 1100 for index in selected)
