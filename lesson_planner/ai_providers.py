from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlparse

import requests

from . import progress


class ProviderError(RuntimeError):
    """A readable error returned by an AI provider."""


@dataclass(slots=True)
class ProviderConfig:
    provider: str
    api_key: str
    model: str
    base_url: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProviderConfig":
        provider = str(raw.get("provider", "")).strip().lower()
        api_key = str(raw.get("api_key", "")).strip()
        model = str(raw.get("model", "")).strip()
        base_url = str(raw.get("base_url", "")).strip().rstrip("/")
        if provider not in {"gemini", "groq", "mistral", "openrouter", "custom"}:
            raise ProviderError("Choose a supported AI provider.")
        if provider != "custom":
            base_url = ""
        if not api_key:
            raise ProviderError("Paste an API key for the selected provider.")
        if provider == "openrouter" and not model:
            model = AUTO_MODEL
        if not model:
            raise ProviderError("Enter a model name.")
        if provider == "custom" and not base_url:
            raise ProviderError("A base URL is required for a custom provider.")
        return cls(provider, api_key, model, base_url)


OPENAI_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


AUTO_MODEL = "auto"
# OpenRouter's own router. It is the only model id LessonFlow ever names without first
# seeing it in the live catalogue, and only as a last resort when the catalogue is unreachable.
OPENROUTER_ROUTER = "openrouter/free"
_auto_session: dict[str, str] = {}


