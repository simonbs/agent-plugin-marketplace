#!/usr/bin/env python3
"""Generate marketplace metadata from portable Agent Plugin manifests."""

from __future__ import annotations

import argparse
import json
import ipaddress
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "marketplace.config.json"
PLUGINS_DIR = ROOT / "plugins"
CLAUDE_MARKETPLACE_PATH = ROOT / ".claude-plugin" / "marketplace.json"
AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGIN_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
PLUGIN_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if generated files are not up to date.",
    )
    args = parser.parse_args()

    config = read_json(CONFIG_PATH)
    marketplace = require_object(config, "marketplace", CONFIG_PATH)
    defaults = config.get("defaults", {})
    plugin_overrides = config.get("plugins", {})
    if not isinstance(defaults, dict) or not isinstance(plugin_overrides, dict):
        raise SystemExit("`defaults` and `plugins` in marketplace.config.json must be objects")

    plugins = discover_plugins()
    writes: dict[Path, Any] = {}
    writes[CLAUDE_MARKETPLACE_PATH] = build_claude_marketplace(
        marketplace,
        defaults,
        plugin_overrides,
        plugins,
    )
    writes.update(build_claude_mcp_configs(plugins))

    if args.check:
        return check_outputs(writes)

    for path, payload in writes.items():
        write_json(path, payload)
    print(f"Generated {len(writes)} metadata files for {len(plugins)} plugin(s).")
    return 0


def discover_plugins() -> list[tuple[Path, dict[str, Any]]]:
    if not PLUGINS_DIR.is_dir():
        return []
    plugins: list[tuple[Path, dict[str, Any]]] = []
    for plugin_root in sorted(path for path in PLUGINS_DIR.iterdir() if path.is_dir()):
        manifest_path = plugin_root / "plugin.json"
        if not manifest_path.is_file():
            continue
        manifest = read_json(manifest_path)
        validate_portable_manifest(plugin_root, manifest)
        plugins.append((plugin_root, manifest))
    return plugins


def validate_portable_manifest(plugin_root: Path, manifest: dict[str, Any]) -> None:
    name = manifest.get("name")
    if not isinstance(name, str) or not PLUGIN_NAME_RE.fullmatch(name):
        raise SystemExit(f"{plugin_root}/plugin.json has an invalid Agent Plugin name")
    if "--" in name or ".." in name:
        raise SystemExit(f"{plugin_root}/plugin.json has an invalid Agent Plugin name")
    if plugin_root.name != name:
        raise SystemExit(f"{plugin_root}/plugin.json name must match its plugin directory")
    if manifest.get("$schema") != AGENT_PLUGIN_SCHEMA:
        raise SystemExit(f"{plugin_root}/plugin.json must use {AGENT_PLUGIN_SCHEMA}")
    validate_mcp_config(plugin_root)


def validate_mcp_config(plugin_root: Path) -> None:
    mcp_path = plugin_root / "mcp.json"
    if not mcp_path.exists():
        return
    if not mcp_path.is_file():
        raise SystemExit(f"{mcp_path} must be a regular file")

    config = read_json(mcp_path)
    if config.get("$schema") != AGENT_PLUGIN_MCP_SCHEMA:
        raise SystemExit(f"{mcp_path} must use {AGENT_PLUGIN_MCP_SCHEMA}")
    allowed_top_level = {"$schema", "mcpServers"}
    unknown_top_level = set(config) - allowed_top_level
    if unknown_top_level:
        raise SystemExit(f"{mcp_path} has unsupported top-level field(s): {sorted(unknown_top_level)}")

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        raise SystemExit(f"`mcpServers` in {mcp_path} must be an object")
    for server_name, server in servers.items():
        if not isinstance(server_name, str) or not server_name:
            raise SystemExit(f"{mcp_path} contains an invalid MCP server name")
        if not isinstance(server, dict):
            raise SystemExit(f"`mcpServers.{server_name}` in {mcp_path} must be an object")
        validate_mcp_server(mcp_path, server_name, server)


def validate_mcp_server(mcp_path: Path, server_name: str, server: dict[str, Any]) -> None:
    server_type = server.get("type")
    if server_type == "stdio":
        require_only_keys(mcp_path, server_name, server, {"type", "command", "args", "env", "cwd"})
        command = require_string(server, "command", mcp_path)
        if command.startswith("../") or "/" in command and not command.startswith("./"):
            raise SystemExit(f"`mcpServers.{server_name}.command` in {mcp_path} must be a bare executable or start with ./")
        args = server.get("args", [])
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise SystemExit(f"`mcpServers.{server_name}.args` in {mcp_path} must be an array of strings")
        env = server.get("env", {})
        if not isinstance(env, dict) or any(key in {"PLUGIN_ROOT", "PLUGIN_DATA"} for key in env):
            raise SystemExit(f"`mcpServers.{server_name}.env` in {mcp_path} must be an object and not override PLUGIN_ROOT or PLUGIN_DATA")
        if any(not isinstance(value, str) for value in env.values()):
            raise SystemExit(f"`mcpServers.{server_name}.env` in {mcp_path} must contain string values")
        cwd = server.get("cwd")
        if cwd is not None and (not isinstance(cwd, str) or not is_valid_mcp_cwd(cwd)):
            raise SystemExit(f"`mcpServers.{server_name}.cwd` in {mcp_path} must be plugin-rooted or PLUGIN_DATA-rooted")
        return

    if server_type in {"streamable-http", "sse"}:
        require_only_keys(mcp_path, server_name, server, {"type", "url", "headers"})
        url = require_string(server, "url", mcp_path)
        validate_mcp_url(mcp_path, server_name, url)
        headers = server.get("headers", {})
        if not isinstance(headers, dict) or any(not isinstance(value, str) for value in headers.values()):
            raise SystemExit(f"`mcpServers.{server_name}.headers` in {mcp_path} must be an object of strings")
        if len({key.lower() for key in headers}) != len(headers):
            raise SystemExit(f"`mcpServers.{server_name}.headers` in {mcp_path} contains duplicate header names")
        return

    raise SystemExit(f"`mcpServers.{server_name}.type` in {mcp_path} must be stdio, streamable-http, or sse")


