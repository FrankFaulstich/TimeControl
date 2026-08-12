# Open issues after the synchronisation work

Ready to paste into GitHub, one section per issue. Written in English to match
the repository. Delete this file once they are filed.

Not repeated here: **“Sync server: compact the operation log”**, which was
drafted separately — file that one from the earlier text so there are not two
slightly different versions of it.

---

## 1. Sync: changing the server address after signing in has no effect

**Labels:** `bug`, `sync`

`Settings → Sync Server Settings` writes `sync.base_url` into `config.json`,
but nothing reads it again once you are signed in. Every request goes to the
address stored inside the credential (`tt/sync_client.py`, `_authenticated()`
uses `load_credentials()['base_url']`), which was frozen at sign-in time.

So moving the server, or fixing a typo you only noticed later, silently does
nothing: the app keeps talking to the old address, the settings screen shows
the new one, and the two never meet.

The address actually in use is not displayed anywhere either, so there is no
way to tell from the interface which one is live.

**Suggested fix:** show the address from the credential next to the field
(“currently in use: …”), and either re-resolve `base_url` on each request or
tell the user plainly that a changed address takes effect after signing in
again. Signing out and back in already works — it just is not discoverable.

---

## 2. Sync: unknown server errors surface as “Sign-in failed” in the background

**Labels:** `bug`, `sync`

`_sync_error_message()` (`sl/SL_Menu.py`) maps thirteen codes and falls back to
`"Sign-in failed ({code})."`. That wording was right when the function was only
used by the sign-in form. It is now also used by the settings status line,
which passes whatever `sync_engine.snapshot()` last recorded — so any code the
table does not know is reported to a user who has not touched the sign-in form:

    busy          -> "Sign-in failed (busy)."
    too_many_ops  -> "Sign-in failed (too_many_ops)."

The header notice is not affected: it filters to a whitelist of five codes that
the table does cover.

**Suggested fix:** give the function a context, or split it: a sign-in variant
and a general one whose fallback reads like “Synchronisation failed ({code}).”
Add the codes the server can actually return that are missing from the table.

---

## 3. Sync: the settings screen’s “unreachable” branch can never run

**Labels:** `bug`, `sync`, `good first issue`

`view_settings()` still has a branch rendering “The server could not be reached
({reason})”. It is dead: the surrounding code no longer calls
`sync_client.status()` on every redraw — that was removed because it put a
blocking network round trip behind every keystroke — and reads the cached
credential instead, which only ever yields `ok` or `not_configured`.

The state does still occur; it now arrives through `sync_engine.snapshot()` and
is rendered a few lines higher. So this is dead code that looks like coverage.

**Suggested fix:** remove the branch, or make the **Check connection** button
(which does call `status()`) the thing that feeds it.

---

## 4. Sync: the repo-hygiene guard does not cover the sync block

**Labels:** `bug`, `sync`, `security`

`tests/test_repo_hygiene.py` exists to stop a real e-mail address being
committed in the tracked `config.json`. The settings screen now also writes a
`sync` block into that same tracked file, containing the address of the user’s
private server — and the guard says nothing about it.

`config.json` is tracked in a public repository, so this is the same class of
leak the guard was written to prevent.

**Suggested fix:** extend the guard to fail when `sync.base_url` is set to
anything other than empty or the documented placeholder.

---

## 5. Sync server: the installer skips its own reachability proof

**Labels:** `bug`, `sync`, `php-server`

`setup.php` proves the store is not web-readable by fetching a canary over
HTTP before it will install. That proof is skipped when `tc/` is not directly
under the document root, because the installer cannot work out the canary’s
public URL — and it then installs anyway, dropping the store somewhere inside
the web space.

The whole storage design rests on the store not being readable from outside.
Skipping the proof in exactly the layout where it is least obvious whether the
store is exposed is the wrong way round.

**Suggested fix:** when the URL cannot be derived, ask for it rather than
proceeding, and refuse to install until the canary comes back 403/404.

---

## 6. Sync server: a BOM in setup.enable means “Wrong passphrase” for ever

**Labels:** `bug`, `sync`, `php-server`

The installation passphrase is read from `setup.enable` and compared verbatim.
A file saved by Notepad or a Windows editor carries a UTF-8 byte order mark,
which becomes part of the comparison — so every attempt is refused, with a
message saying the passphrase is wrong when it is not.

There is no way to tell the two cases apart from the page.

**Suggested fix:** strip a leading BOM and surrounding whitespace when reading
the file. Worth checking the same for the username and password fields.

---

## 7. Sync server: open registration

**Labels:** `enhancement`, `sync`, `php-server`

Deliberately not implemented. Accounts are created by hand through `setup.php`.

Adding a self-service registration button needs the protections that go with
it: rate limiting that cannot be used to lock out existing users, some defence
against automated sign-ups, a decision about whether new accounts need
approval, and a way to remove abandoned ones. None of that exists yet.

Not needed while the server has one user.

---

## 8. Sync: only the GUI sends; the MCP, REST and SOAP servers only queue

**Labels:** `enhancement`, `sync`

`sync_engine.ensure_started()` is called from `sl/SL_Menu.py` and nowhere else,
so the background worker exists only in the GUI process. Changes made through
the MCP, REST or SOAP interfaces are recorded into the outgoing queue correctly
— but they leave the machine only once the GUI is running.

For someone who drives TimeControl mainly through Claude Desktop and rarely
opens the GUI, their work reaches the second machine hours late or not at all.

**Suggested fix:** start the worker from the three server entry points too. The
cross-process cycle lock already exists, so several workers are safe; the open
question is whether a short-lived stdio MCP process should sync at all, or
whether it should push once at exit.

---

