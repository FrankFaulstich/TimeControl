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

Authentication, the operation log, and compaction.

| Action | Method | Purpose |
|---|---|---|
| `?a=login` | POST | username + password + device id &rarr; token |
| `?a=ping` | GET | proves a token is still valid |
| `?a=logout` | GET | revokes the token that was presented |
| `?a=head` | GET | current sequence number &ndash; the cheap poll |
| `?a=push` | POST | submit operations, and receive everything newer |
| `?a=pull` | GET | catch up from a given sequence number |
| `?a=snapshot` | GET | the document the log has been compacted into |
| `?a=snapshot&seq=N` | POST | offer a document as the snapshot at `N` |

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

### Compaction

A log that only grows has two problems that arrive slowly. A new or restored
machine replays the entire history &ndash; bounded at 500 operations per
request &ndash; so after a year of daily use it needs several cycles before it
shows anything useful. And the store grows without limit on web space usually
measured in a few hundred megabytes, even though nearly all of it is
superseded: a task edited fifty times keeps all fifty `task.set` operations.

So the server may hold a **snapshot** beside the log: the document as it stood
at one sequence number. A machine below that point fetches the snapshot and
then only the tail.

**The snapshot is uploaded by a client, never computed here.** This server
stores operations without understanding them, and folding them into a document
would put the merge rules in two places, in two languages, where they would
drift apart. `tt/sync_apply.py` already produces exactly this document, and
its `seed_operations()` already does the reverse &ndash; which is how a client
takes a snapshot back apart into the operations that would build it, rather
than installing it over the top of what it has.

It is accepted only from a device claiming a cursor equal to `head`, so the
uploader demonstrably held the whole log. That is an assertion rather than a
proof, and it is the weakest link here; two things reduce what a wrong one
costs. A document with no projects at all is refused outright, because that is
the shape a `data.json` that was emptied or replaced takes. And the segments a
snapshot replaces are not deleted with it &ndash; they are set aside and swept
only after a week, so a mistake noticed in that time is still recoverable. The
snapshot it replaced is kept until the one after that arrives.

`?a=pull` and `?a=push` report `snapshot_seq`, and set `needs_snapshot` for a
caller below it. Such a caller is sent **no operations at all**, rather than
the part that survives: that part begins in the middle, so every object
created before the point would be missing and almost everything after it would
be dropped as naming something unknown &ndash; silently. The refusal is what
tells the client to take the snapshot first.

The document travels as the entire request body, with the sequence number in
the query string, so it can be stored exactly as it arrived instead of being
decoded and re-encoded on a host where memory is the scarce thing. The upload
limit is 4 MiB, against 1 MiB for every other request.

**What this costs.** A machine that has been out of contact for longer than
the client's tombstone retention (ninety days) can resurrect an object deleted
while it was away: the deletion has aged out of the snapshot, and the log no
longer reaches back to it. Replaying the whole log would have caught that, so
this is a real regression for that one case. The alternative &ndash; treating
the snapshot as authoritative and deleting whatever it does not mention &ndash;
trades a visible, correctable annoyance for the silent loss of work that was
never sent. A reappearing task is the better failure.

Three test scripts cover the server, none of which needs anything installed.
`php php-server/test-oplog.php` calls the log functions directly against a
throwaway store, which is how the awkward cases &ndash; a request killed
between two writes, a segment holding operations on both sides of the snapshot
point &ndash; get set up exactly. `php php-server/test-endpoints.php` starts
PHP's own built-in server against a copy of `tc/` in a temporary directory and
talks to it over HTTP, which is the only way to reach the routing, the size
limits, and the snapshot response. `php php-server/test-setup.php` covers what the
installer checks before it will act: how the operator passphrase is read out
of `setup.enable`, and its proof that the store is unreadable &ndash; which
address it probes for which layout, and whether a fetch can tell a served
directory from an unserved one. `check-oplog.py` remains the one that exercises a real
installation on real web space.

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
more). Any text editor will do &ndash; surrounding whitespace and a leading
UTF-8 byte order mark are both ignored, so Notepad's habit of adding three
invisible bytes to the front of a file no longer turns the right passphrase
into "Wrong passphrase." (setup tells you when it found one; the file keeps
it until you save it differently.) A file saved as **UTF-16** is refused
outright, with a message saying so: every character in it would carry a NUL
byte and nothing you could type would ever match.

Write access to the directory is what proves you are the operator. It is the
one capability guaranteed on every shared host &ndash; it is how the code got
there &ndash; and it needs no shell, no cron and no admin account.

**4. Install.** Open `https://<host>/tc/setup.php`, enter the passphrase and
press *Install*. Without `setup.enable`, or over plain HTTP, the page is a
bare 404 and does nothing.

Setup picks a location for the store, preferring one above the document root
and falling back to a randomly named directory inside the web space. Either
way it **proves** the store cannot be fetched over the web: it writes a marker
file and tries to retrieve it, at every address that could plausibly reach it.
A location whose marker comes back, or that could not be checked, is discarded
and the next one tried; if none can be shown to be safe, nothing is installed
and it says which addresses it asked.

Before any of that it fetches a file from `tc/` itself, and stops if that
fails. Without it, "the marker did not come back" would have two readings -
the store is protected, or the wrong address was asked - and a rewrite rule,
an alias or a proxy in front of the site all produce the second silently.

The preferred location used to be taken on trust, on the grounds that two
directories above `tc/` had to be outside the web space. That holds when `tc/`
sits directly under the document root and fails the moment it does not: with
`tc/` at `/apps/tc`, two levels up **is** the document root, and the store
went there unprotected while setup reported success. Nothing is assumed now.

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
