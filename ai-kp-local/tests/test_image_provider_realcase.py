import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

from PIL import Image

from ai_kp.infrastructure.images.openai_compatible import (
    OpenAICompatibleImageProvider,
)
from ai_kp.platform.ports.images import MapImageRequest


class _ImageServiceHandler(BaseHTTPRequestHandler):
    received_payload: dict | None = None

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._json_response({"data": [{"id": "period-map-test"}]})

    def do_POST(self) -> None:
        if self.path != "/v1/images/generations":
            self.send_error(404)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(content_length))
        type(self).received_payload = payload
        width, height = (int(value) for value in payload["size"].split("x"))
        buffer = BytesIO()
        Image.new("RGB", (width, height), (61, 69, 82)).save(buffer, format="PNG")
        self._json_response(
            {
                "data": [
                    {
                        "b64_json": base64.b64encode(buffer.getvalue()).decode("ascii"),
                        "revised_prompt": payload["prompt"],
                    }
                ]
            }
        )

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json_response(self, payload: dict) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def test_openai_compatible_image_provider_over_real_http() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ImageServiceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = OpenAICompatibleImageProvider(
            f"http://127.0.0.1:{server.server_port}/v1",
            "",
            "period-map-test",
            timeout_seconds=5,
        )
        result = asyncio.run(
            provider.generate(
                MapImageRequest(
                    prompt="1928 年新英格兰警局平面图",
                    width=512,
                    height=512,
                    seed=1928,
                )
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.mime_type == "image/png"
    assert result.width == 512
    assert result.height == 512
    assert result.seed == 1928
    assert _ImageServiceHandler.received_payload == {
        "model": "period-map-test",
        "prompt": "1928 年新英格兰警局平面图",
        "n": 1,
        "size": "512x512",
        "response_format": "b64_json",
        "seed": 1928,
    }
