"""Plan unique production images and assemble Docker-only releases."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

from . import __version__
from .config import ConfigError, ShipConfig


def plan_images(model: dict, tag: str) -> tuple[dict, list[dict]]:
    """Give identical build definitions one image tag and one build owner."""
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
        owner = "app" if "app" in members else members[0]
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
    return planned, sorted(images, key=lambda entry: entry["owner"])


def _run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ConfigError(f"{' '.join(command[:3])} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def _release_model(model: dict) -> dict:
    release = deepcopy(model)
    for service in release["services"].values():
        service.pop("build", None)
        for env_file in service.get("env_file", []):
            if isinstance(env_file, dict) and env_file.get("path") == ".env.production":
                env_file["path"] = ".env"
                env_file["required"] = True
    return release


def _deploy_script(config: ShipConfig, model: dict) -> str:
    infra = sorted(set(model["services"]) - {"app", *config.processes})
    lines = ["#!/bin/sh", "set -eu", 'cd "$(dirname "$0")"']
    if infra:
        lines.append("docker compose up -d --wait " + " ".join(map(shlex.quote, infra)))
    for command in config.deploy_commands:
        lines.append("docker compose run --rm --no-deps app " + shlex.join(command))
    return "\n".join(lines) + "\n"


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def assemble_release(root: Path, config: ShipConfig, model: dict, tag: str) -> Path:
    """Export all images and publish the artifact only after every step succeeds."""
    source_env = root / ".env.production"
    if not source_env.is_file():
        raise ConfigError("ship release requires .env.production, even when no selected service needs credentials.")
    parent = root / "dist" / "ship"
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".release-", dir=parent))
    destination = parent / tag
    try:
        if os.name != "nt":
            stage.chmod(0o700)
        images_dir = stage / "images"
        images_dir.mkdir()
        if os.name != "nt":
            images_dir.chmod(0o700)
        compose = _release_model(model)
        (stage / "docker-compose.yml").write_text(json.dumps(compose, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(source_env, stage / ".env")
        if os.name != "nt":
            (stage / ".env").chmod(0o600)
        images = []
        by_image: dict[str, list[str]] = {}
        for name, service in compose["services"].items():
            image = service.get("image")
            if not isinstance(image, str) or not image:
                raise ConfigError(f"Release service {name!r} has no image.")
            by_image.setdefault(image, []).append(name)
        for image, members in sorted(by_image.items()):
            if subprocess.run(["docker", "image", "inspect", image], cwd=root, capture_output=True).returncode != 0:
                _run(["docker", "pull", image], root)
            archive = f"images/{sorted(members)[0]}.tar"
            _run(["docker", "save", "-o", str(stage / archive), image], root)
            digest_hash = hashlib.sha256()
            with (stage / archive).open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest_hash.update(chunk)
            digest = digest_hash.hexdigest()
            images.append({"image": image, "file": archive, "sha256": digest, "services": sorted(members)})
        if config.deploy_commands:
            script = stage / "deploy-commands.sh"
            script.write_text(_deploy_script(config, compose), encoding="utf-8")
            if os.name != "nt":
                script.chmod(0o700)
        instructions = ["# Python Ship release", "", "From this directory, with Docker installed:", "",
                        *[f"docker load -i {entry['file']}" for entry in images], ""]
        if config.deploy_commands:
            instructions += ["sh deploy-commands.sh", ""]
        instructions += ["docker compose up -d", "", "The .env file contains production secrets; keep this directory private.", ""]
        (stage / "README.md").write_text("\n".join(instructions), encoding="utf-8")
        manifest = {"tag": tag, "ship_version": __version__, "git_commit": _git_commit(root), "images": images}
        (stage / "release.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if destination.is_symlink():
            raise ConfigError(f"Release destination {destination} is a symlink; remove it before retrying.")
        if destination.exists():
            shutil.rmtree(destination)
        stage.replace(destination)
        return destination
    finally:
        if stage.exists():
            shutil.rmtree(stage)
