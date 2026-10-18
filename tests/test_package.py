"""Packaging smoke test: the package imports and declares a version."""

import factorio_maxxing


def test_package_imports():
    assert factorio_maxxing is not None


def test_version_is_declared():
    assert factorio_maxxing.__version__ == "0.1.0"


def test_fle_is_pinned_to_one_commit():
    """Every run of the eval grid must use the same FLE (D61).

    0.4.8 exists only on GitHub, so the pin is a full commit SHA; a version range or a
    branch name would let two runs drift onto different FLE code.
    """
    import re
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extras = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]
    (fle,) = [d for d in extras["fle"] if d.startswith("factorio-learning-environment")]
    assert re.search(r"@ git\+https://\S+@[0-9a-f]{40}$", fle), fle
    assert not any(d.startswith("a2a-sdk") for d in extras["fle"]), (
        "FLE 0.4.8 pins a2a-sdk==1.1 itself; a second pin can only conflict"
    )
