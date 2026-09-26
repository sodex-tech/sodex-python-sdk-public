"""Install the reviewed SDK in an isolated runtime and check public connectivity."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

SDK_REVISION = "732ac02c0297e2b8ce65d23780cdab246c659d63"
SDK_REPOSITORY = "https://github.com/sodex-tech/sodex-python-sdk-public.git"
DEFAULT_RUNTIME = Path.home() / ".cache" / "sodex-skill" / SDK_REVISION[:12]


def run(*args: str) -> None:
    subprocess.run(args, check=True, stdout=sys.stderr)


def install(runtime: Path) -> tuple[Path, Path]:
    sdk = runtime / "sdk"
    environment = runtime / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    runtime.mkdir(parents=True, exist_ok=True)
    if not sdk.exists():
        run("git", "clone", "--quiet", "--no-checkout", SDK_REPOSITORY, str(sdk))
        run("git", "-C", str(sdk), "checkout", "--quiet", SDK_REVISION)
    revision = subprocess.check_output(
        ["git", "-C", str(sdk), "rev-parse", "HEAD"], text=True
    ).strip()
    changes = subprocess.check_output(
        ["git", "-C", str(sdk), "status", "--porcelain"], text=True
    ).strip()
    if revision != SDK_REVISION or changes:
        raise RuntimeError(
            "Cached SDK differs from the reviewed revision. Preserve your edits "
            "and use --runtime-dir with a new empty directory."
        )
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    marker = environment / "sodex-revision"
    if not marker.exists() or marker.read_text().strip() != SDK_REVISION:
        run(str(python), "-m", "pip", "install", "--disable-pip-version-check", str(sdk))
        marker.write_text(SDK_REVISION + "\n")
    return python, sdk


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--network", choices=("mainnet", "testnet"), default="mainnet")
    parser.add_argument("--skip-check", action="store_true", help="Install without calling Gateway")
    args = parser.parse_args()
    if sys.version_info < (3, 9):
        parser.error("Python 3.9 or later is required")
    try:
        python, sdk = install(args.runtime_dir.expanduser().resolve())
        check = None
        if not args.skip_check:
            result = subprocess.check_output([
                str(python), str(Path(__file__).with_name("doctor.py")),
                "--network", args.network,
            ], text=True)
            check = json.loads(result)
        print(json.dumps({
            "python": str(python), "sdk": str(sdk),
            "revision": SDK_REVISION, "check": check,
        }, indent=2))
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
