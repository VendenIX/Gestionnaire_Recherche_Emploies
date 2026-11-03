"""CLI : sous-commande folder, add et show avec le dossier Nextcloud (serveur uvicorn réel + faux WebDAV)."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
import uvicorn

from backend.app.main import create_app
from backend.tests.conftest import make_settings
from backend.tests.test_cli import _free_port, run
from backend.tests.test_nextcloud import PASSWORD, ROOT, USER, FakeDav, make_handler

EXISTING = "05 - Chargé de mission Acme"


def _serve(settings):
    app = create_app(settings)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1) as resp:
                if resp.status == 200:
                    return url, srv, thread
        except OSError:
            time.sleep(0.05)
    srv.should_exit = True
    pytest.fail("le serveur uvicorn n'a pas démarré")


@pytest.fixture(scope="module")
def dav():
    fake = FakeDav()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield fake
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def nc_server(tmp_path_factory, dav):
    settings = make_settings(
        tmp_path_factory.mktemp("cli-nc"),
        nextcloud_webdav=True,
        nextcloud_url=dav.url,
        nextcloud_user=USER,
        nextcloud_app_password=PASSWORD,
        nextcloud_root=ROOT,
    )
    url, srv, thread = _serve(settings)
    yield url
    srv.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def plain_server(tmp_path_factory):
    url, srv, thread = _serve(make_settings(tmp_path_factory.mktemp("cli-plain")))
    yield url
    srv.should_exit = True
    thread.join(timeout=10)


def test_folder_of_existing_offer(nc_server, dav):
    dav.add_dir(f"{ROOT}/{EXISTING}")
    dav.files[f"{ROOT}/{EXISTING}/CV.pdf"] = 150_000
    code, stdout, stderr = run(nc_server, "add", "--id", "5", "--title", "Chargé de mission")
    assert code == 0, stderr
    assert f"Dossier Nextcloud existant, réutilisé : {EXISTING}" in stdout

    code, stdout, stderr = run(nc_server, "folder", "5")
    assert code == 0, stderr
    assert f"Dossier : {EXISTING}" in stdout
    assert f"Lien    : {dav.url}/apps/files/files/12345?dir=/Candidatures/05%20-%20Charg" in stdout
    assert "CV.pdf  (146,5 Ko, 2026-11-03)" in stdout

    code, stdout, _ = run(nc_server, "folder", "5", "--json")
    data = json.loads(stdout)
    assert data["name"] == EXISTING
    by_name = {f["name"]: f for f in data["files"]}
    assert by_name["CV.pdf"]["size"] == 150_000
    # Owner 2026-11-03 : CV/ et Lettre/ manquants ajoutés au dossier existant.
    assert by_name["CV"]["is_dir"] and by_name["Lettre"]["is_dir"]

    code, stdout, _ = run(nc_server, "show", "5")
    assert code == 0
    assert "Dossier Nextcloud    : http" in stdout and "?dir=/Candidatures/05%20-" in stdout
    code, stdout, _ = run(nc_server, "show", "5", "--json")
    assert "folder" not in json.loads(stdout)
    assert PASSWORD not in stdout


def test_add_creates_folder_and_folder_create(nc_server, dav):
    code, stdout, stderr = run(nc_server, "add", "--title", "Ingénieur", "--company", "Thales")
    assert code == 0, stderr
    assert "Dossier Nextcloud créé : " in stdout and "Ingénieur Thales" in stdout
    assert "Lien : http" in stdout

    # Offre sans dossier (création automatique en échec), puis folder --create.
    dav.force_mkcol = 503
    code, stdout, stderr = run(nc_server, "add", "--id", "40", "--title", "Analyste")
    dav.force_mkcol = None
    assert code == 0, stderr
    assert "Offre n° 40 créée." in stdout
    assert "Dossier Nextcloud non créé (l'offre est bien enregistrée) : " in stdout

    code, _, stderr = run(nc_server, "folder", "40")
    assert code == 1
    assert "HTTP 404" in stderr and "Pour le créer : jobs.py folder 40 --create" in stderr
    code, stdout, _ = run(nc_server, "show", "40")
    assert code == 0 and "aucun (pour le créer : jobs.py folder 40 --create)" in stdout

    code, stdout, stderr = run(nc_server, "folder", "40", "--create")
    assert code == 0, stderr
    assert "Dossier créé." in stdout and "Dossier : 40 - Analyste" in stdout
    assert "  CV/" in stdout and "  Lettre/" in stdout
    code, stdout, _ = run(nc_server, "folder", "40", "--create")
    assert code == 0 and "Le dossier existait déjà : rien n'a été modifié." in stdout


def test_folder_errors(nc_server, dav):
    code, _, stderr = run(nc_server, "folder", "998")
    assert code == 1 and "HTTP 404" in stderr and "Offre introuvable" in stderr
    run(nc_server, "add", "--id", "60", "--title", "A")
    dav.add_dir(f"{ROOT}/060 - Doublon")
    code, _, stderr = run(nc_server, "folder", "60")
    assert code == 1 and "HTTP 409" in stderr and "060 - Doublon" in stderr
    code, stdout, _ = run(nc_server, "show", "60")
    assert code == 0 and "Dossier Nextcloud    : indisponible (" in stdout


def test_meta_mentions_nextcloud(nc_server, plain_server):
    assert "Dossiers Nextcloud : activés (WebDAV)" in run(nc_server, "meta")[1]
    assert "Dossiers Nextcloud : désactivés" in run(plain_server, "meta")[1]


def test_disabled_server(plain_server):
    code, stdout, _ = run(plain_server, "add", "--title", "X")
    assert code == 0 and "Dossier" not in stdout
    code, stdout, _ = run(plain_server, "show", "1")
    assert code == 0 and "Dossier" not in stdout
    code, _, stderr = run(plain_server, "folder", "1")
    assert code == 1 and "HTTP 503" in stderr and "NEXTCLOUD_WEBDAV" in stderr


def test_folder_help_is_french():
    code, stdout, _ = run(None, "folder", "--help")
    assert code == 0 and "--create" in stdout and "jamais renommé" in stdout
