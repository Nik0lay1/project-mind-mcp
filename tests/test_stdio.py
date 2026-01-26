#!/usr/bin/env python3
"""Test MCP server stdio communication"""

import json
import subprocess
import sys


def test_mcp_server():
    """Test basic MCP server communication"""

    # Start server
    process = subprocess.Popen(
        [".venv/bin/python", "mcp_server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0
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
                "clientInfo": {
                    "name": "test-client",
                    "version": "1.0.0"
                }
            }
        }

        print("Sending initialize request...")
        request_str = json.dumps(initialize_request) + "\n"
        process.stdin.write(request_str)
        process.stdin.flush()

        # Read response
        print("Waiting for response...")
        response_line = process.stdout.readline()
        print(f"Response: {response_line}")

        if response_line:
            response = json.loads(response_line)
            print(f"Parsed response: {json.dumps(response, indent=2)}")

            if "result" in response:
                print("\n✅ Server responded successfully!")
                print(f"Server capabilities: {response['result'].get('capabilities', {})}")
                return True
            elif "error" in response:
                print(f"\n❌ Server returned error: {response['error']}")
                return False
        else:
            print("\n❌ No response from server")
            stderr = process.stderr.read()
            if stderr:
                print(f"Server stderr: {stderr}")
            return False

    except Exception as e:
        print(f"\n❌ Error: {e}")
        stderr = process.stderr.read()
        if stderr:
            print(f"Server stderr: {stderr}")
        return False
    finally:
        process.terminate()
        process.wait(timeout=1)

if __name__ == "__main__":
    success = test_mcp_server()
    sys.exit(0 if success else 1)
