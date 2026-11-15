"""An independently installed, config-aware Ship service."""


class ExampleHttpService:
    key = "example-http"
    group = "example"
    label = "Example HTTP service"

    def compose_fragment(self) -> dict:
        return self.compose_fragment_for(None, {"name": self.key, "prefix": ""})

    def compose_fragment_for(self, config, instance: dict) -> dict:
        name = instance["name"]
        return {name: {
            "image": "python:3.13-slim",
            "command": ["python", "-m", "http.server", "8081", "--bind", "0.0.0.0",
                        "--directory", "/usr/local/lib/python3.13"],
            "healthcheck": {
                "test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081', timeout=2)"],
                "interval": "5s", "timeout": "3s", "retries": 10,
            },
        }}

    def app_environment(self) -> dict[str, str]:
        return self.app_environment_for(None, {"name": self.key, "prefix": ""})

    def app_environment_for(self, config, instance: dict) -> dict[str, str]:
        return {"EXAMPLE_HTTP_URL": f"http://{instance['name']}:8081/"}
