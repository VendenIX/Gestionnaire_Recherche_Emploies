"""Routes /api/offers/{id}/folder et création du dossier avec l'offre, contre le faux WebDAV."""

from __future__ import annotations

import logging
import socket
import threading
from http.server import ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from backend.app import nextcloud
from backend.app.main import create_app
from backend.tests.conftest import TOKEN, make_settings
from backend.tests.test_nextcloud import PASSWORD, ROOT, USER, FakeDav, make_handler

EXISTING = "01 - Chargé de mission Acme"


@pytest.fixture
def dav():
    fake = FakeDav()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield fake
    server.shutdown()
    server.server_close()


def _closed_port_url() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def nc_settings(tmp_path, url, **overrides):
    values = dict(
        nextcloud_webdav=True,
        nextcloud_url=url,
        nextcloud_user=USER,
        nextcloud_app_password=PASSWORD,
        nextcloud_root=ROOT,
    )
    values.update(overrides)
    return make_settings(tmp_path, **values)


def _client(settings):
    c = TestClient(create_app(settings))
    c.headers["Authorization"] = f"Bearer {TOKEN}"
    return c


@pytest.fixture
def api(tmp_path, dav):
    with _client(nc_settings(tmp_path, dav.url)) as c:
        yield c


def _create(c, **fields):
    resp = c.post("/api/offers", json=fields)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def no_network(monkeypatch):
    """Tout appel WebDAV fait échouer le test."""
    calls = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("appel réseau Nextcloud inattendu")

    monkeypatch.setattr(nextcloud.urllib.request, "urlopen", boom)
    return calls


# --- WebDAV désactivé ------------------------------------------------------------------------


def test_disabled_makes_no_network_call(tmp_path, dav, no_network):
    # URL et identifiants présents mais NEXTCLOUD_WEBDAV faux : aucun appel.
    with _client(nc_settings(tmp_path, dav.url, nextcloud_webdav=False)) as c:
        meta = c.get("/api/meta").json()
        assert meta["nextcloud_enabled"] is False and meta["nextcloud_error"] is None
        offer = _create(c, title="Dev", company="ACME")
        assert offer["folder"] == {"status": "disabled", "name": None, "url": None, "detail": None}
        for method in (c.get, c.post):
            resp = method(f"/api/offers/{offer['id']}/folder")
            assert resp.status_code == 503
            assert resp.json()["code"] == "nextcloud_disabled"
            assert "NEXTCLOUD_WEBDAV" in resp.json()["detail"]
    assert dav.methods == [] and no_network == []


def test_default_settings_disabled(client, no_network):
    assert client.get("/api/meta").json()["nextcloud_enabled"] is False
    assert _create(client, title="X")["folder"]["status"] == "disabled"
    assert no_network == []


# --- WebDAV actif ----------------------------------------------------------------------------


def test_meta_enabled(api):
    meta = api.get("/api/meta").json()
    assert meta["nextcloud_enabled"] is True and meta["nextcloud_error"] is None


def test_get_existing_folder_with_files(api, dav):
    dav.add_dir(f"{ROOT}/{EXISTING}")
    dav.add_dir(f"{ROOT}/{EXISTING}/CV")
    dav.add_dir(f"{ROOT}/{EXISTING}/Lettre")
    dav.files[f"{ROOT}/{EXISTING}/Lettre motivation.pdf"] = 2048
    dav.add_dir(f"{ROOT}/10 - Autre")
    _create(api, id=1, title="Chargé de mission")  # dossier déjà là : réutilisé
    dav.methods.clear()

    resp = api.get("/api/offers/1/folder")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == EXISTING
    assert body["url"].startswith(f"{dav.url}/apps/files/files/12345?dir=/Candidatures/01%20-%20Charg")
    assert body["created"] is False
    assert [(f["name"], f["is_dir"], f["size"]) for f in body["files"]] == [
        ("CV", True, None),
        ("Lettre", True, None),
        ("Lettre motivation.pdf", False, 2048),
    ]
    assert body["files"][0]["url"].endswith("Acme/CV")
    assert body["files"][2]["modified"].startswith("2026-11-03T10:00:00")
    assert set(dav.methods) == {"PROPFIND"}


def test_create_offer_creates_folder(api, dav):
    offer = _create(api, title="Ingénieur systèmes", company="Thales")
    folder = offer["folder"]
    assert folder["status"] == "created" and folder["detail"] is None
    assert folder["name"] == f"{offer['id']:02d} - Ingénieur systèmes Thales"
    assert folder["url"].startswith(f"{dav.url}/apps/files/files?dir=/Candidatures/")
    path = f"{ROOT}/{folder['name']}"
    assert {path, f"{path}/CV", f"{path}/Lettre"} <= dav.dirs
    assert set(dav.methods) <= set(nextcloud.ALLOWED_METHODS)
    # Le reste de la réponse est l'offre, identique à GET.
    offer.pop("folder")
    assert api.get(f"/api/offers/{offer['id']}").json() == offer


