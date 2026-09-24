from __future__ import annotations

import json
import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

try:
    from langfuse import Langfuse
except ImportError:  # pragma: no cover - optional dependency
    Langfuse = None


class _NoopObservation:
    def update(self, **kwargs: Any) -> None:
        return None


_NOOP_OBSERVATION = _NoopObservation()


def _trim_string(value: str, max_length: int = 4000) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + '...'


def _serialize(value: Any, *, max_string_length: int = 4000, max_collection_items: int = 30, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _trim_string(value, max_string_length)
    if depth >= 4:
        return _trim_string(str(value), max_string_length)
    if isinstance(value, dict):
        items = list(value.items())
        result: dict[str, Any] = {}
        for index, (key, item_value) in enumerate(items[:max_collection_items]):
            result[_trim_string(str(key), 200)] = _serialize(
                item_value,
                max_string_length=max_string_length,
                max_collection_items=max_collection_items,
                depth=depth + 1,
            )
        if len(items) > max_collection_items:
            result['_truncated_keys'] = len(items) - max_collection_items
        return result
    if isinstance(value, (list, tuple, set)):
        sequence = list(value)
        result = [
            _serialize(
                item,
                max_string_length=max_string_length,
                max_collection_items=max_collection_items,
                depth=depth + 1,
            )
            for item in sequence[:max_collection_items]
        ]
        if len(sequence) > max_collection_items:
            result.append(f'... {len(sequence) - max_collection_items} more items')
        return result
    try:
        json.dumps(value)
        return value
    except TypeError:
        return _trim_string(str(value), max_string_length)


@lru_cache(maxsize=1)
def get_langfuse_client():
    public_key = (os.getenv('LANGFUSE_PUBLIC_KEY') or '').strip()
    secret_key = (os.getenv('LANGFUSE_SECRET_KEY') or '').strip()
    host = (
        os.getenv('LANGFUSE_HOST')
        or os.getenv('LANGFUSE_BASE_URL')
        or 'https://cloud.langfuse.com'
    ).strip()
    if Langfuse is None or not public_key or not secret_key:
        return None
    try:
        return Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    except Exception:
        return None


def langfuse_enabled() -> bool:
    return get_langfuse_client() is not None


def langfuse_auth_check() -> bool | None:
    client = get_langfuse_client()
    if client is None:
        return None
    try:
        return bool(client.auth_check())
    except Exception:
        return None


@contextmanager
def start_observation(
    *,
    name: str,
    as_type: str = 'span',
    input: Any = None,
    output: Any = None,
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Iterator[Any]:
    client = get_langfuse_client()
    if client is None:
        yield _NOOP_OBSERVATION
        return

    observation_kwargs: dict[str, Any] = {
        'name': name,
        'as_type': as_type,
    }
    if input is not None:
        observation_kwargs['input'] = _serialize(input)
    if output is not None:
        observation_kwargs['output'] = _serialize(output)
    if metadata:
        observation_kwargs['metadata'] = _serialize(metadata)

    for key, value in kwargs.items():
        if value is not None:
            observation_kwargs[key] = _serialize(value)

    with client.start_as_current_observation(**observation_kwargs) as observation:
        yield observation


@contextmanager
def attach_trace_context(*, name: str | None = None, tags: list[str] | None = None, user_id: str | None = None, session_id: str | None = None) -> Iterator[None]:
    # Langfuse Python SDK v4 does not expose propagate_attributes on the root client.
    # The request root span started in the API layer acts as the trace anchor instead.
    yield


def current_trace_id() -> str | None:
    client = get_langfuse_client()
    if client is None:
        return None
    try:
        return client.get_current_trace_id()
    except Exception:
        return None


def current_trace_url(trace_id: str | None = None) -> str | None:
    client = get_langfuse_client()
    if client is None:
        return None
    try:
        if trace_id:
            return client.get_trace_url(trace_id=trace_id)
        return client.get_trace_url()
    except Exception:
        return None


def current_observation_id() -> str | None:
    client = get_langfuse_client()
    if client is None:
        return None
    try:
        return client.get_current_observation_id()
    except Exception:
        return None


def create_score(
    *,
    name: str,
    value: float | str,
    data_type: str | None = None,
    comment: str | None = None,
    metadata: Any = None,
    trace_id: str | None = None,
    observation_id: str | None = None,
) -> bool:
    client = get_langfuse_client()
    if client is None:
        return False
    try:
        kwargs: dict[str, Any] = {
            'name': name,
            'value': value,
        }
        if data_type is not None:
            kwargs['data_type'] = data_type
        if comment is not None:
            kwargs['comment'] = _trim_string(comment, 1000)
        if metadata is not None:
            kwargs['metadata'] = _serialize(metadata)
        if trace_id is not None:
            kwargs['trace_id'] = trace_id
        if observation_id is not None:
            kwargs['observation_id'] = observation_id
        client.create_score(**kwargs)
        return True
    except Exception:
        return False


def flush_langfuse() -> None:
    client = get_langfuse_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        return None


def shutdown_langfuse() -> None:
    client = get_langfuse_client()
    if client is None:
        return
    try:
        client.shutdown()
    except Exception:
        return None
