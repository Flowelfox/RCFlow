"""Native RCFlow tools — session-aware in-worker callables (``python`` executor).

Unlike shell/http tools, native tools run in-process with direct access to
the live :class:`~src.core.session.ActiveSession` and the ``PromptRouter``, so
they can push client notifications, read session state, register artifacts,
and manage tasks. They are dispatched from
``PromptRouter.execute_one_shot_tool`` — the shared choke point used by both
the LLM tool loop and the MCP agent bridge — so exposing one to nested agents
is the same registry-driven, zero-touch flow as any other tool.

A tool definition selects its callable via
``executor_config.python.callable = "module:function"``. The function is
``async def run(ctx: NativeToolContext, params: dict) -> str`` and may raise on
error (the dispatcher turns that into an ``is_error`` outcome).
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.prompt_router import PromptRouter
    from src.core.session import ActiveSession

NativeToolFn = Callable[["NativeToolContext", dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class NativeToolContext:
    """Everything a native tool needs to touch RCFlow state."""

    session: ActiveSession
    router: PromptRouter


class NativeToolRegistry:
    """Resolves and runs ``module:function`` native-tool callables (cached)."""

    def __init__(self, router: PromptRouter) -> None:
        self._router = router
        self._cache: dict[str, NativeToolFn] = {}

    def _resolve(self, dotted: str) -> NativeToolFn:
        fn = self._cache.get(dotted)
        if fn is not None:
            return fn
        module_name, _, attr = dotted.partition(":")
        if not module_name or not attr:
            raise ValueError(f"Invalid native tool callable '{dotted}' (expected 'module:function')")
        module = importlib.import_module(module_name)
        resolved = getattr(module, attr, None)
        if resolved is None or not callable(resolved):
            raise ValueError(f"Native tool callable '{dotted}' not found or not callable")
        self._cache[dotted] = resolved
        return resolved

    async def dispatch(
        self,
        session: ActiveSession,
        dotted_callable: str,
        params: dict[str, Any],
    ) -> str:
        """Run the native tool; exceptions propagate to the caller's error path."""
        fn = self._resolve(dotted_callable)
        ctx = NativeToolContext(session=session, router=self._router)
        return await fn(ctx, params)
