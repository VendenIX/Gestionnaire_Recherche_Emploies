"""Tests du client WebDAV Nextcloud contre un faux serveur WebDAV (thread local)."""

from __future__ import annotations

import base64
import inspect
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

import pytest

from backend.app import nextcloud
from backend.app.config import get_settings
from backend.app.nextcloud import (
    AmbiguousFolderError,
    NextcloudAuthError,
    NextcloudClient,
    NextcloudConfig,
    NextcloudConfigError,
    NextcloudError,
    NextcloudRootMissing,
    NextcloudUnreachable,
    folder_name_for,
    folder_number,
    parse_multistatus,
    web_url,
)

USER = "alice"
PASSWORD = "s3cret-AppPass-XYZ"
ROOT = "/Candidatures"
PREFIX = f"/remote.php/dav/files/{USER}"
ACCENT = "01 - Chargé de mission Acme"


class FakeDav:
    """Arborescence en mémoire : dossiers (chemins décodés) et fichiers (chemin -> taille)."""

    def __init__(self):
        self.dirs: set[str] = {"/", ROOT}
        self.files: dict[str, int] = {}
        self.methods: list[str] = []
        self.force_mkcol: int | None = None  # statut imposé aux MKCOL

    def add_dir(self, path: str) -> None:
        self.dirs.add(path.rstrip("/"))

    def children(self, path: str):
        base = path.rstrip("/") + "/"
        for d in sorted(self.dirs):
            if d.startswith(base) and "/" not in d[len(base):] and d != path:
                yield d, True, None
        for f, size in sorted(self.files.items()):
            if f.startswith(base) and "/" not in f[len(base):]:
                yield f, False, size


def _response(path: str, is_dir: bool, size: int | None) -> str:
    href = urllib.parse.quote(PREFIX + path + ("/" if is_dir else ""))
    rtype = "<d:collection/>" if is_dir else ""
    length = f"<d:getcontentlength>{size}</d:getcontentlength>" if size is not None else ""
    missing = "" if size is not None else "<d:getcontentlength/>"
    return (
        f"<d:response><d:href>{escape(href)}</d:href>"
        f"<d:propstat><d:prop><d:resourcetype>{rtype}</d:resourcetype>{length}"
        "<d:getlastmodified>Tue, 03 Nov 2026 10:00:00 GMT</d:getlastmodified>"
        "<oc:fileid>12345</oc:fileid></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
        f"<d:propstat><d:prop>{missing}</d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>"
        "</d:response>"
    )


def make_handler(dav: FakeDav):
    expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def _start(self) -> str | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)  # P-002 : lire toute la requête
            dav.methods.append(self.command)
            if self.headers.get("Authorization") != expected:
                self._send(401, b"")
                return None
            raw = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
            assert raw.startswith(PREFIX + "/")
            return raw[len(PREFIX):].rstrip("/") or "/"

        def _send(self, code: int, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            if body:
                self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_PROPFIND(self):  # noqa: N802
            path = self._start()
            if path is None:
                return
            if path not in dav.dirs:
                self._send(404, b"")
                return
            parts = [_response(path, True, None)]
            parts += [_response(p, d, s) for p, d, s in dav.children(path)]
            body = (
                '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
                + "".join(parts)
                + "</d:multistatus>"
            ).encode()
            self._send(207, body)

        def do_MKCOL(self):  # noqa: N802
            path = self._start()
            if path is None:
                return
            if dav.force_mkcol is not None:
                self._send(dav.force_mkcol, b"")
            elif path in dav.dirs or path in dav.files:
                self._send(405, b"")
            elif (path.rsplit("/", 1)[0] or "/") not in dav.dirs:
                self._send(409, b"")
            else:
                dav.dirs.add(path)
                self._send(201, b"")

    return Handler


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


def client_for(dav, password=PASSWORD, root=ROOT) -> NextcloudClient:
    return NextcloudClient(NextcloudConfig(url=dav.url, user=USER, password=password, root=root, timeout=5))


# --- numéros et noms -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,number",
    [("1 - A", 1), ("01 - A", 1), ("017 - A", 17), ("0017 - A", 17), ("01-A", 1), ("01", 1),
     ("10 - A", 10), ("11 - A", 11), ("Archives", None), ("01abc", None), ("", None),
     ("01_x", 1), ("01 x", 1), ("01. x", None), ("01\u2013x", None),
     ("\u0660\u0661 - x", None), ("\uff10\uff11 - x", None), ("1\u0661 - x", None)],
)
def test_folder_number(name, number):
    assert folder_number(name) == number


