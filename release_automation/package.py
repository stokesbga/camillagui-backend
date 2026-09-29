"""Stage source releases or freeze, verify and archive a native bundle."""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from release_automation.smoke import smoke_test

ROOT = Path(__file__).resolve().parent.parent


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    path.with_name(path.name + ".sha256").write_text(
        f"{digest.hexdigest()}  {path.name}\n", encoding="utf-8"
    )


def stage_source(output, env_dir, frontend_repository, frontend_ref):
    if not (ROOT / "build/index.html").is_file():
        raise SystemExit("Missing build/index.html: build/download the frontend first")
    stage = output / "source"
    # Refuse to mix an earlier release's files into this one.
    stage.mkdir(parents=True, exist_ok=False)
    for directory in ("backend", "config", "build", "docs", "release_automation"):
        shutil.copytree(
            ROOT / directory,
            stage / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for filename in ("main.py", "README.md", "LICENSE.txt"):
        shutil.copy2(ROOT / filename, stage / filename)
    for filename in ("requirements.txt", "cdsp_conda.yml", "pyproject.toml"):
        shutil.copy2(env_dir / filename, stage / filename)
    backend_ref = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT
        ).strip()
    )
    (stage / "release.json").write_text(
        json.dumps(
            {
                "backend_commit": backend_ref,
                "backend_dirty": dirty,
                "frontend_repository": frontend_repository,
                "frontend_commit": frontend_ref,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    archive = Path(shutil.make_archive(str(output / "camillagui"), "zip", stage))
    checksum(archive)
    return archive


def bundle(output, asset_name):
    if not (ROOT / "build/index.html").is_file():
        raise SystemExit("Missing build/index.html")
    if Path(asset_name).name != asset_name or not asset_name.endswith(
        (".tar.gz", ".zip")
    ):
        raise SystemExit("Asset name must be a .tar.gz or .zip filename")
    dist = output / "dist"
    frozen = dist / "camillagui_backend"
    if frozen.exists():
        raise SystemExit(f"Use a fresh output directory; {frozen} already exists")
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(ROOT / "main.py"),
            "--noconfirm",
            "--clean",
            "--name",
            "camillagui_backend",
            "--distpath",
            str(dist),
            "--workpath",
            str(output / "work"),
            "--specpath",
            str(output),
            "--add-data",
            f"{ROOT / 'config'}:config",
            "--add-data",
            f"{ROOT / 'build'}:build",
            "--collect-data",
            "camilladsp_plot",
        ],
        check=True,
        cwd=ROOT,
    )
    # ALSA executes this separately with the host's /usr/bin/python3.
    shutil.copy2(ROOT / "backend/spectrum_tap.py", frozen / "spectrum_tap.py")
    shutil.copytree(ROOT / "docs", frozen / "docs")
    shutil.copy2(ROOT / "LICENSE.txt", frozen / "LICENSE.txt")
    metadata_path = ROOT / "release.json"
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    metadata.update(
        {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        }
    )
    (frozen / "release.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (frozen / "python-packages.txt").write_text(
        subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    )
    smoke_test(frozen)
    archive = output / asset_name
    if asset_name.endswith(".zip"):
        shutil.make_archive(str(archive.with_suffix("")), "zip", dist)
    else:

        def normalize(info):
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            return info

        with tarfile.open(archive, "w:gz") as tar:
            tar.add(frozen, arcname=frozen.name, filter=normalize)
    checksum(archive)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release-dist")
    commands = parser.add_subparsers(dest="command", required=True)
    source = commands.add_parser("source")
    source.add_argument("--env-dir", type=Path, default=ROOT / ".release-env")
    source.add_argument("--frontend-repository", required=True)
    source.add_argument(
        "--frontend-ref", required=True, help="Resolved frontend commit SHA"
    )
    frozen = commands.add_parser("bundle")
    frozen.add_argument("--asset-name", required=True)
    args = parser.parse_args()
    if args.command == "source":
        archive = stage_source(
            args.output_dir.resolve(),
            args.env_dir.resolve(),
            args.frontend_repository,
            args.frontend_ref,
        )
    else:
        archive = bundle(args.output_dir.resolve(), args.asset_name)
    print(archive)


if __name__ == "__main__":
    main()
