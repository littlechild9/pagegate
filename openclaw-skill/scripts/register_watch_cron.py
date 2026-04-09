#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_RESULT_FILE = os.path.expanduser("~/.openclaw/workspace/memory/pagegate-register-watch-cron-result.json")
_DEVNULL = open(os.devnull, "w", encoding="utf-8")
sys.stdout = _DEVNULL
sys.stderr = _DEVNULL

SCRIPT_DIR = Path(__file__).resolve().parent
CHECK_WATCHER = SCRIPT_DIR / "check-watcher.sh"
DEFAULT_JOB_NAME = "PageGate Watcher Keepalive"
DEFAULT_CRON = "*/1 * * * *"
DEFAULT_TIMEOUT_SECONDS = 20


def result_file_path() -> Path:
    return Path(os.environ.get("PAGEGATE_REGISTER_CRON_RESULT_FILE", DEFAULT_RESULT_FILE)).expanduser()


def emit(payload: dict, exit_code: int = 0):
    result_file = result_file_path()
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    raise SystemExit(exit_code)


def fail(message: str, exit_code: int = 1, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    emit(payload, exit_code=exit_code)


def extract_json(raw: str):
    decoder = json.JSONDecoder()
    text = raw.strip()
    for idx, ch in enumerate(text):
        if ch not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[idx:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError("No JSON object found in command output")


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        raise RuntimeError(combined or f"command failed: {' '.join(cmd)}")
    return combined


def list_jobs():
    payload = extract_json(run(["openclaw", "cron", "list", "--json"]))
    return payload.get("jobs", [])


def find_jobs_by_name(name: str):
    return [job for job in list_jobs() if job.get("name") == name]


def build_message():
    return (
        "请由 OpenClaw 主 agent 运行一次 PageGate watcher 健康检查，只执行这条命令，不要做额外探索：\n\n"
        f"`bash \"{CHECK_WATCHER}\"`\n\n"
        "直接返回命令 stdout；如果命令失败，只返回一行错误。"
    )


def build_command(args, existing_job_id=None):
    base = ["openclaw", "cron"]
    if existing_job_id:
        cmd = base + ["edit", existing_job_id]
    else:
        cmd = base + ["add", "--json"]
    cmd.extend([
        "--name",
        args.name,
        "--description",
        args.description,
        "--cron",
        args.cron,
        "--session",
        "isolated",
        "--wake",
        "now",
        "--no-deliver",
        "--light-context",
        "--thinking",
        "minimal",
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--message",
        build_message(),
        "--exact",
    ])
    if args.tz:
        cmd.extend(["--tz", args.tz])
    return cmd


def main():
    parser = argparse.ArgumentParser(description="Register or update the PageGate watcher keepalive cron job")
    parser.add_argument("--name", default=DEFAULT_JOB_NAME)
    parser.add_argument("--description", default="Keep the PageGate watcher healthy via OpenClaw cron")
    parser.add_argument("--cron", default=DEFAULT_CRON)
    parser.add_argument("--tz", default="")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        emit({
            "ok": True,
            "dryRun": True,
            "action": "add",
            "existingJobId": None,
            "jobName": args.name,
            "cron": args.cron,
            "agentLed": True,
            "checkWatcher": str(CHECK_WATCHER),
            "command": build_command(args),
        })

    if not CHECK_WATCHER.exists():
        fail(f"check-watcher script not found: {CHECK_WATCHER}")

    if not shutil.which("openclaw"):
        fail("openclaw CLI not found")

    matching_jobs = find_jobs_by_name(args.name)
    existing_job = matching_jobs[0] if matching_jobs else None
    cmd = build_command(args, existing_job_id=existing_job.get("id") if existing_job else None)

    if existing_job:
        run(cmd)
        refreshed_jobs = find_jobs_by_name(args.name)
        current_job = refreshed_jobs[0] if refreshed_jobs else existing_job
        action = "updated"
    else:
        current_job = extract_json(run(cmd))
        action = "created"

    emit({
        "ok": True,
        "action": action,
        "agentLed": True,
        "job": current_job,
        "checkWatcher": str(CHECK_WATCHER),
    })


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        fail("Cancelled by user", exit_code=130)
    except Exception as e:
        fail(f"Unexpected error: {e}")
