"""
Tools must not execute on the MCP server's event loop.

FastMCP calls a synchronous tool body inline in the asyncio task that reads
stdin, so before this was fixed a single slow tool froze the whole server: the
transport could not read the next request and the client saw no response at all
until it gave up. `mcp_server` therefore registers every sync tool behind a
coroutine that offloads to a worker thread.
"""

from __future__ import annotations

import asyncio
import inspect
import time

import mcp_server


def test_registered_tools_are_coroutines() -> None:
    """Each tool reaches FastMCP as an async callable, not a blocking def."""
    tools = mcp_server.mcp._tool_manager.list_tools()
    assert tools, "no tools registered"
    for tool in tools:
        assert tool.is_async, f"tool {tool.name} would run on the event loop"


def test_tool_schemas_survive_the_wrapper() -> None:
    """The thread wrapper must not erase parameter names or docstrings."""
    tools = {t.name: t for t in mcp_server.mcp._tool_manager.list_tools()}

    session_init = tools["session_init"]
    assert "project_path" in session_init.parameters["properties"]
    assert "Single-call session bootstrap" in session_init.description

    search = tools["search_codebase"]
    assert {"query", "n_results"} <= set(search.parameters["properties"])


def test_decorated_tools_stay_callable_as_plain_functions() -> None:
    """Direct callers (tests, run_index.py) keep the synchronous function."""
    assert not inspect.iscoroutinefunction(mcp_server.health)
    assert isinstance(mcp_server.health(), str)


def test_slow_tool_leaves_the_event_loop_free() -> None:
    """A tool that blocks for a while must not stall concurrent loop work."""

    def slow_tool() -> str:
        """A deliberately slow tool."""
        time.sleep(0.6)
        return "done"

    mcp_server.mcp.tool()(slow_tool)

    async def scenario() -> tuple[int, str]:
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        result = await mcp_server.mcp._tool_manager.call_tool("slow_tool", {})
        beat.cancel()
        return ticks, str(result)

    ticks, result = asyncio.run(scenario())

    assert "done" in result
    # A blocked loop produces zero heartbeats; a free one produces ~30.
    assert ticks > 5, f"event loop was starved during the tool call ({ticks} ticks)"
