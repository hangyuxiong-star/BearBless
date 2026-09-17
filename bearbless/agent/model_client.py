from __future__ import annotations

import base64
import ast
import json
from pathlib import Path
import re
import time
import hashlib
from typing import Protocol
from urllib.request import Request, urlopen

from bearbless.config import Config


class ModelClientError(RuntimeError):
    pass


class PhoneModelClient(Protocol):
    def complete(self, prompt: str, frame_path: str | None = None) -> dict: ...


class OpenAICompatiblePhoneModel:
    """Minimal multimodal client for AutoGLM-Phone, UI-TARS and similar APIs."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout_seconds: float = 90) -> None:
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._text_cache: dict[str, dict] = {}
        self.model_calls_total = 0
        self.model_image_calls = 0
        self.model_latency_ms = 0
        self.model_input_tokens = 0
        self.model_output_tokens = 0

    def complete(self, prompt: str, frame_path: str | None = None) -> dict:
        # Only cache text-only analysis. Caching a GUI action could replay a
        # click after the screen has changed and is therefore intentionally
        # forbidden.
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if frame_path is None and cache_key in self._text_cache:
            return dict(self._text_cache[cache_key])
        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        if frame_path:
            path = Path(frame_path)
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        started = time.perf_counter()
        try:
            with urlopen(Request(self.endpoint, data=body, headers=headers), timeout=self.timeout_seconds) as response:
                envelope = json.loads(response.read().decode("utf-8"))
            self.model_calls_total += 1
            self.model_image_calls += int(frame_path is not None)
            self.model_latency_ms += int((time.perf_counter() - started) * 1000)
            usage = envelope.get("usage") or {}
            self.model_input_tokens += int(usage.get("prompt_tokens") or 0)
            self.model_output_tokens += int(usage.get("completion_tokens") or 0)
            content_text = envelope["choices"][0]["message"]["content"]
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content_text.strip(), flags=re.I | re.S)
            result = json.loads(cleaned)
            # Some OpenAI-compatible endpoints serialize the JSON object as a
            # JSON string. Unwrap once locally; a format-only model retry adds
            # latency and cost without adding reasoning value.
            if isinstance(result, str):
                nested = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.strip(), flags=re.I | re.S)
                result = json.loads(nested)
            if not isinstance(result, dict):
                raise ModelClientError(
                    f"phone model returned {type(result).__name__}, expected a JSON object"
                )
            if frame_path is None:
                if len(self._text_cache) >= 32:
                    self._text_cache.pop(next(iter(self._text_cache)))
                self._text_cache[cache_key] = dict(result)
            return result
        except ModelClientError:
            raise
        except Exception as exc:
            raise ModelClientError(f"phone model unavailable: {exc}") from exc

    def usage_metrics(self) -> dict[str, int]:
        return {
            "model_calls_total": self.model_calls_total,
            "model_image_calls": self.model_image_calls,
            "model_latency_ms": self.model_latency_ms,
            "model_input_tokens": self.model_input_tokens,
            "model_output_tokens": self.model_output_tokens,
        }

    def select_package(self, goal: str, installed_packages: list[str]) -> str:
        payload = self.complete(
            "根据用户任务，从已安装包名中选出应启动的唯一应用。"
            f"用户任务：{goal}\n已安装包名：{json.dumps(installed_packages, ensure_ascii=False)}\n"
            '只返回 JSON：{"package":"完整包名","reason":"简体中文理由"}。'
            "package 必须逐字来自列表；无法确定则返回空字符串。"
        )
        package = str(payload.get("package") or "")
        if package not in installed_packages:
            raise ModelClientError(f"无法从已安装应用中解析目标：{goal}")
        return package


class GUIPlusPhoneModel:
    """GUI-Plus planner with a general VLM for contracts and verification.

    GUI-Plus intentionally does not support JSON structured outputs. Its
    documented ``<tool_call>`` envelope is translated into BearBless's typed
    decision protocol before the action reaches the policy/guard layer.
    """

    native_tool_protocol = True
    SYSTEM_PROMPT = """# Tools
