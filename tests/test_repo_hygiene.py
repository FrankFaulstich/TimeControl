import json
import os
import subprocess
import unittest

# Add parent directory to path to import modules from root
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Fields of config.json's "email" block that must never reach the repository
# with a value in them. imap_server is included alongside the obvious two: it
# names infrastructure, and it has no business in a shipped default config
# either.
EMAIL_FIELDS_THAT_MUST_BE_BLANK = ("imap_server", "user", "password")


def _committed(path):
    """
    Returns the contents of a path as it exists in HEAD, or None.

    Deliberately reads the committed blob rather than the working tree: the
    point is to catch what would be published, and checking the working copy
    would instead fail throughout any legitimate local test with real
    settings entered - which is exactly when a noisy test suite is least
    useful.

    :param path: Repository-relative path to read.
    :return: The file contents as text, or None if unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "show", "HEAD:%s" % path],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


class TestRepoHygiene(unittest.TestCase):
    """
    Guards against secrets reaching the repository.

    config.json is tracked on purpose - it is the default configuration a
    fresh install starts from, and update.py lists it as a protected file so
    an update never overwrites a user's own. That also means the live file
    and the shipped file are one and the same, so anything typed into the
    Settings screen is one routine 'git add -A' away from being published.
    The repository is public, so that would be permanent and would reach
    every clone and fork.

    Note that .gitignore is no help here: it only applies to files that are
    not yet tracked, and both config.json and data.json already are.
    """

    def test_committed_config_has_no_email_credentials(self):
        raw = _committed("config.json")
        if raw is None:
            self.skipTest("config.json is not retrievable from HEAD (no git checkout?)")

        email = json.loads(raw).get("email", {})
        populated = [f for f in EMAIL_FIELDS_THAT_MUST_BE_BLANK if email.get(f)]

        self.assertEqual(
            populated,
            [],
            "config.json in HEAD carries real email settings in %s.\n"
            "The repository is public, so committing this publishes it "
            "permanently.\n"
            "Blank the fields in the Settings screen (or edit config.json), "
            "then amend the commit - and remember that removing it in a later "
            "commit does NOT remove it from the history."
            % ", ".join(populated),
        )

    def test_committed_config_email_stays_disabled(self):
        """A shipped default must not have email import switched on."""
        raw = _committed("config.json")
        if raw is None:
            self.skipTest("config.json is not retrievable from HEAD (no git checkout?)")

        email = json.loads(raw).get("email", {})
        self.assertFalse(
            email.get("enabled", False),
            "config.json in HEAD has email import enabled. A fresh install "
            "would start trying to fetch mail from settings that are not its "
            "own.",
        )


if __name__ == '__main__':
    unittest.main()
