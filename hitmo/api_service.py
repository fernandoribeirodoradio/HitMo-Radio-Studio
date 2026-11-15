from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import ssl
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .database import Database, default_data_dir

PASSWORD_ITERATIONS = 310_000
MAX_BODY_BYTES = 1_048_576
ROLE_SCOPES = {
    "admin": {"catalog.read", "catalog.write", "program.read", "program.write", "history.read", "clients.read"},
    "editor": {"catalog.read", "catalog.write", "program.read", "program.write", "history.read"},
    "reader": {"catalog.read", "program.read", "history.read"},
}


class APIError(Exception):
    def __init__(self, status: int, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or {}


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        raise ValueError("A senha deve ter entre 12 e 1024 caracteres.")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return salt, digest


def create_api_user(db: Database, username: str, password: str, role: str = "reader") -> None:
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,64}", username):
        raise ValueError("Usuário deve ter 3 a 64 caracteres alfanuméricos, ponto, hífen, _ ou @.")
    if role not in ROLE_SCOPES:
        raise ValueError("Papel deve ser admin, editor ou reader.")
    salt, digest = hash_password(password)
    try:
        with db.connect() as con:
            con.execute(
                "INSERT INTO api_users(username,salt,password_hash,role) VALUES(?,?,?,?)",
                (username, salt, digest, role),
            )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise ValueError("Esse usuário já existe.") from exc
        raise


def verify_api_user(db: Database, username: str, password: str) -> dict[str, Any] | None:
    user = db.one("SELECT id,username,salt,password_hash,role,active FROM api_users WHERE username=? COLLATE NOCASE", (username,))
    if not user or not user["active"]:
        # Keep failed-user timing close to the valid-user password check.
        salt, expected = hash_password("invalid-placeholder-password")
        hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, PASSWORD_ITERATIONS)
        return None
    if not isinstance(password, str) or len(password) > 1024:
        return None
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes(user["salt"]), PASSWORD_ITERATIONS)
    if not hmac.compare_digest(candidate, bytes(user["password_hash"])):
        return None
    return {"id": int(user["id"]), "username": user["username"], "role": user["role"]}


def _jsonable(row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _material_json(row) -> dict[str, Any]:
    result = _jsonable(row)
    path = str(result.get("path") or "")
    try:
        result["path_exists_on_server"] = bool(path and not path.startswith("hitmo://") and Path(path).is_file())
    except OSError:
        result["path_exists_on_server"] = False
    return result


def _parse_days(value: Any) -> list[int]:
    if not isinstance(value, list) or not value:
        raise APIError(422, "days deve ser uma lista não vazia de dias 0–6.")
    if any(type(day) is not int or day not in range(7) for day in value):
        raise APIError(422, "days deve conter apenas inteiros de 0 a 6.")
    return sorted(set(value))


def validate_program_payload(db: Database, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("blocks"), list):
        raise APIError(422, "O corpo deve conter uma lista blocks.")
    if len(value["blocks"]) > 200:
        raise APIError(422, "Limite de 200 blocos por revisão.")
    blocks = []
    for index, source in enumerate(value["blocks"]):
        if not isinstance(source, dict):
            raise APIError(422, f"Bloco {index} inválido.")
        name = str(source.get("name", "")).strip()
        block_type = source.get("block_type")
        time_of_day = str(source.get("time_of_day", ""))
        if not name or len(name) > 200:
            raise APIError(422, f"Bloco {index}: nome obrigatório (até 200 caracteres).")
        if block_type not in {"Música", "Comercial"}:
            raise APIError(422, f"Bloco {index}: block_type deve ser Música ou Comercial.")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_of_day):
            raise APIError(422, f"Bloco {index}: horário inválido, use HH:MM.")
        days = _parse_days(source.get("days"))
        items = source.get("items", [])
        if not isinstance(items, list) or len(items) > 500:
            raise APIError(422, f"Bloco {index}: items deve ser uma lista com até 500 IDs.")
        media_items = []
        for item_id in items:
            if type(item_id) is not int or item_id <= 0:
                raise APIError(422, f"Bloco {index}: ID de material inválido.")
            media = db.one(
                "SELECT m.id,m.category,COALESCE(c.material_kind,'audio') AS material_kind "
                "FROM media m LEFT JOIN material_categories c ON c.name=m.category "
                "WHERE m.id=? AND m.active=1", (item_id,),
            )
            if not media:
                raise APIError(422, f"Material {item_id} não existe ou está inativo.")
            if media["material_kind"] != "audio":
                raise APIError(422, f"Material {item_id} não é áudio executável.")
            if block_type == "Comercial" and media["category"] not in {"Comercial", "Vinheta"}:
                raise APIError(422, f"Material {item_id} não pode entrar em bloco comercial.")
            if block_type == "Música" and media["category"] == "Comercial":
                raise APIError(422, f"Material comercial {item_id} não pode entrar em bloco musical.")
            media_items.append(item_id)
        block_id = source.get("id")
        if block_id is not None and (type(block_id) is not int or block_id <= 0):
            raise APIError(422, f"Bloco {index}: ID inválido.")
        blocks.append({
            "id": block_id,
            "name": name,
            "block_type": block_type,
            "time_of_day": time_of_day,
            "days": days,
            "enabled": bool(source.get("enabled", True)),
            "items": media_items,
        })
    return {"blocks": blocks}


class HitMoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, db: Database, logger: logging.Logger):
        super().__init__(address, HitMoRequestHandler)
        self.db = db
        self.logger = logger
        self.clients: dict[tuple[str, str], float] = {}
        self.clients_lock = threading.Lock()


class HitMoRequestHandler(BaseHTTPRequestHandler):
    server: HitMoHTTPServer
    server_version = "HitMoAPI/1"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        self.server.logger.info("%s %s", self.client_address[0], fmt % args)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, error: APIError) -> None:
        self._send(error.status, {"error": error.message, **error.details})

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise APIError(400, "Content-Length inválido.") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise APIError(413, "Corpo da requisição excede 1 MiB.")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise APIError(400, "JSON UTF-8 inválido.") from exc
        if not isinstance(value, dict):
            raise APIError(400, "O corpo JSON deve ser um objeto.")
        return value

    def _authenticate(self, scope: str | None = None) -> dict[str, Any]:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            raise APIError(401, "Autenticação Basic obrigatória.")
        try:
            username, password = base64.b64decode(header[6:], validate=True).decode("utf-8").split(":", 1)
        except (ValueError, UnicodeDecodeError) as exc:
            raise APIError(401, "Credenciais inválidas.") from exc
        user = verify_api_user(self.server.db, username, password)
        if not user:
            self.server.logger.warning("Autenticação recusada para %s de %s", username[:64], self.client_address[0])
            raise APIError(401, "Credenciais inválidas.")
        if scope and scope not in ROLE_SCOPES[user["role"]]:
            raise APIError(403, "Usuário sem permissão para esta operação.")
        with self.server.clients_lock:
            self.server.clients[(self.client_address[0], user["username"])] = time.monotonic()
        return user

    def _expected_revision(self, con, table: str) -> int:
        header = self.headers.get("If-Match", "").strip().strip('"')
        if not header.isdigit():
            raise APIError(428, "Envie If-Match com a revisão consultada.")
        current = int(con.execute(f"SELECT revision FROM {table} WHERE id=1").fetchone()["revision"])
        expected = int(header)
        if expected != current:
            raise APIError(409, "A revisão mudou; atualize os dados antes de gravar.", {"current_revision": current})
        return current

    @staticmethod
    def _audit(con, user: dict[str, Any], action: str, resource: str, resource_id: Any = "", detail: str = "") -> None:
        con.execute(
            "INSERT INTO api_audit_log(username,action,resource,resource_id,detail) VALUES(?,?,?,?,?)",
            (user["username"], action, resource, str(resource_id), detail[:2000]),
        )

    def do_GET(self) -> None:
        try:
            parsed = urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            if path == "/healthz":
                self._send(200, {"status": "ok", "version": __version__})
                return
            if path == "/api/v1/version":
                user = self._authenticate("catalog.read")
                self._send(200, {
                    "version": __version__,
                    "catalog_revision": int(self.server.db.one("SELECT revision FROM catalog_state WHERE id=1")["revision"]),
                    "program_revision": int(self.server.db.one("SELECT revision FROM program_state WHERE id=1")["revision"]),
                    "authenticated_as": user["username"],
                    "role": user["role"],
                })
                return
            if path == "/api/v1/categories":
                self._authenticate("catalog.read")
                active_only = query.get("include_inactive", ["0"])[0] != "1"
                sql = "SELECT * FROM material_categories" + (" WHERE active=1" if active_only else "") + " ORDER BY name COLLATE NOCASE"
                self._send(200, {"categories": [_jsonable(row) for row in self.server.db.all(sql)]})
                return
            if path == "/api/v1/folders":
                self._authenticate("catalog.read")
                active_only = query.get("include_inactive", ["0"])[0] != "1"
                sql = "SELECT f.*,c.name AS category FROM media_folders f LEFT JOIN material_categories c ON c.id=f.category_id"
                if active_only:
                    sql += " WHERE f.active=1"
                sql += " ORDER BY f.name COLLATE NOCASE"
                self._send(200, {"folders": [_jsonable(row) for row in self.server.db.all(sql)]})
                return
            if path == "/api/v1/playlist-references":
                self._authenticate("catalog.read")
                catalog = self.server.db.one("SELECT id,source_path,version,imported_at FROM playlist_catalogs ORDER BY imported_at DESC,id DESC LIMIT 1")
                if not catalog:
                    self._send(200, {"catalog": None, "entries": []})
                    return
                entries = self.server.db.all(
                    "SELECT playlist_id,title,type_code,target,icon_location,shortcut_arguments,shortcut_path_name,icon_index,output,total_files,dbf_id,target_exists,position "
                    "FROM playlist_folder_entries WHERE catalog_id=? ORDER BY position,playlist_id",
                    (int(catalog["id"]),),
                )
                self._send(200, {"catalog": _jsonable(catalog), "entries": [_jsonable(row) for row in entries]})
                return
            if path == "/api/v1/materials":
                self._authenticate("catalog.read")
                include_inactive = query.get("include_inactive", ["0"])[0] == "1"
                sql = (
                    "SELECT m.*,COALESCE(c.material_kind,'audio') AS material_kind "
                    "FROM media m LEFT JOIN material_categories c ON c.name=m.category"
                )
                if include_inactive:
                    # Inactive records are still visible to read-capable users; no deletion occurs.
                    rows = self.server.db.all(sql + " ORDER BY m.title COLLATE NOCASE")
                else:
                    rows = self.server.db.all(sql + " WHERE m.active=1 ORDER BY m.title COLLATE NOCASE")
                self._send(200, {"materials": [_material_json(row) for row in rows]})
                return
            if path == "/api/v1/programs":
                self._authenticate("program.read")
                blocks = self.server.db.all("SELECT * FROM program_blocks ORDER BY time_of_day,name COLLATE NOCASE")
                programs = []
                for block in blocks:
                    items = self.server.db.all(
                        "SELECT i.id AS item_id,i.position,m.id AS media_id,m.title,m.artist,m.category,m.path "
                        "FROM program_block_items i LEFT JOIN media m ON m.id=i.media_id WHERE i.block_id=? ORDER BY i.position,i.id",
                        (int(block["id"]),),
                    )
                    programs.append({**_jsonable(block), "items": [_jsonable(item) for item in items]})
                running = self.server.db.all(
                    "SELECT id,origin_type,block_id,timeline_event_id,scheduled_for,status,operator,program_revision_id "
                    "FROM program_occurrences WHERE status='running' ORDER BY scheduled_for,id"
                )
                state = self.server.db.one("SELECT revision FROM program_state WHERE id=1")
                self._send(200, {"saved_revision": int(state["revision"]), "blocks": programs, "running": [_jsonable(row) for row in running]})
                return
            if path == "/api/v1/program-drafts":
                user = self._authenticate("program.read")
                rows = self.server.db.all(
                    "SELECT id,name,owner,base_revision,draft_revision,status,created_at,updated_at,published_revision "
                    "FROM api_program_drafts WHERE owner=? OR ?='admin' ORDER BY id DESC",
                    (user["username"], user["role"]),
                )
                self._send(200, {"drafts": [_jsonable(row) for row in rows]})
                return
            if path == "/api/v1/history":
                self._authenticate("history.read")
                try:
                    limit = max(1, min(1000, int(query.get("limit", ["100"])[0])))
                    offset = max(0, int(query.get("offset", ["0"])[0]))
                except ValueError as exc:
                    raise APIError(400, "limit/offset devem ser inteiros.") from exc
                rows = self.server.db.all(
                    "SELECT id,media_id,title,artist,path,category,operator,started_at,ended_at,played_seconds,status,planned_for,source_type,error_message "
                    "FROM playback_history ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset),
                )
                self._send(200, {"history": [_jsonable(row) for row in rows], "limit": limit, "offset": offset})
                return
            if path == "/api/v1/clients":
                self._authenticate("clients.read")
                cutoff = time.monotonic() - 90
                with self.server.clients_lock:
                    for key in [key for key, last in self.server.clients.items() if last < cutoff]:
                        del self.server.clients[key]
                    clients = [{"address": address, "username": username} for address, username in sorted(self.server.clients)]
                self._send(200, {"clients": clients})
                return
            if path.startswith("/api/v1/program-drafts/"):
                user = self._authenticate("program.read")
                draft_id = self._path_id(path, "/api/v1/program-drafts/")
                row = self.server.db.one("SELECT * FROM api_program_drafts WHERE id=?", (draft_id,))
                if not row:
                    raise APIError(404, "Rascunho não encontrado.")
                self._assert_draft_access(row, user)
                result = _jsonable(row)
                result["payload"] = json.loads(result["payload"])
                self._send(200, result)
                return
            raise APIError(404, "Endpoint não encontrado.")
        except APIError as exc:
            self._error(exc)
        except Exception:
            self.server.logger.exception("Erro inesperado em GET %s", self.path)
            self._send(500, {"error": "Erro interno do serviço."})

    def do_POST(self) -> None:
        try:
            path = urlsplit(self.path).path.rstrip("/") or "/"
            if path == "/api/v1/materials":
                user = self._authenticate("catalog.write")
                body = self._body()
                media = self._validate_material(body)
                with self.server.db.connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    self._expected_revision(con, "catalog_state")
                    existing = con.execute("SELECT id FROM media WHERE path=? COLLATE NOCASE", (media["path"],)).fetchone()
                    if existing:
                        raise APIError(409, "Esse caminho já está cadastrado; edite o material existente.", {"id": int(existing["id"])})
                    try:
                        cur = con.execute(
                            "INSERT INTO media(path,title,artist,category,duration,modified,description,icon_path,text_content,folder_id,active) VALUES(?,?,?,?,?,?,?,?,?,?,1)",
                            (media["path"], media["title"], media["artist"], media["category"], media["duration"], media["modified"], media["description"], media["icon_path"], media["text_content"], media["folder_id"]),
                        )
                    except Exception as exc:
                        if "UNIQUE constraint failed" in str(exc):
                            raise APIError(409, "Esse caminho já está cadastrado; edite o material existente.") from exc
                        raise
                    ident = int(cur.lastrowid)
                    self._audit(con, user, "create", "media", ident, media["path"])
                revision = int(self.server.db.one("SELECT revision FROM catalog_state WHERE id=1")["revision"])
                self._send(201, {"id": ident, "revision": revision})
                return
            if path == "/api/v1/program-drafts":
                user = self._authenticate("program.write")
                body = self._body()
                name = str(body.get("name", "")).strip()
                if not name or len(name) > 200:
                    raise APIError(422, "Informe um nome de rascunho (até 200 caracteres).")
                try:
                    base_revision = int(body["base_revision"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise APIError(422, "base_revision é obrigatória.") from exc
                payload = validate_program_payload(self.server.db, body)
                with self.server.db.connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    current = int(con.execute("SELECT revision FROM program_state WHERE id=1").fetchone()["revision"])
                    if base_revision != current:
                        raise APIError(409, "A programação mudou; recarregue antes de criar o rascunho.", {"current_revision": current})
                    cur = con.execute(
                        "INSERT INTO api_program_drafts(name,owner,base_revision,payload) VALUES(?,?,?,?)",
                        (name, user["username"], base_revision, json.dumps(payload, ensure_ascii=False)),
                    )
                    draft_id = int(cur.lastrowid)
                    self._audit(con, user, "create", "program_draft", draft_id, f"base_revision={base_revision}")
                self._send(201, {"id": draft_id, "draft_revision": 1, "base_revision": base_revision, "status": "draft"})
                return
            if re.fullmatch(r"/api/v1/materials/\d+/(?:deactivate|restore)", path):
                user = self._authenticate("catalog.write")
                parts = path.split("/")
                media_id = int(parts[4])
                action = parts[5]
                with self.server.db.connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    self._expected_revision(con, "catalog_state")
                    row = con.execute("SELECT id,active FROM media WHERE id=?", (media_id,)).fetchone()
                    if not row:
                        raise APIError(404, "Material não encontrado.")
                    if action == "deactivate":
                        con.execute("UPDATE media SET active=0,deactivated_at=CURRENT_TIMESTAMP,revision=revision+1 WHERE id=? AND active=1", (media_id,))
                    else:
                        con.execute("UPDATE media SET active=1,deactivated_at='',revision=revision+1 WHERE id=? AND active=0", (media_id,))
                    self._audit(con, user, action, "media", media_id)
                revision = int(self.server.db.one("SELECT revision FROM catalog_state WHERE id=1")["revision"])
                self._send(200, {"id": media_id, "active": action == "restore", "revision": revision})
                return
            if re.fullmatch(r"/api/v1/program-drafts/\d+/publish", path):
                user = self._authenticate("program.write")
                draft_id = int(path.split("/")[4])
                self._publish_draft(draft_id, user)
                return
            raise APIError(404, "Endpoint não encontrado.")
        except APIError as exc:
            self._error(exc)
        except Exception:
            self.server.logger.exception("Erro inesperado em POST %s", self.path)
            self._send(500, {"error": "Erro interno do serviço."})

    def do_PUT(self) -> None:
        try:
            path = urlsplit(self.path).path.rstrip("/") or "/"
            if re.fullmatch(r"/api/v1/materials/\d+", path):
                user = self._authenticate("catalog.write")
                media_id = int(path.rsplit("/", 1)[1])
                body = self._body()
                old = self.server.db.one("SELECT * FROM media WHERE id=?", (media_id,))
                if not old:
                    raise APIError(404, "Material não encontrado.")
                merged = {key: body.get(key, old[key]) for key in ("path", "title", "artist", "category", "duration", "description", "icon_path", "text_content", "folder_id")}
                media = self._validate_material(merged)
                with self.server.db.connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    self._expected_revision(con, "catalog_state")
                    duplicate = con.execute("SELECT id FROM media WHERE path=? COLLATE NOCASE AND id<>?", (media["path"], media_id)).fetchone()
                    if duplicate:
                        raise APIError(409, "Esse caminho já pertence a outro cadastro.", {"id": int(duplicate["id"])})
                    try:
                        cur = con.execute(
                            "UPDATE media SET path=?,title=?,artist=?,category=?,duration=?,modified=?,description=?,icon_path=?,text_content=?,folder_id=?,revision=revision+1 WHERE id=?",
                            (media["path"], media["title"], media["artist"], media["category"], media["duration"], media["modified"], media["description"], media["icon_path"], media["text_content"], media["folder_id"], media_id),
                        )
                    except Exception as exc:
                        if "UNIQUE constraint failed" in str(exc):
                            raise APIError(409, "Esse caminho já pertence a outro cadastro.") from exc
                        raise
                    self._audit(con, user, "update", "media", media_id, media["path"])
                revision = int(self.server.db.one("SELECT revision FROM catalog_state WHERE id=1")["revision"])
                self._send(200, {"id": media_id, "revision": revision})
                return
            if re.fullmatch(r"/api/v1/program-drafts/\d+", path):
                user = self._authenticate("program.write")
                draft_id = int(path.rsplit("/", 1)[1])
                body = self._body()
                payload = validate_program_payload(self.server.db, body)
                expected = self.headers.get("If-Match", "").strip().strip('"')
                if not expected.isdigit():
                    raise APIError(428, "Envie If-Match com a revisão do rascunho.")
                with self.server.db.connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    draft = con.execute("SELECT * FROM api_program_drafts WHERE id=?", (draft_id,)).fetchone()
                    if not draft:
                        raise APIError(404, "Rascunho não encontrado.")
                    self._assert_draft_access(draft, user)
                    if draft["status"] != "draft":
                        raise APIError(409, "Somente rascunhos podem ser editados.")
                    if int(expected) != int(draft["draft_revision"]):
                        raise APIError(409, "O rascunho foi editado por outro cliente.", {"current_revision": int(draft["draft_revision"])})
                    con.execute(
                        "UPDATE api_program_drafts SET payload=?,draft_revision=draft_revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (json.dumps(payload, ensure_ascii=False), draft_id),
                    )
                    self._audit(con, user, "update", "program_draft", draft_id, f"draft_revision={int(expected)+1}")
                self._send(200, {"id": draft_id, "draft_revision": int(expected) + 1})
                return
            raise APIError(404, "Endpoint não encontrado.")
        except APIError as exc:
            self._error(exc)
        except Exception:
            self.server.logger.exception("Erro inesperado em PUT %s", self.path)
            self._send(500, {"error": "Erro interno do serviço."})

    def _validate_material(self, body: dict[str, Any]) -> dict[str, Any]:
        path = body.get("path")
        title = str(body.get("title", "")).strip()
        artist = str(body.get("artist", "")).strip()
        category = str(body.get("category", "")).strip()
        if path is None:
            path = ""
        if not isinstance(path, str) or len(path) > 4096:
            raise APIError(422, "path deve ser um caminho (máximo 4096 caracteres).")
        if not title or len(title) > 500:
            raise APIError(422, "title é obrigatório (máximo 500 caracteres).")
        if not category or len(category) > 200:
            raise APIError(422, "category é obrigatória (máximo 200 caracteres).")
        category_row = self.server.db.one("SELECT material_kind FROM material_categories WHERE name=? AND active=1", (category,))
        if not category_row:
            raise APIError(422, "Categoria ativa não encontrada.")
        material_kind = category_row["material_kind"]
        folder_id = body.get("folder_id")
        if folder_id in (None, ""):
            folder_id = None
        else:
            try:
                folder_id = int(folder_id)
            except (TypeError, ValueError) as exc:
                raise APIError(422, "folder_id deve ser o ID de uma pasta cadastrada.") from exc
            folder = self.server.db.one(
                "SELECT f.active,c.name AS category FROM media_folders f LEFT JOIN material_categories c ON c.id=f.category_id WHERE f.id=?",
                (folder_id,),
            )
            if not folder or not folder["active"]:
                raise APIError(422, "A pasta selecionada não existe ou está inativa.")
            if folder["category"] and folder["category"] != category:
                raise APIError(422, "A categoria do material não corresponde à categoria da pasta.")
        text_content = str(body.get("text_content", ""))
        if len(text_content.encode("utf-8")) > 512_000:
            raise APIError(422, "text_content excede o limite de 512 KiB.")
        if material_kind == "audio" and text_content:
            raise APIError(422, "text_content só pode ser usado em uma categoria não sonora.")
        path = path.strip()
        if path.startswith("hitmo://"):
            if material_kind == "audio" or not re.fullmatch(r"hitmo://(?:text|material)/[A-Za-z0-9_.-]{1,120}", path):
                raise APIError(422, "Referência lógica inválida para este tipo de material.")
            resolved = path
            modified = 0.0
        elif path:
            resolved = str(Path(path).expanduser().resolve())
            try:
                modified = Path(resolved).stat().st_mtime
            except OSError:
                # Keep the catalog record and ID when a local or shared path is offline.
                modified = 0.0
        elif material_kind == "audio":
            raise APIError(422, "Materiais de áudio precisam de um caminho físico.")
        else:
            token = secrets.token_hex(12)
            resolved = f"hitmo://{'text' if material_kind == 'text' else 'material'}/{token}"
            modified = 0.0
        try:
            duration = max(0.0, float(body.get("duration", 0) or 0))
        except (TypeError, ValueError) as exc:
            raise APIError(422, "duration deve ser numérica.") from exc
        if duration > 86_400:
            raise APIError(422, "duration excede 24 horas.")
        return {
            "path": resolved, "title": title, "artist": artist[:500], "category": category,
            "duration": duration, "modified": modified,
            "description": str(body.get("description", ""))[:4000],
            "icon_path": str(body.get("icon_path", ""))[:4096],
            "text_content": text_content,
            "folder_id": folder_id,
        }

    @staticmethod
    def _path_id(path: str, prefix: str) -> int:
        tail = path[len(prefix):]
        if not tail.isdigit():
            raise APIError(404, "Endpoint não encontrado.")
        return int(tail)

    @staticmethod
    def _assert_draft_access(row, user: dict[str, Any]) -> None:
        if row["owner"] != user["username"] and user["role"] != "admin":
            raise APIError(403, "Este rascunho pertence a outro usuário.")

    def _publish_draft(self, draft_id: int, user: dict[str, Any]) -> None:
        expected_draft = self.headers.get("If-Match", "").strip().strip('"')
        if not expected_draft.isdigit():
            raise APIError(428, "Envie If-Match com a revisão do rascunho.")
        result: dict[str, Any] = {}
        with self.server.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            draft = con.execute("SELECT * FROM api_program_drafts WHERE id=?", (draft_id,)).fetchone()
            if not draft:
                raise APIError(404, "Rascunho não encontrado.")
            self._assert_draft_access(draft, user)
            if draft["status"] != "draft":
                raise APIError(409, "Somente rascunhos podem ser publicados.")
            if int(expected_draft) != int(draft["draft_revision"]):
                raise APIError(409, "O rascunho foi alterado; recarregue antes de publicar.", {"current_revision": int(draft["draft_revision"])})
            current = int(con.execute("SELECT revision FROM program_state WHERE id=1").fetchone()["revision"])
            if current != int(draft["base_revision"]):
                raise APIError(409, "A programação salva mudou após a criação do rascunho.", {"current_revision": current})
            payload = json.loads(draft["payload"])
            # Catalog records can change while a draft is being reviewed. Recheck
            # every referenced item inside the publish transaction so a material
            # deactivated after draft validation cannot enter the live schedule.
            for block in payload["blocks"]:
                for media_id in block["items"]:
                    media = con.execute(
                        "SELECT m.category,COALESCE(c.material_kind,'audio') AS material_kind "
                        "FROM media m LEFT JOIN material_categories c ON c.name=m.category "
                        "WHERE m.id=? AND m.active=1",
                        (media_id,),
                    ).fetchone()
                    if not media or media["material_kind"] != "audio":
                        raise APIError(409, f"O material {media_id} foi desativado ou deixou de ser executável; atualize o rascunho.")
                    if block["block_type"] == "Comercial" and media["category"] not in {"Comercial", "Vinheta"}:
                        raise APIError(409, f"O material {media_id} não pode mais entrar no bloco comercial.")
                    if block["block_type"] == "Música" and media["category"] == "Comercial":
                        raise APIError(409, f"O material {media_id} não pode mais entrar no bloco musical.")
            existing = {int(row["id"]) for row in con.execute("SELECT id FROM program_blocks")}
            con.execute("UPDATE program_blocks SET enabled=0")
            saved_ids = []
            for block in payload["blocks"]:
                block_id = block.get("id")
                values = (block["name"], block["block_type"], block["time_of_day"], ",".join(str(day) for day in block["days"]), int(block["enabled"]))
                if block_id is not None:
                    if int(block_id) not in existing:
                        raise APIError(409, f"O bloco {block_id} não existe mais.")
                    con.execute("UPDATE program_blocks SET name=?,block_type=?,time_of_day=?,days=?,enabled=? WHERE id=?", (*values, int(block_id)))
                    ident = int(block_id)
                else:
                    cur = con.execute("INSERT INTO program_blocks(name,block_type,time_of_day,days,enabled) VALUES(?,?,?,?,?)", values)
                    ident = int(cur.lastrowid)
                saved_ids.append(ident)
                con.execute("DELETE FROM program_block_items WHERE block_id=?", (ident,))
                for position, media_id in enumerate(block["items"]):
                    con.execute("INSERT INTO program_block_items(block_id,media_id,position,item_kind) VALUES(?,?,?,'media')", (ident, media_id, position))
            revision = int(con.execute("SELECT revision FROM program_state WHERE id=1").fetchone()["revision"])
            con.execute("UPDATE api_program_drafts SET status='published',published_revision=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (revision, draft_id))
            self._audit(con, user, "publish", "program_draft", draft_id, f"program_revision={revision}; blocks={saved_ids}")
            result = {"draft_id": draft_id, "published_revision": revision, "block_ids": saved_ids}
        self._send(200, result)

    def _run_verb(self, method: str) -> None:
        self._send(405, {"error": "Método não permitido."})

    def do_DELETE(self) -> None:
        self._send(405, {"error": "Exclusão física não é disponibilizada pela API."})


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_server(db: Database, host: str = "127.0.0.1", port: int = 8765, cert: str = "", key: str = "") -> HitMoHTTPServer:
    if not _is_loopback_host(host) and (not cert or not key):
        raise ValueError("Para aceitar conexões LAN, configure certificado TLS (--cert/--key). HTTP sem TLS fica limitado a loopback.")
    logger = logging.getLogger("hitmo.api")
    server = HitMoHTTPServer((host, port), db, logger)
    if cert or key:
        if not cert or not key:
            server.server_close()
            raise ValueError("Certificado e chave TLS devem ser informados juntos.")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert, keyfile=key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def configure_logging() -> logging.Logger:
    folder = default_data_dir() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hitmo.api")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(folder / "api.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.addHandler(logging.StreamHandler())
    return logger
