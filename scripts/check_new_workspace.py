#!/usr/bin/env python3
"""
scripts/check_new_workspace.py
Proves the system is not tied to the demo data: it connects a database StateSync has never seen, uploads a
document it has never seen, builds a workspace on them, and asks one question of each source. Nothing is
tuned for this data; the same code paths answer the SEC demo.

  ./demo/local_shop_db.sh                    # first: a small furniture-shop Postgres on localhost:5433
  python3 scripts/check_new_workspace.py     # add --keep to leave the workspace for a live demo

Steps (each through the public API, as the web app does it):
  1. Library -> Databases: connect the shop database with its read-only login (StateSync checks the login
     cannot write) and choose its schema "sales".
  2. Library -> Documents: create a folder and upload demo/documents/Returns and refunds policy.pdf; wait
     until it is parsed, cut into passages and embedded ("ready").
  3. Workspaces: create a workspace on that database and give it the folder.
  4. Ask a database question and a documents question; check the routes and that answers came back.
Without --keep, the workspace, folder, connection and its conversations are deleted at the end. The sign-in token is read
from secrets/api_token_dev and never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from api_auth import api_token

REPO = Path(__file__).resolve().parent.parent
DOCUMENT = REPO / "demo" / "documents" / "Returns and refunds policy.pdf"
# The throwaway demo database's read-only login, as demo/local_shop_db.sh creates it (demo-only values).
SHOP = {"name": "Furniture shop (check)", "host": "host.docker.internal", "port": 5433, "dbname": "shop",
        "username": "statesync_reader", "password": "demo-reader-pass", "sslmode": "disable"}
QUESTIONS = [("Which city has the most customers?", "sql"),
             ("How many days does a customer have to return an item, according to the returns policy?", "docs")]


class Api:
    def __init__(self, base: str, token: str) -> None:
        self.base, self.token = base, token

    def call(self, method: str, path: str, body: object = None, raw: bytes | None = None,
             headers: dict[str, str] | None = None, timeout: int = 300) -> dict:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Authorization": f"Bearer {self.token}", **({"Content-Type": "application/json"} if raw is None else {}),
             **(headers or {})}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read()
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{method} {path}: HTTP {exc.code}: {exc.read().decode()[:300]}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8090")
    parser.add_argument("--keep", action="store_true", help="leave the workspace, folder and connection")
    args = parser.parse_args()
    api = Api(args.api, api_token())
    created: dict[str, str] = {}
    sessions: list[str] = []
    failures = 0
    try:
        conn = api.call("POST", "/library/connections", SHOP)
        connection = conn.get("connection") or conn
        created["connection"] = connection["connection_id"]
        print(f"1. connected the shop database ({connection.get('status')}); warnings: {conn.get('warnings') or 'none'}")
        api.call("PUT", f"/library/connections/{created['connection']}/schema", {"schema_name": "sales"})

        folder = api.call("POST", "/library/folders", {"name": f"Shop policies {uuid.uuid4().hex[:6]}"})["folder"]
        created["folder"] = folder["folder_id"]
        doc = api.call("POST", f"/library/folders/{created['folder']}/documents", raw=DOCUMENT.read_bytes(),
                       headers={"Content-Type": "application/pdf", "X-Filename": urllib.parse.quote(DOCUMENT.name)})
        document_id = doc["document"]["document_id"]
        for _ in range(60):
            docs = api.call("GET", f"/library/documents?folder_id={created['folder']}").get("documents", [])
            status = next((d["status"] for d in docs if d["document_id"] == document_id), "missing")
            if status != "processing":
                break
            time.sleep(2)
        print(f"2. uploaded {DOCUMENT.name}: {status}")
        failures += status != "ready"

        ws = api.call("POST", "/workspaces", {"name": "Flexibility check", "target_schema": created["connection"],
                                              "business_context": "A furniture shop selling online, in an app and in stores."})
        created["workspace"] = ws["workspace"]["workspace_id"]
        api.call("PUT", f"/workspaces/{created['workspace']}/folders", {"folder_ids": [created["folder"]]})
        print(f"3. workspace {created['workspace']} on the shop database, searching the folder")

        for question, route in QUESTIONS:
            sessions.append(str(uuid.uuid4()))
            res = api.call("POST", "/query", {"query": question, "workspace_id": created["workspace"],
                                              "session_id": sessions[-1]})
            got = (res.get("state_trace") or {}).get("route")
            ok = got == route and bool((res.get("answer_text") or "").strip())
            failures += not ok
            print(f"4. {'PASS' if ok else 'FAIL'} {question}\n   route {got} (expected {route}): {res.get('answer_text', '')[:200]!r}")
    finally:
        if not args.keep:
            for session_id in sessions:
                api.call("DELETE", f"/sessions/{session_id}")
            for kind, path in (("workspace", "/workspaces/{}"), ("folder", "/library/folders/{}"),
                               ("connection", "/library/connections/{}")):
                if kind in created:
                    api.call("DELETE", path.format(created[kind]))
            print("cleaned up (use --keep to leave them)")
    print("All checks passed." if failures == 0 else f"{failures} check(s) failed.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
