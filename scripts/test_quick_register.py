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
        server.REGISTRATION_MODE = "open"

        write_json(server.INDEX_FILE, {"pages": []})
        write_json(server.VISITORS_FILE, {"visitors": []})
        write_json(server.USERS_FILE, {"users": []})

        with TestClient(server.app) as client:
            first = client.post("/api/auth/register", json={
                "pagegate_name": "Xuan's PageGate",
            })
            expect(first.status_code == 200, "quick register should succeed")
            first_payload = first.json()
            expect(first_payload["ok"] is True, "response should be ok")
            expect(first_payload["quick_registered"] is True, "response should indicate quick registration")
            expect(first_payload["pagegate_name"] == "Xuan's PageGate", "pagegate name should round-trip")
            expect(first_payload["username"] == "xuan", "username should derive cleanly from pagegate name")
            expect(first_payload["pagegate_url"].endswith("/xuan"), "pagegate URL should use derived username")
            expect(first_payload["token"].startswith("uhub_"), "token should be returned")

            users = server.read_users()["users"]
            expect(len(users) == 1, "user should be persisted")
            expect(users[0]["pagegate_name"] == "Xuan's PageGate", "stored user should keep pagegate name")
            expect(users[0]["username"] == "xuan", "stored user should keep derived username")
            expect(users[0]["email"] == "xuan@pagegate.local", "server should generate synthetic email")

            second = client.post("/api/auth/register", json={
                "pagegate_name": "Xuan's PageGate",
            })
            expect(second.status_code == 200, "second quick register should also succeed")
            second_payload = second.json()
            expect(second_payload["username"] == "xuan-2", "duplicate quick registrations should get unique usernames")
            expect(second_payload["pagegate_url"].endswith("/xuan-2"), "unique username should affect pagegate URL")

            explicit = client.post("/api/auth/register", json={
                "pagegate_name": "Friends Archive",
                "username": "friends",
            })
            expect(explicit.status_code == 200, "quick register with explicit username should succeed")
            explicit_payload = explicit.json()
            expect(explicit_payload["username"] == "friends", "explicit username should be respected")
            expect(explicit_payload["pagegate_name"] == "Friends Archive", "explicit username path should still keep pagegate name")

    print("quick register test passed")


if __name__ == "__main__":
    main()
