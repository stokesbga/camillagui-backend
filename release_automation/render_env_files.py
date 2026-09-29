import argparse
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from backend.version import VERSION


def render(output_dir):
    script_dir = Path(__file__).resolve().parent
    versions = yaml.safe_load((script_dir / "versions.yml").read_text())
    versions["backend_version"] = ".".join(str(v) for v in VERSION)
    environment = Environment(loader=FileSystemLoader(script_dir / "templates"))
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("requirements.txt", "cdsp_conda.yml", "pyproject.toml"):
        rendered = environment.get_template(filename + ".j2").render(versions)
        (output_dir / filename).write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render release dependency files")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    render(parser.parse_args().output_dir)