def test_folder_name_for():
    assert folder_name_for({"id": 7, "title": "Ingénieur", "company": "Thales"}) == "07 - Ingénieur Thales"
    assert folder_name_for({"id": 123, "title": "Dev", "company": None}) == "123 - Dev"
    assert folder_name_for({"id": 3, "title": None, "company": None}) == "03"
    assert folder_name_for({"id": 4, "title": "  ..  ", "company": ""}) == "04"
    odd = folder_name_for({"id": 5, "title": "R&D / IA\\ML\tCDI\n", "company": "Acme. "})
    assert "/" not in odd and "\\" not in odd and "\t" not in odd and "\n" not in odd
    assert odd == "05 - R&D IA ML CDI Acme"
    long = folder_name_for({"id": 9, "title": "é" * 400, "company": "X"})
    assert len(long) <= nextcloud.MAX_NAME_CHARS and len(long.encode()) <= nextcloud.MAX_NAME_BYTES


def test_folder_name_for_object_and_leading_dot():
    class Offer:
        id = 12
        title = ".hidden title."
        company = "Co"

    assert folder_name_for(Offer()) == "12 - .hidden title. Co"
    assert not nextcloud.sanitize_name(" ..x.. ").startswith(".")


def test_web_url_matches_owner_example():
    base = "http://nextcloud.local:8080"
    assert web_url(base, "/Candidatures", "01 - Chargé") == (
        "http://nextcloud.local:8080/apps/files/files?dir=/Candidatures/01%20-%20Charg%C3%A9"
    )
    assert web_url(base + "/", "Candidatures/", "01 - Chargé", file_id="12345") == (
        "http://nextcloud.local:8080/apps/files/files/12345?dir=/Candidatures/01%20-%20Charg%C3%A9"
    )
    assert "%26" in web_url(base, "/J", "02 - R&D #1") and "%23" in web_url(base, "/J", "02 - R&D #1")


# --- recherche -----------------------------------------------------------------------------------


def test_lookup_paddings_and_neighbours(dav):
    for name in ("1 - Un", "10 - Dix", "11 - Onze", "0017 - Dix-sept", "023-Vingt-trois", "05"):
        dav.add_dir(f"{ROOT}/{name}")
    dav.files[f"{ROOT}/04 - notes.txt"] = 12  # un fichier ne compte pas
    c = client_for(dav)
    assert c.find_offer_folder(1).name == "1 - Un"
    assert c.find_offer_folder(10).name == "10 - Dix"
    assert c.find_offer_folder(11).name == "11 - Onze"
    assert c.find_offer_folder(17).name == "0017 - Dix-sept"
    assert c.find_offer_folder(23).name == "023-Vingt-trois"
    assert c.find_offer_folder(5).name == "05"
    assert c.find_offer_folder(4) is None
    assert c.find_offer_folder(99) is None


def test_lookup_accents_spaces_and_listing(dav):
    dav.add_dir(f"{ROOT}/{ACCENT}")
    dav.add_dir(f"{ROOT}/{ACCENT}/CV")
    dav.files[f"{ROOT}/{ACCENT}/Lettre de motivation é.pdf"] = 2048
    c = client_for(dav)
    folder = c.find_offer_folder(1)
    assert folder.name == ACCENT and folder.is_dir and folder.file_id == "12345"
    found, files = c.list_offer_files(1)
    assert found.name == ACCENT
    assert [(f.name, f.is_dir, f.size) for f in files] == [
        ("CV", True, None),
        ("Lettre de motivation é.pdf", False, 2048),
    ]
    assert files[1].modified.year == 2026
    assert c.web_url(folder.name) == (
        f"{dav.url}/apps/files/files?dir=/Candidatures/01%20-%20Charg%C3%A9%20de%20mission%20Acme"
    )
    assert c.list_offer_files(2) is None


