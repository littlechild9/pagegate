#!/usr/bin/env python3

import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import server


def write_json(path: Path, payload: dict):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_page(pages_dir: Path, slug: str, title: str):
    page_dir = pages_dir / slug
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "index.html").write_text(
        f"<html><body><h1>{title}</h1></body></html>",
        encoding="utf-8",
    )


def expect(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir = root / "data"
        pages_dir = root / "pages"
        data_dir.mkdir()
        pages_dir.mkdir()

        server.DATA_DIR = data_dir
        server.PAGES_DIR = pages_dir
        server.INDEX_FILE = data_dir / "index.json"
        server.VISITORS_FILE = data_dir / "visitors.json"
        server.USERS_FILE = data_dir / "users.json"

        write_page(pages_dir, "alice-approval", "Alice Approval")
        write_page(pages_dir, "alice-private", "Alice Private")
        write_page(pages_dir, "bob-private", "Bob Private")

        write_json(server.INDEX_FILE, {
            "pages": [
                {
                    "slug": "alice-approval",
                    "title": "Alice Approval",
                    "category": "test",
                    "access": "approval",
                    "description": "",
                    "owner": "alice@example.com",
                    "created_at": "2026-04-09T00:00:00+00:00",
                    "updated_at": "2026-04-09T00:00:00+00:00",
                },
                {
                    "slug": "alice-private",
                    "title": "Alice Private",
                    "category": "test",
                    "access": "private",
                    "description": "",
                    "owner": "alice@example.com",
                    "created_at": "2026-04-09T00:00:00+00:00",
                    "updated_at": "2026-04-09T00:00:00+00:00",
                },
                {
                    "slug": "bob-private",
                    "title": "Bob Private",
                    "category": "test",
                    "access": "private",
                    "description": "",
                    "owner": "bob@example.com",
                    "created_at": "2026-04-09T00:00:00+00:00",
                    "updated_at": "2026-04-09T00:00:00+00:00",
                },
            ]
        })

        write_json(server.USERS_FILE, {
            "users": [
                {
                    "email": "alice@example.com",
                    "password_hash": "",
                    "token": "alice-token",
                    "role": "admin",
                    "created_at": "2026-04-09T00:00:00+00:00",
                },
                {
                    "email": "bob@example.com",
                    "password_hash": "",
                    "token": "bob-token",
                    "role": "admin",
                    "created_at": "2026-04-09T00:00:00+00:00",
                },
            ]
        })

        write_json(server.VISITORS_FILE, {
            "visitors": [
                {
                    "id": "visitor-1",
                    "provider": "dingtalk",
                    "name": "Visitor One",
                    "avatar": "",
                    "first_seen": "2026-04-09T00:00:00+00:00",
                    "approved_pages": [],
                    "pending_pages": ["alice-approval"],
                    "whitelisted_owners": [],
                    "blocked": False,
                }
            ]
        })

        auth = {"Authorization": "Bearer alice-token"}
        visitor_cookie = {
            server.SESSION_COOKIE: server.signer.sign("visitor-1").decode()
        }
        alice_approval_url = "/alice/alice-approval/"
        alice_private_url = "/alice/alice-private/"
        bob_private_url = "/bob/bob-private/"

        with TestClient(server.app) as client:
            dashboard_before = client.get("/dashboard?token=alice-token")
            expect(dashboard_before.status_code == 200, "dashboard should render")
            expect("用户级白名单" in dashboard_before.text, "dashboard should show owner whitelist section")
            expect("Visitor One" in dashboard_before.text, "dashboard should include known visitor")

            visitors_before = client.get("/api/visitors", headers=auth)
            expect(visitors_before.status_code == 200, "visitor list should succeed")
            payload = visitors_before.json()
            expect(payload["count"] == 1, "alice should see one applicant")
            expect(payload["visitors"][0]["whitelisted"] is False, "visitor should start unwhitelisted")
            expect(payload["visitors"][0]["requested_pages"] == ["alice-approval"], "requested pages should be scoped to alice")

            approval_before = client.get(alice_approval_url, cookies=visitor_cookie)
            expect(approval_before.status_code == 200, "pending approval page should render waiting screen")
            expect(
                ("审批通过后页面会自动刷新" in approval_before.text)
                or ("审批通过后会自动进入页面" in approval_before.text),
                "approval page should still be pending before whitelist",
            )

            private_before = client.get(alice_private_url, cookies=visitor_cookie)
            expect(private_before.status_code == 403, "private page should reject before whitelist")

            whitelist_resp = client.post("/api/visitors/visitor-1/whitelist", headers=auth)
            expect(whitelist_resp.status_code == 200, "whitelist add should succeed")
            expect(
                whitelist_resp.json()["cleared_pending_pages"] == ["alice-approval"],
                "whitelist should clear alice pending approvals",
            )

            visitors_after = client.get("/api/visitors", headers=auth)
            expect(visitors_after.json()["visitors"][0]["whitelisted"] is True, "visitor should now be whitelisted")

            dashboard_after = client.get("/dashboard?token=alice-token")
            expect("已在白名单" in dashboard_after.text, "dashboard should reflect whitelist status")

            approval_after = client.get(alice_approval_url, cookies=visitor_cookie)
            expect(approval_after.status_code == 200, "approval page should open after whitelist")
            expect("Alice Approval" in approval_after.text, "approval page content should render after whitelist")

            private_after = client.get(alice_private_url, cookies=visitor_cookie)
            expect(private_after.status_code == 200, "private page should open after whitelist")
            expect("Alice Private" in private_after.text, "private page content should render after whitelist")

            bob_private = client.get(bob_private_url, cookies=visitor_cookie)
            expect(bob_private.status_code == 403, "alice whitelist must not grant bob's private page")

            remove_resp = client.delete("/api/visitors/visitor-1/whitelist", headers=auth)
            expect(remove_resp.status_code == 200, "whitelist removal should succeed")

            private_removed = client.get(alice_private_url, cookies=visitor_cookie)
            expect(private_removed.status_code == 403, "private page should close again after whitelist removal")

    print("owner whitelist test passed")


if __name__ == "__main__":
    main()
