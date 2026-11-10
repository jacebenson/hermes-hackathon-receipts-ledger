"""Two manifests, two jobs, and the plugin needs both.

``plugin.yaml`` is the agent manifest: tools, config schema, description.
``dashboard/manifest.json`` is what the web server scans for -- it only looks at
``<plugins root>/<name>/dashboard/manifest.json``. A plugin with the YAML and no
JSON is never even a candidate, so its router never mounts, every request 404s,
and the pane looks broken. That is exactly what happened in the real app while
these tests were green, because they imported plugin_api directly.

Parsed with the stdlib on purpose: the plugin must not need PyYAML to be tested.
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def dashboard_manifest() -> dict:
    return json.loads((ROOT / "dashboard" / "manifest.json").read_text())


def declared_api() -> str:
    api = dashboard_manifest().get("api")
    assert api, "dashboard/manifest.json must declare api: so Hermes mounts the routes"
    return api


def test_dashboard_manifest_is_discoverable():
    manifest = dashboard_manifest()
    assert manifest["name"] == "receipt-ledger"
    assert manifest.get("label"), "the sidebar row needs a label"
    assert (ROOT / "dashboard" / manifest["api"]).is_file(), f"dashboard/{manifest['api']} does not exist"


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


def test_api_file_imports_the_way_the_host_imports_it():
    """Hermes loads this file by path with no sys.path help; siblings must still resolve."""
    api = declared_api()
    code = f'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location("host_style_check", {str(ROOT / "dashboard" / api)!r})
mod = importlib.util.module_from_spec(spec)
sys.modules["host_style_check"] = mod
spec.loader.exec_module(mod)
assert hasattr(mod, "router")
print("ok")
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=str(ROOT))
    assert result.returncode == 0, result.stderr[-800:]
    assert "ok" in result.stdout


def test_agent_manifest_version_is_semver():
    text = (ROOT / "plugin.yaml").read_text()
    match = re.search(r'^version:\s*"?([0-9.]+)"?\s*$', text, re.M)
    assert match, "plugin.yaml needs a version"
    assert match.group(1).count(".") == 2, match.group(1)