def test_lookup_ambiguity(dav):
    dav.add_dir(f"{ROOT}/01 - Ancien")
    dav.add_dir(f"{ROOT}/1 - Nouveau")
    with pytest.raises(AmbiguousFolderError) as err:
        client_for(dav).find_offer_folder(1)
    assert err.value.names == ["01 - Ancien", "1 - Nouveau"]
    assert "01 - Ancien" in str(err.value) and "1 - Nouveau" in str(err.value)


def test_root_missing(dav):
    with pytest.raises(NextcloudRootMissing, match="NEXTCLOUD_ROOT"):
        client_for(dav, root="/Absent").find_offer_folder(1)
    with pytest.raises(NextcloudRootMissing):
        client_for(dav, root="/Absent/sous").create_offer_folder({"id": 1, "title": "T", "company": "C"})


# --- création ------------------------------------------------------------------------------------


def test_create_offer_folder(dav):
    c = client_for(dav)
    offer = {"id": 18, "title": "Ingénieur systèmes", "company": "Thales"}
    name, created, added = c.create_offer_folder(offer)
    assert (name, created, added) == ("18 - Ingénieur systèmes Thales", True, ("CV", "Lettre"))
    assert {f"{ROOT}/{name}", f"{ROOT}/{name}/CV", f"{ROOT}/{name}/Lettre"} <= dav.dirs
    assert c.find_offer_folder(18).name == name


def test_existing_folder_reused(dav):
    dav.add_dir(f"{ROOT}/0018 - Nom du propriétaire")
    dav.add_dir(f"{ROOT}/0018 - Nom du propriétaire/CV")
    dav.add_dir(f"{ROOT}/0018 - Nom du propriétaire/Lettre")
    before = set(dav.dirs)
    name, created, added = client_for(dav).create_offer_folder({"id": 18, "title": "Autre", "company": "X"})
    assert (name, created, added) == ("0018 - Nom du propriétaire", False, ())
    assert dav.dirs == before
    assert "MKCOL" not in dav.methods


def test_existing_folder_subfolder_failure_is_best_effort(dav, caplog):
    folder = f"{ROOT}/03 - Owner"
    dav.add_dir(folder)
    dav.force_mkcol = 403  # partage en lecture seule : MKCOL de CV/ et Lettre/ refusés
    with caplog.at_level("WARNING", logger="backend.app.nextcloud"):
        result = client_for(dav).create_offer_folder({"id": 3, "title": "Autre", "company": "X"})
    assert result == ("03 - Owner", False, ())
    assert "MKCOL" in dav.methods and "403" in caplog.text


def test_existing_folder_auth_error_still_raised(dav, monkeypatch):
    dav.add_dir(f"{ROOT}/03 - Owner")

    def denied(self, name):
        raise NextcloudAuthError("401")

    monkeypatch.setattr(NextcloudClient, "_ensure_subfolders", denied)
    with pytest.raises(NextcloudAuthError):
        client_for(dav).create_offer_folder({"id": 3, "title": "Autre", "company": "X"})


def test_new_folder_reports_only_subfolders_really_created(dav, monkeypatch):
    real_mkcol = NextcloudClient._mkcol

    def mkcol(self, *segments):
        if segments[-1] == "CV":  # CV/ déjà présent (course) : MKCOL -> 405, pas d'ajout
            return False
        return real_mkcol(self, *segments)

    monkeypatch.setattr(NextcloudClient, "_mkcol", mkcol)
    result = client_for(dav).create_offer_folder({"id": 12, "title": "Neuf", "company": "X"})
    assert result == ("12 - Neuf X", True, ("Lettre",))


