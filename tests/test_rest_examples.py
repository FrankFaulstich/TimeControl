"""
End-to-end smoke test for the example scripts in examples/REST/.

Unlike test_TimeTrackerREST_Server.py (which mocks TimeTracker and drives
the FastAPI app in-process via TestClient, never touching real network
traffic), this test starts the *actual* REST server as a subprocess and runs
each example script exactly as a user would from the command line, in the
order documented in examples/REST/README.md. Its purpose is to catch the
examples silently going out of sync with the real interface (e.g. a renamed
endpoint or field) - a class of bug the mocked tests cannot see. Mirrors
test_soap_examples.py step for step.

The server is pointed at a throwaway temporary directory for its
config.json/data.json, so this test never touches the real project data.

This test needs the 'requests' package (the HTTP client library the
examples use) and a working 'fastapi'/'uvicorn' installation (the REST
server libraries) to actually run; it skips itself cleanly if either is
unavailable.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXAMPLES_DIR = os.path.join(REPO_ROOT, 'examples', 'REST')
REST_SERVER_SCRIPT = os.path.join(REPO_ROOT, 'TimeTrackerREST_Server.py')

# Must match the BASE_URL / PROJECT_NAME / TASK_NAME constants hardcoded in
# the example scripts themselves.
REST_PORT = 8800
READY_URL = f"http://localhost:{REST_PORT}/openapi.json"
PROJECT_NAME = "REST Example Project"
TASK_NAME = "Write REST documentation"

try:
    import requests  # noqa: F401  (only used here to decide whether we can run this test)
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    import fastapi  # noqa: F401
    import uvicorn  # noqa: F401
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False


@unittest.skipUnless(REQUESTS_AVAILABLE, "requests is not installed (pip install requests); skipping REST example smoke test")
@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi/uvicorn are not installed; skipping REST example smoke test")
class TestRestExamplesSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sandbox_dir = tempfile.mkdtemp(prefix="timecontrol_rest_smoke_")
        with open(os.path.join(cls.sandbox_dir, 'config.json'), 'w', encoding='utf-8') as f:
            json.dump({"rest_port": REST_PORT, "data_file": "data.json", "language": "en"}, f)

        # Run from the sandbox directory so the server reads/writes its
        # config.json and data.json there instead of the real project files.
        cls.server_process = subprocess.Popen(
            [sys.executable, REST_SERVER_SCRIPT],
            cwd=cls.sandbox_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        if not cls._wait_for_server(timeout=60):
            output = cls._stop_server() or "(no output captured)"
            shutil.rmtree(cls.sandbox_dir, ignore_errors=True)
            if "fastapi" in output.lower() or "uvicorn" in output.lower() or "Bibliotheken" in output:
                raise unittest.SkipTest(
                    "The REST server could not start because 'fastapi'/'uvicorn' are not usable "
                    f"on this Python version. Server output:\n{output}"
                )
            raise RuntimeError(f"REST server did not become ready in time. Output:\n{output}")

    @classmethod
    def _wait_for_server(cls, timeout):
        """Polls the OpenAPI schema endpoint until it responds, the process exits, or timeout is reached."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cls.server_process.poll() is not None:
                return False
            try:
                urllib.request.urlopen(READY_URL, timeout=2)
                return True
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.5)
        return False

    @classmethod
    def _stop_server(cls):
        """Terminates the server process (if still running) and returns whatever it printed."""
        if cls.server_process.poll() is None:
            cls.server_process.terminate()
            try:
                cls.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.server_process.kill()
                cls.server_process.wait(timeout=5)
        try:
            return cls.server_process.stdout.read()
        except ValueError:
            return ""

    @classmethod
    def tearDownClass(cls):
        cls._stop_server()
        shutil.rmtree(cls.sandbox_dir, ignore_errors=True)

    def _run_example(self, script_name):
        """Runs an example script exactly as documented (`python examples/REST/<script>`)."""
        result = subprocess.run(
            [sys.executable, os.path.join(EXAMPLES_DIR, script_name)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            result.returncode, 0,
            f"{script_name} exited with {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        return result.stdout

    # Test methods are numbered (and therefore run in that order, since
    # unittest sorts test names alphabetically by default) because the
    # examples themselves are a sequence: each one builds on state created
    # by the previous one, just like a user following the README would.

    def test_01_create_project(self):
        output = self._run_example("01_create_project.py")
        self.assertIn(PROJECT_NAME, output)

    def test_02_create_task(self):
        output = self._run_example("02_create_task.py")
        self.assertIn(TASK_NAME, output)

    def test_03_start_work(self):
        output = self._run_example("03_start_work.py")
        self.assertIn("Started work on", output)
        self.assertIn("Currently active", output)
        self.assertIn(TASK_NAME, output)

    def test_04_stop_work(self):
        output = self._run_example("04_stop_work.py")
        self.assertIn("stopped", output)

    def test_05_daily_report(self):
        output = self._run_example("05_daily_report.py")
        # The task we started and stopped work on above should show up in
        # today's report.
        self.assertIn(TASK_NAME, output)


if __name__ == '__main__':
    unittest.main()
