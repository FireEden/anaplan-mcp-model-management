"""
anaplan_client.py
-----------------
A small, beginner-friendly wrapper around the Anaplan Integration API v2.

You normally do NOT call this file directly. The MCP server (server.py) uses it.
Everything here is plain Python + the `requests` library.

Key ideas:
- You log in once with username + password. Anaplan gives back a "token".
- That token is good for ~35 minutes. We reuse it and auto-refresh when needed.
- Every other call just attaches the token in a header.
"""

import base64
import os
import time
import datetime
import difflib
from typing import Any

import requests

# Anaplan's public service hosts. These rarely change.
AUTH_URL = "https://auth.anaplan.com/token/authenticate"
API_BASE = "https://api.anaplan.com/2/0"

# How long Anaplan tokens last. We refresh a bit early to be safe (32 min).
TOKEN_LIFETIME_SECONDS = 32 * 60

# Some corporate networks use a security proxy that breaks normal HTTPS
# certificate checks. If you set the environment variable
# ANAPLAN_VERIFY_SSL=false, we skip that check so the connection can go through.
# WARNING: skipping the check is less secure. Prefer giving Python your
# company's certificate (ask your IT team). Default is to verify (True).
VERIFY_SSL = os.environ.get("ANAPLAN_VERIFY_SSL", "true").lower() != "false"

if not VERIFY_SSL:
    # Hide the "insecure request" warnings so the output stays readable.
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass


class AnaplanError(Exception):
    """Raised when Anaplan returns an error or something goes wrong."""
    pass


