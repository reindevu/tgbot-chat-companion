from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class PolzaLLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_retries: int = 2,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    async def generate(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        attempt = 0
        last_exc: Exception | None = None

        while attempt <= self._max_retries:
            attempt += 1
            started = time.perf_counter()
            try:
                print(messages)
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    timeout=self._timeout_seconds,
                    max_tokens=350
                )
                latency_ms = int((time.perf_counter() - started) * 1000)

                content = (response.choices[0].message.content or "").strip()
                if not content:
                    raise LLMError("Empty response from LLM")

                usage = response.usage.model_dump() if response.usage else None
                meta = {
                    "model": response.model,
                    "latency_ms": latency_ms,
                    "token_usage": usage,
                    "request_id": getattr(response, "id", None),
                }
                return content, meta
            except (APIConnectionError, RateLimitError) as exc:
                last_exc = exc
                if attempt > self._max_retries:
                    break
                await asyncio.sleep(2 ** (attempt - 1))
            except APIStatusError as exc:
                last_exc = exc
                retryable = exc.status_code in {429, 500, 502, 503, 504}
                if not retryable or attempt > self._max_retries:
                    break
                await asyncio.sleep(2 ** (attempt - 1))
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                break

        logger.exception("LLM request failed")
        raise LLMError("LLM request failed") from last_exc
