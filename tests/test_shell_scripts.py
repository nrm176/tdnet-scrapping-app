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
            "--legacy-local",
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


def _fake_python(path: Path, record: Path, exit_code: int = 0) -> None:
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {record}\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_daily_metadata_validates_date_and_forwards_exact_controller_argv(tmp_path):
    script = REPO_ROOT / "scripts" / "tdnet_daily_metadata.sh"
    research_root = tmp_path / "research"
    python = research_root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    record = tmp_path / "argv"
    _fake_python(python, record)

    env = {**os.environ, "RESEARCH_ROOT": str(research_root), "TDNET_PYTHON": str(python)}
    result = subprocess.run(
        ["/bin/bash", str(script), "--date", "2026-09-10"],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert result.returncode == 0
    assert record.read_text(encoding="utf-8").splitlines() == [
        "-m", "hedge_research.disclosure_environment", "exec", "tdnet", "--",
        str(python), "-m", "tdnet.research_ingest", "--date", "2026-09-10"
    ]
    assert subprocess.run(["/bin/bash", str(script), "--date", "2026-9-10"], env=env).returncode != 0
    assert subprocess.run(["/bin/bash", str(script), "--date", "2026-02-30"], env=env).returncode != 0


def test_daily_metadata_propagates_child_failure_and_requires_dependency(tmp_path):
    script = REPO_ROOT / "scripts" / "tdnet_daily_metadata.sh"
    root = tmp_path / "research"
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    _fake_python(python, tmp_path / "argv", exit_code=37)
    env = {**os.environ, "RESEARCH_ROOT": str(root), "TDNET_PYTHON": str(python)}
    assert subprocess.run(["/bin/bash", str(script), "--date", "2026-09-10"], env=env).returncode == 37
    missing = {**env, "TDNET_PYTHON": str(tmp_path / "missing-python")}
    assert subprocess.run(["/bin/bash", str(script), "--date", "2026-09-10"], env=missing).returncode != 0


def test_daily_metadata_rejects_duplicate_singleton_options(tmp_path):
    script = REPO_ROOT / "scripts" / "tdnet_daily_metadata.sh"
    root = tmp_path / "research"
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    record = tmp_path / "argv"
    _fake_python(python, record)
    env = {**os.environ, "RESEARCH_ROOT": str(root), "TDNET_PYTHON": str(python)}
    for args in (
        ["--date", "2026-09-10", "--date", "2026-09-11"],
        ["--date", "2026-09-10", "--output", "a.json", "--output", "b.json"],
    ):
        result = subprocess.run(["/bin/bash", str(script), *args], env=env)
        assert result.returncode == 2
        assert not record.exists()


def test_all_in_one_requires_legacy_opt_in_and_strips_marker(tmp_path):
    script = REPO_ROOT / "scripts" / "tdnet_all_in_one.sh"
    no_opt_in = subprocess.run(["/bin/bash", str(script), "--dry-run"], cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert no_opt_in.returncode != 0
    assert "--legacy-local" in no_opt_in.stdout
    opted_in = subprocess.run(
        ["/bin/bash", str(script), "--legacy-local", "--dry-run", "--skip-postgres", "--skip-install", "--skip-scrape", "--skip-download", "--skip-parse", "--skip-parse-text", "--skip-tag"],
        cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    assert "Unknown option: --legacy-local" not in opted_in.stdout


def test_all_in_one_with_ocr_requires_and_forwards_legacy_opt_in(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    wrapper = scripts_dir / "tdnet_all_in_one_with_ocr.sh"
    shutil.copy(REPO_ROOT / "scripts" / "tdnet_all_in_one_with_ocr.sh", wrapper)
    record = tmp_path / "argv"
    child = scripts_dir / "tdnet_all_in_one.sh"
    _fake_python(child, record)

    for args in ([], ["--dry-run"]):
        result = subprocess.run(["/bin/bash", str(wrapper), *args], cwd=tmp_path)
        assert result.returncode == 2
        assert not record.exists()

    result = subprocess.run(
        ["/bin/bash", str(wrapper), "--legacy-local", "--days", "7"],
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert record.read_text(encoding="utf-8").splitlines() == [
        "--legacy-local", "--with-ocr", "--days", "7"
    ]


def test_daily_metadata_default_research_root_is_canonical():
    script = (REPO_ROOT / "scripts" / "tdnet_daily_metadata.sh").read_text(encoding="utf-8")

    assert 'RESEARCH_ROOT="${RESEARCH_ROOT:-/Users/nrm176p/GitHub2/hedge-fund-takedo}"' in script
    assert "input-observations" not in script
