"""Built-in S3-compatible storage services."""


def _value(name: str, default: str, production: bool = False) -> str:
    return f"${{{name}:?{name} is required}}" if production else f"${{{name}:-{default}}}"


def _prefix(instance: dict) -> str:
    return instance["prefix"] + "_" if instance["prefix"] else ""


def _app_environment(instance: dict, port: int, region: str, access: str, secret: str, bucket: str) -> dict[str, str]:
    return {
        "AWS_ENDPOINT": f"http://{instance['name']}:{port}",
        "AWS_USE_PATH_STYLE_ENDPOINT": "true",
        "AWS_DEFAULT_REGION": region,
        "AWS_ACCESS_KEY_ID": access,
        "AWS_SECRET_ACCESS_KEY": secret,
        "AWS_BUCKET": bucket,
    }


class GarageService:
    key = "garage"
    group = "storage"
    label = "Garage"

    def compose_fragment(self) -> dict:
        return self.compose_fragment_for(None, {"name": self.key, "prefix": "", "_production": False})

    def compose_fragment_for(self, config, instance: dict) -> dict:
        name, prefix = instance["name"], _prefix(instance)
        production = instance.get("_production", False)
        return {name: {
            "build": {
                "context": "./ship/garage",
                "dockerfile": "Dockerfile",
                "args": {"GARAGE_RPC_PUBLIC_ADDR": f"{name}:3901"},
            },
            "command": ["/garage", "server", "--single-node", "--default-access-key", "--default-bucket"],
            "environment": {
                "GARAGE_DEFAULT_ACCESS_KEY": _value(prefix + "AWS_ACCESS_KEY_ID", "shipaccesskey"),
                "GARAGE_DEFAULT_SECRET_KEY": _value(prefix + "AWS_SECRET_ACCESS_KEY", "shipsecretplaceholder", production),
                "GARAGE_DEFAULT_BUCKET": _value(prefix + "AWS_BUCKET", "local"),
                "GARAGE_RPC_SECRET": _value(prefix + "GARAGE_RPC_SECRET", "0" * 64, production),
                "GARAGE_ADMIN_TOKEN": _value(prefix + "GARAGE_ADMIN_TOKEN", "shipsecret", production),
            },
            "volumes": [f"{name}_data:/data", f"{name}_meta:/meta"],
            "healthcheck": {
                "test": ["CMD", "/garage", "status"],
                "interval": "5s", "timeout": "5s", "retries": 12, "start_period": "10s",
            },
        }}

    def app_environment(self) -> dict[str, str]:
        return self.app_environment_for(None, {"name": self.key, "prefix": "", "_production": False})

    def app_environment_for(self, config, instance: dict) -> dict[str, str]:
        prefix = _prefix(instance)
        production = instance.get("_production", False)
        return _app_environment(
            instance, 3900, "garage",
            _value(prefix + "AWS_ACCESS_KEY_ID", "shipaccesskey"),
            _value(prefix + "AWS_SECRET_ACCESS_KEY", "shipsecretplaceholder", production),
            _value(prefix + "AWS_BUCKET", "local"),
        )


_SEAWEED_IDENTITY = (
    "mkdir -p /etc/seaweedfs"
    " && ACCESS_KEY_ESC=$$(printf '%s' \"$$AWS_ACCESS_KEY_ID\" | sed 's/\\\\/\\\\\\\\/g; s/\"/\\\\\"/g')"
    " && SECRET_KEY_ESC=$$(printf '%s' \"$$AWS_SECRET_ACCESS_KEY\" | sed 's/\\\\/\\\\\\\\/g; s/\"/\\\\\"/g')"
    " && printf '{\"identities\":[{\"name\":\"ship\",\"credentials\":[{\"accessKey\":\"%s\",\"secretKey\":\"%s\"}],\"actions\":[\"Admin\",\"Read\",\"Write\",\"List\",\"Tagging\"]}]}' \"$$ACCESS_KEY_ESC\" \"$$SECRET_KEY_ESC\" > /etc/seaweedfs/s3_identity.json"
    " && exec weed server -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3_identity.json -dir=/data"
)


