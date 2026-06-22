"""
try_login.py
------------
A quick check that your Anaplan login works, separate from the MCP server.

Run it with:   python try_login.py

It reads ANAPLAN_USERNAME and ANAPLAN_PASSWORD from your environment
variables (see the README), so no secrets live in this file.

If your corporate network blocks the certificate check, set
ANAPLAN_VERIFY_SSL=false in your environment before running (see README).
"""

import os
from anaplan_client import AnaplanClient

user = os.environ.get("ANAPLAN_USERNAME", "")
pw = os.environ.get("ANAPLAN_PASSWORD", "")

print(f"Username found: {user or '(none)'}")
print(f"Password found: {'yes' if pw else 'NO - not set!'}")
print("Trying to log in...\n")

client = AnaplanClient()
try:
    print(client.login(user, pw))   # should say "Logged in successfully."
    print("\nYour models:\n")
    for m in client.list_models():
        print(f"  {m['model_name']}  (workspace: {m['workspace_name']})")
        print(f"    workspace_id={m['workspace_id']}  model_id={m['model_id']}")
except Exception as e:
    print(f"FAILED: {e}")
