"""API REST : CRUD, filtres, stats, meta, authentification, messages d'erreur en français."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.conftest import TOKEN, make_settings


def _create(client, **fields):
    resp = client.post("/api/offers", json=fields)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Authentification ---------------------------------------------------------------------


def test_health_without_auth(anon):
    resp = anon.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/api/offers", "/api/stats", "/api/meta", "/api/offers/1"])
def test_401_without_token(anon, path):
    resp = anon.get(path)
    assert resp.status_code == 401
    assert "Authentification requise" in resp.json()["detail"]
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/offers", {"title": "x"}),
        ("patch", "/api/offers/1", {"title": "y"}),
        ("put", "/api/offers/1", {"title": "y"}),
        ("delete", "/api/offers/1", None),
    ],
)
def test_401_on_writes_without_token(client, anon, method, path, body):
    # Revue db-api #9 : les écritures sans jeton sont refusées et ne modifient rien.
    _create(client, title="original")
    kwargs = {"json": body} if body is not None else {}
    resp = anon.request(method.upper(), path, **kwargs)
    assert resp.status_code == 401
    assert "Authentification requise" in resp.json()["detail"]
    resp = anon.request(method.upper(), path, headers={"Authorization": "Bearer mauvais"}, **kwargs)
    assert resp.status_code == 401
    offers = client.get("/api/offers").json()
    assert [(o["id"], o["title"]) for o in offers] == [(1, "original")]


@pytest.mark.parametrize("header", ["Bearer mauvais", f"Basic {TOKEN}", TOKEN, "Bearer "])
def test_401_with_wrong_token(anon, header):
    resp = anon.get("/api/offers", headers={"Authorization": header})
    assert resp.status_code == 401


def test_refuses_to_start_without_token(tmp_path):
    for token in (None, "", "   "):
        with pytest.raises(RuntimeError, match="API_TOKEN"):
            create_app(make_settings(tmp_path, api_token=token))


def test_openapi_docs_available(anon):
    assert anon.get("/docs").status_code == 200
    assert "/api/offers" in anon.get("/openapi.json").json()["paths"]


# --- CRUD -----------------------------------------------------------------------------------


def test_crud_roundtrip(client):
    offer = _create(
        client,
        title="Data engineer",
        company="ACME",
        contract_type="CDI",
        location="Lyon",
        department="69",
        status="À postuler",
        applied_at="2026-10-01T09:30:00",
        application_sent=True,
        deadline="2026-12-01",
        deadline_note="?/fin novembre",
        links=["https://example.org/offre/1", "http://exemple.fr"],
    )
    assert offer["id"] == 1
    assert offer["region"] == "Auvergne-Rhône-Alpes"
    assert offer["country"] == "FR"
    assert offer["links"] == ["https://example.org/offre/1", "http://exemple.fr"]
    assert offer["application_sent"] is True and offer["follow_up_done"] is False
    assert offer["deadline_note"] == "?/fin novembre"
    assert offer["created_at"] and offer["updated_at"]

    # POST ajoute seulement « folder » (phase 3) ; le reste est identique à GET.
    folder = offer.pop("folder")
    assert folder == {"status": "disabled", "name": None, "url": None, "detail": None}
    got = client.get("/api/offers/1").json()
    assert got == offer

    assert client.delete("/api/offers/1").status_code == 204
    resp = client.get("/api/offers/1")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Offre introuvable : 1"
    assert client.delete("/api/offers/1").status_code == 404
    assert client.patch("/api/offers/1", json={"title": "x"}).json()["detail"] == "Offre introuvable : 1"


def test_partial_update_keeps_other_fields_and_null_clears(client):
    offer = _create(
        client, title="Dev", company="ACME", department="2A", status="En cours", links=["https://a.fr"]
    )
    resp = client.patch(f"/api/offers/{offer['id']}", json={"company": "Globex"})
    assert resp.status_code == 200
    after = resp.json()
    assert after["company"] == "Globex"
    for field in ("title", "department", "status", "links", "region", "created_at"):
        assert after[field] == offer[field]

    cleared = client.patch(
        f"/api/offers/{offer['id']}",
        json={"company": None, "department": None, "status": None, "links": None},
    ).json()
    assert cleared["company"] is None
    assert cleared["department"] is None and cleared["region"] is None
    assert cleared["status"] is None
    assert cleared["links"] == []
    assert cleared["title"] == "Dev"


def test_patch_rejects_null_boolean(client):
    offer = _create(client, title="x")
    resp = client.patch(f"/api/offers/{offer['id']}", json={"follow_up_done": None})
    assert resp.status_code == 422
    assert "null" in resp.json()["detail"]


def test_put_replaces_everything(client):
    offer = _create(client, title="Dev", company="ACME", department="75", application_sent=True)
    resp = client.put(f"/api/offers/{offer['id']}", json={"title": "Dev senior"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Dev senior"
    assert body["company"] is None and body["department"] is None
    assert body["application_sent"] is False and body["country"] == "FR"
    assert client.put("/api/offers/999", json={"title": "x"}).status_code == 404


def test_id_never_reused_after_delete(client):
    first = _create(client, title="a")
    second = _create(client, title="b")
    assert client.delete(f"/api/offers/{second['id']}").status_code == 204
    third = _create(client, title="c")
    assert third["id"] == second["id"] + 1 > first["id"]


def test_explicit_id_and_conflict(client):
    assert _create(client, id=120, title="dossier 120")["id"] == 120
    resp = client.post("/api/offers", json={"id": 120, "title": "doublon"})
    assert resp.status_code == 409
    assert "120" in resp.json()["detail"]
    assert _create(client, title="suivant")["id"] == 121


@pytest.mark.parametrize("code,region", [("2A", "Corse"), ("2B", "Corse"), ("01", "Auvergne-Rhône-Alpes")])
def test_department_string_roundtrip(client, code, region):
    offer = _create(client, title="x", department=code)
    got = client.get(f"/api/offers/{offer['id']}").json()
    assert got["department"] == code
    assert got["region"] == region
    listed = client.get("/api/offers", params={"department": code}).json()
    assert [o["department"] for o in listed] == [code]


def test_foreign_country_is_hors_france(client):
    offer = _create(client, title="Genève", country="ch")
    assert offer["country"] == "CH"
    assert offer["region"] == "Hors France"


# --- Validation (422 en français) -----------------------------------------------------------


def _error_422(client, payload):
    resp = client.post("/api/offers", json=payload)
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"].startswith("Données invalides")
    return body


def test_422_bad_status(client):
    body = _error_422(client, {"title": "x", "status": "Envoyé"})
    assert body["errors"][0]["field"] == "status"
    assert "Statut inconnu" in body["errors"][0]["message"]


def test_422_department_20(client):
    body = _error_422(client, {"title": "x", "department": "20"})
    assert body["errors"][0]["field"] == "department"
    assert "Code de département inconnu" in body["errors"][0]["message"]


def test_422_department_json_number(client):
    body = _error_422(client, {"title": "x", "department": 75})
    assert body["errors"][0]["field"] == "department"
    assert "chaîne de caractères" in body["errors"][0]["message"]
    # même règle en PATCH
    offer = _create(client, title="x")
    resp = client.patch(f"/api/offers/{offer['id']}", json={"department": 1})
    assert resp.status_code == 422
    assert "chaîne de caractères" in resp.json()["detail"]


def test_422_department_not_normalised(client):
    body = _error_422(client, {"title": "x", "department": "1"})
    assert "Code de département inconnu" in body["errors"][0]["message"]


@pytest.mark.parametrize(
    "payload,field,fragment",
    [
        ({"surprise": 1}, "surprise", "champ inconnu"),
        ({"application_sent": "oui"}, "application_sent", "booléen"),
        ({"title": 12}, "title", "chaîne de caractères"),
        ({"deadline": "31/12/2026"}, "deadline", "AAAA-MM-JJ"),
        ({"deadline": "2026-12-31T10:00:00"}, "deadline", "AAAA-MM-JJ"),
        ({"applied_at": "hier"}, "applied_at", "date et heure invalides"),
        ({"country": "FRA"}, "country", "2 lettres"),
        ({"links": ["ftp://x.fr"]}, "links", "Lien invalide"),
        ({"links": "https://x.fr"}, "links", "liste"),
        ({"id": 0}, "id", "supérieur ou égal à 1"),
        # Revue db-api #2 : pas de mode lax (horodatages Unix, formats compacts).
        ({"deadline": 86400}, "deadline", "AAAA-MM-JJ"),
        ({"deadline": "1700006400"}, "deadline", "AAAA-MM-JJ"),
        ({"deadline": "20250131"}, "deadline", "AAAA-MM-JJ"),
        ({"deadline": True}, "deadline", "AAAA-MM-JJ"),
        ({"applied_at": 0}, "applied_at", "date et heure invalides"),
        ({"applied_at": 1700006400.5}, "applied_at", "date et heure invalides"),
        ({"applied_at": "1700006400"}, "applied_at", "date et heure invalides"),
        # Revue db-api #3 : id borné.
        ({"id": 2**63}, "id", "inférieur ou égal à 1000000000"),
        ({"id": 2**63 - 1}, "id", "inférieur ou égal à 1000000000"),
        ({"id": 10**9 + 1}, "id", "inférieur ou égal à 1000000000"),
    ],
)
def test_422_french_messages(client, payload, field, fragment):
    body = _error_422(client, payload)
    assert body["errors"][0]["field"] == field
    assert fragment in body["errors"][0]["message"]


def test_dates_accept_iso_text(client):
    offer = _create(client, applied_at="2025-01-02", deadline="2025-01-31")
    assert offer["applied_at"] == "2025-01-02T00:00:00"
    assert offer["deadline"] == "2025-01-31"
    offer = _create(client, applied_at="2025-01-02 10:30:00")
    assert offer["applied_at"] == "2025-01-02T10:30:00"


def test_huge_ids_never_break_creation(client):
    # Revue db-api #3 : un id proche de 2**63 rendait toutes les créations suivantes en 500.
    assert client.post("/api/offers", json={"id": 2**63 - 1}).status_code == 422
    assert _create(client, id=10**9)["id"] == 10**9
    assert _create(client, title="suivante")["id"] == 10**9 + 1
    for method in ("GET", "PATCH", "PUT", "DELETE"):
        kwargs = {"json": {}} if method in ("PATCH", "PUT") else {}
        resp = client.request(method, "/api/offers/99999999999999999999", **kwargs)
        assert resp.status_code == 422, (method, resp.text)
        assert resp.json()["errors"][0]["field"] == "offer_id"
    assert client.get(f"/api/offers/{2**63}").status_code == 422


def test_422_malformed_json_field_is_body(client):
    # Revue db-api #4 : l'erreur JSON indiquait la position du caractère comme nom de champ.
    for raw in (b"{bad", b'{"title": "x",}', b"[1, 2"):
        resp = client.post("/api/offers", content=raw, headers={"Content-Type": "application/json"})
        assert resp.status_code == 422
        body = resp.json()
        assert body["errors"][0]["field"] == "corps"
        assert body["errors"][0]["message"] == "JSON invalide."
        assert body["detail"] == "Données invalides : corps : JSON invalide."


def test_422_invalid_json_and_query(client):
    resp = client.post("/api/offers", content=b"{pas du json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 422
    assert "JSON invalide" in resp.json()["detail"]
    assert client.get("/api/offers/abc").status_code == 422
    for params, field in (
        ({"status": "Nope"}, "status"),
        ({"department": "20"}, "department"),
        ({"region": "Atlantide"}, "region"),
        ({"order": "up"}, "order"),
    ):
        resp = client.get("/api/offers", params=params)
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["field"] == field


# --- Liste et filtres -----------------------------------------------------------------------


@pytest.fixture
def seeded(client):
    _create(
        client,
        title="Ingénieur données",
        company="Écolab",
        location="Lyon",
        department="69",
        status="En cours",
    )
    _create(client, title="Développeur", company="ACME", location="Ajaccio", department="2A", status="Rejeté")
    _create(
        client,
        title="Chef de projet",
        company="Globex",
        country="CH",
        status="En cours",
        description="Poste à ÉVIAN côté suisse",
    )
    _create(client, title="Stage", company="Initech", main_issue="Salaire trop bas")
    return client


def test_list_sorted_by_id(seeded):
    ids = [o["id"] for o in seeded.get("/api/offers").json()]
    assert ids == [1, 2, 3, 4]
    assert [o["id"] for o in seeded.get("/api/offers", params={"order": "desc"}).json()] == [4, 3, 2, 1]


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"status": "En cours"}, [1, 3]),
        ({"status": "À qualifier"}, [4]),
        ({"department": "2A"}, [2]),
        ({"region": "Corse"}, [2]),
        ({"region": "Auvergne-Rhône-Alpes"}, [1]),
        ({"region": "Hors France"}, [3]),
        ({"region": "Sans département"}, [4]),
        ({"q": "écolab"}, [1]),
        ({"q": "évian"}, [3]),
        ({"q": "AJACCIO"}, [2]),
        ({"q": "salaire"}, [4]),
        ({"q": "développeur"}, [2]),
        ({"status": "En cours", "region": "Hors France"}, [3]),
        ({"q": "introuvable"}, []),
    ],
)
def test_filters(seeded, params, expected):
    assert [o["id"] for o in seeded.get("/api/offers", params=params).json()] == expected


# --- Champs calculés ------------------------------------------------------------------------


@pytest.mark.parametrize("needle", ["%", "_", "\\", "100%", "a_b"])
def test_search_wildcards_are_literal(client, needle):
    # Revue db-api #5 : q=% ne doit pas tout renvoyer ; % et _ sont des caractères ordinaires.
    _create(client, title="Remise 100% télétravail")
    _create(client, title="Poste a_b")
    _create(client, title="Chemin C:\\data")
    _create(client, title="Sans joker")
    titles = [o["title"] for o in client.get("/api/offers", params={"q": needle}).json()]
    assert "Sans joker" not in titles
    expected = {
        "%": ["Remise 100% télétravail"],
        "_": ["Poste a_b"],
        "\\": ["Chemin C:\\data"],
        "100%": ["Remise 100% télétravail"],
        "a_b": ["Poste a_b"],
    }[needle]
    assert titles == expected


@pytest.mark.parametrize("value", ["", "   "])
def test_422_empty_department_filter(seeded, value):
    # Revue db-api #6 : ?department= vide était ignoré en silence (toutes les offres renvoyées).
    resp = seeded.get("/api/offers", params={"department": value})
    assert resp.status_code == 422
    body = resp.json()
    assert body["errors"][0]["field"] == "department"
    assert "valeur vide" in body["errors"][0]["message"]
    assert body["detail"].startswith("Données invalides : department : ")


def test_follow_up_and_days_left(client):
    now = datetime.now().replace(microsecond=0)
    old = _create(
        client, title="vieille", status="En cours", applied_at=(now - timedelta(days=20)).isoformat()
    )
    recent = _create(
        client, title="récente", status="En cours", applied_at=(now - timedelta(days=5)).isoformat()
    )
    done = _create(
        client,
        title="relancée",
        status="En cours",
        follow_up_done=True,
        applied_at=(now - timedelta(days=20)).isoformat(),
    )
    other = _create(
        client, title="entretien", status="Entretien", applied_at=(now - timedelta(days=20)).isoformat()
    )
    assert old["follow_up_suggested"] is True
    assert recent["follow_up_suggested"] is False
    assert done["follow_up_suggested"] is False
    assert other["follow_up_suggested"] is False
    with_deadline = _create(client, title="d", deadline=(date.today() + timedelta(days=3)).isoformat())
    assert with_deadline["days_left"] == 3
    assert old["days_left"] is None


def test_follow_up_days_setting(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, follow_up_days=3))) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        applied = (datetime.now() - timedelta(days=5)).isoformat()
        assert _create(c, title="x", status="En cours", applied_at=applied)["follow_up_suggested"] is True
        assert c.get("/api/meta").json()["follow_up_days"] == 3


# --- Stats et meta --------------------------------------------------------------------------


def test_stats(seeded):
    today = date.today()
    for delta, title in ((0, "aujourd'hui"), (7, "J+7"), (8, "J+8"), (-1, "hier")):
        _create(seeded, title=title, department="75", deadline=(today + timedelta(days=delta)).isoformat())
    stats = seeded.get("/api/stats").json()
    assert stats["total"] == 8
    assert stats["by_status"]["En cours"] == 2
    assert stats["by_status"]["Rejeté"] == 1
    assert stats["by_status"]["À qualifier"] == 5
    assert stats["by_status"]["Offre reçue"] == 0
    assert list(stats["by_status"])[-1] == "À qualifier"
    assert stats["by_region"]["Corse"] == 1
    assert stats["by_region"]["Auvergne-Rhône-Alpes"] == 1
    assert stats["by_region"]["Hors France"] == 1
    assert stats["by_region"]["Sans département"] == 1
    assert stats["by_region"]["Île-de-France"] == 4
    assert sum(stats["by_region"].values()) == 8
    assert [o["title"] for o in stats["deadlines_soon"]] == ["aujourd'hui", "J+7"]
    assert stats["deadlines_soon"][1]["days_left"] == 7


def test_meta(client):
    meta = client.get("/api/meta").json()
    assert meta["statuses"] == ["À postuler", "En cours", "Entretien", "Offre reçue", "Rejeté", "Abandon"]
    assert len(meta["regions"]) == 14 and meta["regions"][-1] == "Hors France"
    assert meta["out_of_france_label"] == "Hors France" and meta["out_of_france_label"] in meta["regions"]
    assert meta["no_department_label"] == "Sans département"
    codes = [d["code"] for d in meta["departments"]]
    assert codes.index("19") + 1 == codes.index("2A")
    assert codes.index("2B") + 1 == codes.index("21")
    assert codes[0] == "01" and "20" not in codes
    corse = next(d for d in meta["departments"] if d["code"] == "2A")
    assert corse == {"code": "2A", "name": "Corse-du-Sud", "region": "Corse"}
    assert meta["follow_up_days"] == 14


# --- Front statique -------------------------------------------------------------------------


def test_static_frontend_mounted_when_present(tmp_path):
    front = tmp_path / "front"
    front.mkdir()
    (front / "index.html").write_text("<h1>Suivi</h1>", encoding="utf-8")
    with TestClient(create_app(make_settings(tmp_path, frontend_dir=front))) as c:
        assert "Suivi" in c.get("/").text
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/offers").status_code == 401
