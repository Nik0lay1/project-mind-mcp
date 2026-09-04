#!/usr/bin/env python3
"""Test MCP server stdio communication"""

import json
import subprocess
import sys
import time

import pytest

# The server imports tree-sitter and the numpy/torch stack on the main thread
# before it answers anything (see warmup.py — those imports hang if a worker
# thread does them). That is real work: ~10 s idle, more on a loaded machine,
# and it lands entirely inside `initialize`. The suite-wide 30 s budget started
# failing here at random once the imports moved to boot.
BOOT_BUDGET_SECONDS = 120.0


@pytest.mark.timeout(180)
def test_mcp_server() -> None:
    """Test basic MCP server communication"""

    python_path = sys.executable

    # Start server
    process = subprocess.Popen(
        [python_path, "mcp_server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0,
    )

    try:
        # Send initialize request
        initialize_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }

        print("Sending initialize request...")
        request_str = json.dumps(initialize_request) + "\n"
        assert process.stdin is not None
        process.stdin.write(request_str)
        process.stdin.flush()

        # Read response
        print("Waiting for response...")
        assert process.stdout is not None
        started = time.monotonic()
        response_line = process.stdout.readline()
        boot_seconds = time.monotonic() - started
        print(f"Boot-to-initialize: {boot_seconds:.1f}s")
        print(f"Response: {response_line}")

        # Guards the operational limit, not the test: an MCP client gives the
        # server a fixed window to answer `initialize`. If boot creeps past it,
        # the server stops being launchable and this is where we find out.
        assert boot_seconds < BOOT_BUDGET_SECONDS, (
            f"server took {boot_seconds:.1f}s to answer initialize; clients will "
            "time out on startup"
        )

        if response_line:
            response = json.loads(response_line)
            print(f"Parsed response: {json.dumps(response, indent=2)}")

            if "result" in response:
                print("\n✅ Server responded successfully!")
                print(f"Server capabilities: {response['result'].get('capabilities', {})}")
                assert True
            elif "error" in response:
                print(f"\n❌ Server returned error: {response['error']}")
                raise AssertionError(f"Server returned error: {response['error']}")
        else:
            print("\n❌ No response from server")
            assert process.stderr is not None
            stderr = process.stderr.read()
            if stderr:
                print(f"Server stderr: {stderr}")
            raise AssertionError("No response from server")

    except json.JSONDecodeError as e:
        print(f"\n❌ JSON Error: {e}")
        assert process.stderr is not None
        stderr = process.stderr.read()
        if stderr:
            print(f"Server stderr: {stderr}")
        raise AssertionError(f"JSON decode error: {e}") from e
    finally:
        process.terminate()
        process.wait(timeout=1)


if __name__ == "__main__":
    try:
        test_mcp_server()
        sys.exit(0)
    except AssertionError:
        sys.exit(1)
