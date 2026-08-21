import numpy as np

from scripts.fuse_anchored_supplements import fit_similarity, robust_similarity


def test_similarity_recovery() -> None:
    rng = np.random.default_rng(12)
    source = rng.normal(size=(500, 3))
    angle = np.deg2rad(27.0)
    rotation = np.asarray([
        [np.cos(angle), -np.sin(angle), 0.0],
        [np.sin(angle), np.cos(angle), 0.0],
        [0.0, 0.0, 1.0],
    ])
    target = 1.37 * (source @ rotation.T) + np.asarray([0.8, -0.3, 2.1])
    scale, fitted_rotation, translation = fit_similarity(source, target)
    prediction = scale * (source @ fitted_rotation.T) + translation
    assert np.max(np.abs(prediction - target)) < 1e-10


def test_robust_similarity_ignores_outliers() -> None:
    rng = np.random.default_rng(34)
    source = rng.normal(size=(800, 3))
    target = 0.82 * source + np.asarray([-0.4, 1.2, 0.3])
    target[:100] = rng.normal(20.0, 5.0, size=(100, 3))
    scale, rotation, translation, residual = robust_similarity(source, target)
    prediction = scale * (source[100:] @ rotation.T) + translation
    expected = 0.82 * source[100:] + np.asarray([-0.4, 1.2, 0.3])
    assert abs(scale - 0.82) < 1e-3
    assert np.median(np.linalg.norm(prediction - expected, axis=1)) < 1e-3
    assert np.quantile(residual[100:], 0.9) < 1e-3
