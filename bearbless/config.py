from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Config:
    adb_path: str = "adb"
    scrcpy_path: str = "scrcpy"
    android_serial: str | None = None
    shadow_width: int = 1080
    shadow_height: int = 2400
    shadow_dpi: int = 420
    browser_package: str | None = None
    notes_package: str | None = None
    command_timeout_seconds: float = 10.0
    display_start_timeout_seconds: float = 15.0
    phone_model_provider: str = "ollama"
    phone_model_base_url: str = "http://127.0.0.1:11434/api/chat"
    phone_model_name: str = "qwen3-vl:4b"
    phone_verifier_model_name: str = "qwen3-vl-plus"
    phone_model_api_key: str | None = None
    accessibility_bridge_authority: str = "com.bearbless.bridge.control"
    device_reconnect_attempts: int = 2
    device_reconnect_delay_seconds: float = 1.0

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Config":
        file_values = _read_env(env_file or Path(".env"))

        def value(name: str, default: str = "") -> str:
            return os.environ.get(name, file_values.get(name, default))

        return cls(
            adb_path=value("ADB_PATH", "adb"),
            scrcpy_path=value("SCRCPY_PATH", "scrcpy"),
            android_serial=value("ANDROID_SERIAL") or None,
            shadow_width=int(value("SHADOW_WIDTH", "1080")),
            shadow_height=int(value("SHADOW_HEIGHT", "2400")),
            shadow_dpi=int(value("SHADOW_DPI", "420")),
            browser_package=value("BROWSER_PACKAGE") or None,
            notes_package=value("NOTES_PACKAGE") or None,
            phone_model_provider=value("PHONE_MODEL_PROVIDER", "ollama"),
            phone_model_base_url=value("PHONE_MODEL_BASE_URL", "http://127.0.0.1:11434/api/chat"),
            phone_model_name=value("PHONE_MODEL_NAME", "qwen3-vl:4b"),
            phone_verifier_model_name=value("PHONE_VERIFIER_MODEL_NAME", "qwen3-vl-plus"),
            phone_model_api_key=value("PHONE_MODEL_API_KEY") or None,
            accessibility_bridge_authority=value(
                "ACCESSIBILITY_BRIDGE_AUTHORITY", "com.bearbless.bridge.control"
            ),
            device_reconnect_attempts=int(value("DEVICE_RECONNECT_ATTEMPTS", "2")),
            device_reconnect_delay_seconds=float(value("DEVICE_RECONNECT_DELAY_SECONDS", "1.0")),
        )
