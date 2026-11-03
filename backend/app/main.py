"""Application FastAPI : API REST des offres (/api), protégée par un jeton Bearer.

``create_app(settings)`` refuse de démarrer sans API_TOKEN, applique les migrations, lance la
sauvegarde quotidienne (lifespan) et sert le front statique s'il existe.

Pas de ``from __future__ import annotations`` ici : FastAPI doit résoudre l'alias local
``Conn`` (Annotated + Depends) défini dans create_app ; en annotations différées, il échoue.
"""

import asyncio
import contextlib
import hmac
import logging
import sqlite3
from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import geo, repository
from .backup import backup_loop, safe_daily_backup
from .config import Settings, get_settings
from .db import connect, init_db
from .excel_export import XLSX_MEDIA_TYPE, workbook_bytes
from .nextcloud import (
    OFFER_CREATE_TIMEOUT,
    AmbiguousFolderError,
    DavEntry,
    NextcloudAuthError,
    NextcloudClient,
    NextcloudConfig,
    NextcloudConfigError,
    NextcloudError,
    NextcloudRootMissing,
    NextcloudUnreachable,
)
from .schemas import (
    FOLDER_CREATED,
    FOLDER_DISABLED,
    FOLDER_ERROR,
    FOLDER_EXISTING,
    MAX_OFFER_ID,
    NO_DEPARTMENT,
    STATUS_UNSET,
    STATUSES,
    FolderOut,
    MetaOut,
    OfferCreate,
    OfferCreatedOut,
    OfferOut,
    OfferReplace,
    OfferUpdate,
    StatsOut,
    check_department,
)

DEFAULT_FRONTEND_DIR = Path(__file__).resolve().parent / "static"

logger = logging.getLogger(__name__)

# Messages français pour les erreurs de validation Pydantic les plus courantes.
_MESSAGES = {
    "missing": "champ obligatoire manquant.",
    "extra_forbidden": "champ inconnu.",
    "string_type": "une chaîne de caractères est attendue.",
    "bool_type": "un booléen (true ou false) est attendu.",
    "int_type": "un entier est attendu.",
    "int_parsing": "un entier est attendu.",
    "greater_than_equal": "doit être un entier supérieur ou égal à 1.",
    "less_than_equal": f"doit être un entier inférieur ou égal à {MAX_OFFER_ID}.",
    "list_type": "une liste est attendue.",
    "datetime_type": "date et heure invalides (format ISO attendu, ex. 2025-01-31T10:00:00).",
    "datetime_parsing": "date et heure invalides (format ISO attendu, ex. 2025-01-31T10:00:00).",
    "datetime_from_date_parsing": "date et heure invalides (format ISO attendu, ex. 2025-01-31T10:00:00).",
    "date_type": "date invalide (format AAAA-MM-JJ attendu).",
    "date_parsing": "date invalide (format AAAA-MM-JJ attendu).",
    "date_from_datetime_parsing": "date invalide (format AAAA-MM-JJ attendu).",
    "date_from_datetime_inexact": "date invalide (format AAAA-MM-JJ attendu).",
    "model_attributes_type": "le corps de la requête doit être un objet JSON.",
    "dict_type": "le corps de la requête doit être un objet JSON.",
    "json_invalid": "JSON invalide.",
}


def _french_message(error: dict[str, Any]) -> str:
    if error.get("type") == "value_error" and "error" in error.get("ctx", {}):
        return str(error["ctx"]["error"])
    return _MESSAGES.get(error.get("type", ""), f"valeur invalide ({error.get('msg', '')}).")


def _field_name(error: dict[str, Any]) -> str:
    if error.get("type") == "json_invalid":
        # loc vaut ("body", <position du caractère fautif>) : ce n'est pas un nom de champ.
        return "corps"
    loc = error.get("loc", ())
    parts = [str(p) for p in loc if p not in ("body", "query", "path")]
    return ".".join(parts) or "corps"


