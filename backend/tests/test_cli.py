"""CLI cli/jobs.py : lancée en sous-processus contre la vraie application servie par uvicorn."""

from __future__ import annotations

import ast
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import openpyxl
import pytest
import uvicorn

from backend.app.main import create_app
from backend.tests.conftest import TOKEN, make_settings

ROOT = Path(__file__).resolve().parents[2]
JOBS = ROOT / "cli" / "jobs.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cli")
    app = create_app(make_settings(tmp))
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1) as resp:
                if resp.status == 200:
                    break
        except OSError:
            time.sleep(0.05)
    else:
        srv.should_exit = True
        pytest.fail("le serveur uvicorn n'a pas démarré")
    yield url
    srv.should_exit = True
    thread.join(timeout=10)


def run(server_url: str | None, *args: str, token: str | None = TOKEN, cwd: Path | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JOBS_API_", "PYTHONIOENCODING"))}
    if server_url is not None:
        env["JOBS_API_URL"] = server_url
    if token is not None:
        env["JOBS_API_TOKEN"] = token
    # Pas de PYTHONIOENCODING ni de mode UTF-8 forcé : la CLI doit gérer l'encodage seule.
    env["LC_ALL"] = "C"
    env["PYTHONUTF8"] = "0"
    proc = subprocess.run(
        [sys.executable, str(JOBS), *args],
        capture_output=True,
        env=env,
        cwd=cwd,
        timeout=60,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def run_json(server_url, *args):
    code, stdout, stderr = run(server_url, *args, "--json")
    assert code == 0, stderr
    return json.loads(stdout)


# --- Contrôle statique ---------------------------------------------------------------------


def test_jobs_imports_only_stdlib():
    tree = ast.parse(JOBS.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "import relatif interdit"
            modules.add(node.module.split(".")[0])
    non_stdlib = {m for m in modules if m not in sys.stdlib_module_names and m != "__future__"}
    assert not non_stdlib, non_stdlib
    assert {"argparse", "urllib"} <= modules


def test_help_is_french_with_examples():
    code, stdout, _ = run(None, "--help", token=None)
    assert code == 0
    assert "Exemples" in stdout and "jobs.py add" in stdout and "JOBS_API_TOKEN" in stdout
    for command in ("add", "update", "show", "list", "delete", "export", "meta"):
        code, stdout, _ = run(None, command, "--help", token=None)
        assert code == 0, command
        assert "Exemple" in stdout, command
    code, stdout, _ = run(None, "update", "--help", token=None)
    for option in ("--title", "--contract-type", "--main-issue", "--rejection-reason", "--applied-at",
                   "--application-sent", "--no-application-sent", "--follow-up-done",
                   "--no-follow-up-done", "--deadline-note", "--link", "--clear"):
        assert option in stdout, option


def test_usage_errors_are_french():
    code, _, stderr = run(None, "show", token=None)
    assert code == 2 and "utilisation : jobs.py show" in stderr
    assert "erreur : arguments obligatoires manquants : ID" in stderr
    code, _, stderr = run(None, "show", "abc", token=None)
    assert code == 2 and "numéro d'offre invalide" in stderr


# --- Sous-commandes ------------------------------------------------------------------------


def test_add_show_and_human_output(server):
    code, stdout, stderr = run(
        server, "add", "--title", "Ingénieur données", "--company", "Société Générale",
        "--department", "2A", "--status", "En cours", "--deadline", "2099-01-01",
        "--link", "https://exemple.fr/a", "--link", "https://exemple.fr/b", "--application-sent",
    )
    assert code == 0, stderr
    assert "créée" in stdout and "Ingénieur données" in stdout and "Société Générale" in stdout
    offer_id = int(stdout.split("n° ")[1].split()[0])

    offer = run_json(server, "show", str(offer_id))
    assert offer["department"] == "2A"
    assert offer["region"] == "Corse"
    assert offer["links"] == ["https://exemple.fr/a", "https://exemple.fr/b"]
    assert offer["application_sent"] is True
    assert offer["follow_up_done"] is False
    # Un champ non fourni reste vide : rien d'inventé.
    assert offer["location"] is None and offer["contract_type"] is None

    code, stdout, _ = run(server, "show", str(offer_id))
    assert code == 0
    assert "Ingénieur données" in stdout and "Corse" in stdout and "J-" in stdout


def test_add_with_forced_id_and_conflict(server):
    offer = run_json(server, "add", "--id", "777", "--title", "Analyste", "--department", "01")
    assert offer["id"] == 777 and offer["department"] == "01"
    code, _, stderr = run(server, "add", "--id", "777", "--title", "Doublon")
    assert code == 1
    assert "Une offre porte déjà le numéro 777." in stderr


def test_update_keeps_other_fields_and_clear(server):
    offer = run_json(
        server, "add", "--title", "Chargé d'études", "--company", "ACME", "--location", "Lyon",
        "--department", "69", "--deadline", "2099-05-01", "--deadline-note", "avant midi",
        "--link", "https://exemple.fr/x",
    )
    oid = str(offer["id"])
    updated = run_json(server, "update", oid, "--status", "Entretien", "--follow-up-done")
    assert updated["status"] == "Entretien" and updated["follow_up_done"] is True
    for name in ("title", "company", "location", "department", "deadline", "deadline_note", "links"):
        assert updated[name] == offer[name], name

    cleared = run_json(server, "update", oid, "--clear", "deadline-note", "--clear", "deadline")
    assert cleared["deadline"] is None and cleared["deadline_note"] is None
    assert cleared["status"] == "Entretien" and cleared["title"] == "Chargé d'études"

    code, stdout, _ = run(server, "update", oid, "--no-follow-up-done", "--link", "https://exemple.fr/y")
    assert code == 0 and "mise à jour" in stdout
    again = run_json(server, "show", oid)
    assert again["follow_up_done"] is False and again["links"] == ["https://exemple.fr/y"]
    assert again["status"] == "Entretien"


def test_update_errors(server):
    offer = run_json(server, "add", "--title", "Erreurs")
    oid = str(offer["id"])
    code, _, stderr = run(server, "update", oid)
    assert code == 2 and "Rien à modifier" in stderr
    code, _, stderr = run(server, "update", oid, "--clear", "application-sent")
    assert code == 2 and "booléen" in stderr
    code, _, stderr = run(server, "update", oid, "--clear", "inconnu")
    assert code == 2 and "champ inconnu" in stderr
    code, _, stderr = run(server, "update", oid, "--status", "Bof")
    assert code == 1 and "Statut inconnu" in stderr
    code, _, stderr = run(server, "update", oid, "--deadline", "31/12/2025")
    assert code == 1 and "AAAA-MM-JJ" in stderr
    code, _, stderr = run(server, "update", "999999", "--title", "x")
    assert code == 1 and "Offre introuvable : 999999" in stderr
    assert run_json(server, "show", oid)["title"] == "Erreurs"


def test_list_filters_and_human_lines(server):
    run_json(server, "add", "--title", "Poste corse filtre", "--company", "Ajaccio SA",
             "--department", "2A", "--status", "À postuler")
    run_json(server, "add", "--title", "Poste ain filtre", "--company", "Bourg SA", "--department", "01")

    corse = run_json(server, "list", "--department", "2A")
    assert corse and all(o["department"] == "2A" for o in corse)
    ain = run_json(server, "list", "--department", "01", "--text", "filtre")
    assert [o["title"] for o in ain] == ["Poste ain filtre"]
    by_region = run_json(server, "list", "--region", "Corse", "--status", "À postuler")
    assert [o["title"] for o in by_region] == ["Poste corse filtre"]
    unset = run_json(server, "list", "--status", "À qualifier", "--text", "ain filtre")
    assert [o["title"] for o in unset] == ["Poste ain filtre"]

    code, stdout, _ = run(server, "list", "--text", "filtre")
    assert code == 0
    lines = [line for line in stdout.splitlines() if line.startswith("#")]
    assert len(lines) == 2
    assert any("À postuler" in line and "Poste corse filtre" in line and "Ajaccio SA" in line
               for line in lines)
    assert any("À qualifier" in line and "sans date limite" in line for line in lines)
    assert stdout.strip().endswith("2 offres.")

    code, _, stderr = run(server, "list", "--region", "Atlantide")
    assert code == 1 and "région inconnue" in stderr


def test_delete_requires_yes(server):
    offer = run_json(server, "add", "--title", "À supprimer")
    oid = str(offer["id"])
    code, stdout, stderr = run(server, "delete", oid)
    assert code != 0 and "--yes" in stderr and stdout == ""
    assert run_json(server, "show", oid)["title"] == "À supprimer"
    code, stdout, _ = run(server, "delete", oid, "--yes")
    assert code == 0 and "supprimée" in stdout
    code, _, stderr = run(server, "show", oid)
    assert code == 1 and f"Offre introuvable : {oid}" in stderr


def test_export_writes_valid_xlsx(server, tmp_path):
    run_json(server, "add", "--title", "Pour l'export", "--department", "2A")
    target = tmp_path / "suivi.xlsx"
    code, stdout, stderr = run(server, "export", "-o", str(target))
    assert code == 0, stderr
    assert "suivi.xlsx" in stdout
    wb = openpyxl.load_workbook(io.BytesIO(target.read_bytes()))
    assert "Suivi" in wb.sheetnames
    values = [c.value for row in wb["Suivi"].iter_rows() for c in row]
    assert "Pour l'export" in values

    code, stdout, stderr = run(server, "export", "--json", cwd=tmp_path)
    assert code == 0, stderr
    info = json.loads(stdout)
    assert info["file"].startswith("suivi-offres-") and info["file"].endswith(".xlsx")
    assert (tmp_path / info["file"]).stat().st_size == info["bytes"] > 0


def test_meta(server):
    meta = run_json(server, "meta")
    assert "En cours" in meta["statuses"]
    code, stdout, _ = run(server, "meta")
    assert code == 0 and "Statuts" in stdout and "2A" in stdout and "Corse" in stdout


# --- Erreurs de connexion ------------------------------------------------------------------


def test_bad_token_shows_french_api_error(server):
    code, _, stderr = run(server, "list", token="mauvais-jeton")
    assert code == 1 and "HTTP 401" in stderr and "Authentification requise" in stderr


def test_url_and_token_options_override_env(server):
    code, stdout, stderr = run(
        "http://127.0.0.1:1", "list", "--url", server + "/api/", "--token", TOKEN, token="faux"
    )
    assert code == 0, stderr
    assert "offre" in stdout


def test_missing_token(server):
    code, _, stderr = run(server, "list", token=None)
    assert code == 2 and "JOBS_API_TOKEN" in stderr


def test_network_error():
    port = _free_port()  # rien n'écoute sur ce port
    code, stdout, stderr = run(f"http://127.0.0.1:{port}", "list", "--timeout", "5")
    assert code == 1 and stdout == ""
    assert "Impossible de joindre l'API" in stderr
    assert "Traceback" not in stderr


def test_malformed_urls_are_usage_errors():
    for url in ("http://127.0.0.1:abc", "http://", "http://[::1", "ftp://exemple.fr", "localhost:8000",
                "http://127.0.0.1:99999"):
        code, stdout, stderr = run(url, "list")
        assert code == 2, (url, stderr)
        assert "Adresse de l'API invalide" in stderr and "Traceback" not in stderr, url
        assert stdout == ""


@pytest.mark.parametrize("value", ["-1", "0", "nan", "inf", "-inf", "1e309", "x", ""])
def test_invalid_timeout_is_usage_error(value):
    for args in (["--timeout", value, "list"], ["list", f"--timeout={value}"]):
        code, stdout, stderr = run("http://127.0.0.1:1", *args)
        assert code == 2, (args, stderr)
        assert "Traceback" not in stderr and stdout == "", args
        # « --timeout -inf » : argparse prend -inf pour une option (erreur d'usage, autre message).
        if not (value == "-inf" and args[0] == "--timeout"):
            assert "délai invalide" in stderr, (args, stderr)


def test_valid_fractional_timeout_accepted(server):
    code, stdout, stderr = run(server, "--timeout", "2.5", "meta", "--json")
    assert code == 0, stderr
    assert "statuses" in json.loads(stdout)


def test_env_url_is_stripped(server):
    # Espaces autour d'une adresse valide : ignorés.
    code, stdout, stderr = run(f"  {server}\t", "list", "--json")
    assert code == 0, stderr
    assert isinstance(json.loads(stdout), list)
    # Variable faite uniquement d'espaces : traitée comme absente, donc adresse par défaut.
    code, _, stderr = run("   ", "list", "--timeout", "2")
    assert "Adresse de l'API invalide" not in stderr and "Traceback" not in stderr
    # Selon qu'un service écoute ou non sur localhost:8000 (ex. conteneur de dev avec un autre jeton) :
    # succès, erreur de connexion nommant l'adresse par défaut, ou erreur HTTP de ce service par défaut.
    assert code == 0 or "localhost:8000" in stderr or "Erreur de l'API (HTTP " in stderr, stderr


# --- Faux serveurs : réponses invalides ----------------------------------------------------


def _read_request(conn: socket.socket) -> None:
    """Lit la requête entière (en-têtes puis Content-Length octets de corps) avant de répondre."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip())
    while len(body) < length:
        chunk = conn.recv(65536)
        if not chunk:
            return
        body += chunk


def _raw_server(response: bytes) -> str:
    """Socket brute qui répond `response` à toute connexion (ni HTTP valide, ni API)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)

    def loop():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(10)
                    _read_request(conn)
                    conn.sendall(response)
                    # Fermeture propre : FIN puis vidage, pour que le client ne reçoive jamais de RST.
                    conn.shutdown(socket.SHUT_WR)
                    while conn.recv(65536):
                        pass
                except OSError:
                    pass

    threading.Thread(target=loop, daemon=True).start()
    return f"http://127.0.0.1:{sock.getsockname()[1]}"


def _http_200(body: bytes, content_type: str = "text/html") -> bytes:
    head = f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
    return head.encode("ascii") + b"Connection: close\r\n\r\n" + body


@pytest.mark.parametrize(
    ("response", "label"),
    [
        (b"HELLO\r\n\r\n", "BadStatusLine"),
        (b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{"a"', "IncompleteRead"),
    ],
)
def test_invalid_http_reply_is_french_error(response, label, tmp_path):
    url = _raw_server(response)
    export = ["export", "-o", str(tmp_path / "e.xlsx")]
    for args in (["list"], ["show", "1"], ["add", "--title", "x"], export):
        code, stdout, stderr = run(url, *args, "--timeout", "5")
        assert code == 1, (args, stderr)
        assert "Réponse HTTP invalide" in stderr and label in stderr, args
        assert "Traceback" not in stderr and stdout == ""
    assert not (tmp_path / "e.xlsx").exists()


def test_export_refuses_non_xlsx_content(tmp_path):
    url = _raw_server(_http_200(b"<html><body>Connexion au proxy</body></html>"))
    target = tmp_path / "suivi.xlsx"
    code, stdout, stderr = run(url, "export", "-o", str(target))
    assert code == 1 and stdout == ""
    assert "n'est pas un classeur Excel" in stderr and "Traceback" not in stderr
    assert not target.exists()
    code, _, stderr = run(url, "export", cwd=tmp_path)
    assert code == 1 and "n'est pas un classeur Excel" in stderr
    assert list(tmp_path.iterdir()) == []


def test_unexpected_json_shapes(tmp_path):
    obj = _raw_server(_http_200(b"{}", "application/json"))
    arr = _raw_server(_http_200(b"[]", "application/json"))
    bad_list = _raw_server(_http_200(b'[{"titre": "x"}]', "application/json"))
    cases = [
        (obj, ["list"]),
        (bad_list, ["list"]),
        (obj, ["show", "1"]),
        (arr, ["show", "1"]),
        (obj, ["add", "--title", "x"]),
        (obj, ["update", "1", "--title", "x"]),
        (arr, ["meta"]),
        (obj, ["meta"]),
    ]
    for url, args in cases:
        for extra in ([], ["--json"]):
            code, stdout, stderr = run(url, *args, *extra)
            assert code == 1, (args, extra, stderr)
            assert "Réponse inattendue de l'API" in stderr and "Traceback" not in stderr, args
            assert stdout == "", args


# --- Options globales et analyse stricte ---------------------------------------------------


def test_common_options_before_or_after_subcommand(server):
    offers = json.loads(run(None, "--json", "--url", server, "list")[1])
    assert isinstance(offers, list)
    code, stdout, stderr = run(
        "http://127.0.0.1:1", "--url", server, "--token", TOKEN, "--json", "--timeout", "10", "meta",
        token="faux",
    )
    assert code == 0, stderr
    assert "En cours" in json.loads(stdout)["statuses"]
    # Une valeur donnée après la sous-commande l'emporte, une absente n'écrase rien.
    code, stdout, stderr = run(None, "--url", "http://127.0.0.1:1", "list", "--url", server, "--json")
    assert code == 0, stderr
    assert isinstance(json.loads(stdout), list)
    code, stdout, stderr = run(None, "--json", "list", "--url", server)
    assert code == 0, stderr
    assert isinstance(json.loads(stdout), list)


def test_abbreviated_options_rejected(server):
    code, stdout, stderr = run(server, "list", "--sta", "En cours")
    assert code == 2 and stdout == ""
    assert "arguments non reconnus" in stderr and "--sta" in stderr
    code, _, stderr = run(server, "add", "--tit", "x")
    assert code == 2 and "--tit" in stderr
    code, _, stderr = run(server, "--js", "list")
    assert code == 2


def test_export_never_overwrites_without_force(server, tmp_path):
    target = tmp_path / "suivi.xlsx"
    target.write_bytes(b"ancien contenu")
    code, stdout, stderr = run(server, "export", "-o", str(target))
    assert code == 2 and stdout == ""
    assert "existe déjà" in stderr and "--force" in stderr
    assert target.read_bytes() == b"ancien contenu"

    code, stdout, stderr = run(server, "export", "-o", str(target), "--force")
    assert code == 0, stderr
    assert target.read_bytes().startswith(b"PK\x03\x04")
    openpyxl.load_workbook(io.BytesIO(target.read_bytes()))

    # Sans -o : le nom proposé par le service n'écrase pas non plus un fichier existant.
    code, stdout, stderr = run(server, "export", "--json", cwd=tmp_path)
    assert code == 0, stderr
    name = json.loads(stdout)["file"]
    (tmp_path / name).write_bytes(b"x")
    code, _, stderr = run(server, "export", cwd=tmp_path)
    assert code == 2 and "existe déjà" in stderr
    assert (tmp_path / name).read_bytes() == b"x"


def test_help_mentions_utf8_and_force():
    code, stdout, _ = run(None, "--help", token=None)
    assert code == 0 and "UTF-8" in stdout and "PowerShell" in stdout
    code, stdout, _ = run(None, "export", "--help", token=None)
    assert code == 0 and "--force" in stdout
