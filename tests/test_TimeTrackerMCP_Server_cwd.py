"""
Regression test: TimeTrackerMCP_Server.py must resolve config.json/data.json
relative to its OWN location on disk, not the process's current working
directory.

Why this matters: MCP clients (Claude Desktop in particular) launch this
script with an undefined, unpredictable working directory - not the repo
root - and do not reliably honor a `cwd` override in their server config
even though some setups suggest one. Before the fix, every relative path
here and inside TimeTracker/i18n (config.json, data.json, ...) resolved
against that undefined cwd instead, so config.json was silently "not found"
and the server fell back to defaults - including the wrong transport
(streamable-http instead of the configured stdio) - which breaks the
handshake with a stdio-based client like Claude Desktop without any visible
error. See TimeTrackerMCP_Server.py's SCRIPT_DIR/os.chdir() comment for the
fix itself.

This test builds a minimal, self-contained copy of the server and its
dependencies (tt/, i18n.py) in a temporary "install" directory with its own
config.json (mcp_transport: "stdio"), then launches it with a *different*
temporary directory as its cwd - mirroring how an MCP client invokes it -
and drives a real MCP stdio client against it. It never touches the real
project's config.json/data.json.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

try:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    MCP_SDK_AVAILABLE = True
except ImportError:
    MCP_SDK_AVAILABLE = False


@unittest.skipUnless(sys.version_info >= (3, 10), "mcp requires Python 3.10+")
@unittest.skipUnless(MCP_SDK_AVAILABLE, "mcp SDK not installed (pip install mcp); skipping")
class TestMcpServerCwdIndependence(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.install_dir = tempfile.mkdtemp(prefix="timecontrol_mcp_install_")
        cls.launch_cwd = tempfile.mkdtemp(prefix="timecontrol_mcp_launch_cwd_")

        # A minimal, self-contained copy of exactly what the MCP server needs
        # to import and run - not the whole repo - so this never touches the
        # real project files.
        shutil.copy(os.path.join(REPO_ROOT, 'TimeTrackerMCP_Server.py'), cls.install_dir)
        shutil.copy(os.path.join(REPO_ROOT, 'i18n.py'), cls.install_dir)
        shutil.copytree(
            os.path.join(REPO_ROOT, 'tt'),
            os.path.join(cls.install_dir, 'tt'),
            ignore=shutil.ignore_patterns('__pycache__'),
        )

        with open(os.path.join(cls.install_dir, 'config.json'), 'w', encoding='utf-8') as f:
            json.dump({"mcp_transport": "stdio", "language": "en", "data_file": "data.json"}, f)
        with open(os.path.join(cls.install_dir, 'data.json'), 'w', encoding='utf-8') as f:
            json.dump({"projects": [], "next_id": 1}, f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.install_dir, ignore_errors=True)
        shutil.rmtree(cls.launch_cwd, ignore_errors=True)

    def test_starts_in_stdio_mode_regardless_of_launch_cwd(self):
        server_script = os.path.join(self.install_dir, 'TimeTrackerMCP_Server.py')

        async def run():
            params = StdioServerParameters(
                command=sys.executable,
                args=[server_script],
                # Deliberately NOT install_dir - this is the crux of the
                # regression: the server must not need to be launched with
                # any particular cwd to find its own config.json/data.json.
                cwd=self.launch_cwd,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), timeout=15)
                    tools = await session.list_tools()
                    self.assertGreater(len(tools.tools), 0)

                    result = await session.call_tool("get_version", {})
                    text = result.content[0].text if result.content else None
                    self.assertIsNotNone(text, "get_version returned no content")

        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
