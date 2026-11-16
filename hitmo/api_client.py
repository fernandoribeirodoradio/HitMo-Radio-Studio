from __future__ import annotations

import base64
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HitMoAPIClient:
    """Small standard-library client for the separately hosted central API."""

    def __init__(self, base_url: str, username: str, password: str, timeout: float = 3.0, ca_file: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = max(0.25, min(float(timeout), 30.0))
        self.ssl_context = ssl.create_default_context(cafile=ca_file) if self.base_url.lower().startswith("https://") else None

    def _open(self, request):
        if self.ssl_context is not None:
            return urlopen(request, timeout=self.timeout, context=self.ssl_context)
        return urlopen(request, timeout=self.timeout)

    def request(self, method: str, path: str, *, body: dict | None = None, revision: int | None = None):
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        credentials = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        headers = {"Authorization": f"Basic {credentials}", "Accept": "application/json"}
        if raw is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if revision is not None:
            headers["If-Match"] = str(int(revision))
        req = Request(f"{self.base_url}{path}", data=raw, headers=headers, method=method.upper())
        try:
            with self._open(req) as response:
                payload = response.read(1_048_577)
                if len(payload) > 1_048_576:
                    raise RuntimeError("Resposta da API excede 1 MiB.")
                return json.loads(payload.decode("utf-8")) if payload else {}
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read(1_048_577).decode("utf-8"))
            except Exception:
                payload = {"error": f"HTTP {exc.code}"}
            raise RuntimeError(f"API HitMo {exc.code}: {payload.get('error', 'erro')}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(f"Serviço central HitMo indisponível: {exc}") from exc

    def health(self):
        # Health endpoint is intentionally unauthenticated for availability checks.
        req = Request(f"{self.base_url}/healthz", headers={"Accept": "application/json"})
        try:
            with self._open(req) as response:
                return json.loads(response.read(4096).decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(f"Serviço central HitMo indisponível: {exc}") from exc

    def version(self):
        return self.request("GET", "/api/v1/version")

    def categories(self, include_inactive: bool = False):
        flag = "1" if include_inactive else "0"
        return self.request("GET", f"/api/v1/categories?include_inactive={flag}")

    def folders(self, include_inactive: bool = False):
        flag = "1" if include_inactive else "0"
        return self.request("GET", f"/api/v1/folders?include_inactive={flag}")

    def playlist_references(self):
        return self.request("GET", "/api/v1/playlist-references")

    def materials(self, include_inactive: bool = False):
        flag = "1" if include_inactive else "0"
        return self.request("GET", f"/api/v1/materials?include_inactive={flag}")

    def add_material(self, material: dict, catalog_revision: int):
        return self.request("POST", "/api/v1/materials", body=material, revision=catalog_revision)

    def update_material(self, material_id: int, material: dict, catalog_revision: int):
        return self.request("PUT", f"/api/v1/materials/{int(material_id)}", body=material, revision=catalog_revision)

    def deactivate_material(self, material_id: int, catalog_revision: int):
        return self.request("POST", f"/api/v1/materials/{int(material_id)}/deactivate", body={}, revision=catalog_revision)

    def restore_material(self, material_id: int, catalog_revision: int):
        return self.request("POST", f"/api/v1/materials/{int(material_id)}/restore", body={}, revision=catalog_revision)

    def programs(self):
        return self.request("GET", "/api/v1/programs")

    def occurrences(self, start_date: str = "", end_date: str = "", status: str = "", limit: int = 200, offset: int = 0):
        from urllib.parse import urlencode
        params = {"from": start_date, "to": end_date, "status": status, "limit": int(limit), "offset": int(offset)}
        return self.request("GET", "/api/v1/occurrences?" + urlencode({key: value for key, value in params.items() if value != ""}))

    def drafts(self):
        return self.request("GET", "/api/v1/program-drafts")

    def get_draft(self, draft_id: int):
        return self.request("GET", f"/api/v1/program-drafts/{int(draft_id)}")

    def create_draft(self, name: str, base_revision: int, blocks: list[dict]):
        return self.request("POST", "/api/v1/program-drafts", body={"name": name, "base_revision": int(base_revision), "blocks": blocks})

    def update_draft(self, draft_id: int, draft_revision: int, blocks: list[dict]):
        return self.request("PUT", f"/api/v1/program-drafts/{int(draft_id)}", body={"blocks": blocks}, revision=draft_revision)

    def publish_draft(self, draft_id: int, draft_revision: int):
        return self.request("POST", f"/api/v1/program-drafts/{int(draft_id)}/publish", body={}, revision=draft_revision)

    def history(self, limit: int = 100, offset: int = 0):
        return self.request("GET", f"/api/v1/history?limit={int(limit)}&offset={int(offset)}")

    def clients(self):
        return self.request("GET", "/api/v1/clients")
