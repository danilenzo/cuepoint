"""Shared HTTP retry logic with exponential backoff and jitter."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

import httpx
import requests
from loguru import logger

_RETRYABLE_STATUSES = (429, 500, 502, 503, 504)
_RETRYABLE_EXCEPTIONS = (requests.ConnectionError, requests.Timeout)
_RETRYABLE_EXCEPTIONS_ASYNC = (httpx.ConnectError, httpx.TimeoutException, httpx.PoolTimeout)


def retry_on_failure(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_statuses: tuple[int, ...] = _RETRYABLE_STATUSES,
    retryable_exceptions: tuple[type[Exception], ...] = _RETRYABLE_EXCEPTIONS,
) -> Callable[..., Any]:
    """Decorator that retries a function on transient HTTP failures.

    The decorated function must either:
      - Return a requests.Response (status checked against retryable_statuses)
      - Raise requests.HTTPError (status extracted from response)
      - Raise one of retryable_exceptions

    Exponential backoff with jitter: delay = min(base_delay * 2^attempt + random(0,1), max_delay).
    Respects Retry-After header on 429 responses.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    result = fn(*args, **kwargs)
                    # If the function returns a Response, check its status
                    if isinstance(result, requests.Response) and result.status_code in retryable_statuses:
                        if attempt < max_retries:
                            delay = _calc_delay(result, attempt, base_delay, max_delay)
                            logger.debug(
                                f"Retry {attempt + 1}/{max_retries} for {fn.__name__} "
                                f"(HTTP {result.status_code}), waiting {delay:.1f}s"
                            )
                            time.sleep(delay)
                            continue
                    return result
                except requests.HTTPError as e:
                    resp = e.response
                    if resp is not None and resp.status_code in retryable_statuses and attempt < max_retries:
                        delay = _calc_delay(resp, attempt, base_delay, max_delay)
                        logger.debug(
                            f"Retry {attempt + 1}/{max_retries} for {fn.__name__} "
                            f"(HTTP {resp.status_code}), waiting {delay:.1f}s"
                        )
                        time.sleep(delay)
                        last_exc = e
                        continue
                    raise
                except retryable_exceptions as e:
                    if attempt < max_retries:
                        delay = min(base_delay * (2**attempt) + random.random(), max_delay)
                        logger.debug(
                            f"Retry {attempt + 1}/{max_retries} for {fn.__name__} "
                            f"({type(e).__name__}), waiting {delay:.1f}s"
                        )
                        time.sleep(delay)
                        last_exc = e
                        continue
                    raise
            # Exhausted retries — raise the last exception
            if last_exc is not None:
                raise last_exc

        return wrapper

    return decorator


def async_retry_on_failure(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_statuses: tuple[int, ...] = _RETRYABLE_STATUSES,
) -> Callable[..., Any]:
    """Async decorator that retries on transient HTTP failures (httpx).

    Same semantics as retry_on_failure but for async functions using httpx.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    result = await fn(*args, **kwargs)
                    if isinstance(result, httpx.Response) and result.status_code in retryable_statuses:
                        if attempt < max_retries:
                            delay = _calc_delay(result, attempt, base_delay, max_delay)
                            logger.debug(
                                f"Retry {attempt + 1}/{max_retries} for {fn.__name__} "
                                f"(HTTP {result.status_code}), waiting {delay:.1f}s"
                            )
                            await asyncio.sleep(delay)
                            continue
                    return result
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in retryable_statuses and attempt < max_retries:
                        delay = _calc_delay(e.response, attempt, base_delay, max_delay)
                        logger.debug(
                            f"Retry {attempt + 1}/{max_retries} for {fn.__name__} "
                            f"(HTTP {e.response.status_code}), waiting {delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                        last_exc = e
                        continue
                    raise
                except _RETRYABLE_EXCEPTIONS_ASYNC as e:
                    if attempt < max_retries:
                        delay = min(base_delay * (2**attempt) + random.random(), max_delay)
                        logger.debug(
                            f"Retry {attempt + 1}/{max_retries} for {fn.__name__} "
                            f"({type(e).__name__}), waiting {delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                        last_exc = e
                        continue
                    raise
            if last_exc is not None:
                raise last_exc

        return wrapper

    return decorator


def _calc_delay(
    response: requests.Response | httpx.Response | None, attempt: int, base_delay: float, max_delay: float
) -> float:
    """Calculate delay, respecting Retry-After header on 429."""
    if response is not None and response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), max_delay)
            except ValueError:
                pass
    return float(min(base_delay * (2**attempt) + random.random(), max_delay))
