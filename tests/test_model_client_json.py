import json
from unittest.mock import patch

from bearbless.agent.model_client import OpenAICompatiblePhoneModel


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        nested = json.dumps({"package": "com.netease.cloudmusic"}, ensure_ascii=False)
        return json.dumps({
            "choices": [{"message": {"content": json.dumps(nested)}}],
            "usage": {},
        }).encode()


def test_openai_compatible_unwraps_double_encoded_json_once():
    client = OpenAICompatiblePhoneModel("https://example.invalid", "test", "key")
    with patch("bearbless.agent.model_client.urlopen", return_value=Response()):
        result = client.complete("return JSON")
    assert result == {"package": "com.netease.cloudmusic"}