def test_existing_folder_gets_missing_cv_only(dav):
    folder = f"{ROOT}/07 - Dossier du propriétaire"
    dav.add_dir(folder)
    dav.add_dir(f"{folder}/Lettre")
    dav.files[f"{folder}/Lettre/lettre.pdf"] = 10
    dav.files[f"{folder}/Notes.txt"] = 5
    files_before = dict(dav.files)
    mkcols = []
    real_request = NextcloudClient._request

    def spy(self, method, path, body=None, depth=None):
        if method == "MKCOL":
            mkcols.append(urllib.parse.unquote(path))
        return real_request(self, method, path, body=body, depth=depth)

    c = client_for(dav)
    c._request = spy.__get__(c)
    result = c.create_offer_folder({"id": 7, "title": "Autre", "company": "X"})
    assert result == ("07 - Dossier du propriétaire", False, ("CV",))
    assert result.created is False and result.subfolders_added == ("CV",)
    assert f"{folder}/CV" in dav.dirs
    assert mkcols == [f"{PREFIX}{folder}/CV/"]  # aucun MKCOL sur Lettre/ ni sur le dossier
    assert dav.files == files_before
    assert dav.methods == ["PROPFIND", "PROPFIND", "MKCOL"]  # racine, dossier, puis CV/


def test_existing_subfolder_case_or_file_left_alone(dav):
    folder = f"{ROOT}/08 - X"
    dav.add_dir(folder)
    dav.add_dir(f"{folder}/cv")  # autre casse : considéré présent
    dav.files[f"{folder}/Lettre"] = 3  # un fichier « Lettre » : jamais touché
    name, created, added = client_for(dav).create_offer_folder({"id": 8, "title": "X"})
    assert (name, created, added) == ("08 - X", False, ())
    assert "MKCOL" not in dav.methods
    assert f"{folder}/CV" not in dav.dirs and dav.files[f"{folder}/Lettre"] == 3


def test_with_timeout_copies_config(dav):
    c = NextcloudClient(NextcloudConfig(url=dav.url, user=USER, password=PASSWORD, root=ROOT))
    fast = c.with_timeout(nextcloud.OFFER_CREATE_TIMEOUT)
    assert fast.config.timeout == 5.0 and c.config.timeout == nextcloud.DEFAULT_TIMEOUT == 10.0
    assert fast.config.password == PASSWORD and fast.root == c.root


def test_only_safe_methods(dav):
    client_for(dav).create_offer_folder({"id": 2, "title": "T", "company": "C"})
    assert set(dav.methods) <= {"PROPFIND", "MKCOL"}
    assert nextcloud.ALLOWED_METHODS == frozenset({"PROPFIND", "MKCOL"})
    source = inspect.getsource(nextcloud)
    for verb in ("DELETE", "MOVE", "PUT", "COPY", "PROPPATCH"):
        assert not re.search(rf"\b{verb}\b", source), verb
    with pytest.raises(ValueError):
        client_for(dav)._request("DELETE", "/x")


# --- erreurs et secrets --------------------------------------------------------------------------


def test_bad_password_401(dav):
    with pytest.raises(NextcloudAuthError, match="401") as err:
        client_for(dav, password="wrong-password").find_offer_folder(1)
    assert "wrong-password" not in str(err.value)


def test_unreachable_without_credentials(monkeypatch):
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    c = NextcloudClient(NextcloudConfig(url=f"http://127.0.0.1:{port}", user=USER, password=PASSWORD,
                                        root=ROOT, timeout=2))
    with pytest.raises(NextcloudUnreachable, match="injoignable") as err:
        c.find_offer_folder(1)
    assert PASSWORD not in str(err.value)
    assert err.value.__cause__ is None


def test_password_never_in_repr(monkeypatch):
    cfg = NextcloudConfig(url="http://nc", user=USER, password=PASSWORD, root=ROOT)
    assert PASSWORD not in repr(cfg)
    assert PASSWORD not in repr(NextcloudClient(cfg))
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", PASSWORD)
    s = get_settings()
    assert s.nextcloud_app_password == PASSWORD
    assert PASSWORD not in repr(s)


