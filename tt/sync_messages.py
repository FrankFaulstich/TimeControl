"""
What a synchronisation error code means, in words.

Kept out of the interface for the reason update.py's should_check_for_updates
is: this is a decision, not a drawing, and putting it here lets it be tested
without a UI framework running. It imports nothing but the translator, so it
is safe to load even where the sync client itself is unavailable.

TRANSLATIONS
------------
The strings here are extracted into locale/timetracker.pot along with those in
sl/SL_Menu.py, tt/TimeTracker.py and update.py. See the Translations section
of the README for the command - a module left out of that list keeps working
and quietly stops being translated.
"""

from i18n import _

# The server would not take what this machine offered it. These codes differ
# only in which of its checks tripped, and say one thing to the person
# reading: this is a version mismatch or a bug rather than anything they did,
# and nothing will move until it is sorted out. The code travels with the
# message because it is the only part worth reporting onwards.
REJECTED_BY_SERVER = frozenset((
    'bad_json', 'ops_not_a_list', 'op_not_an_object', 'unknown_op',
    'bad_lc', 'bad_uid', 'bad_fields', 'method_not_allowed', 'unknown_action',
))


def _messages():
    """
    The table, built per call so a language change is picked up.

    Every code here can genuinely reach a screen. Ones that cannot are left
    out on purpose: an entry for something unreachable is a claim about the
    system that nobody can check.
    """
    return {
        # --- the sign-in form ---
        'no_server': _("No server address is set. Enter one above and save it first."),
        'https_required': _("The address must start with https:// - a token sent over "
                            "plain HTTP could be read by anyone on the way."),
        'missing_credentials': _("Please enter both a username and a password."),
        'invalid_credentials': _("Wrong username or password."),
        'too_many_attempts': _("Too many sign-in attempts on the server. Try again in a minute."),

        # --- reaching the server, from anywhere ---
        'tls_failed': _("The server's certificate could not be verified."),
        'timeout': _("The server did not answer in time."),
        'unreachable': _("The server could not be reached. Check the address and your connection."),
        'bad_response': _("The address answered, but not like a TimeControl sync server. "
                          "Check that it points at the right directory."),
        'not_installed': _("The server is reachable but has not been set up yet."),
        'busy': _("The server was dealing with something else. This one retries on its own."),

        # --- the background sync, which the user was not looking at ---
        'not_signed_in': _("This device is not signed in to the server."),
        'invalid_token': _("This device is no longer signed in. Please sign in again."),
        'address_changed': _("The saved server address is not the one this device is "
                             "signed in to. Sign in again to start using it."),
        'local_io': _("The synchronisation files on this computer could not be written."),
        'snapshot_unavailable': _("The stored copy of the data could not be fetched from "
                                  "the server, so catching up had to stop."),
        # Both mean the same thing and neither clears itself: the batch is too
        # heavy for this server, and the queue will not drain until that
        # changes.
        'body_too_large': _("This machine tried to send more at once than the server "
                            "accepts. It will keep trying, but cannot get past this "
                            "on its own."),
        'too_many_ops': _("This machine tried to send more at once than the server "
                          "accepts. It will keep trying, but cannot get past this "
                          "on its own."),
    }


def _message(code, fallback):
    """
    One table, two headlines.

    The explanations are the same wherever a failure turns up. What to call an
    unrecognised one is not, which is what the two functions below supply.

    :param fallback: Wording for a code the table does not know, carrying a
                     {code} placeholder - for an unrecognised failure the code
                     is the only part worth passing on.
    """
    if code in REJECTED_BY_SERVER:
        return _("The server would not accept what this machine sent ({code}). The two "
                 "are probably running different versions.").format(code=code)
    return _messages().get(code) or fallback.format(code=code or '?')


def sync_error_message(code):
    """
    What to show wherever synchronisation reports a failure by itself.

    The common case, and the reason this is not one function: the settings
    status line, the header notice and the connection check all pass on
    whatever the last cycle happened to record. Calling that "Sign-in failed"
    told a user who had not touched the sign-in form for months that signing
    in was the problem - "Sign-in failed (busy)." for a server that was merely
    occupied at that moment.
    """
    return _message(code, _("Unexpected error ({code})."))


def sign_in_error_message(code):
    """
    What to show under the sign-in form, where a failure really is about
    signing in - and where naming that is more use than a bare code.
    """
    return _message(code, _("Sign-in failed ({code})."))
