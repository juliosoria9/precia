import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "chromium: abre un Chromium real (Playwright) contra un servidor en 127.0.0.1"
    )
