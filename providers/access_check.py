"""Explicit metadata-only access check: no generation and no private payload."""

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class AccessResult:
    state: str
    message: str


def check_access(settings, provider, models, *, transport=None, timeout=5):
    if provider not in ("gemini", "openai") or not models:
        return AccessResult("model", "Выберите провайдера и модель")
    key = getattr(settings, provider + "_api_key").get_secret_value().strip()
    if not key:
        return AccessResult("missing", "Ключ отсутствует")

    async def run():
        headers = (
            {"Authorization": "Bearer " + key}
            if provider == "openai"
            else {"x-goog-api-key": key}
        )
        base = (
            "https://api.openai.com/v1/models/"
            if provider == "openai"
            else "https://generativelanguage.googleapis.com/v1beta/models/"
        )
        async with httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        ) as client:
            for model in dict.fromkeys(models):
                async with client.stream(
                    "GET", base + quote(model, safe="")
                ) as response:
                    if response.status_code in (401, 403):
                        return AccessResult(
                            "auth", "Ошибка авторизации или прав доступа"
                        )
                    if response.status_code == 404:
                        return AccessResult(
                            "model", "Модель недоступна для этого ключа"
                        )
                    if response.status_code != 200:
                        return AccessResult(
                            "error", "Проверка API не выполнена. Повторите позже"
                        )
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 65536:
                            return AccessResult(
                                "error", "Некорректный ответ проверки API"
                            )
                        data.extend(chunk)
                    payload = json.loads(data)
                    if not isinstance(payload, dict) or (
                        payload.get("id") != model
                        if provider == "openai"
                        else payload.get("name") != "models/" + model
                    ):
                        return AccessResult(
                            "model", "Метаданные выбранной модели не подтверждены"
                        )
        return AccessResult("verified", "Доступ к метаданным моделей проверен")

    try:
        return asyncio.run(asyncio.wait_for(run(), timeout))
    except (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException):
        return AccessResult("timeout", "Истекло время проверки доступа")
    except Exception:  # noqa: BLE001 - never expose keys, URLs or server bodies
        return AccessResult("error", "Не удалось проверить доступ к API")
