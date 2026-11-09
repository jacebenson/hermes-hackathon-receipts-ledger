"""The manifest is the only way Hermes learns this plugin has a backend.

Hermes mounts /api/plugins/<name>/ only when plugin.yaml declares ``api:`` with a
file inside the plugin's dashboard directory that exposes ``router``. Without it
the desktop half loads, the pane renders, and every request 404s -- which looks
like a broken plugin rather than a missing line of YAML.

Parsed with the stdlib on purpose: the plugin must not need PyYAML to be tested.
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def declared_api() -> str:
    text = (ROOT / "plugin.yaml").read_text()
    match = re.search(r"^api:\s*(\S+)\s*$", text, re.M)
    assert match, "plugin.yaml must declare api: so Hermes mounts the routes"
    return match.group(1).strip("\"'")


def test_manifest_declares_the_api_file():
    api = declared_api()
    assert (ROOT / "dashboard" / api).is_file(), f"dashboard/{api} does not exist"


def test_api_file_exposes_a_router():
    api = declared_api()
    dashboard = str(ROOT / "dashboard")
    if dashboard not in sys.path:
        sys.path.insert(0, dashboard)
    spec = importlib.util.spec_from_file_location("manifest_api_check", ROOT / "dashboard" / api)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "router"), "the declared api file must expose router"
    paths = [getattr(route, "path", "") for route in module.router.routes]
    assert any(path.startswith("/") for path in paths), paths


def test_manifest_version_is_semver():
    text = (ROOT / "plugin.yaml").read_text()
    match = re.search(r'^version:\s*"?([0-9.]+)"?\s*$', text, re.M)
    assert match, "plugin.yaml needs a version"
    assert match.group(1).count(".") == 2, match.group(1)
