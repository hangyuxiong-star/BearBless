from pathlib import Path

from bearbless.config import Config


def test_config_loads_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ADB_PATH", raising=False)
    env = tmp_path / ".env"
    env.write_text("ADB_PATH=/opt/adb\nSHADOW_WIDTH=900\n", encoding="utf-8")
    config = Config.load(env)
    assert config.adb_path == "/opt/adb"
    assert config.shadow_width == 900
