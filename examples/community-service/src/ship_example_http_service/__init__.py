"""An independently installed Ship service: a small HTTP server the app can call."""

from python_ship.contracts import ServiceContext


class ExampleHttpService:
    key = "example-http"
    group = "example"
    label = "Example HTTP service"

    def compose_fragment(self, context: ServiceContext) -> dict:
        return {context.service_name: {
            "image": "python:3.13-slim",
            "command": ["python", "-m", "http.server", "8081", "--bind", "0.0.0.0", "--directory", "/usr/local/lib"],
            "healthcheck": {
                "test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081', timeout=2)"],
                "interval": "5s", "timeout": "3s", "retries": 10,
            },
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        return {f"{context.env_prefix}EXAMPLE_HTTP_URL": f"http://{context.service_name}:8081/"}
