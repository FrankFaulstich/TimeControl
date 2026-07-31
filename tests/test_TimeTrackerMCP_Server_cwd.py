"""
Regression tests for TimeTrackerMCP_Server.py's MCP-SDK compatibility.

Covers two things:

1. The server must resolve config.json/data.json relative to its OWN
   location on disk, not the process's current working directory.

   Why this matters: MCP clients (Claude Desktop in particular) launch this
   script with an undefined, unpredictable working directory - not the repo
   root - and do not reliably honor a `cwd` override in their server config
   even though some setups suggest one. Without the fix, every relative path
   here and inside TimeTracker/i18n (config.json, data.json, ...) resolves
   against that undefined cwd instead, so config.json is silently "not
   found" and the server falls back to defaults - including the wrong
   transport (streamable-http instead of the configured stdio) - which
   breaks the handshake with a stdio-based client like Claude Desktop
   without any visible error. See TimeTrackerMCP_Server.py's
   SCRIPT_DIR/os.chdir() comment for the fix itself.

2. The server must work correctly against BOTH major versions of the mcp
   Python SDK - v1.x (mcp.server.fastmcp.FastMCP) and v2.x
   (mcp.server.mcpserver.MCPServer).

   Why this matters: mcp 2.0 renamed/moved FastMCP to MCPServer with an
   incompatible constructor and no backwards-compatible import shim, which
   once already broke this server outright (see TimeTrackerMCP_Server.py's
   import-time comment). Neither this repo's own requirements pin nor an end
   user's own system Python (which is what Claude Desktop's stdio config
   often actually points at) is guaranteed to have one SDK major version
   over the other, so both need to keep working - and a future mcp release
   could silently break either code path again without a test exercising it.

Both build one minimal, self-contained copy of the server and its
dependencies (tt/, i18n.py) in a temporary "install" directory with its own
config.json (mcp_transport: "stdio"), then launch it with a *different*
temporary directory as its cwd - mirroring how an MCP client invokes it -
and drive a real MCP stdio client against it. Neither ever touches the real
project's config.json/data.json.

What differs for the SDK-version tests is which Python interpreter launches
that install: two disposable venvs are created, one with "mcp<2" and one
with "mcp>=2", so both SDK major versions actually get exercised end-to-end
rather than just whichever one happens to be installed in the environment
running the test suite itself. The test client side (ClientSession,
stdio_client) is unaffected by which SDK major version the server-side venv
has - the two communicate over the wire protocol, not shared Python
imports - so the outer test process's own installed mcp version is used for
that regardless of which server venv is under test.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

try:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    MCP_SDK_AVAILABLE = True
except ImportError:
    MCP_SDK_AVAILABLE = False


def _venv_python(venv_dir):
    """Path to the interpreter inside a venv created by venv.create()."""
    if sys.platform == 'win32':
        return os.path.join(venv_dir, 'Scripts', 'python.exe')
    return os.path.join(venv_dir, 'bin', 'python')


def _build_install_dir():
    """
    A minimal, self-contained copy of exactly what the MCP server needs to
    import and run - not the whole repo - so these tests never touch the
    real project's files.
    """
    install_dir = tempfile.mkdtemp(prefix="timecontrol_mcp_install_")
    shutil.copy(os.path.join(REPO_ROOT, 'TimeTrackerMCP_Server.py'), install_dir)
    shutil.copy(os.path.join(REPO_ROOT, 'i18n.py'), install_dir)
    shutil.copytree(
        os.path.join(REPO_ROOT, 'tt'),
        os.path.join(install_dir, 'tt'),
        ignore=shutil.ignore_patterns('__pycache__'),
    )
    with open(os.path.join(install_dir, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump({"mcp_transport": "stdio", "language": "en", "data_file": "data.json"}, f)
    with open(os.path.join(install_dir, 'data.json'), 'w', encoding='utf-8') as f:
        json.dump({"projects": [], "next_id": 1}, f)
    return install_dir


async def _run_stdio_session(python_executable, server_script, launch_cwd, check):
    """
    Launches server_script over stdio with the given interpreter/cwd, drives
    a real MCP client session against it, and hands the session to `check`
    (an async callable) to do the actual assertions.
    """
    params = StdioServerParameters(
        command=python_executable,
        args=[server_script],
        # Deliberately NOT install_dir - this is the crux of the cwd
        # regression: the server must not need to be launched with any
        # particular cwd to find its own config.json/data.json.
        cwd=launch_cwd,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=15)
            await check(session)


async def _assert_server_responds(session, test_case):
    """Shared assertions for both the cwd test and the SDK-version tests."""
    tools = await session.list_tools()
    test_case.assertGreater(len(tools.tools), 0)

    result = await session.call_tool("get_version", {})
    text = result.content[0].text if result.content else None
    test_case.assertIsNotNone(text, "get_version returned no content")

    # A list-returning tool, not just get_version's plain string - v2
    # introduced explicit structured-output handling in its @tool()
    # decorator, so this specifically guards against a serialization
    # difference between the two SDK versions that a string-only check
    # wouldn't catch.
    projects_result = await session.call_tool("list_main_projects", {})
    test_case.assertIsNotNone(projects_result.content)


@unittest.skipUnless(sys.version_info >= (3, 10), "mcp requires Python 3.10+")
@unittest.skipUnless(MCP_SDK_AVAILABLE, "mcp SDK not installed (pip install mcp); skipping")
class TestMcpServerCwdIndependence(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.install_dir = _build_install_dir()
        cls.launch_cwd = tempfile.mkdtemp(prefix="timecontrol_mcp_launch_cwd_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.install_dir, ignore_errors=True)
        shutil.rmtree(cls.launch_cwd, ignore_errors=True)

    def test_starts_in_stdio_mode_regardless_of_launch_cwd(self):
        server_script = os.path.join(self.install_dir, 'TimeTrackerMCP_Server.py')

        async def check(session):
            await _assert_server_responds(session, self)

        asyncio.run(_run_stdio_session(sys.executable, server_script, self.launch_cwd, check))


@unittest.skipUnless(sys.version_info >= (3, 10), "mcp requires Python 3.10+")
@unittest.skipUnless(MCP_SDK_AVAILABLE, "mcp SDK not installed (pip install mcp); skipping")
class TestMcpServerAcrossSdkMajorVersions(unittest.TestCase):
    """
    Runs the server under two disposable venvs - one pinned to "mcp<2", one
    to "mcp>=2" - so both SDK major versions are exercised end-to-end,
    regardless of which one happens to be installed in the environment
    running the test suite itself.
    """

    VENV_SPECS = {'v1': 'mcp<2', 'v2': 'mcp>=2'}

    @classmethod
    def setUpClass(cls):
        cls.install_dir = _build_install_dir()
        cls.venv_dirs = {}
        cls.venv_pythons = {}
        for label, constraint in cls.VENV_SPECS.items():
            venv_dir = tempfile.mkdtemp(prefix=f"timecontrol_mcp_venv_{label}_")
            cls.venv_dirs[label] = venv_dir
            try:
                venv.create(venv_dir, with_pip=True)
                python_bin = _venv_python(venv_dir)
                subprocess.run(
                    [python_bin, '-m', 'pip', 'install', '--quiet', constraint],
                    check=True, timeout=180, capture_output=True, text=True,
                )
            except Exception as e:
                # A real-environment integration test (actual venv creation,
                # actual network installs) isn't worth blocking the rest of
                # the suite on when the sandbox running it has no internet
                # access or no venv/pip support - skip rather than fail.
                cls.tearDownClass()
                raise unittest.SkipTest(f"Could not set up an isolated venv for '{constraint}': {e}")
            cls.venv_pythons[label] = python_bin

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.install_dir, ignore_errors=True)
        for venv_dir in cls.venv_dirs.values():
            shutil.rmtree(venv_dir, ignore_errors=True)

    def _check_under(self, label):
        server_script = os.path.join(self.install_dir, 'TimeTrackerMCP_Server.py')
        launch_cwd = tempfile.mkdtemp(prefix="timecontrol_mcp_launch_cwd_")
        try:
            async def check(session):
                await _assert_server_responds(session, self)

            asyncio.run(_run_stdio_session(self.venv_pythons[label], server_script, launch_cwd, check))
        finally:
            shutil.rmtree(launch_cwd, ignore_errors=True)

    def test_works_under_mcp_v1(self):
        self._check_under('v1')

    def test_works_under_mcp_v2(self):
        self._check_under('v2')


if __name__ == '__main__':
    unittest.main()
