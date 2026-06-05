from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_all_in_one_script_avoids_bash4_only_builtins():
    script = (REPO_ROOT / "scripts" / "tdnet_all_in_one.sh").read_text(encoding="utf-8")

    assert "mapfile" not in script
    assert "readarray" not in script


def test_all_in_one_dry_run_keeps_single_date_without_trailing_newline(tmp_path):
    scripts_dir = tmp_path / "scripts"
    venv_bin = tmp_path / ".venv" / "bin"
    scripts_dir.mkdir()
    venv_bin.mkdir(parents=True)
    shutil.copy(REPO_ROOT / "scripts" / "tdnet_all_in_one.sh", scripts_dir / "tdnet_all_in_one.sh")
    (venv_bin / "python").symlink_to(sys.executable)
    tdnet = venv_bin / "tdnet"
    tdnet.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    tdnet.chmod(0o755)

    bash = "/bin/bash" if Path("/bin/bash").exists() else "bash"
    env = {
        **os.environ,
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }
    result = subprocess.run(
        [
            bash,
            str(scripts_dir / "tdnet_all_in_one.sh"),
            "--dry-run",
            "--skip-postgres",
            "--skip-install",
            "--days",
            "1",
            "--download-limit",
            "1",
            "--parse-limit",
            "1",
            "--parse-text-limit",
            "1",
            "--tag-limit",
            "1",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    assert "(1 days)" in result.stdout
    assert "COMMAND_DRY_RUN step=scrape:" in result.stdout
