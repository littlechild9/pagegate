#!/usr/bin/env python3
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "openclaw-skill" / "scripts" / "pagegate_onboard.py"


def load_module():
    spec = importlib.util.spec_from_file_location("pagegate_onboard", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def main():
    old_home = os.environ.get("HOME")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["HOME"] = tmpdir
            home = Path(tmpdir)

            write_json(
                home / ".openclaw" / "openclaw.json",
                {
                    "channels": {
                        "discord": {"enabled": True},
                        "openclaw-weixin": {"enabled": True},
                    },
                    "gateway": {"port": 18789},
                },
            )
            weixin_session_file = home / ".openclaw" / "agents" / "main" / "sessions" / "weixin.jsonl"
            discord_session_file = home / ".openclaw" / "agents" / "main" / "sessions" / "discord.jsonl"
            write_jsonl(
                weixin_session_file,
                [
                    {
                        "type": "message",
                        "timestamp": "2099-04-11T10:00:00Z",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "pagegate42"}],
                        },
                    }
                ],
            )
            write_jsonl(
                discord_session_file,
                [
                    {
                        "type": "message",
                        "timestamp": "2099-04-11T09:00:00Z",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "hello from discord"}],
                        },
                    }
                ],
            )
            write_json(
                home / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json",
                {
                    "agent:main:openclaw-weixin:direct:o9cq@im.wechat": {
                        "updatedAt": 1775901287977,
                        "deliveryContext": {
                            "channel": "openclaw-weixin",
                            "to": "o9cq803hkgAzZHoDOwT1V9a6yeHQ@im.wechat",
                            "accountId": "0d37d3b58715-im-bot",
                        },
                        "sessionFile": str(weixin_session_file),
                    },
                    "agent:main:discord:direct:517235631227666440": {
                        "updatedAt": 1775800000000,
                        "deliveryContext": {
                            "channel": "discord",
                            "to": "user:517235631227666440",
                            "accountId": "",
                        },
                        "sessionFile": str(discord_session_file),
                    },
                },
            )
            write_json(
                home / ".openclaw" / "openclaw-weixin" / "accounts.json",
                ["0d37d3b58715-im-bot"],
            )

            module = load_module()
            discovered = module.discover_openclaw_config("pagegate42")

            assert discovered["channels"] == ["discord", "openclaw-weixin"], discovered
            assert discovered["gateway_url"] == "http://127.0.0.1:18789", discovered
            assert discovered["account"] == "0d37d3b58715-im-bot", discovered
            assert discovered["current_route"]["channel"] == "openclaw-weixin", discovered
            assert discovered["current_route"]["target"] == "o9cq803hkgAzZHoDOwT1V9a6yeHQ@im.wechat", discovered
            assert discovered["current_route"]["account"] == "0d37d3b58715-im-bot", discovered
            assert discovered["handshake_route"]["channel"] == "openclaw-weixin", discovered
            assert discovered["handshake_route"]["target"] == "o9cq803hkgAzZHoDOwT1V9a6yeHQ@im.wechat", discovered

            args = SimpleNamespace(
                notify_channel=None,
                notify_target=None,
                notify_account=None,
                notify_handshake="pagegate42",
            )
            route = module.resolve_notify_route(args, discovered)
            assert route == {
                "channel": "openclaw-weixin",
                "target": "o9cq803hkgAzZHoDOwT1V9a6yeHQ@im.wechat",
                "account": "0d37d3b58715-im-bot",
                "source": "handshake",
            }, route

            explicit_args = SimpleNamespace(
                notify_channel="discord",
                notify_target="user:517235631227666440",
                notify_account="",
                notify_handshake="",
            )
            explicit_route = module.resolve_notify_route(explicit_args, discovered)
            assert explicit_route["channel"] == "discord", explicit_route
            assert explicit_route["target"] == "user:517235631227666440", explicit_route
            assert explicit_route["account"] == "0d37d3b58715-im-bot", explicit_route
            assert explicit_route["source"] == "discovered", explicit_route

            default_args = module.parser.parse_args(
                ["--auth-mode", "token", "--api-token", "tok_test"]
            )
            assert default_args.start_watcher is True, default_args
            assert default_args.notify_handshake is None, default_args

            no_watcher_args = module.parser.parse_args(
                ["--auth-mode", "token", "--api-token", "tok_test", "--no-start-watcher"]
            )
            assert no_watcher_args.start_watcher is False, no_watcher_args

            print("ok")
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


if __name__ == "__main__":
    main()
