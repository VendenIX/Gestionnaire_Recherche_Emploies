"""Accès aux offres : CRUD, liste filtrée, statistiques.

Les fonctions prennent une connexion (db.connect) et des dictionnaires de valeurs Python
déjà validées (schemas). La région, les jours restants et la relance suggérée sont calculés
à la lecture, jamais stockés.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import geo
from .schemas import BOOL_FIELDS, MAX_OFFER_ID, NO_DEPARTMENT, STATUS_UNSET, STATUSES

COLUMNS = (
    "title",
    "contract_type",
    "company",
    "location",
    "department",
    "country",
    "status",
    "main_issue",
    "rejection_reason",
    "source",
    "applied_at",
    "application_sent",
    "follow_up_done",
    "deadline",
    "deadline_note",
    "description",
    "links",
)
DEFAULTS: dict[str, Any] = {
    "country": geo.FRANCE,
    "application_sent": False,
    "follow_up_done": False,
    "links": [],
}
SEARCH_COLUMNS = ("title", "company", "location", "description", "main_issue")
UPCOMING_DAYS = 7


class OfferExists(Exception):
    """Un id explicite déjà utilisé."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _to_db(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name == "links":
        return json.dumps(list(value), ensure_ascii=False)
    if name in BOOL_FIELDS:
        return 1 if value else 0
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _db_values(data: dict[str, Any]) -> dict[str, Any]:
    values = {}
    for name, value in data.items():
        if name not in COLUMNS:
            raise KeyError(f"Colonne inconnue : {name}")
        if value is None and name in DEFAULTS:
            value = DEFAULTS[name]
        values[name] = _to_db(name, value)
    return values


