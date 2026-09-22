"""Validate notebook-generated files without modifying the host VM."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "DD2482_Ansible_IaC_Tutorial.ipynb"


def main() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    code_cells = []
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] == "code":
            source = "".join(cell["source"])
            compile(source, f"notebook cell {index}", "exec")
            code_cells.append(source)

    with tempfile.TemporaryDirectory(prefix="dd2482-notebook-check-") as temporary:
        work = Path(temporary)
        scope = {
            "WORK": work,
            "BACKEND_PORT": 38000,
            "PUBLIC_PORT": 48000,
            "CAN_INSTALL_OS_PACKAGES": False,
            "APP_VENV": work / "venv",
            "NGINX_BINARY": str(work / "nginx"),
            "sys": sys,
            "textwrap": textwrap,
            "json": json,
        }
        for prefix in (
            "import textwrap\n",
            "write_source('templates/app.py.j2'",
            'playbook_yaml = """',
        ):
            matches = [source for source in code_cells if source.startswith(prefix)]
            if len(matches) != 1:
                raise AssertionError(f"Expected one notebook cell beginning {prefix!r}")
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(matches[0], "notebook source cell", "exec"), scope)

        variables = yaml.safe_load((work / "vars.yml").read_text())
        assert variables["backend_port"] == 38000
        assert variables["public_port"] == 48000
        assert variables["app_version"] == "1.0"
        assert variables["manage_os_packages"] is False
        assert variables["tutorial_workdir"] == str(work)
        inventory = (work / "inventory.ini").read_text()
        assert "[tutorial]" in inventory
        assert "localhost ansible_connection=local" in inventory

        templates = jinja2.Environment(loader=jinja2.FileSystemLoader(work / "templates"))
        for version in ("1.0", "2.0"):
            deployment = dict(variables, app_version=version, deploy_dir=str(work / "deployed"))
            rendered_config = templates.get_template("config.json.j2").render(**deployment)
            assert json.loads(rendered_config) == {
                "application": "dd2482-demo",
                "version": version,
                "environment": "tutorial",
            }
        deployment = dict(variables, deploy_dir=str(work / "deployed"))
        rendered_app = templates.get_template("app.py.j2").render(**deployment)
        compile(rendered_app, "rendered app.py", "exec")
        assert str(work / "deployed/config.json") in rendered_app
        rendered_nginx = templates.get_template("nginx.conf.j2").render(**deployment)
        assert "127.0.0.1:38000" in rendered_nginx
        assert "127.0.0.1:48000" in rendered_nginx
        playbook = yaml.safe_load((work / "playbook.yml").read_text())
        assert len(playbook) == 1 and playbook[0]["hosts"] == "tutorial"

        ansible_home = work / "ansible-home"
        ansible_home.mkdir()
        environment = dict(os.environ, ANSIBLE_HOME=str(ansible_home))
        subprocess.run(
            ["ansible-playbook", "-i", "inventory.ini", "--syntax-check", "playbook.yml"],
            cwd=work,
            env=environment,
            check=True,
        )

    print("Validated notebook code, generated files, and Ansible syntax.")


if __name__ == "__main__":
    main()
