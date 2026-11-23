"""Built-in S3-compatible storage services."""

from __future__ import annotations

from typing import Any

from .contracts import ServiceContext


def _s3_environment(context: ServiceContext, port: int, region: str, access_default: str, secret_default: str) -> dict[str, str]:
    prefix = context.env_prefix
    return {
        f"{prefix}AWS_ENDPOINT": f"http://{context.service_name}:{port}",
        f"{prefix}AWS_USE_PATH_STYLE_ENDPOINT": "true",
        f"{prefix}AWS_DEFAULT_REGION": region,
        f"{prefix}AWS_ACCESS_KEY_ID": context.setting("AWS_ACCESS_KEY_ID", access_default),
        f"{prefix}AWS_SECRET_ACCESS_KEY": context.credential("AWS_SECRET_ACCESS_KEY", secret_default),
        f"{prefix}AWS_BUCKET": context.setting("AWS_BUCKET", "local"),
    }


def _healthcheck(test: list[str]) -> dict[str, Any]:
    return {"test": test, "interval": "5s", "timeout": "5s", "retries": 12, "start_period": "10s"}


class GarageService:
    """Provisions its default key and bucket on boot.

    The final image has no shell, so the per-instance RPC address is substituted
    into ``ship/garage/garage.toml`` at image build time instead.
    """

    key = "garage"
    group = "storage"
    label = "Garage"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        return {context.service_name: {
            "build": {
                "context": "./ship/garage",
                "dockerfile": "Dockerfile",
                "args": {"GARAGE_RPC_PUBLIC_ADDR": f"{context.service_name}:3901"},
            },
            "command": ["/garage", "server", "--single-node", "--default-access-key", "--default-bucket"],
            "environment": {
                "GARAGE_DEFAULT_ACCESS_KEY": context.setting("AWS_ACCESS_KEY_ID", "shipaccesskey"),
                "GARAGE_DEFAULT_SECRET_KEY": context.credential("AWS_SECRET_ACCESS_KEY", "shipsecretplaceholder"),
                "GARAGE_DEFAULT_BUCKET": context.setting("AWS_BUCKET", "local"),
                "GARAGE_RPC_SECRET": context.credential("GARAGE_RPC_SECRET", "0" * 64),
                "GARAGE_ADMIN_TOKEN": context.credential("GARAGE_ADMIN_TOKEN", "shipsecret"),
            },
            "volumes": [f"{context.resource_name}_data:/data", f"{context.resource_name}_meta:/meta"],
            "healthcheck": _healthcheck(["CMD", "/garage", "status"]),
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        return _s3_environment(context, 3900, "garage", "shipaccesskey", "shipsecretplaceholder")


# SeaweedFS's S3 gateway is unauthenticated without an identity file, so one is generated from the
# injected credentials at boot: JSON-escaped, and $$-escaped so Compose doesn't interpolate it.
_SEAWEED_IDENTITY = (
    "mkdir -p /etc/seaweedfs"
    " && ACCESS_KEY_ESC=$$(printf '%s' \"$$AWS_ACCESS_KEY_ID\" | sed 's/\\\\/\\\\\\\\/g; s/\"/\\\\\"/g')"
    " && SECRET_KEY_ESC=$$(printf '%s' \"$$AWS_SECRET_ACCESS_KEY\" | sed 's/\\\\/\\\\\\\\/g; s/\"/\\\\\"/g')"
    " && printf '{\"identities\":[{\"name\":\"ship\",\"credentials\":[{\"accessKey\":\"%s\",\"secretKey\":\"%s\"}],"
    "\"actions\":[\"Admin\",\"Read\",\"Write\",\"List\",\"Tagging\"]}]}' \"$$ACCESS_KEY_ESC\" \"$$SECRET_KEY_ESC\""
    " > /etc/seaweedfs/s3_identity.json"
    " && exec weed server -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3_identity.json -dir=/data"
)


class SeaweedFSService:
    key = "seaweedfs"
    group = "storage"
    label = "SeaweedFS"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        return {context.service_name: {
            "build": {
                "context": ".",
                "dockerfile_inline": "FROM ghcr.io/chrislusf/seaweedfs:4.47\nRUN apk upgrade --no-cache\n",
            },
            "entrypoint": ["/bin/sh", "-c"],
            "command": [_SEAWEED_IDENTITY],
            "environment": {
                "AWS_ACCESS_KEY_ID": context.setting("AWS_ACCESS_KEY_ID", "ship"),
                "AWS_SECRET_ACCESS_KEY": context.credential("AWS_SECRET_ACCESS_KEY", "shipsecret"),
            },
            "volumes": [f"{context.resource_name}_data:/data"],
            "healthcheck": {
                **_healthcheck(["CMD", "wget", "-q", "-O", "-", "http://127.0.0.1:9333/cluster/status"]),
                "interval": "10s", "retries": 10,
            },
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        return _s3_environment(context, 8333, "us-east-1", "ship", "shipsecret")


class RustFSService:
    """RustFS publishes no console port: its console currently returns AccessDenied (rustfs/rustfs#8013)."""

    key = "rustfs"
    group = "storage"
    label = "RustFS"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        return {context.service_name: {
            "image": "rustfs/rustfs:1.0.6",
            "environment": {
                "RUSTFS_ACCESS_KEY": context.setting("AWS_ACCESS_KEY_ID", "ship"),
                "RUSTFS_SECRET_KEY": context.credential("AWS_SECRET_ACCESS_KEY", "shipsecret"),
            },
            "volumes": [f"{context.resource_name}_data:/data"],
            "healthcheck": _healthcheck(["CMD", "curl", "-f", "http://127.0.0.1:9000/health"]),
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        return _s3_environment(context, 9000, "us-east-1", "ship", "shipsecret")


class SiloService:
    """``siloImage`` picks the standard image (RHEL 9 base, needs an x86-64-v2 CPU) or the distroless one."""

    key = "silo"
    group = "storage"
    label = "Silo"
    release = "RELEASE.2026-11-12T00-00-00Z"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        suffix = "-distroless" if context.config.silo_image == "distroless" else ""
        model: dict[str, Any] = {
            "image": f"pgsty/silo:{self.release}{suffix}",
            "command": ["server", "/data", "--console-address", ":9001"],
            "environment": {
                "MINIO_ROOT_USER": context.setting("AWS_ACCESS_KEY_ID", "ship"),
                "MINIO_ROOT_PASSWORD": context.credential("AWS_SECRET_ACCESS_KEY", "shipsecret"),
            },
            "volumes": [f"{context.resource_name}_data:/data"],
            # The distroless image has no curl; Silo's own probe works in both.
            "healthcheck": _healthcheck(["CMD", "/usr/bin/silo", "healthcheck", "live"]),
        }
        if context.environment.is_development:
            variable = f"{context.env_prefix}SILO_CONSOLE_PORT"
            default = "9001" if context.instance_name is None else "0"
            model["ports"] = [f"127.0.0.1:${{{variable}:-{default}}}:9001"]
        return {context.service_name: model}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        return _s3_environment(context, 9000, "us-east-1", "ship", "shipsecret")


BUILTIN_STORAGE = (GarageService(), SeaweedFSService(), RustFSService(), SiloService())