def _parse_dt(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


def follow_up_suggested(
    status: str | None, follow_up_done: bool, applied_at: datetime | None, follow_up_days: int, now: datetime
) -> bool:
    """Relance suggérée : "En cours", relance non faite, candidature de plus de N jours."""
    if status != "En cours" or follow_up_done or applied_at is None:
        return False
    if applied_at.tzinfo is not None and now.tzinfo is None:
        now = now.astimezone()
    elif applied_at.tzinfo is None and now.tzinfo is not None:
        now = now.astimezone().replace(tzinfo=None)
    return now - applied_at > timedelta(days=follow_up_days)


def row_to_offer(
    row: sqlite3.Row, follow_up_days: int, today: date | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Ligne SQLite -> dictionnaire OfferOut (avec region, days_left, follow_up_suggested)."""
    now = now or datetime.now()
    today = today or now.date()
    offer = dict(row)
    offer["application_sent"] = bool(offer["application_sent"])
    offer["follow_up_done"] = bool(offer["follow_up_done"])
    offer["links"] = json.loads(offer["links"] or "[]")
    applied_at = _parse_dt(offer["applied_at"])
    deadline = date.fromisoformat(offer["deadline"]) if offer["deadline"] else None
    offer["applied_at"] = applied_at
    offer["deadline"] = deadline
    offer["created_at"] = _parse_dt(offer["created_at"])
    offer["updated_at"] = _parse_dt(offer["updated_at"])
    offer["region"] = geo.region_for(offer["department"], offer["country"])
    offer["days_left"] = (deadline - today).days if deadline else None
    offer["follow_up_suggested"] = follow_up_suggested(
        offer["status"], offer["follow_up_done"], applied_at, follow_up_days, now
    )
    return offer


def _check_offer_id(offer_id: int | None) -> None:
    if offer_id is not None and not 1 <= offer_id <= MAX_OFFER_ID:
        raise ValueError(f"Numéro de dossier hors limites : {offer_id} (1 à {MAX_OFFER_ID}).")


def _insert(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    offer_id: int | None,
    created_at: str | None,
    updated_at: str | None,
) -> int:
    """INSERT sans commit (l'appelant gère la transaction)."""
    _check_offer_id(offer_id)
    values = {**_db_values(DEFAULTS), **_db_values(data)}
    stamp = now_iso()
    values["created_at"] = created_at or stamp
    values["updated_at"] = updated_at or created_at or stamp
    if offer_id is not None:
        values["id"] = offer_id
    names = list(values)
    sql = f"INSERT INTO offers ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})"
    try:
        cur = conn.execute(sql, [values[n] for n in names])
    except sqlite3.IntegrityError as exc:
        if offer_id is not None and get_row(conn, offer_id) is not None:
            raise OfferExists(offer_id) from exc
        raise
    return int(cur.lastrowid)


def create_offer(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    *,
    offer_id: int | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> int:
    """Insère une offre ; id explicite permis (OfferExists s'il est pris). Renvoie l'id.

    Un id explicite hors de 1..MAX_OFFER_ID lève ValueError (proche de 2**63, il bloquerait
    AUTOINCREMENT pour toutes les créations suivantes).
    """
    _check_offer_id(offer_id)
    with conn:
        return _insert(conn, data, offer_id, created_at, updated_at)


def import_offers(
    conn: sqlite3.Connection,
    offers: list[tuple[int | None, dict[str, Any]]],
    *,
    replace: bool = False,
) -> tuple[list[int], int]:
    """Import en bloc, en UNE transaction : tout ou rien.

    ``offers`` = [(id explicite ou None, données)]. Avec ``replace``, toutes les offres
    existantes sont d'abord supprimées (dans la même transaction). Les offres à id explicite
    sont insérées avant celles sans id (qui ne peuvent donc pas leur prendre leur numéro).
    Vérifié avant toute écriture : ids hors limites (ValueError), en double dans ``offers``
    ou déjà en base sans ``replace`` (OfferExists, premier id fautif). Toute erreur annule
    tout : la base reste inchangée. Renvoie (ids dans l'ordre de ``offers``, nb supprimées).
    """
    seen: set[int] = set()
    for offer_id, _ in offers:
        _check_offer_id(offer_id)
        if offer_id is not None:
            if offer_id in seen:
                raise OfferExists(offer_id)
            seen.add(offer_id)
    if conn.in_transaction:
        raise RuntimeError("import_offers : une transaction est déjà ouverte sur cette connexion.")
    order = [i for i, (oid, _) in enumerate(offers) if oid is not None]
    order += [i for i, (oid, _) in enumerate(offers) if oid is None]
    ids: list[int] = [0] * len(offers)
    with conn:  # commit à la fin, rollback sur toute exception
        conn.execute("BEGIN IMMEDIATE")
        if not replace:
            for offer_id in sorted(seen):
                if get_row(conn, offer_id) is not None:
                    raise OfferExists(offer_id)
        replaced = conn.execute("DELETE FROM offers").rowcount if replace else 0
        for i in order:
            offer_id, data = offers[i]
            ids[i] = _insert(conn, data, offer_id, None, None)
    return ids, replaced


def get_row(conn: sqlite3.Connection, offer_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()


def update_offer(conn: sqlite3.Connection, offer_id: int, changes: dict[str, Any]) -> bool:
    """Modifie seulement les champs fournis (None efface). False si l'offre n'existe pas."""
    values = _db_values(changes)
    values["updated_at"] = now_iso()
    assignments = ", ".join(f"{name} = ?" for name in values)
    with conn:
        cur = conn.execute(f"UPDATE offers SET {assignments} WHERE id = ?", [*values.values(), offer_id])
    return cur.rowcount > 0


def replace_offer(conn: sqlite3.Connection, offer_id: int, data: dict[str, Any]) -> bool:
    """Remplace tous les champs modifiables (les absents reprennent leur valeur par défaut)."""
    full = {name: None for name in COLUMNS}
    full.update(data)
    return update_offer(conn, offer_id, full)


def delete_offer(conn: sqlite3.Connection, offer_id: int) -> bool:
    with conn:
        cur = conn.execute("DELETE FROM offers WHERE id = ?", (offer_id,))
    return cur.rowcount > 0


def _region_clause(region: str) -> tuple[str, list[Any]]:
    if region == geo.HORS_FRANCE:
        return "country <> ?", [geo.FRANCE]
    if region == NO_DEPARTMENT:
        return "country = ? AND (department IS NULL OR department = '')", [geo.FRANCE]
    codes = [code for code, name in geo.DEPARTMENT_REGION.items() if name == region]
    placeholders = ", ".join("?" for _ in codes)
    return f"country = ? AND department IN ({placeholders})", [geo.FRANCE, *codes]


def _escape_like(text: str) -> str:
    """Échappe les jokers de LIKE (%, _) et le caractère d'échappement lui-même."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_rows(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    department: str | None = None,
    region: str | None = None,
    q: str | None = None,
    descending: bool = False,
) -> list[sqlite3.Row]:
    """Offres filtrées, triées par id. status = STATUS_UNSET sélectionne les statuts null ;
    region accepte les 13 régions, "Hors France" et "Sans département"."""
    where: list[str] = []
    params: list[Any] = []
    if status is not None:
        if status == STATUS_UNSET:
            where.append("status IS NULL")
        else:
            where.append("status = ?")
            params.append(status)
    if department is not None:
        where.append("department = ?")
        params.append(department)
    if region is not None:
        clause, extra = _region_clause(region)
        where.append(clause)
        params.extend(extra)
    if q is not None and q.strip():
        needle = f"%{_escape_like(q.strip().casefold())}%"
        where.append(
            "(" + " OR ".join(f"py_casefold({c}) LIKE ? ESCAPE '\\'" for c in SEARCH_COLUMNS) + ")"
        )
        params.extend([needle] * len(SEARCH_COLUMNS))
    sql = "SELECT * FROM offers"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id " + ("DESC" if descending else "ASC")
    return conn.execute(sql, params).fetchall()


def stats(conn: sqlite3.Connection, follow_up_days: int, today: date | None = None) -> dict[str, Any]:
    """Comptes par statut (null = "À qualifier") et par région (+ "Hors France",
    "Sans département"), et offres dont la deadline tombe entre aujourd'hui et J+7."""
    today = today or date.today()
    by_status = {s: 0 for s in STATUSES}
    by_status[STATUS_UNSET] = 0
    by_region = {r: 0 for r in geo.REGIONS}
    by_region[NO_DEPARTMENT] = 0
    rows = conn.execute("SELECT status, department, country FROM offers").fetchall()
    for row in rows:
        by_status[row["status"] or STATUS_UNSET] += 1
        region = geo.region_for(row["department"], row["country"])
        by_region[region or NO_DEPARTMENT] += 1
    upcoming = conn.execute(
        "SELECT * FROM offers WHERE deadline IS NOT NULL AND deadline >= ? AND deadline <= ? "
        "ORDER BY deadline, id",
        (today.isoformat(), (today + timedelta(days=UPCOMING_DAYS)).isoformat()),
    ).fetchall()
    return {
        "total": len(rows),
        "by_status": by_status,
        "by_region": by_region,
        "deadlines_soon": [row_to_offer(r, follow_up_days, today=today) for r in upcoming],
    }
