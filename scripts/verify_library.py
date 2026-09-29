#!/usr/bin/env python3
"""
scripts/verify_library.py
Live checks for the account document library (docs/V3_PLAN.md Phase 3c slice 5,
D-v3-17, migration 024) against the running stack. It asks no model anything.

What it proves, through the real API:
- folders: create, duplicate names refused, rename;
- upload: a real PDF goes parser -> chunks -> embeddings and ends `ready`
  with its pages and chunks; the same file twice is refused; an unsupported
  type is refused before parsing; garbage bytes come back as a parser refusal;
- isolation: a second principal can neither list, upload to, nor delete the
  first one's folder, nor link it to their own workspace;
- workspace links: a workspace saves and reads back its chosen folders, and
  deleting a folder removes the link;
- retrieval (MCP, no model): a question asked in the workspace finds the
  uploaded document first, with its title, and fetches its passage; the same
  search without the workspace never sees it;
- clean-up: deleting a folder removes its documents.

Needs: the stack running (parser included), migration 024 applied,
secrets/api_token_dev and secrets/api_token_second. Everything it creates is
deleted at the end.

Usage: python3 scripts/verify_library.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from api_auth import api_token  # noqa: E402

BASE = "http://localhost:8090"
TOKEN_A = api_token()
TOKEN_B = (REPO_ROOT / "secrets" / "api_token_second").read_text().strip()
PDF = REPO_ROOT / "fixtures" / "documents" / "supplier_agreement_S001.pdf"
READY_WAIT_S = 90
ORCHESTRATOR = "statesync-orchestrator-1"
POLICY = """# Acme Furniture refund policy 2026

Customers may return any sofa, chair or table within 30 days of delivery.