## 9. Sync: clock skew decides which running session is auto-closed

**Labels:** `bug`, `sync`

When work begins on the second machine, the session left running on the first
is closed at the moment the new one began. Which of the two is “earlier” is
decided by `start_time` — a wall clock, on two machines that are allowed to
disagree.

With a laptop resumed from sleep and no NTP, the machines can be minutes apart,
and `_settle()` then closes the session that is actually still running while
leaving the finished one open.

The server’s sequence numbers already carry the true order and would decide
this correctly, but `sync_apply` deliberately does not look at them here.

**Suggested fix:** where both sessions arrived through the log, prefer the
sequence order over the timestamps; fall back to the clock only for two
sessions that were both created locally.

---

## 10. Sync: verify the client against the real server

**Labels:** `task`, `sync`, `verification`

Everything client-side has so far been verified against a stand-in that
reimplements `php-server/tc` in Python. The stand-in was written from the same
reading of the contract as the client, so a misunderstanding would be present
in both and invisible.

Still to do, against a **throwaway account**:

- `python3 php-server/check-sync-cycle.py` — two machines, the real engine
- `python3 php-server/check-sync-apply.py` — the merge rules
- a two-machine run through the GUI, which the scripts do not cover

Both scripts now ask for the address or read `TC_SYNC_URL`. Delete the old
`synctest2` account first and create a fresh one; the log cannot be cleaned up
selectively.

---

## 11. Sync: the frozen build has never been checked

**Labels:** `task`, `sync`, `windows`

`TimeControl.spec` gained the sync modules under `hiddenimports`, because
`sl/SL_Menu.py` is shipped as data and PyInstaller never scans data files for
imports. That fix has not been checked against an actual build.

If the modules are missing, `SYNC_AVAILABLE` becomes `False` and the feature
silently disappears — the failure mode gives no clue at all. Worth building
once and confirming the settings section is there.

*(The other half of this — whether `tt/filelock.py`’s `msvcrt` branch excludes
two threads of one process, which is what the sync worker and the drawing
thread do — is now answered: `test_two_threads_of_one_process_exclude_each_other`
runs on the Windows CI job and passes.)*

---

## 12. generate_task_report reports the wrong first and last activity

**Labels:** `bug`

Pre-existing, unrelated to synchronisation, but easy to hit now that entries
can arrive out of order.

`generate_task_report()` takes “First entry” from the first element of
`time_entries` and “Last activity” from the last, rather than from the minimum
and maximum. The list is only chronological by accident — `entry.add` appends,
nothing sorts, and `_settle()` moves an open entry to the end.

Reproducible without sync: give a task one entry 12:30–13:00, apply an incoming
entry 09:00–10:00, and the report says the task started at 12:30 and last saw
activity at 10:00. The per-day breakdown is out of order too.

**Suggested fix:** compute both from `min()`/`max()` over the entries, and sort
each day’s lines.

---

## 13. data.json has five potential writers and no lock

**Labels:** `bug`, `reliability`

Pre-existing, but synchronisation makes it sharper.

`TimeTracker._save_data()` writes the whole document through a temp file and
`os.replace()`. That is atomic for *readers* — nobody ever sees a half-written
file — but it does nothing about lost updates. The GUI, the MCP server, the
REST server, the SOAP server and a second browser tab can all hold the document
in memory and write it back; the last one wins and the others’ changes are gone
with no error anywhere.

With sync enabled a lost write is partly recoverable, because the operations
were queued before the save. Partly is not the same as reliably.

`tt/filelock.py` already exists and is used for the outgoing queue.

**Suggested fix:** take the lock around reload → modify → save, at least in the
GUI and the three servers. Note this changes `_save_data()` for every caller and
introduces a failure mode (`LockTimeout`) that none of them currently handle.

---

## 14. data.json and config.json are tracked in a public repository

**Labels:** `security`, `privacy`

Both files are committed and public. No credentials have ever been in them —
the sync token deliberately lives outside the project directory — but
`data.json` carries real project and task names, and its history keeps them
even after they are removed.

`.gitignore` lists `data.json`, which has no effect: the file is already
tracked, so the entry is inert.

**Suggested fix:** `git rm --cached` both, ship `data.example.json` and
`config.example.json` instead, and decide separately whether the history needs
rewriting — that is disruptive and only worth it if the contents are sensitive.

---

## 15. docs/ points autodoc at modules that no longer exist

**Labels:** `bug`, `documentation`

`docs/modules.rst` has `automodule:: TimeTracker` and
`automodule:: TimeTrackerMCP`. Neither resolves: the first moved to
`tt.TimeTracker`, and the second is `TimeTrackerMCP_Server`. Only `update`
still matches.

None of the sync modules are covered either — `tt/sync_client.py`,
`tt/sync_engine.py`, `tt/sync_apply.py`, `tt/sync_outbox.py` and
`tt/filelock.py` all carry full docstrings that appear nowhere in the built
documentation.

**Suggested fix:** correct the two paths and add the five new modules. Worth a
CI check that the build emits no autodoc warnings, or this recurs.

---

## 16. Sync: two functions with no production caller

**Labels:** `chore`, `sync`, `good first issue`

- `sync_client.head()` implements the cheap poll and is called only by its own
  test. Both `README.md` and `php-server/README.md` describe `?a=head` as what
  the client asks first; it does not. Either use it — a cycle could skip the
  push entirely when the head has not moved and there is nothing queued — or
  stop describing it that way.
- `Outbox.clear()` has no caller outside the tests. Its docstring refers to
  re-seeding a machine, which is not something the app does; the re-offer path
  added later works differently.

Neither is harmful. Both are the kind of thing that reads as coverage and is
not.