def test_create_offer_reuses_existing_folder(api, dav):
    dav.add_dir(f"{ROOT}/007 - Ancien nom")
    dav.add_dir(f"{ROOT}/007 - Ancien nom/CV")
    dav.add_dir(f"{ROOT}/007 - Ancien nom/Lettre")
    offer = _create(api, id=7, title="Nouveau", company="X")
    assert offer["folder"]["status"] == "existing"
    assert offer["folder"]["name"] == "007 - Ancien nom"
    assert "MKCOL" not in dav.methods
    assert not any("Nouveau" in d for d in dav.dirs)


def test_existing_folder_gets_missing_cv_lettre_untouched(api, dav, caplog):
    folder = f"{ROOT}/09 - Dossier du propriétaire"
    dav.add_dir(folder)
    dav.add_dir(f"{folder}/Lettre")
    dav.files[f"{folder}/Lettre/lettre.pdf"] = 10
    with caplog.at_level(logging.INFO, logger="backend.app.main"):
        offer = _create(api, id=9, title="Nouveau", company="X")
    assert offer["folder"]["status"] == "existing"  # « created » ne concerne que le dossier de l'offre
    assert offer["folder"]["name"] == "09 - Dossier du propriétaire"
    assert f"{folder}/CV" in dav.dirs
    assert dav.methods.count("MKCOL") == 1  # CV/ seulement, rien sur Lettre/
    assert dav.files == {f"{folder}/Lettre/lettre.pdf": 10}
    assert "ajout de CV" in caplog.text

    # POST /folder sur le même dossier, désormais complet : 200, plus aucun MKCOL.
    dav.methods.clear()
    resp = api.post("/api/offers/9/folder")
    assert resp.status_code == 200 and resp.json()["created"] is False
    assert "MKCOL" not in dav.methods


def test_existing_folder_read_only_subfolders_not_an_error(api, dav):
    dav.add_dir(f"{ROOT}/03 - Owner")
    dav.force_mkcol = 403  # CV/ et Lettre/ manquants, MKCOL refusé (partage en lecture seule)
    offer = _create(api, id=3, title="Nouveau", company="X")
    assert offer["folder"]["status"] == "existing"
    assert offer["folder"]["name"] == "03 - Owner" and offer["folder"]["url"]
    resp = api.post("/api/offers/3/folder")
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] is False and resp.json()["name"] == "03 - Owner"
    assert api.get("/api/offers/3/folder").status_code == 200


def test_post_folder_adds_missing_subfolders(api, dav):
    _create(api, id=4, title="Sans dossier")  # dossier créé automatiquement
    folder = f"{ROOT}/04 - Sans dossier"
    dav.dirs.discard(f"{folder}/CV")
    dav.dirs.discard(f"{folder}/Lettre")
    resp = api.post("/api/offers/4/folder")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is False
    assert [f["name"] for f in body["files"]] == ["CV", "Lettre"]


def _record_timeouts(monkeypatch) -> list[float]:
    seen: list[float] = []
    real = nextcloud.urllib.request.urlopen

    def spy(req, *args, timeout=None, **kwargs):
        seen.append(timeout)
        return real(req, *args, timeout=timeout, **kwargs)

    monkeypatch.setattr(nextcloud.urllib.request, "urlopen", spy)
    return seen


def test_offer_creation_uses_5s_timeout_folder_routes_10s(api, dav, monkeypatch):
    seen = _record_timeouts(monkeypatch)
    offer = _create(api, title="Délai", company="X")
    assert offer["folder"]["status"] == "created"
    assert len(seen) >= 4 and set(seen) == {5.0}  # PROPFIND racine + MKCOL x3, tous à 5 s
    assert nextcloud.OFFER_CREATE_TIMEOUT == 5.0

    seen.clear()
    assert api.get(f"/api/offers/{offer['id']}/folder").status_code == 200
    assert api.post(f"/api/offers/{offer['id']}/folder").status_code == 200
    assert seen and set(seen) == {nextcloud.DEFAULT_TIMEOUT} == {10.0}


def test_folder_not_found_then_post_creates(api, dav):
    dav.force_mkcol = 503  # la création automatique échoue : l'offre existe sans dossier
    offer = _create(api, title="Analyste", company="Globex")
    assert offer["folder"]["status"] == "error"
    dav.force_mkcol = None
    oid = offer["id"]

    resp = api.get(f"/api/offers/{oid}/folder")
    assert resp.status_code == 404
    assert resp.json()["code"] == "folder_not_found"
    assert f"n° {oid}" in resp.json()["detail"] and "/Candidatures" in resp.json()["detail"]

    resp = api.post(f"/api/offers/{oid}/folder")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] is True and body["name"] == f"{oid:02d} - Analyste Globex"
    assert [f["name"] for f in body["files"]] == ["CV", "Lettre"]
    assert "/apps/files/files/12345?dir=" in body["url"]

    again = api.post(f"/api/offers/{oid}/folder")
    assert again.status_code == 200
    assert again.json()["created"] is False and again.json()["name"] == body["name"]
    assert api.get(f"/api/offers/{oid}/folder").status_code == 200


