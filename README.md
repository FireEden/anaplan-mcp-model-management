# Anaplan MCP Server

[![Built with Claude](https://img.shields.io/badge/Built%20with-Claude-D97757)](https://www.anthropic.com/claude)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/Protocol-MCP-000000)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A local [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that lets an AI assistant (Claude, Cursor, etc.) work directly with your **Anaplan** models — listing and running imports/exports, browsing modules, and inspecting module attributes — all from natural-language chat, with a safety confirmation before anything runs.

Built as a hands-on project to explore how MCP can wrap an enterprise planning API (Anaplan Integration API v2). Everything runs locally on your own machine.

> **Note:** This is a personal/portfolio project and is not affiliated with or endorsed by Anaplan. Check your organization's API usage policy before connecting it to a corporate Anaplan tenant.

---

## What it does

| Capability | Tools |
|---|---|
| Username/password login (OAuth on the roadmap) | `anaplan_login` |
| Connect to multiple models and pick one | `anaplan_list_models`, `anaplan_select_model` |
| List imports (name, source label/object, target object, target type, production-data flag) | `anaplan_list_imports` |
| List exports (name, last run start, duration, used-in-process) | `anaplan_list_exports` |
| Find + run exports — **confirms before running** | `anaplan_find_export`, `anaplan_run_export` |
| Find + run imports — **uploads your file, confirms, reports row errors** | `anaplan_find_import`, `anaplan_run_import` |
| Search modules and pull their attributes | `anaplan_list_modules`, `anaplan_find_module`, `anaplan_get_module_attributes` |
| Detailed line item attributes — Applies To (dimensions), Time Scale, format, formula, summary | `anaplan_get_line_items` |

**Safety by design:** the run-tools never execute on the first call. They reply with exactly what they're about to do and wait for you to call again with `confirm=true`. Imports also warn you when they write to **production** data.

---

## How it works

```
You (chat) → AI assistant → MCP server (server.py) → anaplan_client.py → Anaplan REST API
```

- `server.py` — defines the 12 MCP tools.
- `anaplan_client.py` — a small, well-commented wrapper around the Anaplan Integration API v2 (auth tokens, models, imports, exports, modules, chunked file upload, fuzzy name matching).
- `test_server.py` — a full offline test suite that mocks the Anaplan API, so you can verify the logic with no account needed.

---

## Setup

### 1. Prerequisites
- **Python 3.10 or newer.** On Windows, install from the Microsoft Store or [python.org](https://www.python.org/downloads/) (tick *"Add Python to PATH"*).
- An MCP-compatible client such as **Cursor** or **Claude Desktop**.

### 2. Get the code
```bash
git clone https://github.com/YOUR_USERNAME/anaplan-mcp.git
cd anaplan-mcp
pip install -r requirements.txt
```

### 3. Store your Anaplan credentials safely

Your password should **never** go in any project file. Instead, save it as an environment variable in your operating system. The server reads it automatically at startup.

<details open>
<summary><b>Windows (PowerShell)</b></summary>

Run these two lines, replacing the values with your own. Keep the word `"User"` exactly as-is — it just tells Windows to save it for your account.

```powershell
[Environment]::SetEnvironmentVariable("ANAPLAN_USERNAME", "you@email.com", "User")
[Environment]::SetEnvironmentVariable("ANAPLAN_PASSWORD", "your_password", "User")
```

If your password contains special characters (like `@` or `$`), use this safer prompt version so nothing gets mangled or shown on screen:

```powershell
$p = Read-Host "Paste your Anaplan password" -AsSecureString
$plain = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($p))
[Environment]::SetEnvironmentVariable("ANAPLAN_PASSWORD", $plain, "User")
```

**Important:** environment variables are only picked up by *newly opened* windows. After saving, close PowerShell (and fully restart Cursor) before testing.
</details>

<details>
<summary><b>macOS / Linux (bash or zsh)</b></summary>

Add these to your `~/.zshrc` or `~/.bashrc`:
```bash
export ANAPLAN_USERNAME="you@email.com"
export ANAPLAN_PASSWORD="your_password"
```
Then run `source ~/.zshrc` (or open a new terminal).
</details>

### 4. Test your login (optional but recommended)
```bash
python try_login.py
```
This reads your stored credentials, attempts a login, and prints your models if it works — or a clear error if it doesn't.

### 5. Connect it to your AI client

**Cursor:** Settings → MCP → *Add new MCP server*, then edit `mcp.json` (see `mcp.json.example`):
```json
{
  "mcpServers": {
    "anaplan": {
      "command": "C:\\path\\to\\python.exe",
      "args": ["C:\\path\\to\\anaplan_mcp\\server.py"],
      "env": { "ANAPLAN_VERIFY_SSL": "true" }
    }
  }
}
```
Use the full path to your Python (`(Get-Command python).Source` on Windows) and to `server.py`. On Windows, paths in JSON need **double backslashes** (`\\`). Notice there's **no password here** — it comes from your environment variables. Restart your client; you should see a green status and 12 tools.

---

## Usage

In your assistant's chat (Agent mode in Cursor), try:

1. *"Log into Anaplan and list my models."*
2. *"Use the FP&A model."*
3. *"Show me the imports."*
4. *"Run the Load Actuals import using C:\data\actuals.csv."* → it confirms first
5. *"Yes, confirm."* → uploads, runs, reports any failed rows
6. *"What line items are in the Revenue module?"*

You can type partial names — "load actu" finds "Load Actuals". If two names are equally close, it asks you to pick the exact one.

---

## Testing

A full offline test suite (no Anaplan account required) mocks the API and checks login, model selection, import/export listing, the confirm-before-run gate, file upload, error reporting, and module attributes:

```bash
python test_server.py
```

---

## Corporate networks & SSL

Some corporate networks use a security proxy that breaks normal HTTPS certificate checks, causing a `CERTIFICATE_VERIFY_FAILED` error. As a temporary workaround you can set `ANAPLAN_VERIFY_SSL=false` (in the `env` block or as an environment variable). **This lowers security** — the proper fix is to install your company's CA certificate so Python can verify connections normally. Ask your IT team for it.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `CERTIFICATE_VERIFY_FAILED` | Corporate proxy — see the SSL section above. |
| `401 wrong username or password` | Wrong/expired password, **or** your account isn't enabled for API basic auth (an Anaplan admin setting), **or** MFA is enabled on the account. |
| `404` on metadata calls | Most import/export/module calls require a **Workspace Admin** role. |
| Red dot in Cursor | A typo in a path in `mcp.json` (check the `\\` backslashes). |

---

## Known API limitations

Some things FP&A teams want simply aren't exposed by Anaplan's public API yet. This server is honest about them rather than faking data:

- **Line item performance metrics** — cell count, populated cell count, memory usage, calculation complexity, and calculation effort live only in the web **Blueprint** view. The `anaplan_get_line_items` tool returns everything the API *does* expose (Applies To, Time Scale, format, formula, summary) and clearly flags these five as Blueprint-only. (See the open request to [include Calculation Effort in the line items API](https://community.anaplan.com/discussion/160344/include-calculation-effort-in-the-line-items-api).)
- **Which modules a UX App Page uses** — the page "Dependencies" view is UI-only; the standard Integration API doesn't expose page contents.
- **Archiving / copying models** — not available via API ([long-standing community request](https://community.anaplan.com/discussion/135170/automate-model-management)).

## Roadmap

- OAuth 2.0 authentication (replacing username/password)
- Saving exported files to a chosen local folder
- Deeper "used in process" mapping for export actions
- Model lifecycle tools (online/offline, close) once validated against a live tenant

## License

MIT — see [LICENSE](LICENSE).
