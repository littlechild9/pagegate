#!/usr/bin/env python3
"""
End-to-end audit for multi-owner approval attribution.

This script boots isolated temp copies of the FastAPI app, then checks:
1. A normal approval request is visible only to the owning admin.
2. A legacy page without `owner` is migrated to explicit super-admin ownership and
   can then be reassigned to the intended owner.
3. `reindex` does not let the caller steal ownership of orphaned page folders.
4. A non-owner cannot overwrite another owner's page content via publish.

Usage:
    python3 scripts/test_multi_owner_approval.py
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

import httpx
from itsdangerous import TimestampSigner


ROOT = Path(__file__).resolve().parents[1]
SERVER_SRC = ROOT / "server.py"
TEMPLATES_SRC = ROOT / "templates"
SERVER_PYTHON = ROOT / "venv" / "bin" / "python"
SUPER_ADMIN_EMAIL = "__super_admin__"


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class ScenarioResult:
    name: str
    ok: bool
    detail: str


class SandboxServer:
    def __init__(self) -> None:
        self.tempdir_obj = tempfile.TemporaryDirectory(prefix="pagegate-approval-")
        self.root = Path(self.tempdir_obj.name)
        self.port = _pick_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.session_secret = "test-session-secret"
        self.super_admin_token = "super-admin-token"
        self.process: subprocess.Popen[str] | None = None
        self.client: httpx.Client | None = None

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def pages_dir(self) -> Path:
        return self.root / "pages"

    def start(self) -> "SandboxServer":
        shutil.copy2(SERVER_SRC, self.root / "server.py")
        shutil.copytree(TEMPLATES_SRC, self.root / "templates")
        self.data_dir.mkdir(exist_ok=True)
        self.pages_dir.mkdir(exist_ok=True)
        (self.data_dir / "index.json").write_text('{"pages": []}\n', encoding="utf-8")
        (self.data_dir / "visitors.json").write_text('{"visitors": []}\n', encoding="utf-8")
        (self.root / "config.yaml").write_text(
            dedent(
                f"""
                admin_token: "{self.super_admin_token}"
                registration:
                  mode: "open"
                server:
                  host: "127.0.0.1"
                  port: {self.port}
                  base_url: "{self.base_url}"
                  session_secret: "{self.session_secret}"
                dingtalk:
                  app_key: ""
                  app_secret: ""
                wechat:
                  app_id: ""
                  app_secret: ""
                openclaw:
                  webhook_url: ""
                  webhook_token: ""
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        self.process = subprocess.Popen(
            [str(SERVER_PYTHON if SERVER_PYTHON.exists() else Path(sys.executable)), "server.py"],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.client = httpx.Client(base_url=self.base_url, follow_redirects=False, timeout=5.0)
        self._wait_until_ready()
        return self

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None

        self.tempdir_obj.cleanup()

    def __enter__(self) -> "SandboxServer":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _wait_until_ready(self) -> None:
        assert self.client is not None
        deadline = time.time() + 10
        last_error = ""
        while time.time() < deadline:
            if self.process is not None and self.process.poll() is not None:
                output = self._read_process_output()
                raise RuntimeError(f"Server exited early:\n{output}")
            try:
                response = self.client.get("/")
                if response.status_code in (200, 404):
                    return
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.2)
        output = self._read_process_output()
        raise RuntimeError(f"Server did not start in time: {last_error}\n{output}")

    def _read_process_output(self) -> str:
        if self.process is None or self.process.stdout is None:
            return ""
        try:
            return self.process.stdout.read()
        except Exception:
            return ""

    def register_admin(self, email: str, password: str = "password123") -> str:
        assert self.client is not None
        response = self.client.post(
            "/api/auth/register",
            json={"email": email, "password": password},
        )
        response.raise_for_status()
        return response.json()["token"]

    def publish_page(
        self,
        token: str,
        slug: str,
        html: str,
        *,
        title: str | None = None,
        access: str = "approval",
        category: str = "测试",
        description: str = "",
    ) -> httpx.Response:
        assert self.client is not None
        files = {"file": ("index.html", html.encode("utf-8"), "text/html")}
        data = {
            "slug": slug,
            "title": title or slug,
            "category": category,
            "access": access,
            "description": description,
        }
        return self.client.post(
            "/api/publish",
            headers=self._auth(token),
            data=data,
            files=files,
        )

    def reindex(self, token: str) -> httpx.Response:
        assert self.client is not None
        return self.client.post("/api/reindex", headers=self._auth(token))

    def get_pending(self, token: str) -> dict:
        assert self.client is not None
        response = self.client.get("/api/pending", headers=self._auth(token))
        response.raise_for_status()
        return response.json()

    def approve(self, token: str, slug: str, visitor_id: str) -> httpx.Response:
        assert self.client is not None
        return self.client.post(
            f"/api/pages/{slug}/approve",
            headers=self._auth(token),
            json={"visitor_id": visitor_id},
        )

    def update_page(self, token: str, slug: str, **payload: str) -> httpx.Response:
        assert self.client is not None
        return self.client.put(
            f"/api/pages/{slug}",
            headers=self._auth(token),
            json=payload,
        )

    def add_visitor(self, visitor_id: str, name: str = "访客") -> None:
        visitors = self.read_visitors()
        visitors["visitors"].append({
            "id": visitor_id,
            "provider": "dingtalk",
            "name": name,
            "avatar": "",
            "first_seen": "2026-04-08T00:00:00+00:00",
            "approved_pages": [],
            "pending_pages": [],
            "blocked": False,
        })
        self.write_visitors(visitors)

    def request_approval(self, slug: str, visitor_id: str) -> httpx.Response:
        assert self.client is not None
        signer = TimestampSigner(self.session_secret)
        cookie = signer.sign(visitor_id).decode()
        return self.client.get(
            f"/{slug}",
            cookies={"pagegate_session": cookie},
        )

    def create_page_without_owner(self, slug: str, *, access: str = "approval") -> None:
        self.write_index({
            "pages": [{
                "slug": slug,
                "title": slug,
                "category": "测试",
                "access": access,
                "description": "legacy page without owner",
                "created_at": "2026-04-08T00:00:00+00:00",
                "updated_at": "2026-04-08T00:00:00+00:00",
            }]
        })
        self.write_page_file(slug, f"<html><body>{slug}</body></html>")

    def create_orphan_page_dir(self, slug: str) -> None:
        self.write_page_file(slug, f"<html><body>{slug}</body></html>")

    def write_page_file(self, slug: str, html: str) -> None:
        page_dir = self.pages_dir / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(html, encoding="utf-8")

    def read_page_file(self, slug: str) -> str:
        return (self.pages_dir / slug / "index.html").read_text(encoding="utf-8")

    def read_index(self) -> dict:
        return json.loads((self.data_dir / "index.json").read_text(encoding="utf-8"))

    def write_index(self, data: dict) -> None:
        (self.data_dir / "index.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_visitors(self) -> dict:
        return json.loads((self.data_dir / "visitors.json").read_text(encoding="utf-8"))

    def write_visitors(self, data: dict) -> None:
        (self.data_dir / "visitors.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


def scenario_owned_page_routes_to_right_owner() -> ScenarioResult:
    with SandboxServer() as env:
        alice = env.register_admin("alice@example.com")
        bob = env.register_admin("bob@example.com")
        publish = env.publish_page(alice, "alice-owned", "<html><body>alice</body></html>")
        publish.raise_for_status()

        visitor_id = "visitor-owned"
        env.add_visitor(visitor_id, name="正常访客")
        response = env.request_approval("alice-owned", visitor_id)

        alice_pending = env.get_pending(alice)
        bob_pending = env.get_pending(bob)
        bob_approve = env.approve(bob, "alice-owned", visitor_id)
        alice_approve = env.approve(alice, "alice-owned", visitor_id)
        visitor_state = env.read_visitors()["visitors"][0]

        if response.status_code != 200:
            return ScenarioResult(
                "owned-page",
                False,
                f"approval request returned HTTP {response.status_code}",
            )
        if alice_pending["count"] != 1 or bob_pending["count"] != 0:
            return ScenarioResult(
                "owned-page",
                False,
                f"pending mismatch: alice={alice_pending['count']} bob={bob_pending['count']}",
            )
        if bob_approve.status_code != 403:
            return ScenarioResult(
                "owned-page",
                False,
                f"non-owner approve should be 403, got {bob_approve.status_code}",
            )
        if alice_approve.status_code != 200:
            return ScenarioResult(
                "owned-page",
                False,
                f"owner approve should succeed, got {alice_approve.status_code}",
            )
        if "alice-owned" not in visitor_state.get("approved_pages", []):
            return ScenarioResult(
                "owned-page",
                False,
                "visitor was not approved after owner approval",
            )
        return ScenarioResult(
            "owned-page",
            True,
            "owned approval request is visible only to the correct owner and can only be approved by that owner",
        )


def scenario_legacy_page_without_owner_can_be_reassigned() -> ScenarioResult:
    with SandboxServer() as env:
        alice = env.register_admin("alice@example.com")
        bob = env.register_admin("bob@example.com")
        env.create_page_without_owner("legacy-no-owner")

        visitor_id = "visitor-legacy"
        env.add_visitor(visitor_id, name="旧页访客")
        response = env.request_approval("legacy-no-owner", visitor_id)

        super_pending = env.get_pending(env.super_admin_token)
        page = next(item for item in env.read_index()["pages"] if item["slug"] == "legacy-no-owner")
        transfer = env.update_page(env.super_admin_token, "legacy-no-owner", owner="alice@example.com")
        alice_pending = env.get_pending(alice)
        bob_pending = env.get_pending(bob)

        if response.status_code != 200:
            return ScenarioResult(
                "legacy-no-owner",
                False,
                f"approval request returned HTTP {response.status_code}",
            )
        if page.get("owner") != SUPER_ADMIN_EMAIL:
            return ScenarioResult(
                "legacy-no-owner",
                False,
                f"legacy page should be migrated to {SUPER_ADMIN_EMAIL}, got {page.get('owner')!r}",
            )
        if super_pending["count"] != 1:
            return ScenarioResult(
                "legacy-no-owner",
                False,
                f"super admin should see the pending request before reassignment, got {super_pending['count']}",
            )
        if transfer.status_code != 200:
            return ScenarioResult(
                "legacy-no-owner",
                False,
                f"super admin owner transfer failed with HTTP {transfer.status_code}",
            )
        if alice_pending["count"] != 1 or bob_pending["count"] != 0:
            return ScenarioResult(
                "legacy-no-owner",
                False,
                f"reassigned pending mismatch: alice={alice_pending['count']} bob={bob_pending['count']}",
            )
        return ScenarioResult(
            "legacy-no-owner",
            True,
            "legacy page is migrated to explicit super-admin ownership and can be reassigned to the intended owner",
        )


def scenario_reindex_requires_explicit_assignment() -> ScenarioResult:
    with SandboxServer() as env:
        alice = env.register_admin("alice@example.com")
        bob = env.register_admin("bob@example.com")
        env.create_orphan_page_dir("restored-page")

        reindex = env.reindex(bob)
        reindex.raise_for_status()
        page = next(item for item in env.read_index()["pages"] if item["slug"] == "restored-page")
        if page.get("owner") != SUPER_ADMIN_EMAIL:
            return ScenarioResult(
                "reindex-explicit-owner",
                False,
                f"reindex should register orphan pages under {SUPER_ADMIN_EMAIL}, got {page.get('owner')!r}",
            )

        update = env.update_page(
            env.super_admin_token,
            "restored-page",
            owner="alice@example.com",
            access="approval",
        )
        update.raise_for_status()

        visitor_id = "visitor-reindex"
        env.add_visitor(visitor_id, name="重建访客")
        response = env.request_approval("restored-page", visitor_id)
        alice_pending = env.get_pending(alice)
        bob_pending = env.get_pending(bob)

        if response.status_code != 200:
            return ScenarioResult(
                "reindex-explicit-owner",
                False,
                f"approval request returned HTTP {response.status_code}",
            )
        if alice_pending["count"] != 1 or bob_pending["count"] != 0:
            return ScenarioResult(
                "reindex-explicit-owner",
                False,
                "reindexed page was not attributed only to the explicitly assigned owner",
            )
        return ScenarioResult(
            "reindex-explicit-owner",
            True,
            "reindex leaves orphan pages under super admin until they are explicitly assigned",
        )


def scenario_publish_blocks_cross_owner_overwrite() -> ScenarioResult:
    with SandboxServer() as env:
        alice = env.register_admin("alice@example.com")
        bob = env.register_admin("bob@example.com")
        original_html = "<html><body>alice-original</body></html>"
        overwrite_html = "<html><body>bob-overwrite</body></html>"

        first_publish = env.publish_page(alice, "shared-slug", original_html)
        first_publish.raise_for_status()
        second_publish = env.publish_page(bob, "shared-slug", overwrite_html)
        stored_html = env.read_page_file("shared-slug")
        page = next(item for item in env.read_index()["pages"] if item["slug"] == "shared-slug")

        if second_publish.status_code != 403:
            return ScenarioResult(
                "publish-cross-owner-overwrite",
                False,
                f"non-owner publish should be rejected with 403, got {second_publish.status_code}",
            )
        if stored_html != original_html:
            return ScenarioResult(
                "publish-cross-owner-overwrite",
                False,
                "page HTML changed even though the second publish was rejected",
            )
        if page.get("owner") != "alice@example.com":
            return ScenarioResult(
                "publish-cross-owner-overwrite",
                False,
                f"page owner changed unexpectedly to {page.get('owner')!r}",
            )
        return ScenarioResult(
            "publish-cross-owner-overwrite",
            True,
            "publish rejects cross-owner overwrites before touching page content",
        )


def main() -> int:
    scenarios = [
        scenario_owned_page_routes_to_right_owner,
        scenario_legacy_page_without_owner_can_be_reassigned,
        scenario_reindex_requires_explicit_assignment,
        scenario_publish_blocks_cross_owner_overwrite,
    ]

    print("Running multi-owner approval attribution audit...\n")
    results = [scenario() for scenario in scenarios]

    failures = 0
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name}")
        print(f"  {result.detail}")
        if not result.ok:
            failures += 1
        print()

    if failures:
        print(f"Detected {failures} attribution issue(s).")
        return 1

    print("All approval attribution scenarios passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
