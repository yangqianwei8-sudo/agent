"""Package import smoke tests (Phase 1 layer placeholders exist)."""

import importlib


def test_backend_version() -> None:
    import backend

    assert backend.__version__ == "0.2.0"


def test_layer_packages_importable() -> None:
    packages = [
        "backend.api",
        "backend.agent",
        "backend.application",
        "backend.workflow",
        "backend.skills",
        "backend.tools",
        "backend.domain",
        "backend.repositories",
        "backend.models",
        "backend.infrastructure",
        "backend.schemas",
    ]
    for name in packages:
        module = importlib.import_module(name)
        assert module is not None