Refunds are issued to the original payment method within 7 business days after the returned item
has been inspected at our Curitiba warehouse."""
POLICY_QUESTION = "How many days does Acme take to issue a refund after inspection?"

_MCP_SNIPPET = """
import json, sys, urllib.request
req = json.load(sys.stdin)
headers = {"Content-Type": "application/json", "X-Tenant-ID": req["tenant"]}
if req.get("workspace"):
    headers["X-Workspace-ID"] = req["workspace"]
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": req["method"], "params": req["params"]}).encode()
r = urllib.request.Request("http://mcp:8080/rpc", data=body, method="POST", headers=headers)
print(urllib.request.urlopen(r, timeout=60).read().decode())
"""

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    ok = bool(ok)
    results.append((ok, label))
    print(f"  {'✓ PASS' if ok else '✗ FAIL'}: {label}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def call(method: str, path: str, token: str = TOKEN_A, body: dict | None = None,
         raw: bytes | None = None, content_type: str = "application/json", filename: str | None = None):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": content_type}
    if filename is not None:
        headers["X-Filename"] = urllib.parse.quote(filename)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body_raw = e.read()
        try:
            return e.code, json.loads(body_raw or b"{}")
        except json.JSONDecodeError:
            return e.code, {"raw": body_raw.decode(errors="replace")}


def psql(sql: str) -> str:
    out = subprocess.run(["docker", "exec", "-i", "postgres", "psql", "-U", "statesync_migrator", "-d", "statesync",
                          "-qtA"], input=sql, capture_output=True, text=True)
    return out.stdout.strip()


def mcp(tenant: str, workspace: str | None, method: str, params: dict) -> dict:
    """Call the MCP from inside the network, as the orchestrator does."""
    out = subprocess.run(["docker", "exec", "-i", ORCHESTRATOR, "python", "-c", _MCP_SNIPPET],
                         input=json.dumps({"tenant": tenant, "workspace": workspace, "method": method,
                                           "params": params}), capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"FATAL: MCP call failed: {out.stderr.strip()}")
    return json.loads(out.stdout).get("result") or {}


def wait_until_done(folder_id: str) -> dict:
    deadline = time.monotonic() + READY_WAIT_S
    doc: dict = {}
    while time.monotonic() < deadline:
        _, listing = call("GET", f"/library/documents?folder_id={folder_id}")
        doc = (listing.get("documents") or [{}])[0]
        if doc.get("status") != "processing":
            return doc
        time.sleep(1)
    return doc


def main() -> int:
    stamp = str(int(time.time()))
    print("Library (account folders, uploads, workspace links)")

    status, body = call("POST", "/library/folders", body={"name": f"verify {stamp}"})
    if not check(status == 201, "a folder is created", f"{status} {body}"):
        return 1
    folder_id = body["folder"]["folder_id"]
    status, body = call("POST", "/library/folders", body={"name": f"verify {stamp}"})
    check(status == 422 and "already exists" in body.get("detail", ""), "a duplicate folder name is refused",
          f"{status} {body}")
    status, body = call("PATCH", f"/library/folders/{folder_id}", body={"name": f"verify {stamp} renamed"})
    check(status == 200 and body["folder"]["name"] == f"verify {stamp} renamed", "a folder is renamed",
          f"{status} {body}")

    upload = f"/library/folders/{folder_id}/documents"
    status, body = call("POST", upload, raw=b"MZ", content_type="application/x-msdownload", filename="a.exe")
    check(status == 415, "an unsupported type is refused before parsing", f"{status} {body}")
    status, body = call("POST", upload, raw=b"%PDF-1.4\n" + bytes(range(256)) * 8, content_type="application/pdf",
                        filename="broken.pdf")
    check(status == 422 and body.get("detail"), "an unreadable PDF comes back as the parser's refusal",
          f"{status} {body}")

    pdf = PDF.read_bytes()
    status, body = call("POST", upload, raw=pdf, content_type="application/pdf", filename="Supplier agreement S001.pdf")
    check(status == 202 and body.get("document", {}).get("status") == "processing",
          "a PDF upload is accepted and starts processing", f"{status} {body}")
    doc = wait_until_done(folder_id)
    check(doc.get("status") == "ready" and doc.get("chunk_count", 0) >= 1 and doc.get("pages") == 1,
          "the PDF becomes ready with its pages and chunks", str(doc))
    check(doc.get("title") == "Supplier agreement S001", "the title comes from the file name", str(doc.get("title")))
    status, body = call("POST", upload, raw=pdf, content_type="application/pdf", filename="again.pdf")
    check(status == 422 and "already in the folder" in body.get("detail", ""), "the same file twice is refused",
          f"{status} {body}")

    status, _ = call("GET", f"/library/documents?folder_id={folder_id}", token=TOKEN_B)
    check(status == 404, "another principal cannot list the folder", str(status))
    status, _ = call("POST", upload, token=TOKEN_B, raw=b"hello", content_type="text/plain", filename="x.txt")
    check(status == 404, "another principal cannot upload into it", str(status))
    status, _ = call("DELETE", f"/library/folders/{folder_id}", token=TOKEN_B)
    check(status == 404, "another principal cannot delete it", str(status))

    status, body = call("POST", "/workspaces", body={"name": f"verify library {stamp}", "target_schema": "olist"})
    workspace_id = body.get("workspace", {}).get("workspace_id")
    if check(status == 201 and workspace_id, "a workspace to link is created", f"{status} {body}"):
        status, body = call("PUT", f"/workspaces/{workspace_id}/folders", body={"folder_ids": [folder_id]})
        check(status == 200 and body.get("folder_ids") == [folder_id], "the workspace saves its chosen folder",
              f"{status} {body}")
        status, body = call("GET", f"/workspaces/{workspace_id}/folders")
        check(body.get("folder_ids") == [folder_id], "and reads it back", str(body))
        status, body = call("GET", "/library/folders")
        mine = next((f for f in body.get("folders", []) if f["folder_id"] == folder_id), {})
        check(mine.get("workspace_ids") == [workspace_id] and mine.get("ready_count") == 1,
              "the folder shows its workspace and ready count", str(mine))

        status, body = call("POST", upload, raw=POLICY.encode(), content_type="text/markdown",
                            filename="Acme refund policy 2026.md")
        policy = wait_until_done(folder_id)
        check(status == 202 and policy.get("status") == "ready", "a Markdown upload becomes ready", str(policy))
        tenant = psql(f"SELECT tenant_id FROM workspace_docs.folders WHERE folder_id = '{folder_id}';")
        search = {"query_text": POLICY_QUESTION, "top_k": 5}
        inside = mcp(tenant, workspace_id, "vector.semantic_search", search).get("matches", [])
        top = inside[0] if inside else {}
        check(top.get("chunk_id", "").startswith("up_") and top.get("title") == "Acme refund policy 2026",
              "in the workspace, the uploaded document is the best match, with its title",
              str([(m.get("chunk_id"), m.get("title")) for m in inside[:3]]))
        passage = mcp(tenant, workspace_id, "docs.get_passage", {"chunk_id": top.get("chunk_id", "x")})
        check("7 business days" in passage.get("content_md", "") and passage.get("title") == "Acme refund policy 2026",
              "its passage is fetched by chunk id", str(passage)[:200])
        outside = mcp(tenant, None, "vector.semantic_search", search).get("matches", [])
        check(outside and not any(m["chunk_id"].startswith("up_") for m in outside),
              "without the workspace, the search never sees uploads", str([m.get("chunk_id") for m in outside]))

        status, body = call("POST", "/workspaces", token=TOKEN_B,
                            body={"name": f"verify library B {stamp}", "target_schema": "olist"})
        other_ws = body.get("workspace", {}).get("workspace_id")
        status, _ = call("PUT", f"/workspaces/{other_ws}/folders", token=TOKEN_B, body={"folder_ids": [folder_id]})
        check(status == 404, "another principal cannot link the folder to their workspace", str(status))
        if other_ws:
            call("DELETE", f"/workspaces/{other_ws}", token=TOKEN_B)

    status, body = call("DELETE", f"/library/folders/{folder_id}")
    check(status == 200 and body.get("deleted") == 1, "the owner deletes the folder", f"{status} {body}")
    status, _ = call("GET", f"/library/documents?folder_id={folder_id}")
    check(status == 404, "its documents go with it", str(status))
    if workspace_id:
        _, body = call("GET", f"/workspaces/{workspace_id}/folders")
        check(body.get("folder_ids") == [], "and the workspace link is removed", str(body))
        call("DELETE", f"/workspaces/{workspace_id}")

    passed = sum(ok for ok, _ in results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
