#!/usr/bin/env python3
"""
demo/reset_demo_account.py
Empties the DEMO account (secrets/api_token_demo, principal p_demo, tenant t_demo)
through the public API, so a rehearsal can be wiped before the real demo. Other
accounts cannot be touched: every call is made with the demo token, and the
server scopes everything to that principal.

Removes: dashboards (and their tiles), loose tiles, workspaces, conversations,
library folders (and their documents), database connections.

Usage: python3 demo/reset_demo_account.py
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:8090"
TOKEN = (Path(__file__).resolve().parent.parent / "secrets" / "api_token_demo").read_text().strip()


def call(method: str, path: str):
    req = urllib.request.Request(BASE + path, method=method, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def main() -> int:
    removed = {}
    for board in call("GET", "/dashboards")[1].get("dashboards", []):
        removed["dashboards"] = removed.get("dashboards", 0) + (call("DELETE", f"/dashboards/{board['dashboard_id']}")[0] == 200)
    for tile in call("GET", "/tiles")[1].get("tiles", []):
        removed["tiles"] = removed.get("tiles", 0) + (call("DELETE", f"/tiles/{tile['tile_id']}")[0] == 200)
    for ws in call("GET", "/workspaces")[1].get("workspaces", []):
        removed["workspaces"] = removed.get("workspaces", 0) + (call("DELETE", f"/workspaces/{ws['workspace_id']}")[0] == 200)
    for session in call("GET", "/sessions")[1].get("sessions", []):
        removed["conversations"] = removed.get("conversations", 0) + (call("DELETE", f"/sessions/{session['session_id']}")[0] == 200)
    for folder in call("GET", "/library/folders")[1].get("folders", []):
        removed["folders"] = removed.get("folders", 0) + (call("DELETE", f"/library/folders/{folder['folder_id']}")[0] == 200)
    for conn in call("GET", "/library/connections")[1].get("connections", []):
        removed["connections"] = removed.get("connections", 0) + (call("DELETE", f"/library/connections/{conn['connection_id']}")[0] == 200)
    left = {
        "dashboards": len(call("GET", "/dashboards")[1].get("dashboards", [])),
        "workspaces": len(call("GET", "/workspaces")[1].get("workspaces", [])),
        "conversations": len(call("GET", "/sessions")[1].get("sessions", [])),
        "folders": len(call("GET", "/library/folders")[1].get("folders", [])),
        "connections": len(call("GET", "/library/connections")[1].get("connections", [])),
    }
    print("removed:", removed or "nothing")
    print("left:", left)
    return 0 if not any(left.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
