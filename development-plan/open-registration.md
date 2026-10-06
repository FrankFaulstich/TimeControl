# Open registration on the sync server

Design for [#552](https://github.com/FrankFaulstich/TimeControl/issues/552). Nothing here is
implemented, and nothing should be until the questions below have answers someone is willing
to defend. Accounts are created by the operator today &ndash; in `setup.php`, or by redeeming
an invitation code the operator issued (#588) &ndash; and that is not an oversight.

## The question before the question

The server was built so **one person** could keep their own machines in step. That sentence
was the first line of `php-server/README.md`, and every decision in there was first measured
against it: one global rate-limit counter, no admin account, an operator window that opens for
minutes and closes again, a store sized for one document.

Invitations have already ended that premise. `setup.php` could always make accounts for other
people too, as long as the operator chose, and so knew, their passwords; since #588 somebody can
be invited without that, and an installation shared by several people is a use it is meant for.
The README's reasoning was rewritten for that case in #592 (*Who can do what*), including what
does not hold any more. Open registration goes a step further: it turns a server
shared by people the operator chose into one shared with anybody, on shared hosting, and most of
the work below is the cost of that change rather than the cost of a registration form.

**So the recommendation is invitation rather than open registration.** The operator generates
a single-use code in `setup.php`; a client redeems it once and gets an account. This meets the
realistic need &ndash; a partner, a second household, a colleague &ndash; and it dissolves
three of the four requirements the issue lists: the bot defence is the code, the approval is
the act of issuing it, and abandoned accounts stop being an attacker-controlled quantity.

The rest of this document covers both, because "open" may genuinely be wanted one day, and the
requirements read differently once the constraints are written down.

## What the ground looks like

Five properties of this server shape everything that follows. None of them is negotiable
without rebuilding it for a different kind of host.

**There is no cron and no shell.** Nothing runs unless a request runs it. Token expiry already
works this way: `tc_token_check()` unlinks a token when it happens to read an expired one.
Any cleanup added here has to follow that pattern &ndash; lazy, bounded, on a path that was
going to touch the data anyway.

**There is no standing admin surface.** `setup.php` is a bare 404 unless `setup.enable` exists,
and the file is deleted after every change. The window is opened deliberately, but only a change
or the operator closes it: *Show status*, a wrong passphrase and a failed action leave the file
where it is, and nothing counts passphrase attempts, so an operator who only looked keeps it open
until they delete it. This matters more than it looks for the approval question below.

**Password hashing is deliberately expensive.** `TC_BCRYPT_COST = 12`. That is right for
protecting stored passwords and it is also a lever: anyone who can make the server hash can
make it work hard. The existing budget of 30 hashes a minute exists for exactly this reason.

**That budget is global, on purpose.** Per-IP counters let an attacker fill the filesystem with
small files, since they choose the key; per-account counters let them lock out a named account.
One counter can do neither. The cost accepted in exchange is that exhausting it denies
*everyone* &ndash; which for one user, for one minute, was a fair trade. Neither half holds any
more: with several accounts, everyone is several people, and the budget refills each minute only
to be spent again, so it stays shut for as long as somebody keeps spending it.

**Usernames are not filenames.** A user's directory is named after the 32-hex `uid`, and the
account record after the SHA-256 of the username (issue #587). Attacker-chosen usernames
therefore introduce no path handling, which is one worry that can be set aside.

## 1. Rate limiting that cannot lock out existing users

This is the sharpest of the four requirements, and for login it is only half met. Issue #585
added a reserve: when `tc_hash_budget_take()` is spent, a device that has signed in to that same
account before may still draw on a small reserve (`TC_HASH_RESERVE_PER_MINUTE`), decided before
any password is hashed. That lets a holder recover an expired or signed-out machine through a
flood made by a stranger. It does not survive a flood made by an account holder: the reserve is
one counter too, and recognition looks at name and device id before the password, so anybody
with an account &ndash; or with one of its machines &ndash; can spend it by failing under their
own name, and every other account waits. A reserve per account is still needed. See
`php-server/README.md`, *Password checking*.

Two things are left: that reserve per account, and the registration half below. With
registration reachable by anyone, the thing to prevent is registration eating into the login
budget at all &ndash; otherwise each account's reserve would be the only thing standing between
its holder and a cheap, permanent denial of signing in, and in time of synchronising, and it is
sized for recovery, not for that. The reserve as it is built would not even be that: once anybody
can register, an account of one's own is all it takes to spend it.

**Registration must not draw on the login budget.** Two counters, and they must not be
fungible:

- Login keeps `TC_HASH_BUDGET_PER_MINUTE` as it is.
- Registration gets its own, much smaller allowance over a much longer window &ndash; on the
  order of five an hour and twenty a day. A genuine person registers once.
- When the two would compete for the host, registration loses. It is the discretionary one.

**No bcrypt before the gate.** This is the ordering rule that makes the budgets hold. Verify
the invite code, or the proof of work, *first*; hash the password only once that has passed.
Otherwise the attacker's cost is one HTTP request and the server's is 100&nbsp;ms of CPU, and no
counter tuned for humans survives that ratio for long.

Both counters stay single global files, for the reason the existing comment gives: a counter
keyed on anything the caller supplies is a way to make the server create files on demand.

## 2. Defence against automated sign-ups

Four candidates, and the environment rules most of them out.

**CAPTCHA** would send the operator's users to a third party and needs a browser. The client
here is the TimeControl application, not a browser, and a self-hosted server whose selling
point is that the data stays yours should not require a call to Google to create an account.
Rejected.

**E-mail verification** adds mail sending on shared hosting, where deliverability is poor and
the reputation is shared with strangers. It also adds an address to store &ndash; personal data
this server currently does not hold &ndash; and it hands out a way to make the server send mail
to an address of the sender's choosing. Rejected: it is a subsystem, not a check.

**Proof of work** fits this client unusually well, precisely because the client is a program.
The server issues a challenge; the client finds a nonce whose SHA-256 has *n* leading zero
bits; the server verifies in microseconds. No third party, no new personal data, and the cost
is asymmetric in the right direction. It has to be done properly to be worth anything: the
challenge is issued by the server, is bound to a timestamp, expires in minutes, and is
single-use, or it is a token that can be minted once and replayed for ever. Difficulty should
be a constant that can be raised without a client change.

**Invite codes** are simpler than all of it and stronger than any of it. A code is 16 random
hex characters, single-use, with an expiry, stored in the store and consumed under the same
lock that writes the account. There is nothing to guess and nothing to farm.

**Recommendation:** invite codes. If registration is ever opened without them, proof of work
in front of the password hash, with invites still accepted as the way to skip the queue.

## 3. Does a new account need approval?

Only if the answer to section 2 was "open". And an approval queue collides with the ground
described above: draining one requires the operator to be reachable, and this server has no
standing admin surface by design. A queue that only moves when somebody re-uploads
`setup.enable` is a queue that leaves people waiting days for an account, then floods the
operator with a page full of names they cannot tell apart.

The mechanism does not exist either, whatever it looked like. The code checks a `disabled`
flag, but signing in reads it from the account record under `accounts/` and `tc_token_check()`
from `users/<uid>/user.dat.php`, and nothing sets it in either; switching an account off would
have to be built. And the objection would remain without that: the operator is not there.

**Recommendation:** no queue. With invites, issuing the code *is* the approval, and it happens
at a moment the operator has already chosen to be present.

## 4. Removing abandoned accounts

Two different cases, and conflating them is how a cleanup routine deletes somebody's year of
time tracking.

**Never used.** Registered, and nothing ever stored: an empty log. (Not "no `seen/` entry" &ndash;
since #588 registering signs the device in, which writes one.) This is bot residue, it holds
nothing, and it can be removed automatically. Done in issue #589, with "after a few days" made
precise as "once no device of it could still come back without its password" &ndash; 31 days,
one more than a token lasts unused &ndash; since a shorter wait would end sign-ins that still work.

**Used and then abandoned.** There is an operation log, possibly a snapshot, possibly years of
work. Removing this automatically is not cleanup, it is data loss on a timer. It should be
*reported* &ndash; `setup.php`'s *Show status* already lists accounts and could show last-seen
dates and sizes &ndash; and removed by the operator, who is the only one who knows whether the
person is coming back.

The signals needed already exist: `created` in the user record, and the mtime of
`seen/<device_uid>`, which is what idle-token expiry reads.

Where it runs, given no cron: on the registration path itself, bounded to a handful of accounts
per call. That is the path that creates the mess, it is already writing under the users lock,
and it is the one path whose frequency scales with the problem.

## 5. What the issue does not list, and should

**Storage becomes attacker-controlled.** Shared hosting sells a few hundred megabytes. One
account that pushes until the quota is gone takes every account's synchronisation down with it,
and the compaction that keeps a log bounded is per-account and client-driven &ndash; a hostile
client simply does not run it. A per-account cap on store size, checked before an append, is a
prerequisite rather than a refinement, and since #586 it exists. It is not enough on its own: it
bounds each account's log, not their sum, nor what signing in leaves behind, and with
registration open the number of accounts is the attacker's to choose. A limit on the total, or on
the number of accounts, and a check of the room that is left have to come with it.

**`users.dat.php` was one JSON file**, read, decoded, modified and rewritten whole under a lock
for every account change. That is right for a handful of accounts and wrong for a few thousand,
and registration is what makes the count somebody else's decision. Since issue #587 every
account has a file of its own under `accounts/`, found by a computed path; an old list is
converted on the first request after the update. Measured before the change, the old list cost
about 4 ms per sign-up and 3 ms and 5 MiB of memory per sign-in at 5,000 accounts, growing in
step with the count; after it, a sign-up takes about 0.5 ms and a sign-in 0.02 ms at any count.

**The store is shared.** Every account lives under one store directory whose protection was
proved once at install. That does not weaken per-account isolation &ndash; the paths are
uid-based &ndash; but the blast radius of a misconfiguration grows with the number of people on
the installation.

**The threat model in the README needed rewriting**, not amending, and was rewritten in #592
for an installation of invited people. Every "acceptable" in it has to be revisited once more
against "anyone on the internet can create an account"; its *What nothing here defends against
yet* is where to start.

## Order of work, if it is ever taken up

1. Per-account store cap, and a bounded `users.dat.php`. Neither is about registration; both
   have to exist before it, and both are useful on their own. Both done: issues #586 and #587.
2. Invite codes: generation in `setup.php`, redemption via `?a=register`, single-use under the
   existing users lock. Done: issue #588. Single-use rests on renaming the code's file rather
   than on the lock alone, since `store.php` does not trust `flock()`; the code is checked
   before the password is hashed, and registering does not draw on the sign-in budget.
3. Sweep of never-used accounts, on the registration path, bounded per call. Done: issue #589.
   Only accounts made from an invitation are candidates, never ones the operator made.
4. Last-seen and size reporting in *Show status*, so abandoned accounts are visible and removed
   by a person. Done: issues #586 (size) and #590 (last seen).
5. Only then, and only if genuinely wanted: proof of work in front of the hash, and open
   registration behind it. The proof of work is done: issue #591. `?a=challenge` issues an
   HMAC-signed challenge that writes nothing; `?a=register` checks a solution before the code
   and the password, and records it so it counts once. With a code it is checked when sent but
   not demanded, and the client always sends one, so it is in use before anything depends on
   it. Open registration itself (#552) is not done.

Steps 1&ndash;4 give a server that other people can be added to &ndash; as many as the operator
invites, which already makes it a shared one, with the gaps listed under *What nothing here
defends against yet* in `php-server/README.md` (#592). Those come before step 5. Step 5 is the
one that changes what the thing is, and it should be a separate decision made on purpose.
