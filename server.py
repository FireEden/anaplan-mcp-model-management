"""
server.py
---------
The Anaplan MCP server. This is the file you point your MCP client at.

It exposes tools an assistant (or you) can call:
  - anaplan_login
  - anaplan_list_models / anaplan_select_model
  - anaplan_list_imports / anaplan_list_exports
  - anaplan_find_export  / anaplan_run_export   (confirm before running)
  - anaplan_find_import  / anaplan_run_import   (upload file, confirm, report errors)
  - anaplan_list_modules / anaplan_find_module  / anaplan_get_module_attributes

SAFETY DESIGN (your requirement #6 and #7):
Running an import or export changes data. So those tools use a TWO-STEP pattern:
  1. You call anaplan_run_export with confirm=False (the default).
     -> The server replies with EXACTLY what it will run and asks you to confirm.
  2. You call again with confirm=True to actually run it.
The server never runs anything destructive on the first call.

Run locally with:   python server.py
"""

import base64
import os

from mcp.server.fastmcp import FastMCP

from anaplan_client import AnaplanClient, AnaplanError, fuzzy_match, fuzzy_match_scored

mcp = FastMCP("anaplan")

# One shared client for this local session.
client = AnaplanClient()


# --------------------------------------------------------------------------- #
# 1. LOGIN
# --------------------------------------------------------------------------- #
@mcp.tool()
def anaplan_login(username: str = "", password: str = "") -> str:
    """
    Log in to Anaplan with username and password.

    If you leave the arguments blank, it reads ANAPLAN_USERNAME and
    ANAPLAN_PASSWORD from your environment instead (safer than typing them).

    (OAuth is on the future roadmap; for now this is basic username/password.)
    """
    username = username or os.environ.get("ANAPLAN_USERNAME", "")
    password = password or os.environ.get("ANAPLAN_PASSWORD", "")
    if not username or not password:
        return "Please provide username and password (or set ANAPLAN_USERNAME / ANAPLAN_PASSWORD)."
    try:
        return client.login(username, password)
    except AnaplanError as exc:
        return f"Login error: {exc}"


# --------------------------------------------------------------------------- #
# 3. MODELS — list and select
# --------------------------------------------------------------------------- #
@mcp.tool()
def anaplan_list_models() -> str:
    """List every workspace/model you can access, so you can pick one."""
    try:
        models = client.list_models()
    except AnaplanError as exc:
        return f"Error: {exc}"
    if not models:
        return "No models found for this account."
    lines = ["Available models (use anaplan_select_model with the IDs):"]
    for m in models:
        lines.append(
            f"- {m['model_name']}  [workspace: {m['workspace_name']}]\n"
            f"    workspace_id={m['workspace_id']}  model_id={m['model_id']}"
        )
    return "\n".join(lines)


@mcp.tool()
def anaplan_select_model(workspace_id: str, model_id: str) -> str:
    """Choose which model to work in. Required before imports/exports/modules."""
    try:
        return client.select_model(workspace_id, model_id)
    except AnaplanError as exc:
        return f"Error: {exc}"


