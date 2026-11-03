"""Schéma et migrations SQLite."""

from __future__ import annotations

import sqlite3

import pytest

from backend.app import db, repository


def test_migrations_idempotent_and_wal(tmp_path):
    path = db.init_db(tmp_path)
    assert path == tmp_path / "jobs.db"
    db.init_db(tmp_path)  # deuxième démarrage : rien à refaire
    conn = db.connect(path)
    try:
        latest = db.list_migrations()[-1][0]
        assert db.schema_version(conn) == latest
        assert db.migrate(conn) == latest
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_region_never_stored_and_department_is_text(tmp_path):
    conn = db.connect(db.init_db(tmp_path))
    try:
        columns = {r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(offers)")}
    finally:
        conn.close()
    assert "region" not in columns
    assert columns["department"] == "TEXT"
    assert columns["country"] == "TEXT"


def test_status_check_constraint(tmp_path):
    conn = db.connect(db.init_db(tmp_path))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            repository.create_offer(conn, {"title": "x", "status": "Inconnu"})
        with pytest.raises(sqlite3.IntegrityError):
            repository.create_offer(conn, {"title": "x", "country": "fr"})
        offer_id = repository.create_offer(conn, {"title": "x", "department": "01"})
        stored = conn.execute("SELECT department, typeof(department) FROM offers WHERE id=?", (offer_id,))
        assert tuple(stored.fetchone()) == ("01", "text")
    finally:
        conn.close()


def test_explicit_id_conflict(tmp_path):
    conn = db.connect(db.init_db(tmp_path))
    try:
        assert repository.create_offer(conn, {"title": "a"}, offer_id=42) == 42
        with pytest.raises(repository.OfferExists):
            repository.create_offer(conn, {"title": "b"}, offer_id=42)
        assert repository.create_offer(conn, {"title": "c"}) == 43
    finally:
        conn.close()


def test_busy_timeout_set_once_by_connect(tmp_path):
    # Revue db-api #8 : timeout=10 de sqlite3.connect suffit (plus de PRAGMA en double).
    conn = db.connect(db.db_path(tmp_path))
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
    finally:
        conn.close()


@pytest.mark.parametrize("offer_id", [0, -1, 10**9 + 1, 2**63 - 1])
def test_repository_rejects_out_of_range_id(tmp_path, offer_id):
    path = db.init_db(tmp_path)
    conn = db.connect(path)
    try:
        with pytest.raises(ValueError, match="hors limites"):
            repository.create_offer(conn, {"title": "x"}, offer_id=offer_id)
        assert repository.create_offer(conn, {"title": "y"}) == 1
    finally:
        conn.close()