def validation_response(errors: list[dict[str, Any]]) -> JSONResponse:
    """422 au format {"detail": "<résumé en français>", "errors": [{"field", "message"}]}."""
    items = [{"field": _field_name(e), "message": _french_message(e)} for e in errors]
    summary = "Données invalides : " + " ; ".join(f"{i['field']} : {i['message']}" for i in items)
    return JSONResponse(status_code=422, content={"detail": summary, "errors": items})


def _invalid_query(field: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": f"Données invalides : {field} : {message}",
            "errors": [{"field": field, "message": message}],
        },
    )


class QueryError(Exception):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def _not_found(offer_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Offre introuvable : {offer_id}")


class FolderError(Exception):
    """Erreur des routes /folder : {"detail": <message français>, "code": <code stable>}."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def _folder_error(exc: Exception) -> FolderError:
    """Erreur du client WebDAV -> code HTTP et code stable (messages déjà sans identifiants)."""
    if isinstance(exc, AmbiguousFolderError):
        return FolderError(409, "folder_ambiguous", str(exc))
    if isinstance(exc, NextcloudUnreachable):
        return FolderError(503, "nextcloud_unreachable", str(exc))
    if isinstance(exc, NextcloudConfigError):
        return FolderError(503, "nextcloud_config", str(exc))
    if isinstance(exc, NextcloudAuthError):
        return FolderError(502, "nextcloud_auth", str(exc))
    if isinstance(exc, NextcloudRootMissing):
        return FolderError(502, "nextcloud_root_missing", str(exc))
    if isinstance(exc, NextcloudError):
        return FolderError(502, "nextcloud_error", str(exc))
    return FolderError(422, "invalid_offer", str(exc))  # ValueError : numéro d'offre invalide


def _nextcloud_setup(settings: Settings) -> tuple[NextcloudClient | None, str | None]:
    """Client WebDAV, ou (None, message) : désactivé (message None) ou configuration invalide.

    Ne lève jamais : une configuration Nextcloud fautive n'empêche pas le démarrage.
    """
    if not settings.nextcloud_webdav:
        return None, None
    try:
        config = NextcloudConfig.from_settings(settings)
    except NextcloudConfigError as exc:
        return None, f"Configuration Nextcloud invalide : {exc}"
    if config is None:
        missing = [
            name
            for name, value in (
                ("NEXTCLOUD_URL", settings.nextcloud_url),
                ("NEXTCLOUD_USER", settings.nextcloud_user),
                ("NEXTCLOUD_APP_PASSWORD", settings.nextcloud_app_password),
            )
            if not value
        ]
        return None, (
            "Configuration Nextcloud incomplète : NEXTCLOUD_WEBDAV est activé mais "
            f"{', '.join(missing)} {'manque' if len(missing) == 1 else 'manquent'}."
        )
    return NextcloudClient(config), None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.api_token or not settings.api_token.strip():
        raise RuntimeError(
            "API_TOKEN est vide : définissez un jeton long et aléatoire avant de démarrer le service."
        )
    token = settings.api_token.encode("utf-8")
    data_dir = Path(settings.data_dir)
    database = init_db(data_dir)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(safe_daily_backup, data_dir, settings.backup_keep_days)
        task = asyncio.create_task(backup_loop(data_dir, settings.backup_keep_days))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="Suivi des candidatures",
        description="API REST du suivi de recherche d'emploi (remplace le classeur Excel).",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db_path = database

    nc_client, nc_error = _nextcloud_setup(settings)
    if nc_error:
        logger.warning("%s Le lien avec Nextcloud est désactivé.", nc_error)
    elif nc_client is not None:
        logger.info("Lien WebDAV Nextcloud actif : %r", nc_client)
    app.state.nextcloud = nc_client
    # POST /api/offers : délai court par requête WebDAV (5 s) pour ne pas bloquer le formulaire ;
    # les routes /folder gardent le délai normal du client (10 s).
    nc_offer_client = nc_client.with_timeout(OFFER_CREATE_TIMEOUT) if nc_client is not None else None
    app.state.nextcloud_offer = nc_offer_client

    @app.exception_handler(FolderError)
    async def _on_folder_error(request: Request, exc: FolderError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code})

    @app.exception_handler(RequestValidationError)
    async def _on_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return validation_response(list(exc.errors()))

    @app.exception_handler(QueryError)
    async def _on_query_error(request: Request, exc: QueryError) -> JSONResponse:
        return _invalid_query(exc.field, exc.message)

    def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip().encode("utf-8"), token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Authentification requise : en-tête « Authorization: Bearer <jeton> » absent ou invalide."
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(database)
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
    # Borné : un entier hors de la plage SQLite (2**63 et plus) provoquait une erreur 500.
    OfferId = Annotated[int, PathParam(le=MAX_OFFER_ID)]
    follow_up_days = settings.follow_up_days

    def _out(conn: sqlite3.Connection, offer_id: int) -> dict[str, Any]:
        row = repository.get_row(conn, offer_id)
        if row is None:
            raise _not_found(offer_id)
        return repository.row_to_offer(row, follow_up_days)

    @app.get("/api/health", tags=["système"])
    def health() -> dict[str, str]:
        """Vérification de santé (sans authentification, pour le healthcheck Docker)."""
        conn = connect(database)
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return {"status": "ok"}

    api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

    @api.get("/offers", response_model=list[OfferOut], tags=["offres"])
    def list_offers(
        conn: Conn,
        status_filter: Annotated[str | None, Query(alias="status")] = None,
        department: str | None = None,
        region: str | None = None,
        q: str | None = None,
        order: Annotated[str, Query(description="asc ou desc (tri par id)")] = "asc",
    ) -> list[dict[str, Any]]:
        """Liste filtrée (statut, département, région, texte), triée par id."""
        if status_filter is not None and status_filter not in (*STATUSES, STATUS_UNSET):
            raise QueryError(
                "status",
                f"statut inconnu : {status_filter!r}. Valeurs possibles : "
                f"{', '.join((*STATUSES, STATUS_UNSET))}.",
            )
        if department is not None:
            if not department.strip():
                raise QueryError(
                    "department",
                    "valeur vide : indiquez un code de département (ex. 75, 2A) ou retirez le filtre ; "
                    f"pour les offres sans département, utilisez region={NO_DEPARTMENT}.",
                )
            try:
                department = check_department(department)
            except ValueError as exc:
                raise QueryError("department", str(exc)) from exc
        if region is not None and region not in (*geo.REGIONS, NO_DEPARTMENT):
            raise QueryError(
                "region",
                f"région inconnue : {region!r}. Valeurs possibles : "
                f"{', '.join((*geo.REGIONS, NO_DEPARTMENT))}.",
            )
        if order not in ("asc", "desc"):
            raise QueryError("order", "valeur attendue : asc ou desc.")
        rows = repository.list_rows(
            conn, status=status_filter, department=department, region=region, q=q, descending=order == "desc"
        )
        return [repository.row_to_offer(r, follow_up_days) for r in rows]

    def _client() -> NextcloudClient:
        if nc_client is not None:
            return nc_client
        if nc_error:
            raise FolderError(503, "nextcloud_config", nc_error)
        raise FolderError(
            503,
            "nextcloud_disabled",
            "Le lien avec Nextcloud n'est pas activé (NEXTCLOUD_WEBDAV) : aucun dossier disponible.",
        )

    def _offer_for_folder(conn: sqlite3.Connection, offer_id: int) -> dict[str, Any]:
        row = repository.get_row(conn, offer_id)
        if row is None:
            raise FolderError(404, "offer_not_found", f"Offre introuvable : {offer_id}")
        return repository.row_to_offer(row, follow_up_days)

    def _folder_out(client: NextcloudClient, folder: DavEntry, entries: list[DavEntry]) -> dict[str, Any]:
        files = [
            {
                "name": e.name,
                "is_dir": e.is_dir,
                "size": e.size,
                "modified": e.modified,
                "url": client.web_url(f"{folder.name}/{e.name}", e.file_id)
                if e.is_dir
                else client.web_url(folder.name, e.file_id),
            }
            for e in entries
        ]
        return {"name": folder.name, "url": client.web_url(folder.name, folder.file_id), "files": files}

    def _create_folder_outcome(offer: dict[str, Any]) -> dict[str, Any]:
        """Crée le dossier d'une offre déjà enregistrée ; ne lève jamais (l'offre reste créée)."""
        if nc_offer_client is None:
            if nc_error:
                return {"status": FOLDER_ERROR, "detail": nc_error}
            return {"status": FOLDER_DISABLED}
        try:
            name, created, added = nc_offer_client.create_offer_folder(offer)
        except (NextcloudError, ValueError) as exc:
            logger.warning("Dossier Nextcloud de l'offre n° %s non créé : %s", offer["id"], exc)
            return {"status": FOLDER_ERROR, "detail": str(exc)}
        except Exception as exc:  # l'offre est déjà enregistrée : ne jamais répondre 500
            logger.error(
                "Dossier Nextcloud de l'offre n° %s non créé : erreur inattendue (%s).",
                offer["id"],
                type(exc).__name__,
            )
            return {
                "status": FOLDER_ERROR,
                "detail": f"Erreur inattendue lors de la création du dossier ({type(exc).__name__}).",
            }
        if added and not created:
            logger.info(
                "Dossier Nextcloud de l'offre n° %s : ajout de %s.", offer["id"], ", ".join(added)
            )
        return {
            "status": FOLDER_CREATED if created else FOLDER_EXISTING,
            "name": name,
            "url": nc_offer_client.web_url(name),
        }

    @api.post("/offers", response_model=OfferCreatedOut, status_code=201, tags=["offres"])
    def create_offer(payload: OfferCreate, conn: Conn) -> dict[str, Any]:
        """Crée l'offre, puis (WebDAV actif) son dossier Nextcloud ; « folder » dit ce qu'il en est.

        L'offre est créée même si Nextcloud échoue (folder.status = "error", détail en français).
        Chaque requête WebDAV de cette route attend au plus OFFER_CREATE_TIMEOUT (5 s).
        """
        data = payload.model_dump(exclude={"id"})
        try:
            offer_id = repository.create_offer(conn, data, offer_id=payload.id)
        except repository.OfferExists as exc:
            raise HTTPException(
                status_code=409, detail=f"Une offre porte déjà le numéro {payload.id}."
            ) from exc
        offer = _out(conn, offer_id)
        return {**offer, "folder": _create_folder_outcome(offer)}

    @api.get("/offers/{offer_id}/folder", response_model=FolderOut, tags=["dossiers"])
    def get_folder(offer_id: OfferId, conn: Conn) -> dict[str, Any]:
        """Dossier Nextcloud de l'offre (trouvé par son numéro) et son contenu.

        Erreurs {"detail", "code"} : 404 offer_not_found / folder_not_found, 409 folder_ambiguous,
        503 nextcloud_disabled / nextcloud_config / nextcloud_unreachable, 502 nextcloud_auth /
        nextcloud_root_missing / nextcloud_error.
        """
        _offer_for_folder(conn, offer_id)
        client = _client()
        try:
            found = client.list_offer_files(offer_id)
        except (NextcloudError, ValueError) as exc:
            raise _folder_error(exc) from None
        if found is None:
            raise FolderError(
                404,
                "folder_not_found",
                f"Aucun dossier Nextcloud pour l'offre n° {offer_id} dans « {client.root or '/'} » "
                "(nom attendu commençant par son numéro, ex. « "
                f"{offer_id:02d} - … »).",
            )
        return _folder_out(client, *found)

    @api.post(
        "/offers/{offer_id}/folder",
        response_model=FolderOut,
        status_code=201,
        tags=["dossiers"],
        responses={
            200: {
                "description": "Dossier existant réutilisé, jamais renommé ; CV/, Lettre/ ajoutés si absents."
            }
        },
    )
    def create_folder(offer_id: OfferId, conn: Conn, response: Response) -> dict[str, Any]:
        """Crée le dossier « NN - <intitulé> <entreprise> » (avec CV/ et Lettre/) s'il n'existe pas.

        201 si créé, 200 si un dossier portant ce numéro existait (jamais renommé ; seuls CV/ ou
        Lettre/ manquants y sont ajoutés, rien d'existant n'est modifié).
        """
        offer = _offer_for_folder(conn, offer_id)
        client = _client()
        try:
            name, created, added = client.create_offer_folder(offer)
            found = client.list_offer_files(offer_id)
        except (NextcloudError, ValueError) as exc:
            raise _folder_error(exc) from None
        if not created:
            response.status_code = 200
            if added:
                logger.info(
                    "Dossier Nextcloud de l'offre n° %s : ajout de %s.", offer_id, ", ".join(added)
                )
        if found is None:  # créé mais pas encore visible : lien sans contenu
            return {"name": name, "url": client.web_url(name), "files": [], "created": created}
        return {**_folder_out(client, *found), "created": created}

    @api.get("/offers/{offer_id}", response_model=OfferOut, tags=["offres"])
    def get_offer(offer_id: OfferId, conn: Conn) -> dict[str, Any]:
        return _out(conn, offer_id)

    @api.patch("/offers/{offer_id}", response_model=OfferOut, tags=["offres"])
    def patch_offer(offer_id: OfferId, payload: OfferUpdate, conn: Conn) -> dict[str, Any]:
        """Mise à jour partielle : seuls les champs fournis changent ; null efface le champ."""
        if not repository.update_offer(conn, offer_id, payload.changes()):
            raise _not_found(offer_id)
        return _out(conn, offer_id)

    @api.put("/offers/{offer_id}", response_model=OfferOut, tags=["offres"])
    def put_offer(offer_id: OfferId, payload: OfferReplace, conn: Conn) -> dict[str, Any]:
        """Remplacement complet : les champs absents reprennent leur valeur par défaut."""
        if not repository.replace_offer(conn, offer_id, payload.model_dump()):
            raise _not_found(offer_id)
        return _out(conn, offer_id)

    @api.delete("/offers/{offer_id}", status_code=204, tags=["offres"])
    def delete_offer(offer_id: OfferId, conn: Conn) -> Response:
        if not repository.delete_offer(conn, offer_id):
            raise _not_found(offer_id)
        return Response(status_code=204)

    @api.get("/stats", response_model=StatsOut, tags=["statistiques"])
    def get_stats(conn: Conn) -> dict[str, Any]:
        return repository.stats(conn, follow_up_days)

    @api.get("/meta", response_model=MetaOut, tags=["référentiels"])
    def get_meta() -> dict[str, Any]:
        return {
            "statuses": list(STATUSES),
            "status_unset_label": STATUS_UNSET,
            "regions": list(geo.REGIONS),
            "out_of_france_label": geo.HORS_FRANCE,
            "no_department_label": NO_DEPARTMENT,
            "departments": [
                {"code": d["code"], "name": d["name"], "region": d["region_name"]} for d in geo.DEPARTMENTS
            ],
            "follow_up_days": follow_up_days,
            "nextcloud_enabled": nc_client is not None,
            "nextcloud_error": nc_error,
        }

    @api.get(
        "/export.xlsx",
        tags=["export"],
        response_class=Response,
        responses={200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "Classeur Excel (format v2)."}},
    )
    def export_xlsx(conn: Conn) -> Response:
        """Toutes les offres dans un classeur identique au suivi Excel v2 (Suivi, Synthèse, Listes)."""
        offers = [repository.row_to_offer(r, follow_up_days) for r in repository.list_rows(conn)]
        filename = f"suivi-offres-{date.today().isoformat()}.xlsx"
        return Response(
            content=workbook_bytes(offers, follow_up_days),
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    app.include_router(api)

    frontend = settings.frontend_dir or DEFAULT_FRONTEND_DIR
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")

    return app


def app_from_env() -> FastAPI:
    """Point d'entrée uvicorn : ``uvicorn backend.app.main:app_from_env --factory``."""
    return create_app(get_settings())