class SeaweedFSService:
    key = "seaweedfs"
    group = "storage"
    label = "SeaweedFS"

    def compose_fragment(self) -> dict:
        return self.compose_fragment_for(None, {"name": self.key, "prefix": "", "_production": False})

    def compose_fragment_for(self, config, instance: dict) -> dict:
        name, prefix = instance["name"], _prefix(instance)
        production = instance.get("_production", False)
        return {name: {
            "image": "chrislusf/seaweedfs:4.46",
            "entrypoint": ["/bin/sh", "-c"],
            "command": [_SEAWEED_IDENTITY],
            "environment": {
                "AWS_ACCESS_KEY_ID": _value(prefix + "AWS_ACCESS_KEY_ID", "ship"),
                "AWS_SECRET_ACCESS_KEY": _value(prefix + "AWS_SECRET_ACCESS_KEY", "shipsecret", production),
            },
            "volumes": [f"{name}_data:/data"],
            "healthcheck": {
                "test": ["CMD", "wget", "-q", "-O", "-", "http://127.0.0.1:9333/cluster/status"],
                "interval": "10s", "timeout": "5s", "retries": 10, "start_period": "10s",
            },
        }}

    def app_environment(self) -> dict[str, str]:
        return self.app_environment_for(None, {"name": self.key, "prefix": "", "_production": False})

    def app_environment_for(self, config, instance: dict) -> dict[str, str]:
        prefix = _prefix(instance)
        production = instance.get("_production", False)
        return _app_environment(
            instance, 8333, "us-east-1",
            _value(prefix + "AWS_ACCESS_KEY_ID", "ship"),
            _value(prefix + "AWS_SECRET_ACCESS_KEY", "shipsecret", production),
            _value(prefix + "AWS_BUCKET", "local"),
        )


class RustFSService:
    key = "rustfs"
    group = "storage"
    label = "RustFS"

    def compose_fragment(self) -> dict:
        return self.compose_fragment_for(None, {"name": self.key, "prefix": "", "_production": False})

    def compose_fragment_for(self, config, instance: dict) -> dict:
        name, prefix = instance["name"], _prefix(instance)
        production = instance.get("_production", False)
        return {name: {
            "image": "rustfs/rustfs:1.0.0",
            "environment": {
                "RUSTFS_ACCESS_KEY": _value(prefix + "AWS_ACCESS_KEY_ID", "ship"),
                "RUSTFS_SECRET_KEY": _value(prefix + "AWS_SECRET_ACCESS_KEY", "shipsecret", production),
            },
            "volumes": [f"{name}_data:/data"],
            "healthcheck": {
                "test": ["CMD", "curl", "-f", "http://127.0.0.1:9000/health"],
                "interval": "5s", "timeout": "5s", "retries": 12, "start_period": "10s",
            },
        }}

    def app_environment(self) -> dict[str, str]:
        return self.app_environment_for(None, {"name": self.key, "prefix": "", "_production": False})

    def app_environment_for(self, config, instance: dict) -> dict[str, str]:
        prefix = _prefix(instance)
        production = instance.get("_production", False)
        return _app_environment(
            instance, 9000, "us-east-1",
            _value(prefix + "AWS_ACCESS_KEY_ID", "ship"),
            _value(prefix + "AWS_SECRET_ACCESS_KEY", "shipsecret", production),
            _value(prefix + "AWS_BUCKET", "local"),
        )


class SiloService:
    key = "silo"
    group = "storage"
    label = "Silo"

    def compose_fragment(self) -> dict:
        return self.compose_fragment_for(None, {"name": self.key, "prefix": "", "_production": False})

    def compose_fragment_for(self, config, instance: dict) -> dict:
        name, prefix = instance["name"], _prefix(instance)
        production = instance.get("_production", False)
        variant = config.silo_image if config else "distroless"
        suffix = "-distroless" if variant == "distroless" else ""
        model = {
            "image": f"pgsty/silo:RELEASE.2026-09-16T00-00-00Z{suffix}",
            "command": ["server", "/data", "--console-address", ":9001"],
            "environment": {
                "MINIO_ROOT_USER": _value(prefix + "AWS_ACCESS_KEY_ID", "ship"),
                "MINIO_ROOT_PASSWORD": _value(prefix + "AWS_SECRET_ACCESS_KEY", "shipsecret", production),
            },
            "volumes": [f"{name}_data:/data"],
            "healthcheck": {
                "test": ["CMD", "/usr/bin/silo", "healthcheck", "live"],
                "interval": "5s", "timeout": "5s", "retries": 12, "start_period": "10s",
            },
        }
        if not production and not instance["prefix"]:
            model["ports"] = ["127.0.0.1:${SILO_CONSOLE_PORT:-9001}:9001"]
        return {name: model}

    def app_environment(self) -> dict[str, str]:
        return self.app_environment_for(None, {"name": self.key, "prefix": "", "_production": False})

    def app_environment_for(self, config, instance: dict) -> dict[str, str]:
        prefix = _prefix(instance)
        production = instance.get("_production", False)
        return _app_environment(
            instance, 9000, "us-east-1",
            _value(prefix + "AWS_ACCESS_KEY_ID", "ship"),
            _value(prefix + "AWS_SECRET_ACCESS_KEY", "shipsecret", production),
            _value(prefix + "AWS_BUCKET", "local"),
        )


BUILTIN_STORAGE = {service.key: service for service in (
    GarageService(), SeaweedFSService(), RustFSService(), SiloService(),
)}
