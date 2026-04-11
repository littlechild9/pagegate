#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
CLIENT_SCRIPT = ROOT_DIR / "openclaw-skill" / "scripts" / "pagegate_client.py"
PYTHON_BIN = ROOT_DIR / "venv" / "bin" / "python"


def expect(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        env_file = tmpdir / ".env"
        result_file = tmpdir / "result.json"

        env_file.write_text(
            "\n".join([
                "PAGEGATE_URL='http://127.0.0.1:9'",
                "PAGEGATE_API_TOKEN='token-from-env-file'",
                "PAGEGATE_USERNAME='xuan'",
                "PAGEGATE_HOME_URL='http://127.0.0.1:9/xuan'",
                f"PAGEGATE_CLIENT_RESULT_FILE='{result_file}'",
                "",
            ]),
            encoding="utf-8",
        )

        env = {
            "PATH": os.environ.get("PATH", ""),
            "PAGEGATE_ENV_FILE": str(env_file),
        }

        completed = subprocess.run(
            [str(PYTHON_BIN if PYTHON_BIN.exists() else Path(sys.executable)), str(CLIENT_SCRIPT), "visitors"],
            cwd=str(ROOT_DIR),
            env=env,
            check=False,
        )

        expect(completed.returncode == 1, "client should reach request stage and fail on unreachable host")
        expect(result_file.exists(), "client should emit a result file")

        payload = json.loads(result_file.read_text(encoding="utf-8"))
        expect(payload["ok"] is False, "result payload should report failure")
        expect("Request failed" in payload["error"], "failure should come from network request, not missing env")

    print("pagegate client env loading test passed")


if __name__ == "__main__":
    main()