def generate_json(
    config: ProviderConfig,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 180,
    validate: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """`validate` lets automatic model routing reject a model that returns the wrong JSON shape."""
    if config.provider == "openrouter" and config.model in {"", AUTO_MODEL}:
        return _openrouter_auto_json(config, system_prompt, user_prompt, timeout, validate)
    if config.provider == "gemini":
        text = _gemini_request(config, system_prompt, user_prompt, timeout)
    else:
        text = _openai_compatible_request(config, system_prompt, user_prompt, timeout)
    return parse_json_response(text)


def openrouter_auto_candidates() -> list[str]:
    """Rank the free OpenRouter models that can actually return the JSON LessonFlow needs."""
    try:
        models = list_openrouter_models()
    except ProviderError:
        # No catalogue means no way to know which ids still exist, so defer to OpenRouter's router.
        return [OPENROUTER_ROUTER]
    usable = [
        model for model in models
        if model["free"] and model["json_mode"] and model["text_only_output"]
    ]
    usable.sort(key=lambda model: (not model["structured"], -model["context_length"]))
    ranked = [model["id"] for model in usable if model["id"] != OPENROUTER_ROUTER][:5]
    # Only name the router if the catalogue still lists it.
    if any(model["id"] == OPENROUTER_ROUTER for model in models):
        ranked.append(OPENROUTER_ROUTER)
    return ranked or [OPENROUTER_ROUTER]


def _openrouter_auto_json(
    config: ProviderConfig,
    system_prompt: str,
    user_prompt: str,
    timeout: int,
    validate: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    candidates = openrouter_auto_candidates()
    preferred = _auto_session.get(config.api_key)
    if preferred:
        candidates = [preferred] + [item for item in candidates if item != preferred]
    failures: list[str] = []
    total = len(candidates)
    for position, candidate in enumerate(candidates, start=1):
        attempt = ProviderConfig(config.provider, config.api_key, candidate, config.base_url)
        # A busy free model is not worth waiting on while alternatives remain: skip to the next
        # one immediately and keep the patient retries for the last candidate, which has no successor.
        attempts = 4 if position == total else 1
        progress.detail(
            f"Asking free model {position} of {total} ({_short_model(candidate)})…"
            if position == 1
            else f"{_short_model(candidates[position - 2])}: {failures[-1].split(': ', 1)[-1][:180] if failures else 'did not work'} "
            f"Now trying model {position} of {total} ({_short_model(candidate)})…"
        )
        try:
            result = parse_json_response(
                _openai_compatible_request(attempt, system_prompt, user_prompt, timeout, attempts, stream=True)
            )
        except ProviderError as exc:
            if _is_key_problem(exc):
                raise
            failures.append(f"{candidate}: {exc}")
            _auto_session.pop(config.api_key, None)
            continue
        if validate is not None:
            try:
                validate(result)
            except Exception as exc:  # a wrong-shaped answer means this model cannot do the job
                failures.append(f"{candidate}: replied in the wrong format ({exc})")
                _auto_session.pop(config.api_key, None)
                continue
        _auto_session[config.api_key] = candidate
        return result
    raise ProviderError(
        "Every free OpenRouter model refused this request. Free models are rate limited, so wait a few minutes "
        "and try again. Details - " + "; ".join(failures[:3])
    )


def _short_model(model_id: str) -> str:
    """The readable half of a slug, for messages a teacher has to look at."""
    return str(model_id).split("/")[-1].replace(":free", "")


def _is_key_problem(exc: ProviderError) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in ("user not found", "no auth", "invalid api key", "unauthorized", "401", "403"))


def _gemini_request(config: ProviderConfig, system_prompt: str, user_prompt: str, timeout: int) -> str:
    model = quote(config.model, safe="-._")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    response = _post_with_retry(
        url,
        headers={"x-goog-api-key": config.api_key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=timeout,
    )
    if response.status_code in {408, 429, 500, 502, 503, 504}:
        raise ProviderError(
            "Gemini is still temporarily busy after four automatic retries. "
            "Choose 'Recommended - Gemini 3.6 Flash' or 'Fastest - Gemini 3.5 Flash-Lite' and try again."
        )
    payload = _response_json(response)
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(_provider_message(payload, "Gemini returned no usable response.")) from exc


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_openrouter_cache: dict[str, Any] = {"fetched_at": 0.0, "models": []}
CATALOGUE_CACHE_PATH = Path(__file__).resolve().parent.parent / "runtime" / "openrouter-models.json"


def _read_cached_catalogue() -> list[dict[str, Any]]:
    """Last known-good catalogue, so one failed fetch does not lose every model id."""
    try:
        stored = json.loads(CATALOGUE_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    models = stored.get("models") if isinstance(stored, dict) else None
    return models if isinstance(models, list) else []


def _write_cached_catalogue(models: list[dict[str, Any]]) -> None:
    try:
        CATALOGUE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CATALOGUE_CACHE_PATH.write_text(
            json.dumps({"fetched_at": time.time(), "models": models}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass  # a missing cache is a slower start, never a failure


def list_openrouter_models(timeout: int = 20, max_age: float = 600.0) -> list[dict[str, Any]]:
    """Return OpenRouter's live catalogue so the teacher can pick any model their key allows."""
    age = time.time() - float(_openrouter_cache["fetched_at"])
    if _openrouter_cache["models"] and age < max_age:
        return list(_openrouter_cache["models"])
    try:
        response = requests.get(OPENROUTER_MODELS_URL, timeout=timeout)
    except requests.RequestException as exc:
        cached = _read_cached_catalogue()
        if cached:
            _openrouter_cache["models"] = cached
            _openrouter_cache["fetched_at"] = time.time()
            return list(cached)
        raise ProviderError(f"Could not reach OpenRouter's model list: {exc}") from exc
    payload = _response_json(response)
    models = []
    for item in payload.get("data", []):
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            continue
        pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
        supported = item.get("supported_parameters")
        supported = supported if isinstance(supported, list) else []
        architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
        output_modalities = architecture.get("output_modalities")
        output_modalities = output_modalities if isinstance(output_modalities, list) else ["text"]
        try:
            context_length = int(item.get("context_length") or 0)
        except (TypeError, ValueError):
            context_length = 0
        models.append(
            {
                "id": str(item["id"]).strip(),
                "name": str(item.get("name") or item["id"]).strip(),
                "context_length": context_length,
                "free": str(pricing.get("prompt", "")).strip() == "0"
                and str(pricing.get("completion", "")).strip() == "0",
                "json_mode": "response_format" in supported,
                "structured": "structured_outputs" in supported,
                "text_only_output": output_modalities == ["text"],
            }
        )
    models.sort(key=lambda model: (not model["free"], not model["json_mode"], model["name"].lower()))
    _openrouter_cache["models"] = models
    _openrouter_cache["fetched_at"] = time.time()
    _write_cached_catalogue(models)
    return list(models)


def _stream_chat_completion(
    url: str, *, headers: dict, payload: dict, timeout: int,
    first_token_limit: float = 30.0, silence_limit: float = 120.0
) -> str:
    """Read a streamed completion.

    Two different waits, because they mean different things. A model that is going to work starts
    producing within seconds, so `first_token_limit` is short: nothing at all by then means this
    model is not answering and the next one should be tried. Once text is flowing the model is
    demonstrably working, so `silence_limit` between chunks is generous and a slow model writing a
    long plan is never cut off.
    """
    body = dict(payload)
    body["stream"] = True
    try:
        response = requests.post(url, headers=headers, json=body, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        raise ProviderError(f"Could not reach the AI provider: {exc}") from exc

    with response:
        # requests falls back to ISO-8859-1 for a text/* stream with no charset, which mangles
        # every en dash and non-breaking hyphen the model writes. The API is always UTF-8.
        response.encoding = "utf-8"
        if not response.ok:
            try:
                raise ProviderError(_provider_message(response.json(), f"Provider request failed with HTTP {response.status_code}."))
            except ValueError:
                raise ProviderError(f"Provider returned HTTP {response.status_code}.") from None

        pieces: list[str] = []
        last_content_at = time.time()
        reported = 0
        for raw_line in response.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = raw_line.strip()
            waited = time.time() - last_content_at
            limit = silence_limit if pieces else first_token_limit
            # Blank lines and ": keep-alive" comments prove the socket lives but are not output.
            if not line or line.startswith(":"):
                if waited > limit:
                    break
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if isinstance(event.get("error"), (dict, str)):
                raise ProviderError(_provider_message(event, "The provider reported an error mid-response."))
            for choice in event.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content") or ""
                if piece:
                    pieces.append(piece)
                    last_content_at = time.time()
            written = sum(len(piece) for piece in pieces)
            if written - reported >= 400:
                reported = written
                progress.detail(f"The AI is writing the answer… {written:,} characters so far")
            if not pieces and time.time() - last_content_at > first_token_limit:
                break

    text = "".join(pieces)
    if not text.strip():
        raise ProviderError(
            f"accepted the request but wrote nothing in {int(first_token_limit)}s"
        )
    return text


def _openai_compatible_request(
    config: ProviderConfig, system_prompt: str, user_prompt: str, timeout: int, attempts: int = 4,
    stream: bool = False,
) -> str:
    base_url = config.base_url or OPENAI_BASE_URLS[config.provider]
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    if config.provider == "openrouter":
        headers.update({"HTTP-Referer": "http://127.0.0.1", "X-Title": "Lesson Planner"})
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    if config.provider == "groq" and config.model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
        payload["reasoning_effort"] = "low"
        payload["include_reasoning"] = False
    if stream:
        return _stream_chat_completion(
            f"{base_url}/chat/completions", headers=headers, payload=payload, timeout=timeout
        )
    response = _post_with_retry(
        f"{base_url}/chat/completions", headers=headers, json=payload, timeout=timeout, attempts=attempts
    )
    if config.provider == "groq" and response.status_code == 400:
        try:
            error_text = json.dumps(response.json()).lower()
        except ValueError:
            error_text = response.text.lower()
        if "failed_generation" in error_text or "failed to validate json" in error_text:
            fallback = dict(payload)
            fallback.pop("response_format", None)
            fallback["messages"] = [
                {
                    "role": "system",
                    "content": system_prompt + " Return exactly one valid JSON object with double-quoted keys and strings. Do not use Markdown or add commentary.",
                },
                {"role": "user", "content": user_prompt},
            ]
            response = _post_with_retry(
                f"{base_url}/chat/completions",
                headers=headers,
                json=fallback,
                timeout=timeout,
                attempts=attempts,
            )
    if config.provider == "custom" and response.status_code == 400:
        try:
            message = json.dumps(response.json()).lower()
        except ValueError:
            message = response.text.lower()
        if "response_format" in message or "json_object" in message:
            payload.pop("response_format", None)
            response = _post_with_retry(
                f"{base_url}/chat/completions", headers=headers, json=payload, timeout=timeout, attempts=attempts
            )
    payload = _response_json(response)
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(_provider_message(payload, "The provider returned no usable response.")) from exc


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(
            f"Provider returned HTTP {response.status_code} with a non-JSON response{_endpoint_note(response)}."
        ) from exc
    if not response.ok:
        message = _provider_message(payload, f"Provider request failed with HTTP {response.status_code}.")
        if response.status_code in {401, 403}:
            message = f"{message.rstrip('.')}. This answer came{_endpoint_note(response)}."
        raise ProviderError(message)
    return payload


def _endpoint_note(response: requests.Response) -> str:
    host = urlparse(str(response.request.url if response.request is not None else "")).netloc
    return f" from {host}" if host else ""


def _post_with_retry(
    url: str, *, headers: dict, json: dict, timeout: int, attempts: int = 4
) -> requests.Response:
    """`attempts=1` means give up immediately, for when a caller has another model to try."""
    transient_statuses = {408, 429, 500, 502, 503, 504}
    delays = [1.0, 2.0, 4.0]
    last_error = None
    for attempt in range(attempts):
        try:
            response = requests.post(url, headers=headers, json=json, timeout=timeout)
            last_error = None
        except requests.RequestException as exc:
            response = None
            last_error = exc
        if response is not None and response.status_code not in transient_statuses:
            return response
        if attempt == attempts - 1:
            if response is not None:
                return response
            raise ProviderError(f"Could not reach the AI provider after four attempts: {last_error}") from last_error
        retry_after = None
        if response is not None:
            try:
                retry_after = float(response.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = None
        delay = min(10.0, retry_after if retry_after is not None else delays[attempt] + random.uniform(0, 0.35))
        time.sleep(delay)
    raise ProviderError("The AI provider remained unavailable after automatic retries.")


def _provider_message(payload: dict[str, Any], fallback: str) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or fallback)
        # OpenRouter's own message is often generic ("Provider returned error") while the useful
        # explanation sits in metadata.raw, so surface that instead of hiding it.
        metadata = error.get("metadata")
        if isinstance(metadata, dict):
            raw = str(metadata.get("raw") or "").strip()
            if raw and raw.lower() not in message.lower():
                message = f"{message.rstrip('.')}: {raw}"
        return message
    if error:
        return str(error)
    return fallback


def repair_mojibake(value: str) -> str:
    """Undo UTF-8 that was decoded as Latin-1 somewhere upstream.

    An en dash arrives as three stray characters, two of them C1 controls that Word drops when it
    renders the page. The stored text then no longer matches the rendered text, so section
    outlines land in the wrong place. Repairing it on the way in keeps the two in step.
    """
    markers = ("\u00e2\u0080", "\u00e2\u0082", "\u00c3\u00a9", "\u00c2\u00a0", "\u00e2\u0084")
    if not any(marker in value for marker in markers):
        return value
    try:
        return value.encode("iso-8859-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _repair_strings(value: Any) -> Any:
    if isinstance(value, str):
        return repair_mojibake(value)
    if isinstance(value, list):
        return [_repair_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _repair_strings(item) for key, item in value.items()}
    return value


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = str(text).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ProviderError("The model did not return valid JSON.")
        try:
            result = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError("The model returned malformed JSON. Try the request again.") from exc
    if not isinstance(result, dict):
        raise ProviderError("The model response must be a JSON object.")
    return _repair_strings(result)
