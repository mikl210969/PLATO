"""
Фикстуры для интеграционных Chaos-тестов.
"""
import pytest
import asyncio
import tempfile
from tests.evil_mocks import EvilRestMock, EvilWsMock, TimeController


@pytest.fixture
def rest_mock():
    """Создать REST-мок."""
    return EvilRestMock()


@pytest.fixture
def ws_mock():
    """Создать WebSocket-мок."""
    return EvilWsMock()


@pytest.fixture
def time_controller():
    """Создать контроллер времени."""
    return TimeController(speed_multiplier=100.0)


@pytest.fixture
def temp_dir():
    """Создать временную директорию для тестов."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir