"""Plan unique production images and assemble Docker-only releases."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Any

from . import __version__
from .compose import render, resolve_selections, server_for
from .config import ConfigError, ShipConfig, command_argv, http_problem, process_definition
from .contracts import ShipEnvironment
from .plugins import console_commands, load_extensions
from .production import credential_problems, read_env, required_variables
from .capabilities import release_description, required_setting_problems

TAG_PATTERN = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"


def plan_images(model: dict[str, Any], tag: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Give identical build definitions one image tag, one build owner and one archive."""
    planned = deepcopy(model)
    services = planned["services"]
    groups: dict[str, list[str]] = {}
    for name, service in services.items():
        if "build" in service:
            signature = json.dumps(service["build"], sort_keys=True, separators=(",", ":"))
            groups.setdefault(signature, []).append(name)
    images = []
    aliases = {}
    for members in groups.values():
        members = sorted(members)
        # The app owns its group; processes share its image.
        app_name = planned.get("x-ship", {}).get("app_services", ["app"])[0]
        owner = app_name if app_name in members else members[0]
        image = f"{planned['name']}-{owner}:{tag}"
        for name in members:
            old_image = services[name].get("image")
            if old_image:
                aliases[old_image] = image
            services[name]["image"] = image
        images.append({"image": image, "owner": owner, "services": members})
    for service in services.values():
        if "build" not in service and service.get("image") in aliases:
            service["image"] = aliases[service["image"]]
    # Processes reuse the app's image without building it; list every service that runs each image.
    for entry in images:
        entry["services"] = sorted(name for name, service in services.items() if service.get("image") == entry["image"])
    return planned, sorted(images, key=lambda entry: entry["owner"])


def _run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ConfigError(f"`{' '.join(command[:3])}` failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def release_model(model: dict[str, Any]) -> dict[str, Any]:
    release = deepcopy(model)
    release.pop("x-ship", None)
    for service in release["services"].values():
        service.pop("build", None)
        for env_file in service.get("env_file", []):
            if isinstance(env_file, dict) and env_file.get("path") == ".env.production":
                env_file["path"] = ".env"
                env_file["required"] = False
    return release


def deployment_plan(config: ShipConfig, model: dict[str, Any]) -> dict[str, Any] | None:
    if not config.deploy_commands:
        return None
    app_services = set(model.get("x-ship", {}).get("app_services", [config.app_service, *config.processes]))
    return {"service": config.app_service, "needs": sorted(set(model["services"]) - app_services),
            "commands": [command_argv(command) for command in config.deploy_commands]}


def deploy_script(config: ShipConfig, model: dict[str, Any]) -> str:
    plan = deployment_plan(config, model)
    lines = ["#!/bin/sh", "set -eu", 'cd "$(dirname "$0")"']
    if plan:
        if plan["needs"]:
            lines.append("docker compose up -d --wait " + shlex.join(plan["needs"]))
        for argv in plan["commands"]:
            lines.append("docker compose run --rm --no-deps -T " + shlex.join([plan["service"], *argv]))
    return "\n".join(lines) + "\n"


def source_metadata(root: Path) -> dict[str, Any]:
    try:
        commit = _run(["git", "rev-parse", "HEAD"], root).stdout.strip()
        if not re.fullmatch(r"[a-f0-9]{40,64}", commit):
            return {"gitCommit": None, "dirty": None}
        status = _run(["git", "status", "--porcelain", "--", ".", ":(exclude)dist/ship",
                       ":(exclude)ship/docker-compose.generated.yml", ":(exclude)ship/docker-compose.production.yml"], root)
        return {"gitCommit": commit, "dirty": bool(status.stdout.strip())}
    except (OSError, ConfigError):
        return {"gitCommit": None, "dirty": None}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def destination_for(root: Path, tag: str, force: bool = False) -> Path:
    if not re.fullmatch(TAG_PATTERN, tag):
        raise ConfigError("Invalid release tag.")
    destination = root / "dist" / "ship" / tag
    if destination.is_symlink():
        raise ConfigError(f"Release destination {destination} is a symlink; remove it before retrying.")
    if destination.exists() and not force:
        raise ConfigError(f"Release {tag} already exists; choose another tag or pass --force.")
    return destination


def _inspect(image: str, root: Path) -> dict[str, Any]:
    try:
        info = json.loads(_run(["docker", "image", "inspect", image], root).stdout)[0]
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", info["Id"]) or not info["Os"] or not info["Architecture"]:
            raise ValueError()
        return info
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ConfigError(f"Cannot inspect image {image}: Docker must return its ID and platform.") from exc


def _named_volumes(service: dict[str, Any]) -> list[str]:
    names = []
    for mount in service.get("volumes", []):
        if isinstance(mount, str):
            source, sep, _ = mount.partition(":")
            if sep and source and not source.startswith((".", "/", "~")):
                names.append(source)
        elif isinstance(mount, dict) and mount.get("type", "volume") == "volume" and mount.get("source"):
            names.append(mount["source"])
    return sorted(set(names))


def referenced_variables(model: dict[str, Any]) -> list[str]:
    text = json.dumps(model).replace("$$", "")
    return sorted(set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", text)))


def assemble_release(root: Path, config: ShipConfig, model: dict[str, Any], tag: str, *,
                     force: bool = False, no_env: bool = False, source: dict[str, Any] | None = None) -> Path:
    """Publish a canonical ship-release artifact; retain the prior release on any failure."""
    destination = destination_for(root, tag, force)
    authored = json.loads((root / "ship.json").read_text())
    if not config.name or authored.get("name") != config.name or model["name"] != config.name:
        raise ConfigError("Release requires an explicit stable name in ship.json matching the Compose project.")
    registry = load_extensions(config)
    description = release_description(registry.adapter(config), config, root)
    issues = credential_problems(model, root) + required_setting_problems(description, root)
    if issues:
        raise ConfigError(" ".join(issues))
    source = source if source is not None else source_metadata(root)
    source_env = root / ".env.production"
    keys = sorted(read_env(source_env))
    include_env = source_env.is_file() and not no_env
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".release-", dir=parent))
    try:
        stage.chmod(0o700)
        (stage / "images").mkdir(mode=0o700)
        compose = release_model(model)
        if include_env:
            shutil.copyfile(source_env, stage / ".env")
            (stage / ".env").chmod(0o600)
        by_image: dict[str, list[str]] = {}
        owned = {service["image"] for service in model["services"].values() if "build" in service}
        for name, service in compose["services"].items():
            image = service.get("image")
            if not isinstance(image, str) or not image:
                raise ConfigError(f"Release service {name!r} has no image.")
            by_image.setdefault(image, []).append(name)
        images, pulled = [], []
        platform = None
        for image, members in sorted(by_image.items()):
            members = sorted(members)
            if image not in owned:
                _run(["docker", "pull", image], root)
            info = _inspect(image, root)
            if image in owned:
                current = info["Os"] + "/" + info["Architecture"] + ("/" + info["Variant"] if info.get("Variant") else "")
                if platform and platform != current:
                    raise ConfigError("Project-built images have mixed platforms; build them for one destination platform.")
                platform = current
                owner = config.app_service if config.app_service in members else members[0]
                archive = f"images/{owner}.tar"
                _run(["docker", "save", "-o", str(stage / archive), image], root)
                images.append({"ref": image, "id": info["Id"], "file": archive,
                               "sha256": _sha256(stage / archive), "services": members})
            else:
                def repository(ref: str) -> str:
                    name = re.sub(r":[^/:]+$", "", ref.split("@")[0])
                    return re.sub(r"^(?:docker.io/)?(?:library/)?", "", name)
                digest = next((ref.split("@", 1)[1] for ref in info.get("RepoDigests", [])
                               if "@" in ref and repository(ref) == repository(image)), None)
                if not digest or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                    raise ConfigError(f"Registry image {image} has no matching repository digest.")
                pinned = image.split("@")[0] + "@" + digest
                for member in members:
                    compose["services"][member]["image"] = pinned
                pulled.append({"ref": pinned, "services": members})
        if not platform:
            raise ConfigError("Release has no project-owned images.")
        http = [{"service": config.app_service, "port": 8000, "role": "site",
                 "health": description.get("health"), "urlSetting": description.get("urlSetting")}]
        for name, value in config.processes.items():
            endpoint = process_definition(value).get("http")
            if endpoint:
                http.append({"service": name, "health": None, "urlSetting": None, **endpoint})
        environment = ShipEnvironment.PRODUCTION
        for selection in resolve_selections(config, registry):
            hook = getattr(selection.service, "http_endpoints", None)
            if hook and selection.service_name in compose["services"]:
                for endpoint in hook(selection.context(config, root, environment)):
                    problem = http_problem(endpoint)
                    if problem:
                        raise ConfigError(f"Service {selection.service.key}: {problem}")
                    http.append({"service": selection.service_name, "health": None, "urlSetting": None, **endpoint})
        # All HTTP services are reachable by the same external reverse-proxy networks.
        for endpoint in http:
            if config.external_networks:
                compose["services"][endpoint["service"]]["networks"] = ["default", *[
                    f"external_{i}" for i, _ in enumerate(config.external_networks)]]
        app_services = model.get("x-ship", {}).get("app_services", [config.app_service, *config.processes])
        deploy = deployment_plan(config, model)
        required = sorted(set(required_variables(compose) + description.get("requiredEnv", [])))
        if not set(required).issubset(keys):
            raise ConfigError("Production settings must declare every required variable.")
        manifest = {
            "format": "ship-release", "app": config.name, "tag": tag,
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "builder": {"package": "python-ship", "version": __version__}, "source": source,
            "platform": platform, "compose": "docker-compose.yml", "images": images, "pulledImages": pulled,
            "http": http,
            "services": {name: {"role": "app" if name == config.app_service else "process" if name in app_services else "infrastructure",
                "image": service["image"], "healthcheck": bool(service.get("healthcheck")) and
                not service["healthcheck"].get("disable", False) and service["healthcheck"].get("test", [None])[0] != "NONE",
                "volumes": _named_volumes(service)} for name, service in sorted(compose["services"].items())},
            "framework": None if config.framework == "generic" else config.framework,
            "runtime": server_for(config, root, registry).key,
            "env": {"file": ".env" if include_env else None, "keys": keys,
                    "referenced": referenced_variables(compose), "required": required},
            "deploy": deploy, "requires": {"networks": sorted(config.external_networks)},
            "console": {"service": config.app_service, "user": "10001:10001", "shell": ["sh"],
                        "commands": console_commands(registry.adapter(config))},
        }
        (stage / "docker-compose.yml").write_text(render(compose), encoding="utf-8")
        if deploy:
            script = stage / "deploy-commands.sh"
            script.write_text(deploy_script(config, model), encoding="utf-8", newline="\n")
            script.chmod(0o755)
        instructions = [f"# Release {tag}", "", "Requires Docker Engine and Compose 2.24+. From this directory:", "", "```sh",
            *["docker network inspect " + shlex.quote(network) + " >/dev/null 2>&1 || docker network create " + shlex.quote(network)
              for network in config.external_networks],
            *[f"docker load -i {entry['file']}" for entry in images],
            *(["sh deploy-commands.sh"] if deploy else []), "docker compose up -d", "```", "",
            "Registry services require registry access. Named volumes contain application data; back them up before upgrades.",
            "Production ports are private unless publishPorts is enabled; route the HTTP endpoints through your reverse proxy.",
            "Keep .env private (0600)." if include_env else "Supply production settings on the destination in .env (0600) before deployment.", ""]
        (stage / "README.md").write_text("\n".join(instructions), encoding="utf-8")
        (stage / "release.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if destination.exists():
            backup = Path(tempfile.mkdtemp(prefix=".previous-", dir=parent)) / "release"
            destination.replace(backup)
            try:
                stage.replace(destination)
            except OSError:
                backup.replace(destination)
                raise
            finally:
                if not backup.exists():
                    backup.parent.rmdir()
            shutil.rmtree(backup.parent)
        else:
            stage.replace(destination)
        return destination
    finally:
        if stage.exists():
            shutil.rmtree(stage)
