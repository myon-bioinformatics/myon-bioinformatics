"""Numerical contracts for the standard-library SVD."""

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("transpose", [False, True])
def test_linalg_min_jacobi_reconstructs_tall_and_wide_matrices(transpose):
    source = [[1.0, 2.0, -1.0], [2.0, 3.0, 1.0], [4.0, 0.0, 1.0], [0.0, 2.0, 3.0]]
    if transpose:
        source = [list(column) for column in zip(*source)]
    path = ROOT / "tools" / "stdlib_min" / "numpy_linalg_min.py"
    spec = importlib.util.spec_from_file_location("numpy_linalg_min", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    u, s, vt = module.linalg.svd(module.array(source), full_matrices=False)
    u_rows, singular, vt_rows = u.tolist(), s.tolist(), vt.tolist()
    assert singular == sorted(singular, reverse=True)
    for left, expected in zip(u_rows, source, strict=True):
        reconstructed = [sum(a * sigma * b for a, sigma, b in zip(left, singular, right))
                         for right in zip(*vt_rows)]
        assert reconstructed == pytest.approx(expected, abs=1e-12)
    for basis in (list(zip(*u_rows)), vt_rows):
        for i, left in enumerate(basis):
            for j, right in enumerate(basis):
                assert sum(a * b for a, b in zip(left, right)) == pytest.approx(float(i == j), abs=1e-12)