def test_settings_webdav(monkeypatch):
    names = ("NEXTCLOUD_WEBDAV", "NEXTCLOUD_USER", "NEXTCLOUD_APP_PASSWORD")
    names += ("NEXTCLOUD_URL", "NEXTCLOUD_ROOT")
    for v in names:
        monkeypatch.delenv(v, raising=False)
    s = get_settings()
    assert s.nextcloud_webdav is False and s.nextcloud_user is None and s.nextcloud_app_password is None
    assert s.nextcloud_root == "/Candidatures"
    assert NextcloudConfig.from_settings(s) is None
    monkeypatch.setenv("NEXTCLOUD_WEBDAV", "true")
    monkeypatch.setenv("NEXTCLOUD_URL", "http://nc")
    monkeypatch.setenv("NEXTCLOUD_USER", USER)
    assert NextcloudConfig.from_settings(get_settings()) is None  # mot de passe manquant
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", PASSWORD)
    monkeypatch.setenv("NEXTCLOUD_ROOT", ROOT)
    cfg = NextcloudConfig.from_settings(get_settings())
    assert (cfg.url, cfg.user, cfg.password, cfg.root) == ("http://nc", USER, PASSWORD, ROOT)
    monkeypatch.setenv("NEXTCLOUD_WEBDAV", "peut-être")
    with pytest.raises(ValueError, match="NEXTCLOUD_WEBDAV"):
        get_settings()


# --- revue 1 -------------------------------------------------------------------------------------


def _garbage_server(reply: bytes):
    """Serveur socket brut : lit toute la requête, répond `reply`, draine puis ferme (P-002)."""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(5)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                head, _, rest = data.partition(b"\r\n\r\n")
                m = re.search(rb"content-length:\s*(\d+)", head, re.I)
                need = int(m.group(1)) if m else 0
                while len(rest) < need:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    rest += chunk
                conn.sendall(reply)
                conn.shutdown(socket.SHUT_WR)
                try:
                    while conn.recv(65536):
                        pass
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    return srv


@pytest.mark.parametrize(
    "reply",
    [
        b"GARBAGE NOT HTTP\r\n\r\n",  # BadStatusLine
        b"HTTP/1.1 207 Multi-Status\r\nContent-Length: 500\r\n\r\n<d:multi",  # IncompleteRead
        b"",  # RemoteDisconnected
    ],
)
def test_invalid_http_reply_is_unreachable(reply):
    srv = _garbage_server(reply)
    try:
        port = srv.getsockname()[1]
        c = NextcloudClient(NextcloudConfig(url=f"http://127.0.0.1:{port}", user=USER, password=PASSWORD,
                                            root=ROOT, timeout=5))
        with pytest.raises(NextcloudUnreachable) as err:
            c.find_offer_folder(1)
        assert PASSWORD not in str(err.value)
        assert f"http://127.0.0.1:{port}" in str(err.value)
    finally:
        srv.close()


@pytest.mark.parametrize(
    "url", ["192.0.2.10:8080", "nextcloud.local:8080", "nc.local", "ftp://nc.local", "http://", "", "http://h:abc"]
)
def test_url_without_scheme_rejected(url):
    with pytest.raises(NextcloudConfigError, match="NEXTCLOUD_URL") as err:
        NextcloudConfig(url=url, user=USER, password=PASSWORD)
    assert not isinstance(err.value, ValueError)
    assert PASSWORD not in str(err.value)


def test_url_without_scheme_rejected_from_settings(monkeypatch):
    monkeypatch.setenv("NEXTCLOUD_WEBDAV", "true")
    monkeypatch.setenv("NEXTCLOUD_URL", "192.0.2.10:8080")
    monkeypatch.setenv("NEXTCLOUD_USER", USER)
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", PASSWORD)
    with pytest.raises(NextcloudConfigError, match="http://"):
        NextcloudConfig.from_settings(get_settings())