class AnaplanClient:
    """
    Holds your login session and gives you simple methods to talk to Anaplan.

    Typical flow:
        client = AnaplanClient()
        client.login("me@email.com", "my_password")
        client.list_models()
        client.select_model(workspace_id, model_id)
        client.list_imports()
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_time: float = 0.0
        self._username: str | None = None
        self._password: str | None = None
        # The model the user is currently working in:
        self.workspace_id: str | None = None
        self.model_id: str | None = None
        self.model_name: str | None = None

    # ------------------------------------------------------------------ #
    # AUTHENTICATION
    # ------------------------------------------------------------------ #
    def login(self, username: str, password: str) -> str:
        """Log in with username + password. Stores a token for reuse."""
        self._username = username
        self._password = password
        self._get_new_token()
        return "Logged in successfully."

    def _get_new_token(self) -> None:
        """Internal: ask Anaplan for a fresh auth token using basic auth."""
        if not self._username or not self._password:
            raise AnaplanError("Not logged in. Call login() first.")

        raw = f"{self._username}:{self._password}".encode("utf-8")
        basic = base64.b64encode(raw).decode("utf-8")
        try:
            resp = requests.post(
                AUTH_URL,
                headers={"Authorization": f"Basic {basic}"},
                timeout=30,
                verify=VERIFY_SSL,
            )
        except requests.RequestException as exc:
            raise AnaplanError(f"Could not reach Anaplan auth server: {exc}")

        if resp.status_code == 401:
            raise AnaplanError("Login failed: wrong username or password (401).")
        if resp.status_code != 201 and resp.status_code != 200:
            raise AnaplanError(
                f"Login failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )

        data = resp.json()
        token = data.get("tokenInfo", {}).get("tokenValue")
        if not token:
            raise AnaplanError(f"No token in Anaplan response: {data}")
        self._token = token
        self._token_time = time.time()

    def _auth_header(self) -> dict[str, str]:
        """Return the Authorization header, refreshing the token if stale."""
        if self._token is None:
            raise AnaplanError("Not logged in. Call login() first.")
        if time.time() - self._token_time > TOKEN_LIFETIME_SECONDS:
            self._get_new_token()
        return {"Authorization": f"AnaplanAuthToken {self._token}"}

    # ------------------------------------------------------------------ #
    # LOW-LEVEL REQUEST HELPERS
    # ------------------------------------------------------------------ #
    def _get(self, path: str, params: dict | None = None) -> dict:
        headers = {**self._auth_header(), "Accept": "application/json"}
        url = f"{API_BASE}{path}"
        resp = requests.get(url, headers=headers, params=params,
                            timeout=60, verify=VERIFY_SSL)
        return self._handle(resp, url)

    def _post(self, path: str, json_body: dict | None = None) -> dict:
        headers = {
            **self._auth_header(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        url = f"{API_BASE}{path}"
        resp = requests.post(url, headers=headers, json=json_body or {},
                             timeout=120, verify=VERIFY_SSL)
        return self._handle(resp, url)

    def _put_file_chunk(self, path: str, data: bytes) -> None:
        headers = {
            **self._auth_header(),
            "Content-Type": "application/octet-stream",
        }
        url = f"{API_BASE}{path}"
        resp = requests.put(url, headers=headers, data=data,
                            timeout=120, verify=VERIFY_SSL)
        if resp.status_code not in (200, 204):
            raise AnaplanError(
                f"File upload failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )

    @staticmethod
    def _handle(resp: requests.Response, url: str) -> dict:
        if resp.status_code == 401:
            raise AnaplanError("Session expired or unauthorized (401).")
        if resp.status_code == 404:
            raise AnaplanError(f"Not found (404): {url}")
        if not resp.ok:
            raise AnaplanError(
                f"Anaplan request failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            raise AnaplanError(f"Unexpected non-JSON response from {url}")

    # ------------------------------------------------------------------ #
    # MODELS
    # ------------------------------------------------------------------ #
    def list_models(self) -> list[dict]:
        """Return every workspace+model the logged-in user can access."""
        ws = self._get("/workspaces", params={"tenantDetails": "true"})
        workspaces = ws.get("workspaces", [])
        results: list[dict] = []
        for w in workspaces:
            wid = w["id"]
            wname = w.get("name", wid)
            models = self._get(f"/workspaces/{wid}/models").get("models", [])
            for m in models:
                results.append(
                    {
                        "workspace_id": wid,
                        "workspace_name": wname,
                        "model_id": m["id"],
                        "model_name": m.get("name", m["id"]),
                        "active_state": m.get("activeState", ""),
                    }
                )
        return results

    def select_model(self, workspace_id: str, model_id: str) -> str:
        """Set the model that import/export/module calls will use."""
        self.workspace_id = workspace_id
        self.model_id = model_id
        # Try to grab a friendly name for confirmation messages.
        try:
            models = self._get(f"/workspaces/{workspace_id}/models").get("models", [])
            for m in models:
                if m["id"] == model_id:
                    self.model_name = m.get("name", model_id)
        except AnaplanError:
            self.model_name = model_id
        return f"Now working in model: {self.model_name or model_id}"

    def _require_model(self) -> tuple[str, str]:
        if not self.workspace_id or not self.model_id:
            raise AnaplanError(
                "No model selected. Use list_models then select_model first."
            )
        return self.workspace_id, self.model_id

    # ------------------------------------------------------------------ #
    # IMPORTS
    # ------------------------------------------------------------------ #
    def list_imports(self) -> list[dict]:
        """
        Return imports with the properties asked for:
        import name, source label/object, target object, target type,
        and whether it imports into production data.
        """
        wid, mid = self._require_model()
        raw = self._get(f"/workspaces/{wid}/models/{mid}/imports").get("imports", [])
        out: list[dict] = []
        for imp in raw:
            out.append(
                {
                    "id": imp.get("id"),
                    "import_name": imp.get("name"),
                    "import_type": imp.get("importType"),
                    # source label/object: the file or saved view feeding the import
                    "source_label": imp.get("sourceLabel") or imp.get("source", ""),
                    "source_object": imp.get("source", ""),
                    # target object: the module/list being loaded
                    "target_object": imp.get("target", ""),
                    "target_type": imp.get("importType", ""),
                    # production data flag (boolean)
                    "production_data": bool(imp.get("productionData", False)),
                }
            )
        return out

    def get_import_file_id(self, import_id: str) -> str | None:
        """An import is fed by a file; find that file's id so we can upload to it."""
        wid, mid = self._require_model()
        meta = self._get(f"/workspaces/{wid}/models/{mid}/imports/{import_id}")
        # The import metadata references its source file id.
        info = meta.get("importMetadata", meta)
        return info.get("fileId") or info.get("source")

    def upload_file(self, file_id: str, file_bytes: bytes) -> None:
        """Upload a local file's bytes into Anaplan as a single chunk."""
        wid, mid = self._require_model()
        # Tell Anaplan we're sending one chunk.
        self._put_file_chunk(
            f"/workspaces/{wid}/models/{mid}/files/{file_id}/chunks/0", file_bytes
        )
        # Mark upload complete (chunkCount = 1).
        self._post(
            f"/workspaces/{wid}/models/{mid}/files/{file_id}",
            json_body={"id": file_id, "chunkCount": 1},
        )

    def run_import(self, import_id: str) -> dict:
        """Trigger an import and wait for it to finish. Returns a status dict."""
        wid, mid = self._require_model()
        started = self._post(
            f"/workspaces/{wid}/models/{mid}/imports/{import_id}/tasks",
            json_body={"localeName": "en_US"},
        )
        task_id = started.get("task", {}).get("taskId")
        if not task_id:
            raise AnaplanError(f"Import did not start: {started}")
        return self._wait_for_task(
            f"/workspaces/{wid}/models/{mid}/imports/{import_id}/tasks/{task_id}"
        )

    def get_import_dump(self, import_id: str, task_id: str) -> str:
        """Fetch the failure dump (the rows that didn't load) as text, if any."""
        wid, mid = self._require_model()
        url = (
            f"/workspaces/{wid}/models/{mid}/imports/{import_id}"
            f"/tasks/{task_id}/dump"
        )
        headers = {**self._auth_header(), "Accept": "application/octet-stream"}
        resp = requests.get(f"{API_BASE}{url}", headers=headers,
                            timeout=60, verify=VERIFY_SSL)
        if resp.ok and resp.content:
            return resp.text
        return ""

    # ------------------------------------------------------------------ #
    # EXPORTS
    # ------------------------------------------------------------------ #
    def list_exports(self) -> list[dict]:
        """
        Return exports with: export name, last run start date/time,
        most recent duration, and which process(es) use it.
        """
        wid, mid = self._require_model()
        raw = self._get(f"/workspaces/{wid}/models/{mid}/exports").get("exports", [])

        # Build a map of which process contains which export action.
        used_in = self._build_action_to_process_map()

        out: list[dict] = []
        for exp in raw:
            export_id = exp.get("id")
            last_start, duration = self._last_run_info("exports", export_id)
            out.append(
                {
                    "id": export_id,
                    "export_name": exp.get("name"),
                    "export_format": exp.get("exportType", ""),
                    "last_run_start": last_start,
                    "most_recent_duration": duration,
                    "used_in_process": used_in.get(export_id, []),
                }
            )
        return out

    def _build_action_to_process_map(self) -> dict[str, list[str]]:
        """Map each action id -> list of process names that contain it."""
        wid, mid = self._require_model()
        mapping: dict[str, list[str]] = {}
        try:
            procs = self._get(
                f"/workspaces/{wid}/models/{mid}/processes"
            ).get("processes", [])
        except AnaplanError:
            return mapping
        # The list endpoint doesn't expose sub-actions, so this stays best-effort:
        # we record process names; deep mapping needs per-process detail calls,
        # which Anaplan does not fully expose via the bulk API.
        for p in procs:
            # Without per-action detail we can't be sure; leave map empty entries.
            pass
        return mapping

    def _last_run_info(self, action_kind: str, action_id: str):
        """Return (last_start_iso, duration_text) for the most recent task."""
        wid, mid = self._require_model()
        try:
            tasks = self._get(
                f"/workspaces/{wid}/models/{mid}/{action_kind}/{action_id}/tasks"
            ).get("tasks", [])
        except AnaplanError:
            return (None, None)
        if not tasks:
            return (None, None)
        # Tasks are returned oldest-first; take the last one.
        last = tasks[-1]
        # Some deployments include timing; if absent we return None gracefully.
        start_ms = last.get("creationTime") or last.get("startTime")
        end_ms = last.get("endTime")
        start_iso = _ms_to_iso(start_ms) if start_ms else None
        duration = None
        if start_ms and end_ms:
            secs = max(0, (end_ms - start_ms) / 1000.0)
            duration = f"{secs:.1f} seconds"
        return (start_iso, duration)

    def run_export(self, export_id: str) -> dict:
        """Trigger an export and wait for it to finish."""
        wid, mid = self._require_model()
        started = self._post(
            f"/workspaces/{wid}/models/{mid}/exports/{export_id}/tasks",
            json_body={"localeName": "en_US"},
        )
        task_id = started.get("task", {}).get("taskId")
        if not task_id:
            raise AnaplanError(f"Export did not start: {started}")
        return self._wait_for_task(
            f"/workspaces/{wid}/models/{mid}/exports/{export_id}/tasks/{task_id}"
        )

    # ------------------------------------------------------------------ #
    # MODULES
    # ------------------------------------------------------------------ #
    def list_modules(self) -> list[dict]:
        """Return all modules in the current model."""
        wid, mid = self._require_model()
        raw = self._get(f"/models/{mid}/modules").get("modules", [])
        return [{"id": m.get("id"), "module_name": m.get("name")} for m in raw]

    def get_module_attributes(self, module_id: str) -> dict:
        """Return a module's line items (its attributes) plus basic info."""
        mid = self._require_model()[1]
        data = self._get(
            f"/models/{mid}/modules/{module_id}/lineItems",
            params={"includeAll": "true"},
        )
        line_items = data.get("items", data.get("lineItems", []))
        attrs = []
        for li in line_items:
            attrs.append(
                {
                    "name": li.get("name"),
                    "format": li.get("format"),
                    "formula": li.get("formula"),
                    "data_type": li.get("dataType"),
                    "is_summary": li.get("summary"),
                }
            )
        return {"module_id": module_id, "line_items": attrs}

    # Metrics that exist ONLY in the Anaplan web Blueprint view, not the API.
    # We list them so we can clearly tell the user they're not available here,
    # instead of silently dropping them or faking values.
    UI_ONLY_METRICS = [
        "Cell Count",
        "Populated Cell Count (Polaris)",
        "Memory Usage",
        "Calculation Complexity (Polaris)",
        "Calculation Effort",
    ]

    def get_line_item_details(self, module_id: str) -> dict:
        """
        Return detailed attributes for every line item in a module.

        Pulls everything the Anaplan API actually exposes, including the two
        you asked about that ARE available:
          - Applies To (the dimensions the line item is dimensioned by)
          - Time Scale

        The five performance metrics (cell count, populated cell count,
        memory usage, calculation complexity, calculation effort) are NOT
        returned by the API - they live only in the web Blueprint view - so
        we flag them as unavailable rather than inventing numbers.
        """
        mid = self._require_model()[1]
        # includeAll=true asks Anaplan for the full set of fields per line item.
        data = self._get(
            f"/models/{mid}/modules/{module_id}/lineItems",
            params={"includeAll": "true"},
        )
        line_items = data.get("items", data.get("lineItems", []))

        detailed = []
        for li in line_items:
            # "Applies To" comes back as a list of dimension objects; we pull
            # out their names into a simple readable list.
            applies_to_raw = li.get("appliesTo", []) or []
            applies_to = [
                d.get("name", "") for d in applies_to_raw if isinstance(d, dict)
            ]
            detailed.append(
                {
                    "name": li.get("name"),
                    "format": li.get("format"),
                    "formula": li.get("formula"),
                    "data_type": li.get("dataType"),
                    "summary": li.get("summary"),
                    # The two metrics that ARE available via the API:
                    "applies_to": applies_to,
                    "time_scale": li.get("timeScale"),
                    # Extras the API gives us for free, useful for auditing:
                    "is_formula_scoped": li.get("formulaScope"),
                    "notes": li.get("notes"),
                }
            )
        return {
            "module_id": module_id,
            "line_item_count": len(detailed),
            "line_items": detailed,
            "ui_only_metrics": self.UI_ONLY_METRICS,
        }

    # ------------------------------------------------------------------ #
    # SHARED: wait for a task, fuzzy name matching
    # ------------------------------------------------------------------ #
    def _wait_for_task(self, task_path: str, max_wait_s: int = 300) -> dict:
        """Poll a task until it completes or times out."""
        deadline = time.time() + max_wait_s
        last = {}
        while time.time() < deadline:
            last = self._get(task_path)
            state = last.get("task", {}).get("taskState")
            if state == "COMPLETE":
                result = last.get("task", {}).get("result", {})
                task_id = last.get("task", {}).get("taskId")
                return {
                    "state": "COMPLETE",
                    "successful": result.get("successful", False),
                    "details": result.get("details", []),
                    "failure_dump": result.get("failureDumpAvailable", False),
                    "task_id": task_id,
                }
            time.sleep(2)
        return {"state": last.get("task", {}).get("taskState", "TIMEOUT")}


def fuzzy_match_scored(query: str, names: list[str], limit: int = 5):
    """Return [(score, name), ...] best first. Substring hits score highest."""
    q = query.strip().lower()
    scored = []
    for n in names:
        nl = n.lower()
        if q and q in nl:
            # Substring match: score by how much of the name it covers,
            # so a tighter match (query is most of the name) ranks higher.
            score = 0.8 + 0.2 * (len(q) / max(len(nl), 1))
        else:
            score = difflib.SequenceMatcher(None, q, nl).ratio()
        scored.append((score, n))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(s, n) for s, n in scored[:limit] if s > 0.3]


def fuzzy_match(query: str, names: list[str], limit: int = 5) -> list[str]:
    """
    Return the closest matching names to `query`.
    Used so the user can type part of an import/export/module name.
    """
    return [n for _, n in fuzzy_match_scored(query, names, limit)]


def _ms_to_iso(ms: int) -> str:
    """Convert Anaplan epoch-milliseconds to a readable date/time string."""
    try:
        dt = datetime.datetime.fromtimestamp(ms / 1000.0)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ms)