You may call one or more functions to assist with the user query.
You are provided with function signatures within <tools></tools> XML tags:
<tools>
{"type":"function","function":{"name":"mobile_use","description":"Use the supplied mobile touchscreen screenshot. Coordinates are normalized to a 1000 by 1000 reference plane. For a picker wheel, start and end the swipe inside the exact hour/minute/date column; never use a page scroll for a picker.","parameters":{"properties":{"action":{"description":"The mobile action to perform.","enum":["key","click","long_press","swipe","type","system_button","wait","terminate","answer","interact"],"type":"string"},"keys":{"type":"array"},"text":{"type":"string"},"coordinate":{"description":"Required start point for click, long_press and swipe.","type":"array"},"coordinate2":{"description":"Required end point for swipe.","type":"array"},"time":{"type":"number"},"button":{"enum":["Back","Enter"],"type":"string"},"status":{"enum":["success","failure"],"type":"string"}},"required":["action"],"type":"object"}}}
</tools>
For each function call, return a JSON object with function name and arguments within <tool_call></tool_call> XML tags.
# Response format
1) Action: a short imperative describing what to do in the UI.
2) A single <tool_call>...</tool_call> block.
Be brief. Do not output anything else. If finishing, use action=terminate."""

    @staticmethod
    def _load_tool_json(raw: str) -> dict:
        candidates = [raw.strip()]
        normalized = raw.strip().replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        normalized = re.sub(r",\s*([}\]])", r"\1", normalized)
        if normalized not in candidates:
            candidates.append(normalized)
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    value = ast.literal_eval(candidate)
                except (ValueError, SyntaxError):
                    continue
            for _ in range(3):
                if isinstance(value, dict):
                    return value
                if not isinstance(value, str):
                    break
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    try:
                        value = ast.literal_eval(value)
                    except (ValueError, SyntaxError):
                        break
        raise ValueError("tool payload is not a valid object")

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        verifier_model: str = "qwen3-vl-plus",
        timeout_seconds: float = 90,
    ) -> None:
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.fallback = OpenAICompatiblePhoneModel(
            base_url, verifier_model, api_key, timeout_seconds
        )
        self.model_calls_total = 0
        self.model_image_calls = 0
        self.model_high_res_calls = 0
        self.model_latency_ms = 0
        self.model_input_tokens = 0
        self.model_output_tokens = 0

    @staticmethod
    def _parse_tool_call(content: str, image_size: tuple[int, int] = (1000, 1000)) -> dict:
        # In practice GUI-Plus may emit an empty <tool_call></tool_call> and
        # place the real JSON object immediately after it, or use a repeated
        # opening tag. Scan tag bodies first, then valid JSON objects in the
        # complete response instead of assuming one perfect XML wrapper.
        candidates = [item.strip() for item in re.findall(
            r"<tool_call>(.*?)</tool_call>", content, re.S | re.I
        ) if item.strip()]
        decoder = json.JSONDecoder()
        for index, character in enumerate(content):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(content[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                candidates.append(json.dumps(value, ensure_ascii=False))
        try:
            envelope = None
            for candidate in [*candidates, content.strip()]:
                try:
                    parsed = GUIPlusPhoneModel._load_tool_json(candidate)
                except ValueError:
                    continue
                if "action" in parsed or parsed.get("name") in {"phone_use", "computer_use", "mobile_use"}:
                    envelope = parsed
                    break
            if envelope is None:
                raise ValueError("tool payload is not a valid object")
            if "action" in envelope:
                envelope = {"name": "phone_use", "arguments": envelope}
            if envelope.get("name") not in {"phone_use", "computer_use", "mobile_use"}:
                raise ValueError("bare response is not a supported GUI tool envelope")
            if "arguments" not in envelope and "parameters" not in envelope:
                raise ValueError("GUI tool envelope has no arguments or parameters")
            args_value = envelope.get("arguments") or envelope.get("parameters") or {}
            if isinstance(args_value, str):
                args_value = GUIPlusPhoneModel._load_tool_json(args_value)
            if not isinstance(args_value, dict):
                raise ValueError("GUI tool arguments are not an object")
            args = dict(args_value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelClientError(f"invalid GUI-Plus tool call: {exc}") from exc
        action = str(args.pop("action", "")).lower()
        mapping = {
            "tap": "TAP",
            "click": "TAP",
            "left_click": "TAP",
            "double_click": "TAP",
            "triple_click": "TAP",
            "click": "TAP",
            "type": "TYPE",
            "swipe": "SWIPE",
            "scroll": "SWIPE",
            "back": "BACK",
            "key": "KEY",
            "system_button": "KEY",
            "wait": "WAIT",
            "report": "REPORT",
            "answer": "REPORT",
            "terminate": "REPORT" if args.get("status") == "success" else "ABORT",
            "take_over": "TAKE_OVER",
            "interact": "TAKE_OVER",
            "abort": "ABORT",
        }
        if action not in mapping:
            if action != "click_element":
                raise ModelClientError(f"unsupported GUI-Plus action: {action or '<empty>'}")
        translated_action = "CLICK_ELEMENT" if action == "click_element" else mapping[action]
        decision: dict[str, object] = {"action": translated_action}
        if translated_action == "CLICK_ELEMENT" and args.get("element_id") is not None:
            decision["element_id"] = args["element_id"]
        width, height = image_size

        def map_point(point) -> tuple[int, int] | None:
            if not isinstance(point, list) or len(point) < 2:
                return None
            return (
                min(max(int(float(point[0]) / 1000 * width), 0), width - 1),
                min(max(int(float(point[1]) / 1000 * height), 0), height - 1),
            )

        coordinate = args.get("coordinate")
        mapped = map_point(coordinate)
        if mapped:
            decision.update({"x": mapped[0], "y": mapped[1]})
            # Some GUI-Plus releases call a coordinate click
            # ``click_element`` despite not returning a Set-of-Mark id.
            if translated_action == "CLICK_ELEMENT" and "element_id" not in decision:
                decision["action"] = "TAP"
        end = args.get("coordinate2") or args.get("end_coordinate")
        mapped_end = map_point(end)
        if mapped_end:
            decision.update({"x2": mapped_end[0], "y2": mapped_end[1]})
        if action == "key":
            keys = args.get("keys") or []
            key = str(keys[0] if isinstance(keys, list) and keys else args.get("text") or args.get("key") or "").upper()
            allowed_keys = {"ENTER", "BACK", "ESC", "HOME"}
            if key not in allowed_keys:
                raise ModelClientError(f"unsupported GUI-Plus key: {key or '<empty>'}")
            if key == "BACK":
                decision["action"] = "BACK"
            else:
                decision["keycode"] = key
        if action == "system_button":
            button = str(args.get("button") or "")
            if button == "Back":
                decision = {"action": "BACK", "capability": "NAVIGATE"}
            elif button == "Enter":
                decision = {"action": "KEY", "keycode": "ENTER", "capability": "SEARCH"}
            else:
                raise ModelClientError(f"unsafe GUI-Plus system button: {button or '<empty>'}")
        if action == "scroll":
            pixels = int(args.get("pixels") or -600)
            decision.update({
                "action": "SWIPE", "x": 540, "x2": 540,
                "y": 800 if pixels > 0 else 1700,
                "y2": 1700 if pixels > 0 else 800,
                "duration_ms": 400,
            })
        if action == "wait" and args.get("time") is not None:
            decision["seconds"] = args["time"]
        for key in ("text", "seconds", "capability", "reason", "result", "target", "confidence"):
            if args.get(key) is not None:
                decision[key] = args[key]
        if decision["action"] in {"TAP", "SWIPE", "BACK"} and "capability" not in decision:
            decision["capability"] = "NAVIGATE"
        elif decision["action"] == "TYPE" and "capability" not in decision:
            decision["capability"] = "ENTER_TEXT"
        elif decision["action"] == "KEY" and "capability" not in decision:
            decision["capability"] = "SEARCH" if decision.get("keycode") == "ENTER" else "NAVIGATE"
        if decision["action"] == "REPORT" and "result" not in decision:
            text = str(args.get("text") or args.get("reason") or "任务完成")
            decision["observed_result"] = text
        return decision

    def _plan(self, prompt: str, frame_path: str) -> dict:
        image_bytes = Path(frame_path).read_bytes()
        encoded = base64.b64encode(image_bytes).decode("ascii")
        if image_bytes[:8] != b"\x89PNG\r\n\x1a\n":
            raise ModelClientError("GUI-Plus frame must be PNG")
        width = int.from_bytes(image_bytes[16:20], "big")
        height = int.from_bytes(image_bytes[20:24], "big")
        element_count = prompt.count('"element_id"')
        high_resolution = element_count >= 18 or any(
            marker in prompt for marker in ("联系人", "商品列表", "餐厅列表", "价格比较")
        )
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{encoded}",
                        "min_pixels": 3136,
                        "max_pixels": 12845056 if high_resolution else 1003520,
                    }},
                    {"type": "text", "text": prompt},
                ]},
            ],
            "temperature": 0.01,
            "vl_high_resolution_images": high_resolution,
            "enable_thinking": False,
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        started = time.perf_counter()
        try:
            with urlopen(Request(self.endpoint, data=body, headers=headers), timeout=self.timeout_seconds) as response:
                envelope = json.loads(response.read().decode("utf-8"))
            self.model_calls_total += 1
            self.model_image_calls += 1
            self.model_high_res_calls += int(high_resolution)
            self.model_latency_ms += int((time.perf_counter() - started) * 1000)
            usage = envelope.get("usage") or {}
            self.model_input_tokens += int(usage.get("prompt_tokens") or 0)
            self.model_output_tokens += int(usage.get("completion_tokens") or 0)
            content = envelope["choices"][0]["message"].get("content") or ""
            try:
                return self._parse_tool_call(content, (width, height))
            except ModelClientError:
                # GUI-Plus occasionally returns a natural-language action or
                # an empty tool envelope after several otherwise valid steps.
                # That is a provider protocol failure, not evidence that the
                # phone task itself failed. Re-ground the *same screenshot*
                # once with the strict JSON verifier model instead of burning
                # the task's replan budget on identical GUI-Plus retries.
                fallback_prompt = f"""你是手机 GUI 单步执行器。根据当前截图和以下上下文，只生成一个下一步动作。
{prompt}
屏幕原始尺寸为 {width}x{height}，坐标必须使用原始像素。
只返回 JSON 对象。action 只能是 TAP、SWIPE、TYPE、KEY、BACK、WAIT、REPORT、TAKE_OVER、ABORT。
TAP 需要 x,y；SWIPE 需要 x,y,x2,y2,duration_ms；TYPE 需要 text；KEY 需要 keycode；WAIT 需要 seconds。
操作动作必须给 capability：NAVIGATE、READ、SEARCH、ENTER_TEXT、MEDIA_CONTROL、CHANGE_SETTING、WRITE_DATA 或 SENSITIVE。
每次只做一个动作；不要返回 Markdown、解释、tool_call 标签或数组。"""
                decision = self.fallback.complete(fallback_prompt, frame_path)
                if not isinstance(decision, dict) or not decision.get("action"):
                    raise ModelClientError("GUI-Plus and fallback model both returned invalid actions")
                return decision
        except ModelClientError:
            raise
        except Exception as exc:
            raise ModelClientError(f"GUI-Plus unavailable: {exc}") from exc

    def complete(self, prompt: str, frame_path: str | None = None) -> dict:
        if frame_path and (
            "你是 BearBless 手机视觉 Agent" in prompt
            or "Please generate the next move according to the UI screenshot" in prompt
        ):
            return self._plan(prompt, frame_path)
        return self.fallback.complete(prompt, frame_path)

    def select_package(self, goal: str, installed_packages: list[str]) -> str:
        return self.fallback.select_package(goal, installed_packages)

    def usage_metrics(self) -> dict[str, int]:
        fallback = self.fallback.usage_metrics()
        return {
            "model_calls_total": self.model_calls_total + fallback["model_calls_total"],
            "model_image_calls": self.model_image_calls + fallback["model_image_calls"],
            "model_high_res_calls": self.model_high_res_calls,
            "model_latency_ms": self.model_latency_ms + fallback["model_latency_ms"],
            "model_input_tokens": self.model_input_tokens + fallback["model_input_tokens"],
            "model_output_tokens": self.model_output_tokens + fallback["model_output_tokens"],
        }


def build_phone_model(config: Config) -> PhoneModelClient:
    if config.phone_model_provider == "ollama":
        from bearbless.agent.vision import OllamaVisionClient
        return OllamaVisionClient(model=config.phone_model_name, endpoint=config.phone_model_base_url)
    if config.phone_model_provider == "openai_compatible":
        return OpenAICompatiblePhoneModel(
            config.phone_model_base_url,
            config.phone_model_name,
            config.phone_model_api_key,
        )
    if config.phone_model_provider == "gui_plus":
        if not config.phone_model_api_key:
            raise ModelClientError("PHONE_MODEL_API_KEY is required for gui_plus")
        return GUIPlusPhoneModel(
            config.phone_model_base_url,
            config.phone_model_name,
            config.phone_model_api_key,
            config.phone_verifier_model_name,
        )
    raise ModelClientError(f"unsupported PHONE_MODEL_PROVIDER={config.phone_model_provider}")
