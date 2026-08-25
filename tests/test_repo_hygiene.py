import json
import os
import subprocess
import unittest
from urllib.parse import urlsplit

# Add parent directory to path to import modules from root
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Fields of config.json's "email" block that must never reach the repository
# with a value in them. imap_server is included alongside the obvious two: it
# names infrastructure, and it has no business in a shipped default config
# either.
EMAIL_FIELDS_THAT_MUST_BE_BLANK = ("imap_server", "user", "password")

# RFC 2606 reserves these for documentation, so an address under one of them
# names nobody's machine and is safe to publish.
PLACEHOLDER_HOSTS = ("example.com", "example.net", "example.org")

# The one the README's Configuration section shows, quoted in the failure
# message so there is something to copy rather than invent.
DOCUMENTED_SYNC_URL = "https://example.com/tc/"


def _is_placeholder(url):
    """
    Whether a sync address names nobody in particular.

    Anything that is not blank and not under a reserved example domain counts
    as somebody's real server - including a bare host, or a line that is not
    an address at all. A guard that tries to reason about which other strings
    might be harmless is a guard that eventually lets one through, and this
    one is protecting something that cannot be taken back.
    """
    text = (url or "").strip()
    if not text:
        return True
    host = (urlsplit(text).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain)
               for domain in PLACEHOLDER_HOSTS)


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

    def test_committed_config_has_no_private_sync_server(self):
        """
        The same leak as the e-mail block, through a door added later.

        The Settings screen writes a 'sync' block into this same tracked file,
        and base_url is the address of a server somebody hosts themselves - on
        their own domain, often enough at their own house. It is not a
        credential, which is presumably why it was not thought of, but it is
        the sort of thing that only has to be published once.
        """
        raw = _committed("config.json")
        if raw is None:
            self.skipTest("config.json is not retrievable from HEAD (no git checkout?)")

        sync = json.loads(raw).get("sync") or {}

        # The address itself is deliberately not repeated here. This message
        # ends up in a CI log, which for a public repository is one more place
        # it would be published from.
        self.assertTrue(
            _is_placeholder(sync.get("base_url")),
            "config.json in HEAD names a sync server.\n"
            "The repository is public, so committing this publishes that "
            "address permanently.\n"
            "Blank the field in the Settings screen, or put the documented "
            "placeholder %s back, then amend the commit - and remember that "
            "removing it in a later commit does NOT remove it from the "
            "history." % DOCUMENTED_SYNC_URL,
        )

    def test_committed_config_sync_stays_disabled(self):
        """
        A shipped default must not have synchronisation switched on, for the
        same reason email import must not: a fresh install would start
        reaching out on settings that are not its own.
        """
        raw = _committed("config.json")
        if raw is None:
            self.skipTest("config.json is not retrievable from HEAD (no git checkout?)")

        sync = json.loads(raw).get("sync") or {}
        self.assertFalse(
            sync.get("enabled", False),
            "config.json in HEAD has synchronisation enabled.",
        )


class TestWhatCountsAsAPlaceholder(unittest.TestCase):
    """
    The rule the guard above leans on, pinned separately - it decides whether
    a leak is reported at all, and it is the kind of judgement that quietly
    loosens over time.
    """

    def test_nothing_at_all_is_fine(self):
        for blank in (None, "", "   "):
            with self.subTest(value=blank):
                self.assertTrue(_is_placeholder(blank))

    def test_the_documented_placeholder_is_fine(self):
        self.assertTrue(_is_placeholder(DOCUMENTED_SYNC_URL))

    def test_so_is_any_other_reserved_example_address(self):
        for url in ("https://example.com", "http://example.org/timecontrol/",
                    "https://sync.example.net/tc/", "HTTPS://EXAMPLE.COM/tc/"):
            with self.subTest(url=url):
                self.assertTrue(_is_placeholder(url))

    def test_a_real_address_is_not(self):
        for url in ("https://sync.somebody.de/tc/", "https://192.0.2.7/tc/",
                    "http://nas.local:8443/tc/"):
            with self.subTest(url=url):
                self.assertFalse(_is_placeholder(url))

    def test_nor_is_something_that_only_looks_like_one(self):
        """
        A host that merely ends in the same letters is a different machine,
        and anything without a scheme has no host to judge - so both are
        treated as real rather than argued about.
        """
        for url in ("https://notexample.com/tc/", "https://example.com.evil.test/tc/",
                    "sync.somebody.de/tc/", "somebody's server"):
            with self.subTest(url=url):
                self.assertFalse(_is_placeholder(url))


class TestTheGuardsActuallyFire(unittest.TestCase):
    """
    On a clean checkout the guards above pass - and so would a guard wired to
    the wrong key, or one whose condition can never be true. So each is handed
    a configuration it is supposed to refuse, and asked to refuse it.

    Nothing here touches the repository: the reader of the committed file is
    replaced for the duration.
    """

    def verdict(self, method, config):
        """
        Runs one guard against a made-up config.json.

        :return: True if the guard accepted it.
        """
        import sys
        module = sys.modules[__name__]
        original = module._committed
        module._committed = lambda path: json.dumps(config)
        try:
            getattr(TestRepoHygiene(method), method)()
            return True
        except AssertionError:
            return False
        finally:
            module._committed = original

    def test_a_private_sync_address_is_refused(self):
        self.assertFalse(self.verdict(
            'test_committed_config_has_no_private_sync_server',
            {"sync": {"base_url": "https://sync.somebody.de/tc/"}}))

    def test_a_placeholder_or_a_blank_is_accepted(self):
        for block in ({}, {"sync": {}}, {"sync": {"base_url": ""}},
                      {"sync": {"base_url": DOCUMENTED_SYNC_URL}}):
            with self.subTest(block=block):
                self.assertTrue(self.verdict(
                    'test_committed_config_has_no_private_sync_server', block))

    def test_synchronisation_left_switched_on_is_refused(self):
        self.assertFalse(self.verdict(
            'test_committed_config_sync_stays_disabled',
            {"sync": {"enabled": True}}))
        self.assertTrue(self.verdict(
            'test_committed_config_sync_stays_disabled',
            {"sync": {"enabled": False}}))

    def test_the_email_guards_this_was_modelled_on_still_fire(self):
        """
        They had never been shown to, and a change to the shared reader could
        have quietly disarmed both.
        """
        self.assertFalse(self.verdict(
            'test_committed_config_has_no_email_credentials',
            {"email": {"user": "somebody@example.invalid", "password": "hunter2"}}))
        self.assertFalse(self.verdict(
            'test_committed_config_email_stays_disabled',
            {"email": {"enabled": True}}))


if __name__ == '__main__':
    unittest.main()
