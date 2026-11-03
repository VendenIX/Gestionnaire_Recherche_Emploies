"""Sauvegarde quotidienne et élagage."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from backend.app import db, repository
from backend.app.backup import backup_file, run_daily_backup


def _seed(data_dir):
    conn = db.connect(db.init_db(data_dir))
    try:
        repository.create_offer(conn, {"title": "Ingénieur", "department": "2A"})
    finally:
        conn.close()


def test_backup_created_once_per_day(tmp_path):
    _seed(tmp_path)
    today = date(2026, 11, 2)
    created = run_daily_backup(tmp_path, keep_days=14, today=today)
    assert created == backup_file(tmp_path, today) == tmp_path / "backups" / "jobs-2026-11-02.db"
    copy = sqlite3.connect(created)
    try:
        assert copy.execute("SELECT title, department FROM offers").fetchall() == [("Ingénieur", "2A")]
    finally:
        copy.close()
    assert run_daily_backup(tmp_path, keep_days=14, today=today) is None  # déjà faite


def test_backup_pruning_keeps_n_days(tmp_path):
    _seed(tmp_path)
    today = date(2026, 11, 2)
    backups = tmp_path / "backups"
    backups.mkdir()
    for age in range(1, 20):
        (backups / f"jobs-{(today - timedelta(days=age)).isoformat()}.db").write_bytes(b"")
    (backups / "notes.txt").write_text("à garder")
    run_daily_backup(tmp_path, keep_days=14, today=today)
    kept = sorted(p.name for p in backups.glob("jobs-*.db"))
    assert len(kept) == 14
    assert kept[0] == f"jobs-{(today - timedelta(days=13)).isoformat()}.db"
    assert kept[-1] == "jobs-2026-11-02.db"
    assert (backups / "notes.txt").exists()


def test_backup_at_startup(client, settings):
    assert backup_file(settings.data_dir, date.today()).exists()
