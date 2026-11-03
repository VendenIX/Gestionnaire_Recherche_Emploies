"""Tests de la configuration par variables d'environnement."""

from pathlib import Path

from backend.app.config import get_settings

VARS = ("API_TOKEN", "DATA_DIR", "FOLLOW_UP_DAYS", "BACKUP_KEEP_DAYS", "NEXTCLOUD_URL", "NEXTCLOUD_ROOT")


def test_defaults(monkeypatch):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)
    s = get_settings()
    assert s.api_token is None
    assert s.data_dir == Path("/data")
    assert s.follow_up_days == 14
    assert s.backup_keep_days == 14
    assert s.nextcloud_url is None
    assert s.nextcloud_root == "/Candidatures"


def test_overrides(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "var")
    monkeypatch.setenv("FOLLOW_UP_DAYS", "7")
    monkeypatch.setenv("API_TOKEN", "x")
    s = get_settings()
    assert s.data_dir == Path("var")
    assert s.follow_up_days == 7
    assert s.api_token == "x"
