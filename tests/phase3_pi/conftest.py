"""Shared pytest configuration for the optional Raspberry Pi topology tests."""

from __future__ import annotations

import pytest

from ..settings import TestSettings


@pytest.fixture(autouse=True)
def require_pi_topology() -> None:
    """Skip the Phase 3 suite unless the Raspberry Pi topology is enabled."""
    settings = TestSettings()
    if not settings.pi.enabled:
        pytest.skip(
            "Phase 3 Raspberry Pi validation is disabled in the active topology. "
            "Enable PI_ENABLED in tests/.env to run the legacy Pi infrastructure suite."
        )
