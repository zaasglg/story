"""Exercise update ordering and runtime rollback without touching Docker or systemd."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("failed_step", [None, "prepare", "migrate", "verify", "ready"])
def test_existing_server_update_and_rollback(tmp_path, failed_step):
    root = Path(__file__).parents[2]
    tools = tmp_path / "tools"
    tools.mkdir()
    stub = tools / "stub"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "name = Path(sys.argv[0]).name\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['TOOL_LOG'], 'a') as output:\n"
        "    output.write(json.dumps([name, *args]) + '\\n')\n"
        "step = None\n"
        "if name == 'host-python':\n"
        "    step = args[1]\n"
        "elif name == 'docker' and 'alembic' in args:\n"
        "    step = 'migrate'\n"
        "elif name == 'curl':\n"
        "    step = 'ready'\n"
        "if step and step == os.environ.get('FAIL_STEP'):\n"
        "    sys.exit(7)\n"
        "if name == 'docker' and args[0] == 'inspect':\n"
        "    print('1234' if 'Pid' in args[2] else 'sha256:previous')\n"
    )
    stub.chmod(0o755)
    for name in ("docker", "systemctl", "curl", "pg_dump", "pg_restore", "host-python"):
        (tools / name).symlink_to(stub)
    env_file = tmp_path / "backend.env"
    env_file.touch()
    # Substitute only host paths and root eligibility in this disposable copy.
    script = (root / "deploy/update_existing.sh").read_text()
    script = script.replace("$EUID -ne 0", "0 -ne 0")
    script = script.replace("/opt/m1917-backend/.venv/bin/python", str(tools / "host-python"))
    script = script.replace("/etc/m1917/backend.codex.env", str(env_file))
    script = script.replace("/usr/bin/pg_dump", str(tools / "pg_dump"))
    script = script.replace("/usr/bin/pg_restore", str(tools / "pg_restore"))
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    candidate = deploy / "update_existing.sh"
    candidate.write_text(script)
    log = tmp_path / "commands.jsonl"
    result = subprocess.run(
        ["bash", str(candidate)],
        env={
            **os.environ,
            "PATH": f"{tools}:{os.environ['PATH']}",
            "TOOL_LOG": str(log),
            "FAIL_STEP": failed_step or "",
        },
        text=True,
        capture_output=True,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    stop = ["systemctl", "stop", "m1917-backend"]
    start = ["systemctl", "start", "m1917-backend"]
    prepared = next(
        i
        for i, call in enumerate(calls)
        if call[:3] == ["host-python", "deploy/prepare_existing.py", "prepare"]
    )
    if failed_step == "prepare":
        assert stop not in calls
        assert start not in calls
    else:
        assert prepared < calls.index(stop)
        assert calls.index(stop) < next(i for i, call in enumerate(calls) if "alembic" in call)
        assert start in calls
    if failed_step:
        assert result.returncode == 7, result.stderr
        assert any(
            call[:3] == ["docker", "image", "tag"] and call[3].startswith("alash-backend:rollback-")
            for call in calls
        )
    else:
        assert result.returncode == 0, result.stderr
        verified = next(
            i
            for i, call in enumerate(calls)
            if call[:3] == ["host-python", "deploy/prepare_existing.py", "verify"]
        )
        assert calls.index(stop) < verified < calls.index(start)
        assert calls.index(start) < next(i for i, call in enumerate(calls) if call[0] == "curl")
