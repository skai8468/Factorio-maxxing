"""Packaging smoke test: the package imports and declares a version."""

import factorio_maxxing


def test_package_imports():
    assert factorio_maxxing is not None


def test_version_is_declared():
    assert factorio_maxxing.__version__ == "0.1.0"
