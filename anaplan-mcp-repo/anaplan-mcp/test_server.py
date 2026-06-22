"""
test_server.py
--------------
Tests the Anaplan MCP server WITHOUT a real Anaplan account.

We replace `requests` calls with a fake Anaplan that returns realistic JSON.
This proves the logic (login, model select, lists, fuzzy match, confirm-before-run,
file upload + import dump reporting, module attributes) all works end to end.
"""

import sys
import os
import types
import json

# ---- 1. Build a fake `requests` module BEFORE importing our code ----------- #

class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)
        self.content = self.text.encode()
    @property
    def ok(self):
        return 200 <= self.status_code < 300
    def json(self):
        return self._payload


WS = "ws123"
MD = "ABC123MODEL"


def fake_post(url, headers=None, json=None, data=None, timeout=None, verify=None):
    if url.endswith("/token/authenticate"):
        auth = headers.get("Authorization", "")
        if "Basic" not in auth:
            return FakeResponse(401, {}, "no basic")
        return FakeResponse(201, {"tokenInfo": {"tokenValue": "FAKE_TOKEN"}})
    # Start an export task
    if "/exports/" in url and url.endswith("/tasks"):
        return FakeResponse(200, {"task": {"taskId": "TASK_EXP"}})
    # Start an import task
    if "/imports/" in url and url.endswith("/tasks"):
        return FakeResponse(200, {"task": {"taskId": "TASK_IMP"}})
    # Mark file upload complete
    if "/files/" in url:
        return FakeResponse(200, {})
    return FakeResponse(200, {})


def fake_get(url, headers=None, params=None, timeout=None, verify=None):
    if url.endswith("/workspaces"):
        return FakeResponse(200, {"workspaces": [{"id": WS, "name": "Finance WS"}]})
    if url.endswith(f"/workspaces/{WS}/models"):
        return FakeResponse(200, {"models": [{"id": MD, "name": "FP&A Model"}]})
    if url.endswith("/imports"):
        return FakeResponse(200, {"imports": [
            {"id": "imp1", "name": "Load Actuals", "importType": "MODULE",
             "source": "Actuals.csv", "sourceLabel": "Actuals File",
             "target": "REV01 Revenue", "productionData": True},
            {"id": "imp2", "name": "Load Headcount", "importType": "LIST",
             "source": "HC.csv", "target": "Employees List",
             "productionData": False},
        ]})
    if url.endswith("/exports"):
        return FakeResponse(200, {"exports": [
            {"id": "exp1", "name": "Export Revenue", "exportType": "GRID_CURRENT_PAGE"},
            {"id": "exp2", "name": "Export Forecast", "exportType": "TABULAR_ALL"},
        ]})
    if url.endswith("/processes"):
        return FakeResponse(200, {"processes": [{"id": "p1", "name": "Daily Refresh"}]})
    # Import metadata (to find file id)
    if "/imports/imp" in url and "/tasks" not in url:
        return FakeResponse(200, {"importMetadata": {"fileId": "file1"}})
    # Export/import task polling
    if "/tasks/TASK_EXP" in url:
        return FakeResponse(200, {"task": {"taskId": "TASK_EXP", "taskState": "COMPLETE",
            "result": {"successful": True, "details": []}}})
    if "/tasks/TASK_IMP" in url:
        return FakeResponse(200, {"task": {"taskId": "TASK_IMP", "taskState": "COMPLETE",
            "result": {"successful": True, "details": [], "failureDumpAvailable": False}}})
    # Task list (for last-run info)
    if url.endswith("/tasks"):
        return FakeResponse(200, {"tasks": [
            {"creationTime": 1700000000000, "endTime": 1700000012500}
        ]})
    if "/modules/" in url and "lineItems" in url:
        return FakeResponse(200, {"items": [
            {"name": "Revenue", "format": "NUMBER", "formula": "Price * Units",
             "dataType": "NUMBER", "summary": "Sum",
             "appliesTo": [{"name": "Product"}, {"name": "Region"}],
             "timeScale": "Month"},
            {"name": "Price", "format": "NUMBER", "formula": "", "dataType": "NUMBER",
             "appliesTo": [{"name": "Product"}], "timeScale": "Year"},
        ]})
    if url.endswith("/modules"):
        return FakeResponse(200, {"modules": [
            {"id": "mod1", "name": "REV01 Revenue Calc"},
            {"id": "mod2", "name": "EXP01 Expense Detail"},
        ]})
    return FakeResponse(200, {})


