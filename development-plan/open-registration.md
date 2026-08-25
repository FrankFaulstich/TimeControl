# Open registration on the sync server

Design for [#552](https://github.com/FrankFaulstich/TimeControl/issues/552). Nothing here is
implemented, and nothing should be until the questions below have answers someone is willing
to defend. Accounts are created by hand in `setup.php` today, and that is not an oversight.

## The question before the question

The server exists so **one person** can keep their own machines in step. That sentence is the
first line of `php-server/README.md` and it is what every decision in there was measured
against: one global rate-limit counter, no admin account, an operator window that opens for
minutes and closes again, a store sized for one document.

Open registration does not add a feature to that server. It turns it into a multi-tenant
service running on shared hosting, and most of the work below is the cost of that change
rather than the cost of a registration form.

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
and the file is deleted after every change. The window is opened deliberately and lasts
minutes. This matters more than it looks for the approval question below.

**Password hashing is deliberately expensive.** `TC_BCRYPT_COST = 12`. That is right for
protecting stored passwords and it is also a lever: anyone who can make the server hash can
make it work hard. The existing budget of 30 hashes a minute exists for exactly this reason.

**That budget is global, on purpose.** Per-IP counters let an attacker fill the filesystem with
small files, since they choose the key; per-account counters let them lock out a named account.
One counter can do neither. The cost accepted in exchange is that exhausting it denies
*everyone* &ndash; which for one user, for one minute, was a fair trade.

**Usernames are not filenames.** A user's directory is named after the 32-hex `uid`, and the
username is only a key inside `users.dat.php`. Attacker-chosen usernames therefore introduce no
path handling, which is one worry that can be set aside.

## 1. Rate limiting that cannot lock out existing users

This is the sharpest of the four requirements, and the current code does not meet it &ndash;
for login, today, with one user. Exhausting `tc_hash_budget_take()` makes `?a=login` answer
`too_many_attempts` to everybody, the owner included. With registration reachable by anyone,
exhausting it stops being a minute's nuisance and becomes a cheap, permanent denial of the
owner's own synchronisation.

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

The account record already carries `disabled`, and `tc_token_check()` already honours it, so
the mechanism exists. The objection is not mechanical, it is that the operator is not there.

**Recommendation:** no queue. With invites, issuing the code *is* the approval, and it happens
at a moment the operator has already chosen to be present.

## 4. Removing abandoned accounts

Two different cases, and conflating them is how a cleanup routine deletes somebody's year of
time tracking.

**Never used.** Registered, and no device ever synchronised: no `seen/` entry, an empty log.
This is bot residue, it holds nothing, and it can be removed automatically after a few days.

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
account that pushes until the quota is gone takes the owner's synchronisation down with it, and
the compaction that keeps a log bounded is per-account and client-driven &ndash; a hostile
client simply does not run it. A per-account cap on store size, checked before an append, is a
prerequisite rather than a refinement.

**`users.dat.php` is one JSON file**, read, decoded, modified and rewritten whole under a lock
for every account change. That is right for a handful of accounts and wrong for a few thousand,
and registration is what makes the count somebody else's decision.

**The store is shared.** Every account lives under one store directory whose protection was
proved once at install. That does not weaken per-account isolation &ndash; the paths are
uid-based &ndash; but the blast radius of a misconfiguration grows with the number of people on
the installation.

**The threat model in the README needs rewriting**, not amending. It currently reasons from
"one person, their own machines, their own server". Almost every "acceptable for one user"
in it has to be revisited against "anyone on the internet can create an account".

## Order of work, if it is ever taken up

1. Per-account store cap, and a bounded `users.dat.php`. Neither is about registration; both
   have to exist before it, and both are useful on their own.
2. Invite codes: generation in `setup.php`, redemption via `?a=register`, single-use under the
   existing users lock.
3. Sweep of never-used accounts, on the registration path, bounded per call.
4. Last-seen and size reporting in *Show status*, so abandoned accounts are visible and removed
   by a person.
5. Only then, and only if genuinely wanted: proof of work in front of the hash, and open
   registration behind it.

Steps 1&ndash;4 give a server that a second person can be added to. Step 5 is the one that
changes what the thing is, and it should be a separate decision made on purpose.