def _ms(*hrefs: str) -> bytes:
    items = "".join(
        f"<d:response><d:href>{h}</d:href><d:propstat><d:prop><d:resourcetype><d:collection/>"
        "</d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        for h in hrefs
    )
    return f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{items}</d:multistatus>'.encode()


def test_multistatus_self_entry_skipped_when_href_prefix_differs():
    req = "/nextcloud/remote.php/dav/files/alice/Documents/Candidatures/"
    # le proxy retire le préfixe /nextcloud des href
    d = "/remote.php/dav/files/alice/Documents"
    body = _ms(f"{d}/Candidatures/", f"{d}/Candidatures/01%20-%20A/")
    assert [e.name for e in parse_multistatus(body, req)] == ["01 - A"]
    # href absolu, avec préfixe ajouté
    root = "/remote.php/dav/files/alice/Documents/Candidatures"
    body = _ms(f"http://nc/x{root}", f"http://nc/x{root}/B")
    assert [e.name for e in parse_multistatus(body, f"{root}/")] == ["B"]
    # chemin de requête encodé, href non encodé
    body = _ms(f"{d}/Dé%20t/", f"{d}/Dé%20t/C/")
    assert [e.name for e in parse_multistatus(body, f"{d}/D%C3%A9%20t/")] == ["C"]



def test_multistatus_self_entry_with_percent_in_name():
    # dossier « 05 - Remise 50%41 test » : encodé une fois dans la requête et dans les href
    d = "/remote.php/dav/files/alice/Documents/Candidatures/05%20-%20Remise%2050%2541%20test"
    body = _ms(f"{d}/", f"{d}/CV/")
    assert [e.name for e in parse_multistatus(body, f"{d}/")] == ["CV"]
    root = "/remote.php/dav/files/alice/Documents/Candidatures/"
    body = _ms(root, f"{d}/")
    assert [e.name for e in parse_multistatus(body, root)] == ["05 - Remise 50%41 test"]

def test_error_messages_show_only_scheme_host_port():
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    cfg = NextcloudConfig(url=f"http://bob:hunter2@127.0.0.1:{port}/nextcloud/", user=USER, password="e",
                          root=ROOT, timeout=2)
    assert cfg.public_url == f"http://127.0.0.1:{port}"
    with pytest.raises(NextcloudUnreachable) as err:
        NextcloudClient(cfg).find_offer_folder(1)
    msg = str(err.value)
    assert msg.startswith(f"Nextcloud injoignable (http://127.0.0.1:{port}) : ")
    assert "bob" not in msg and "hunter2" not in msg and "/nextcloud" not in msg and "***" not in msg
    assert "bob" not in repr(NextcloudClient(cfg)) and "hunter2" not in repr(NextcloudClient(cfg))


def test_mkcol_405_on_file_is_an_error(dav):
    dav.files[f"{ROOT}/07 - X Y"] = 3
    with pytest.raises(NextcloudError, match="pas un dossier"):
        client_for(dav).create_offer_folder({"id": 7, "title": "X", "company": "Y"})
    assert f"{ROOT}/07 - X Y" not in dav.dirs and dav.files[f"{ROOT}/07 - X Y"] == 3


def test_mkcol_405_on_existing_folder_is_reuse(dav):
    # dossier au nom attendu mais pas reconnu par son numéro au moment de la recherche : simulé par 405
    dav.add_dir(f"{ROOT}/08 - X Y")
    c = client_for(dav)
    assert c._mkcol("08 - X Y") is False


def test_mkcol_409_branch(dav):
    dav.force_mkcol = 409
    with pytest.raises(NextcloudRootMissing):
        client_for(dav).create_offer_folder({"id": 9, "title": "T", "company": "C"})
    with pytest.raises(NextcloudError, match="parent manquant"):
        client_for(dav)._mkcol("09 - T C", "CV")
    assert "MKCOL" in dav.methods


@pytest.mark.parametrize("bad", [0, -1])
def test_folder_name_for_rejects_non_positive_id(bad):
    with pytest.raises(ValueError):
        folder_name_for({"id": bad, "title": "T", "company": "C"})
