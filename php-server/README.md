# TimeControl sync server &ndash; PHP implementation

A small PHP service so one person can synchronise their `data.json` between
their own machines. It stores everything in files, needs no database, and is
built for a plain shared webspace with FTP access and no shell.

Those constraints are what shaped it, and they are the reason this lives
under `php-server/` rather than `server/`: a different implementation, freed
from "no database" and "no long-running process", would look substantially
different while speaking the same protocol to the same client.

This directory holds two things:

- **`tc/`** &ndash; the server itself. This is what gets uploaded.
- **`tcprobe/`** &ndash; a throwaway diagnostic that measures whether a given
  host is suitable. Worth re-running after a PHP version change or a hosting
  migration, because those are exactly when `.htaccess` handling and file
  permissions get rewritten underneath you.

## What is implemented so far

Authentication and the operation log. What is *not* here yet: compaction, so
the log grows without bound, and a machine that has been away for a long time
has to replay everything rather than fetching a snapshot.

| Action | Method | Purpose |
|---|---|---|
| `?a=login` | POST | username + password + device id &rarr; token |
| `?a=ping` | GET | proves a token is still valid |
| `?a=logout` | GET | revokes the token that was presented |
| `?a=head` | GET | current sequence number &ndash; the cheap poll |
| `?a=push` | POST | submit operations, and receive everything newer |
| `?a=pull` | GET | catch up from a given sequence number |

### How the log works

Clients exchange intentions, not documents: twelve operation types
(`project.create`, `task.set`, `entry.close`, &hellip;), each naming the
entity by the `uid` the client generated. `*.set` carries only the fields
that actually changed, so two machines editing different attributes of the
same task both keep their change.

**The server's sequence number is the only ordering.** No vector clocks, no
comparing wall clocks &ndash; the timestamps this app records are naive local
time and the two machines are allowed to disagree by minutes. "Last writer
wins" means "last to reach the server", which both replicas compute
identically. Timestamps travel along for display and are never consulted for
conflict resolution.

`push` and `pull` are one round trip: submitting work and learning what
happened elsewhere are the same conversation. A push does not echo the
caller's own operations back &ndash; it already holds their bodies and only
needs to be told which sequence numbers they were given.

**Repeating a push is safe.** Each operation carries a counter the client
never reuses; the server keeps the high-water mark per device and reports
anything at or below it as a duplicate instead of appending it again. A push
whose response was lost can simply be sent again.

The device an operation is credited to comes from the token, never from the
request body &ndash; otherwise one device could move another's duplicate
counter and make that device's retries disappear.

Requests carry the credential in an `X-TC-Token` header; `Authorization:
Bearer` is accepted as an alternative but not relied upon, because some hosts
strip it before PHP sees it.

Every failure returns a stable `error` code alongside the human message, so a
client can tell "your token is gone, sign in again" (`invalid_token`) apart
from a network problem it should simply retry.

## Installing

**1. Upload.** Copy the contents of `tc/` into a directory on the web space,
for example `/tc/`.

**2. Check that both `.htaccess` files actually arrived.** There are two, in
two different directories:

    /tc/.htaccess
    /tc/lib/.htaccess

Most FTP clients hide names beginning with a dot and will skip them without
saying so &ndash; turn on "show hidden files" before uploading, or upload them
under a temporary name and rename them on the server. The one in `/tc/` is
what keeps the next step's passphrase from being served to the world.

To confirm afterwards, open `https://<host>/tc/lib/store.php` in a browser.
It must say **Forbidden**. A blank page means the file is being executed
instead of blocked, and the `.htaccess` did not arrive.

**3. Open the setup window.** Create a file `setup.enable` next to
`setup.php`, containing a passphrase of your choosing (12 characters or
more). Any text editor will do.

Write access to the directory is what proves you are the operator. It is the
one capability guaranteed on every shared host &ndash; it is how the code got
there &ndash; and it needs no shell, no cron and no admin account.

**4. Install.** Open `https://<host>/tc/setup.php`, enter the passphrase and
press *Install*. Without `setup.enable`, or over plain HTTP, the page is a
bare 404 and does nothing.

Setup picks a location for the store, preferring one above the document root
and falling back to a randomly named directory inside the web space. It then
**proves** the store cannot be fetched over the web by writing a marker file
and trying to retrieve it. If the marker comes back, or if the check cannot
be completed, nothing is installed and it tells you why. Protection is
demonstrated, never assumed.

**5. Create an account.** `setup.enable` is deleted after every change, so
upload it again, then use *Create an account*.

**6. Check.** *Show status* lists the store path, the accounts and the number
of live tokens. It does not consume `setup.enable`.

## Things worth knowing

**TLS is mandatory.** A bearer token is worth exactly as much as the channel
carrying it, so both entry points refuse plain HTTP before looking at any
credential.

**Tokens expire** 90 days after being issued and 30 days after last use. The
absolute limit is the only thing that ever ends a compromise nobody noticed:
a copied credential shows up as the device that is legitimately there
already, so there is no new entry to spot.

**Signing in again from the same device replaces that device's token** rather
than adding one. A client whose response got lost can simply retry.

**Password checking is rate limited to 30 attempts per minute in total** &ndash;
one global budget, not one per account or per IP address. Per-account
counters let anyone lock you out by name, and per-IP counters let an attacker
fill the disk with small files. The trade-off is real and deliberate: while
the budget is exhausted, your own sign-in is refused too, for up to a minute.

**Stored files carry a PHP guard line.** If a directory's `.htaccess` ever
stops being honoured, the files are executed rather than served and yield
nothing. The marker file used during installation deliberately does *not*
carry it &ndash; a guarded file would report "protected" whether or not
`.htaccess` works, which would be the check confirming itself.

**`setup.php` checks that its own passphrase file is unreachable** before it
will do anything, by fetching `setup.enable` the way an outsider would. If
the passphrase comes back it stops and tells you to treat it as compromised.
This exists because step 2 is exactly the kind of step that gets skipped, and
a protection that is merely assumed is not one.

**Permissions are set explicitly, never left to the umask.** The probe found
new files arriving as `0640` with a group id shared with other customers, so
every write chmods to `0600` and every directory to `0700`.

**Locking is used to serialise, never for integrity.** `flock()` reported
success on the target host, but that does not prove it locks &ndash; silently
doing nothing is a known behaviour on NFS-backed storage and cannot be
disproved from a single request. Integrity comes from writing to a temporary
file and renaming it into place, which is atomic.

**An interrupted append is discarded, not adopted.** The log is written
before the pointer describing it, so a request killed by the execution limit
leaves a segment holding more bytes than the state file admits to, possibly
ending mid-line. Readers ignore anything past the recorded length, and the
next write truncates it away. Keeping those bytes would mean one sequence
number standing for different content on different machines, which nothing
could later repair &ndash; and the client never got its acknowledgement, so
it will send the same operations again and the duplicate counter makes that
land exactly once.

## Removing an installation

Delete the `tc/` directory and the store. The store path is recorded in
`tc/config.php`; *Show status* prints it.
