"""Fixtures communes : application sur un DATA_DIR temporaire, client authentifié."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app

TOKEN = "test-token-0123456789"


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        api_token=TOKEN,
        data_dir=tmp_path / "data",
        follow_up_days=14,
        backup_keep_days=14,
        nextcloud_url=None,
        nextcloud_root="/Candidatures",
        frontend_dir=tmp_path / "no-frontend",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


@pytest.fixture
def anon(settings):
    with TestClient(create_app(settings)) as c:
        yield c