def test_unknown_offer(api, dav):
    for method in (api.get, api.post):
        resp = method("/api/offers/999/folder")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Offre introuvable : 999", "code": "offer_not_found"}
    assert dav.methods == []


def test_ambiguous_folder_409(api, dav):
    _create(api, id=3, title="A")
    dav.add_dir(f"{ROOT}/03 - A")
    dav.add_dir(f"{ROOT}/003 - B")
    for method in (api.get, api.post):
        resp = method("/api/offers/3/folder")
        assert resp.status_code == 409
        assert resp.json()["code"] == "folder_ambiguous"
        assert "03 - A" in resp.json()["detail"] and "003 - B" in resp.json()["detail"]


def test_bad_password_and_missing_root(tmp_path, dav):
    with _client(nc_settings(tmp_path, dav.url, nextcloud_app_password="mauvais")) as c:
        offer = _create(c, title="X")
        assert offer["folder"]["status"] == "error" and "401" in offer["folder"]["detail"]
        resp = c.get(f"/api/offers/{offer['id']}/folder")
        assert resp.status_code == 502 and resp.json()["code"] == "nextcloud_auth"
    with _client(nc_settings(tmp_path / "b", dav.url, nextcloud_root="/Absent")) as c:
        offer = _create(c, title="X")
        assert offer["folder"]["status"] == "error"
        resp = c.get(f"/api/offers/{offer['id']}/folder")
        assert resp.status_code == 502 and resp.json()["code"] == "nextcloud_root_missing"
        assert "/Absent" in resp.json()["detail"]


# --- Nextcloud injoignable, configuration invalide -----------------------------------------


def test_nextcloud_down_offer_still_created(tmp_path, caplog):
    url = _closed_port_url()
    caplog.set_level(logging.WARNING)
    with _client(nc_settings(tmp_path, url)) as c:
        assert c.get("/api/meta").json()["nextcloud_enabled"] is True
        resp = c.post("/api/offers", json={"title": "Dev", "company": "ACME"})
        assert resp.status_code == 201
        offer = resp.json()
        assert offer["folder"]["status"] == "error"
        assert "injoignable" in offer["folder"]["detail"]
        assert c.get(f"/api/offers/{offer['id']}").status_code == 200
        assert len(c.get("/api/offers").json()) == 1

        for method in (c.get, c.post):
            resp = method(f"/api/offers/{offer['id']}/folder")
            assert resp.status_code == 503
            assert resp.json()["code"] == "nextcloud_unreachable"
            assert PASSWORD not in resp.text
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert f"offre n° {offer['id']} non créé" in logged and "injoignable" in logged
    assert PASSWORD not in logged and PASSWORD not in caplog.text


@pytest.mark.parametrize("bad_url", ["ftp://nextcloud.local", "nextcloud.local", "http://h:99999"])
def test_invalid_url_does_not_block(tmp_path, caplog, bad_url, no_network):
    caplog.set_level(logging.WARNING)
    with _client(nc_settings(tmp_path, bad_url)) as c:  # le démarrage réussit
        meta = c.get("/api/meta").json()
        assert meta["nextcloud_enabled"] is False
        assert "NEXTCLOUD_URL" in meta["nextcloud_error"]
        offer = _create(c, title="Dev")
        assert offer["folder"]["status"] == "error"
        assert "NEXTCLOUD_URL" in offer["folder"]["detail"]
        for method in (c.get, c.post):
            resp = method(f"/api/offers/{offer['id']}/folder")
            assert resp.status_code == 503
            assert resp.json()["code"] == "nextcloud_config"
            assert "NEXTCLOUD_URL" in resp.json()["detail"]
    assert "NEXTCLOUD_URL" in caplog.text and PASSWORD not in caplog.text
    assert no_network == []


def test_incomplete_config_reported(tmp_path, no_network):
    with _client(nc_settings(tmp_path, "http://nextcloud.local:8080", nextcloud_app_password=None)) as c:
        meta = c.get("/api/meta").json()
        assert meta["nextcloud_enabled"] is False
        assert "NEXTCLOUD_APP_PASSWORD manque" in meta["nextcloud_error"]
        offer = _create(c, title="Dev")
        assert offer["folder"]["status"] == "error"
        assert c.get(f"/api/offers/{offer['id']}/folder").json()["code"] == "nextcloud_config"
    assert no_network == []


def test_unexpected_error_never_500(api, monkeypatch):
    def broken(self, offer):
        raise RuntimeError(PASSWORD)

    monkeypatch.setattr(nextcloud.NextcloudClient, "create_offer_folder", broken)
    resp = api.post("/api/offers", json={"title": "X"})
    assert resp.status_code == 201
    assert resp.json()["folder"]["status"] == "error"
    assert PASSWORD not in resp.text


def test_password_never_in_responses(api, dav):
    dav.add_dir(f"{ROOT}/{EXISTING}")
    texts = [
        api.post("/api/offers", json={"id": 1, "title": "X"}).text,
        api.get("/api/offers/1/folder").text,
        api.post("/api/offers/1/folder").text,
        api.get("/api/meta").text,
        api.get("/openapi.json").text,
    ]
    assert all(PASSWORD not in t for t in texts)
