from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.error import URLError
from urllib.request import urlopen

from bearbless.voice import VoiceTranscriptionError, transcribe_audio


HOST = "127.0.0.1"
PORT = 8502
MAX_STREAM_BYTES = 15 * 1024 * 1024
_server: ThreadingHTTPServer | None = None
_server_lock = threading.Lock()
_transcription_lock = threading.Lock()


class _VoiceHandler(BaseHTTPRequestHandler):
    server_version = "BearBlessVoice/1"

    def _headers(self, status: int, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8501")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._headers(204)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._headers(404)
            return
        self._headers(200)
        self.wfile.write(b'{"status":"ok"}')

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/transcribe":
            self._headers(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_STREAM_BYTES:
            self._json(413, {"error": "音频为空或超过 15 MB"})
            return
        audio = self.rfile.read(length)
        try:
            with _transcription_lock:
                transcript = transcribe_audio(audio, suffix=".webm")
            self._json(200, {"transcript": transcript})
        except VoiceTranscriptionError as exc:
            self._json(422, {"error": str(exc)})

    def _json(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status)
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def ensure_realtime_voice_server() -> str:
    global _server
    endpoint = f"http://{HOST}:{PORT}"
    if _server is not None:
        return endpoint
    with _server_lock:
        if _server is not None:
            return endpoint
        try:
            with urlopen(f"{endpoint}/health", timeout=0.25) as response:
                if response.status == 200:
                    return endpoint
        except (URLError, TimeoutError):
            pass
        server = ThreadingHTTPServer((HOST, PORT), _VoiceHandler)
        thread = threading.Thread(target=server.serve_forever, name="bearbless-realtime-voice", daemon=True)
        thread.start()
        _server = server
    return endpoint