# --------------------------------------------------------------------------- #
# 4. LIST IMPORTS (with the requested properties)
# --------------------------------------------------------------------------- #
@mcp.tool()
def anaplan_list_imports() -> str:
    """
    List imports in the selected model with: import name, source label,
    source object, target object, target type, production data (true/false).
    """
    try:
        imports = client.list_imports()
    except AnaplanError as exc:
        return f"Error: {exc}"
    if not imports:
        return "No imports found in this model."
    lines = ["Imports:"]
    for i in imports:
        lines.append(
            f"- {i['import_name']}\n"
            f"    source_label : {i['source_label']}\n"
            f"    source_object: {i['source_object']}\n"
            f"    target_object: {i['target_object']}\n"
            f"    target_type  : {i['target_type']}\n"
            f"    production_data: {i['production_data']}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 5. LIST EXPORTS (with the requested properties)
# --------------------------------------------------------------------------- #
@mcp.tool()
def anaplan_list_exports() -> str:
    """
    List exports in the selected model with: export name, last run
    (start date & time), most recent duration, used in process.
    """
    try:
        exports = client.list_exports()
    except AnaplanError as exc:
        return f"Error: {exc}"
    if not exports:
        return "No exports found in this model."
    lines = ["Exports:"]
    for e in exports:
        used = ", ".join(e["used_in_process"]) if e["used_in_process"] else "(none found)"
        lines.append(
            f"- {e['export_name']}\n"
            f"    last_run_start      : {e['last_run_start'] or 'never / unknown'}\n"
            f"    most_recent_duration: {e['most_recent_duration'] or 'unknown'}\n"
            f"    used_in_process     : {used}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 6. EXPORTS — find by close name, then run with confirmation
# --------------------------------------------------------------------------- #
@mcp.tool()
def anaplan_find_export(name_query: str) -> str:
    """Find exports whose name closely matches your text. Pick one to run."""
    try:
        exports = client.list_exports()
    except AnaplanError as exc:
        return f"Error: {exc}"
    names = [e["export_name"] for e in exports]
    matches = fuzzy_match(name_query, names)
    if not matches:
        return f"No exports close to '{name_query}'. Try anaplan_list_exports."
    return "Closest matching exports:\n" + "\n".join(f"- {m}" for m in matches)


@mcp.tool()
def anaplan_run_export(export_name: str, confirm: bool = False) -> str:
    """
    Run an export by name.

    Step 1: call with confirm=False (default) -> the server tells you exactly
            what it will run and asks you to confirm.
    Step 2: call again with confirm=True       -> it actually runs.
    """
    try:
        exports = client.list_exports()
    except AnaplanError as exc:
        return f"Error: {exc}"

    match = _resolve_one(export_name, [e["export_name"] for e in exports])
    if isinstance(match, str) and match.startswith("__AMBIGUOUS__"):
        return match.replace("__AMBIGUOUS__", "")
    if match is None:
        return f"No export matches '{export_name}'."

    target = next(e for e in exports if e["export_name"] == match)

    if not confirm:
        return (
            f"CONFIRM NEEDED before running.\n"
            f"  Export : {target['export_name']}\n"
            f"  Model  : {client.model_name}\n"
            f"To proceed, call anaplan_run_export again with "
            f"export_name='{target['export_name']}' and confirm=True."
        )

    try:
        result = client.run_export(target["id"])
    except AnaplanError as exc:
        return f"Export failed to run: {exc}"
    ok = result.get("successful")
    return (
        f"Export '{target['export_name']}' finished. "
        f"State={result.get('state')}, successful={ok}."
    )


# --------------------------------------------------------------------------- #
# 7. IMPORTS — find, upload a file, confirm, run, report issues
# --------------------------------------------------------------------------- #
@mcp.tool()
def anaplan_find_import(name_query: str) -> str:
    """Find imports whose name closely matches your text. Pick one to run."""
    try:
        imports = client.list_imports()
    except AnaplanError as exc:
        return f"Error: {exc}"
    names = [i["import_name"] for i in imports]
    matches = fuzzy_match(name_query, names)
    if not matches:
        return f"No imports close to '{name_query}'. Try anaplan_list_imports."
    return "Closest matching imports:\n" + "\n".join(f"- {m}" for m in matches)


@mcp.tool()
def anaplan_run_import(
    import_name: str,
    file_path: str = "",
    confirm: bool = False,
) -> str:
    """
    Run an import by name, uploading a local file first.

    Arguments:
      import_name : the import to run (close match is fine).
      file_path   : path to the local file to upload for this import.
      confirm     : must be True to actually run (safety).

    Flow:
      1. Call with confirm=False -> it shows the import + file and asks to confirm.
      2. Call with confirm=True  -> it uploads the file, runs the import,
         and reports any failed rows (the Anaplan "dump").
    """
    try:
        imports = client.list_imports()
    except AnaplanError as exc:
        return f"Error: {exc}"

    match = _resolve_one(import_name, [i["import_name"] for i in imports])
    if isinstance(match, str) and match.startswith("__AMBIGUOUS__"):
        return match.replace("__AMBIGUOUS__", "")
    if match is None:
        return f"No import matches '{import_name}'."

    target = next(i for i in imports if i["import_name"] == match)

    if not file_path:
        return (
            f"Import '{target['import_name']}' needs a source file.\n"
            f"Please call anaplan_run_import again with file_path set to the "
            f"local file you want to upload."
        )
    if not os.path.isfile(file_path):
        return f"File not found: {file_path}"

    if not confirm:
        prod = " (writes to PRODUCTION data!)" if target["production_data"] else ""
        return (
            f"CONFIRM NEEDED before running.\n"
            f"  Import : {target['import_name']}{prod}\n"
            f"  Target : {target['target_object']}\n"
            f"  File   : {file_path}\n"
            f"  Model  : {client.model_name}\n"
            f"To proceed, call anaplan_run_import again with the same "
            f"import_name and file_path plus confirm=True."
        )

    # 1. Find the file slot, upload bytes.
    try:
        file_id = client.get_import_file_id(target["id"])
        if not file_id:
            return "Could not find the file slot for this import."
        with open(file_path, "rb") as fh:
            client.upload_file(file_id, fh.read())
    except AnaplanError as exc:
        return f"File upload failed: {exc}"

    # 2. Run the import.
    try:
        result = client.run_import(target["id"])
    except AnaplanError as exc:
        return f"Import failed to run: {exc}"

    # 3. Report issues if any rows failed.
    msg = (
        f"Import '{target['import_name']}' finished. "
        f"State={result.get('state')}, successful={result.get('successful')}."
    )
    details = result.get("details") or []
    if details:
        msg += "\nDetails: " + "; ".join(str(d.get("localMessageText", d)) for d in details)
    if result.get("failure_dump"):
        dump = client.get_import_dump(target["id"], result.get("task_id", ""))
        preview = dump[:1000] if dump else "(dump unavailable)"
        msg += f"\nSome rows failed. Failure dump preview:\n{preview}"
    return msg


# --------------------------------------------------------------------------- #
# 8. MODULES — list, find by name, get attributes
# --------------------------------------------------------------------------- #
@mcp.tool()
def anaplan_list_modules() -> str:
    """List all modules in the selected model."""
    try:
        modules = client.list_modules()
    except AnaplanError as exc:
        return f"Error: {exc}"
    if not modules:
        return "No modules found in this model."
    return "Modules:\n" + "\n".join(f"- {m['module_name']}" for m in modules)


@mcp.tool()
def anaplan_find_module(name_query: str) -> str:
    """Find modules whose name closely matches your text."""
    try:
        modules = client.list_modules()
    except AnaplanError as exc:
        return f"Error: {exc}"
    names = [m["module_name"] for m in modules]
    matches = fuzzy_match(name_query, names)
    if not matches:
        return f"No modules close to '{name_query}'. Try anaplan_list_modules."
    return "Closest matching modules:\n" + "\n".join(f"- {m}" for m in matches)


@mcp.tool()
def anaplan_get_module_attributes(module_name: str) -> str:
    """Show a module's attributes (line items: name, format, formula, type)."""
    try:
        modules = client.list_modules()
    except AnaplanError as exc:
        return f"Error: {exc}"

    match = _resolve_one(module_name, [m["module_name"] for m in modules])
    if isinstance(match, str) and match.startswith("__AMBIGUOUS__"):
        return match.replace("__AMBIGUOUS__", "")
    if match is None:
        return f"No module matches '{module_name}'."

    target = next(m for m in modules if m["module_name"] == match)
    try:
        info = client.get_module_attributes(target["id"])
    except AnaplanError as exc:
        return f"Error: {exc}"
    lines = [f"Attributes for module '{match}':"]
    for li in info["line_items"]:
        lines.append(
            f"- {li['name']}  (format={li['format']}, type={li['data_type']})"
            + (f"  formula: {li['formula']}" if li.get("formula") else "")
        )
    return "\n".join(lines)


@mcp.tool()
def anaplan_get_line_items(module_name: str) -> str:
    """
    Show DETAILED line item attributes for a module, including each line
    item's Applies To (dimensions) and Time Scale, plus format, formula,
    data type, and summary.

    Note: cell count, populated cell count, memory usage, calculation
    complexity, and calculation effort are NOT available through Anaplan's
    API - they exist only in the web Blueprint view. This tool says so
    clearly rather than guessing.
    """
    try:
        modules = client.list_modules()
    except AnaplanError as exc:
        return f"Error: {exc}"

    match = _resolve_one(module_name, [m["module_name"] for m in modules])
    if isinstance(match, str) and match.startswith("__AMBIGUOUS__"):
        return match.replace("__AMBIGUOUS__", "")
    if match is None:
        return f"No module matches '{module_name}'."

    target = next(m for m in modules if m["module_name"] == match)
    try:
        info = client.get_line_item_details(target["id"])
    except AnaplanError as exc:
        return f"Error: {exc}"

    lines = [
        f"Line items in module '{match}' ({info['line_item_count']} total):",
        "",
    ]
    for li in info["line_items"]:
        applies = ", ".join(li["applies_to"]) if li["applies_to"] else "(none)"
        lines.append(f"- {li['name']}")
        lines.append(f"    format     : {li['format']}  (type {li['data_type']})")
        lines.append(f"    applies_to : {applies}")
        lines.append(f"    time_scale : {li['time_scale'] or '(none)'}")
        lines.append(f"    summary    : {li['summary'] or '(none)'}")
        if li.get("formula"):
            lines.append(f"    formula    : {li['formula']}")

    # Honest note about what the API can't give us.
    lines.append("")
    lines.append(
        "Not available via API (web Blueprint view only): "
        + ", ".join(info["ui_only_metrics"]) + "."
    )
    lines.append(
        "To get those, open the module's Blueprint in Anaplan and use "
        "'Export' on the Line Items grid."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Helper: resolve a typed name to exactly one real name (or ask to disambiguate)
# --------------------------------------------------------------------------- #
def _resolve_one(query: str, names: list[str]):
    """Return the single best name, None if no match, or an ambiguity prompt."""
    if query in names:
        return query
    scored = fuzzy_match_scored(query, names)
    if not scored:
        return None
    if len(scored) == 1:
        return scored[0][1]
    top_score, top_name = scored[0]
    second_score = scored[1][0]
    # If the best match clearly beats the runner-up, just use it.
    if top_score - second_score >= 0.15:
        return top_name
    listing = "\n".join(f"- {n}" for _, n in scored)
    return (
        "__AMBIGUOUS__Several names match. Please call again with the exact name:\n"
        + listing
    )


if __name__ == "__main__":
    # Runs the MCP server over stdio so a local MCP client can connect.
    mcp.run()