def validate_mcp_url(mcp_path: Path, server_name: str, url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise SystemExit(f"`mcpServers.{server_name}.url` in {mcp_path} must be an absolute HTTP(S) URL without userinfo or fragment")
    if parsed.scheme == "https":
        return
    if not is_loopback_host(parsed.hostname or ""):
        raise SystemExit(f"`mcpServers.{server_name}.url` in {mcp_path} must use HTTPS unless it targets localhost or loopback")


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_valid_mcp_cwd(cwd: str) -> bool:
    return (
        cwd.startswith("./")
        or cwd == "${PLUGIN_ROOT}"
        or cwd.startswith("${PLUGIN_ROOT}/")
        or cwd == "${PLUGIN_DATA}"
        or cwd.startswith("${PLUGIN_DATA}/")
    )


def require_only_keys(mcp_path: Path, server_name: str, server: dict[str, Any], allowed_keys: set[str]) -> None:
    unknown_keys = set(server) - allowed_keys
    if unknown_keys:
        raise SystemExit(f"`mcpServers.{server_name}` in {mcp_path} has unsupported field(s): {sorted(unknown_keys)}")


def build_claude_marketplace(
    marketplace: dict[str, Any],
    defaults: dict[str, Any],
    plugin_overrides: dict[str, Any],
    plugins: list[tuple[Path, dict[str, Any]]],
) -> dict[str, Any]:
    entries = []
    for _, manifest in plugins:
        name = manifest["name"]
        override = plugin_overrides.get(name, {})
        if override is not None and not isinstance(override, dict):
            raise SystemExit(f"`plugins.{name}` in marketplace.config.json must be an object")
        override = override or {}
        entry = {
            "name": name,
            "source": f"./plugins/{name}",
            "displayName": override.get("displayName", titleize(name)),
            "description": override.get("marketplaceDescription", manifest.get("description", "")),
            "version": manifest.get("version", "0.0.0"),
            "category": defaults.get("category", "Productivity"),
        }
        copy_optional(entry, manifest, "keywords", "tags")
        copy_optional(entry, manifest, "author", "author")
        entries.append(entry)
    return {
        "name": require_string(marketplace, "name", CONFIG_PATH),
        "owner": require_object(marketplace, "owner", CONFIG_PATH),
        "description": marketplace.get("description", ""),
        "version": marketplace.get("version", "0.1.0"),
        "plugins": entries,
    }


def build_claude_mcp_configs(plugins: list[tuple[Path, dict[str, Any]]]) -> dict[Path, Any]:
    configs: dict[Path, Any] = {}
    for plugin_root, _ in plugins:
        mcp_path = plugin_root / "mcp.json"
        if not mcp_path.is_file():
            continue
        portable_config = read_json(mcp_path)
        configs[plugin_root / ".mcp.json"] = to_claude_mcp_config(portable_config)
    return configs


def to_claude_mcp_config(portable_config: dict[str, Any]) -> dict[str, Any]:
    claude_servers = {}
    for server_name, portable_server in portable_config["mcpServers"].items():
        claude_server = dict(portable_server)
        if claude_server.get("type") == "streamable-http":
            claude_server["type"] = "http"
        claude_servers[server_name] = claude_server
    return {"mcpServers": claude_servers}


def check_outputs(writes: dict[Path, Any]) -> int:
    stale = []
    for path, payload in writes.items():
        expected = json_text(payload)
        try:
            actual = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            stale.append(path)
            continue
        if actual != expected:
            stale.append(path)
    if stale:
        print("Generated metadata is out of date:")
        for path in stale:
            print(f"- {path.relative_to(ROOT)}")
        print("Run `python3 scripts/generate_native_metadata.py`.")
        return 1
    print("Generated metadata is up to date.")
    return 0


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Missing {path}") from None
    except json.JSONDecodeError as error:
        raise SystemExit(f"Invalid JSON in {path}: {error}") from None
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_text(payload), encoding="utf-8")


def json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def require_string(payload: dict[str, Any], key: str, source: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"`{key}` in {source} must be a non-empty string")
    return value


def require_object(payload: dict[str, Any], key: str, source: Path) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise SystemExit(f"`{key}` in {source} must be an object")
    return value


def copy_optional(target: dict[str, Any], source: dict[str, Any], source_key: str, target_key: str) -> None:
    value = source.get(source_key)
    if value is not None:
        target[target_key] = value


def titleize(name: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-.]", name) if part)


if __name__ == "__main__":
    sys.exit(main())