def fake_put(url, headers=None, data=None, timeout=None, verify=None):
    return FakeResponse(204, {})


fake_requests = types.ModuleType("requests")
fake_requests.post = fake_post
fake_requests.get = fake_get
fake_requests.put = fake_put
class _RE(Exception):
    pass
fake_requests.RequestException = _RE
fake_requests.Response = FakeResponse
sys.modules["requests"] = fake_requests

# ---- 2. Now import the server (it will use our fake requests) -------------- #
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server as S  # noqa: E402


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        check.failures += 1
check.failures = 0


print("=== Anaplan MCP server test ===\n")

# 1. Login
r = S.anaplan_login("me@test.com", "pw")
check("login succeeds", "successfully" in r, r)

# 3. List + select model
r = S.anaplan_list_models()
check("list_models shows model + ids", "FP&A Model" in r and MD in r, r)
r = S.anaplan_select_model(WS, MD)
check("select_model works", "FP&A Model" in r, r)

# 4. List imports with properties
r = S.anaplan_list_imports()
check("imports show production_data flag", "production_data: True" in r, r)
check("imports show target_object", "REV01 Revenue" in r, r)

# 5. List exports with properties
r = S.anaplan_list_exports()
check("exports show last_run + duration", "12.5 seconds" in r, r)

# 6. Find + run export with confirm gate
r = S.anaplan_find_export("revenu")
check("fuzzy find export", "Export Revenue" in r, r)
r = S.anaplan_run_export("Export Revenue", confirm=False)
check("export blocked without confirm", "CONFIRM NEEDED" in r, r)
r = S.anaplan_run_export("Export Revenue", confirm=True)
check("export runs with confirm", "successful=True" in r, r)

# 7. Imports: confirm gate, file prompt, upload+run
r = S.anaplan_run_import("Load Actuals")
check("import prompts for file when none given", "needs a source file" in r, r)

# create a temp file to upload
with open("/tmp/sample.csv", "w") as f:
    f.write("a,b\n1,2\n")
r = S.anaplan_run_import("Load Actuals", file_path="/tmp/sample.csv", confirm=False)
check("import blocked without confirm + warns production",
      "CONFIRM NEEDED" in r and "PRODUCTION" in r, r)
r = S.anaplan_run_import("Load Actuals", file_path="/tmp/sample.csv", confirm=True)
check("import uploads + runs with confirm", "successful=True" in r, r)
r = S.anaplan_run_import("Load Actuals", file_path="/tmp/missing.csv", confirm=True)
check("import reports missing file", "File not found" in r, r)

# 8. Modules: list, find, attributes
r = S.anaplan_list_modules()
check("list_modules works", "EXP01 Expense Detail" in r, r)
r = S.anaplan_find_module("revenue")
check("fuzzy find module", "REV01 Revenue Calc" in r, r)
r = S.anaplan_get_module_attributes("revenue calc")
check("module attributes show line items + formula",
      "Revenue" in r and "Price * Units" in r, r)

# New: detailed line item tool
r = S.anaplan_get_line_items("revenue calc")
check("line items show Applies To dimensions",
      "Product, Region" in r, r)
check("line items show Time Scale", "Month" in r, r)
check("line items flag UI-only metrics honestly",
      "Calculation Effort" in r and "Blueprint" in r, r)

# Bad login path
client2 = S.client.__class__()
try:
    client2._username = None
    client2._get_new_token()
    bad = False
except Exception:
    bad = True
check("auth without creds raises", bad)

print(f"\n=== {('ALL TESTS PASSED' if check.failures==0 else str(check.failures)+' FAILURE(S)')} ===")
sys.exit(1 if check.failures else 0)
