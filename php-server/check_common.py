"""
What the three check-*.py scripts all need before they can talk to a server.

Only the address, for now. It used to be copied into each of them, which is a
poor place for a rule about where a password may be sent: three copies drift,
and the one that drifts is the one nobody re-reads.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# One definition of what counts as loopback, and it belongs to the client:
# that is where the rule has to hold in the shipped application, and two
# copies of a rule about where a password may go is one too many.
from tt.sync_client import LOOPBACK_HOSTS, is_loopback


def server_address(suffix=""):
    """
    Where the server is, asked for rather than baked in.

    These files live in a public repository. A default here would publish the
    address of somebody's private server, and would also be wrong for anyone
    else who ran them.

    https is required, because these scripts send a password. The single
    exception is a loopback address: there the request never reaches a
    network, and it is the only way to run the real php-server/tc code -
    rather than a stand-in written from the same reading of the contract as
    the client - without a live account to write into. PHP's built-in server
    does no TLS, so without this exception that check could never be made
    locally at all.

    :param suffix: appended if the given address does not already end in it.
    :return: the address, without a trailing slash.
    """
    url = os.environ.get('TC_SYNC_URL', '').strip()
    if not url:
        url = input("Server address (https://host/tc/): ").strip()
    if not url:
        sys.exit("No server address given. Set TC_SYNC_URL or type one.")

    lowered = url.lower()
    if not lowered.startswith('https://'):
        if not (lowered.startswith('http://') and is_loopback(url)):
            sys.exit("The address must start with https:// - the server "
                     "refuses anything else. Plain http is allowed only for "
                     "%s, where the request stays on this machine."
                     % ', '.join(LOOPBACK_HOSTS))

    url = url.rstrip('/')
    if suffix and not url.endswith(suffix):
        url += '/' + suffix
    return url
