"""
Install dependencies for a teaching_skills sub-skill if missing.

Usage:
  python teaching_skills/ensure_skill_deps.py pdf
  python teaching_skills/ensure_skill_deps.py xlsx
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def ensure_pip_deps(skill_dir: Path) -> None:
    req = skill_dir / "requirements.txt"
    if not req.exists():
        return

    allow_pip = os.environ.get("AGENT_ALLOW_PIP_INSTALL", "1").strip().lower()
    if allow_pip in ("0", "false", "no", "off"):
        raise RuntimeError(
            f"AGENT_ALLOW_PIP_INSTALL=0, but requirements.txt exists for {skill_dir.name}"
        )

    # Use current interpreter to avoid "wrong pip" problems on Windows.
    _run([sys.executable, "-m", "pip", "install", "-r", str(req)])


def ensure_npm_deps(skill_dir: Path) -> None:
    pkg = skill_dir / "package.json"
    if not pkg.exists():
        return

    # Keep it simple: npm install is idempotent (already satisfied deps are fast).
    _run(["npm", "install"], cwd=skill_dir)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Missing skill name. Example: python teaching_skills/ensure_skill_deps.py pdf")

    skill_name = sys.argv[1].strip()
    base_dir = Path(__file__).resolve().parent
    skill_dir = base_dir / skill_name
    if not skill_dir.exists():
        raise SystemExit(f"Skill dir not found: {skill_dir}")

    ensure_pip_deps(skill_dir)
    ensure_npm_deps(skill_dir)


if __name__ == "__main__":
    main()

