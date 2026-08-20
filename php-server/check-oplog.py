#!/usr/bin/env python3
"""
Exercises the operation log against a running server.

Run this against a THROWAWAY account, not your real one - it writes
operations into that account's log, and the log is not something you can
selectively clean up afterwards. Create one in setup.php, run this, delete it
again in setup.php.

Uses requests rather than urllib on purpose: a python.org install on macOS
ships with an empty certificate store until "Install Certificates" has been
run, and urllib then cannot verify TLS at all. requests brings its own
bundle, which is also why the sync client will use it.
"""

import getpass
import os
import json
import secrets
import sys

try:
    import requests
except ImportError:
    sys.exit("requests is missing - pip install -r requirements.txt")

BASE = None      # set in main(), see server_address()


def server_address(suffix=""):
    """
    Where the server is, asked for rather than baked in.

    This file lives in a public repository. A default here would publish the
    address of somebody's private server, and would also be wrong for anyone
    else who ran it.
    """
    url = os.environ.get('TC_SYNC_URL', '').strip()
    if not url:
        url = input("Server address (https://host/tc/): ").strip()
    if not url:
        sys.exit("No server address given. Set TC_SYNC_URL or type one.")
    if not url.lower().startswith('https://'):
        sys.exit("The address must start with https:// - the server refuses anything else.")
    url = url.rstrip('/')
    if suffix and not url.endswith(suffix):
        url += '/' + suffix
    return url


passed = 0
failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("  ok    %s" % label)
    else:
        failed += 1
        print("  FAIL  %s %s" % (label, detail))


def call(action, payload=None, token=None, params=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-TC-Token"] = token
    query = {"a": action}
    query.update(params or {})
    if payload is None:
        r = requests.get(BASE, params=query, headers=headers, timeout=30)
    else:
        r = requests.post(BASE, params=query, headers=headers,
                          data=json.dumps(payload), timeout=30)
    try:
        return r.json()
    except ValueError:
        return {"ok": False, "error": "not_json", "raw": r.text[:200], "status": r.status_code}


def main():
    global BASE
    BASE = server_address('index.php')
    user = input("Throwaway account name: ").strip()
    if not user:
        sys.exit("No account given.")
    password = getpass.getpass("Password: ")

    dev_a, dev_b = secrets.token_hex(8), secrets.token_hex(8)

    def login(device, name):
        r = call("login", {"username": user, "password": password,
                           "device_uid": device, "device_name": name})
        if not r.get("ok"):
            sys.exit("Sign-in failed: %s" % r.get("error", r))
        return r["token"]

    print("\nSigning in two devices")
    a, b = login(dev_a, "laptop"), login(dev_b, "desktop")
    start = call("head", token=a)["head"]
    print("  log starts at sequence %d" % start)

    uid_p, uid_t, uid_e = (secrets.token_hex(8) for _ in range(3))

    print("\nA submits three operations")
    r = call("push", {"base_seq": start, "ops": [
        {"op": "project.create", "uid": uid_p, "f": {"name": "Probe"},
         "ts": "2026-08-09T10:00:00", "lc": 1},
        {"op": "task.create", "uid": uid_t, "project": uid_p,
         "f": {"task_name": "Entwurf", "priority": 5}, "ts": "2026-08-09T10:01:00", "lc": 2},
        {"op": "entry.add", "uid": uid_e, "task": uid_t,
         "start": "2026-08-09T10:02:00", "lc": 3},
    ]}, token=a)
    check("accepted", r.get("ok"), r.get("error", ""))
    check("three sequence numbers handed out", len(r.get("assigned", [])) == 3, r.get("assigned"))
    check("own operations not echoed back", r.get("ops") == [], r.get("ops"))
    after_a = r["head"]

    print("\nB catches up")
    r = call("pull", token=b, params={"since": start})
    ops = r.get("ops", [])
    check("sees all three", len(ops) == 3, len(ops))
    check("in the right order",
          [o["op"] for o in ops] == ["project.create", "task.create", "entry.add"],
          [o.get("op") for o in ops])
    check("credited to A's device", all(o["dev"] == dev_a for o in ops))

    print("\nB changes the same task")
    r = call("push", {"base_seq": after_a, "ops": [
        {"op": "task.set", "uid": uid_t, "f": {"priority": 9}, "ts": "x", "lc": 1}]}, token=b)
    check("accepted", r.get("ok"), r.get("error", ""))
    after_b = r["head"]
    check("sequence advanced by one", after_b == after_a + 1, (after_a, after_b))

    print("\nA picks up B's change")
    r = call("push", {"base_seq": after_a, "ops": []}, token=a)
    ops = r.get("ops", [])
    check("exactly one foreign operation", len(ops) == 1, len(ops))
    check("it is the priority change",
          ops and ops[0]["op"] == "task.set" and ops[0]["f"] == {"priority": 9},
          ops[0] if ops else None)

    print("\nB repeats its push (a lost response)")
    r = call("push", {"base_seq": after_b, "ops": [
        {"op": "task.set", "uid": uid_t, "f": {"priority": 9}, "ts": "x", "lc": 1}]}, token=b)
    check("reported as a duplicate", r.get("dups") == [1], r.get("dups"))
    check("nothing appended", r.get("assigned") == [], r.get("assigned"))
    check("sequence did not move", r.get("head") == after_b, (after_b, r.get("head")))

    print("\nMalformed input is refused")
    for label, ops_in, expected in [
        ("unknown verb", [{"op": "task.explode", "uid": uid_t, "lc": 50}], "unknown_op"),
        ("uid with path characters", [{"op": "task.set", "uid": "../../etc", "lc": 51}], "bad_uid"),
        ("missing counter", [{"op": "task.set", "uid": uid_t}], "bad_lc"),
    ]:
        r = call("push", {"base_seq": after_b, "ops": ops_in}, token=a)
        check(label, r.get("error") == expected, r.get("error"))

    print("\nThe device is taken from the token, not the body")
    r = call("push", {"base_seq": after_b, "ops": [
        {"op": "task.set", "uid": uid_t, "f": {"priority": 1}, "dev": dev_a, "lc": 900}]}, token=b)
    seq = r["assigned"][0][1]
    found = [o for o in call("pull", token=a, params={"since": seq - 1})["ops"] if o["s"] == seq]
    check("body's claim ignored", found and found[0]["dev"] == dev_b,
          found[0]["dev"][:8] if found else None)

    # test-oplog.php covers the log and compaction offline, and covers them
    # far more thoroughly than this can. What it cannot reach is index.php:
    # the routing, the sequence number arriving in the query string, and the
    # snapshot response, which is assembled by hand rather than through
    # json_encode. Those only ever run over HTTP.
    print("\nCompaction")
    head = call("head", token=a)["head"]
    document = {
        "schema_version": 2, "next_id": 2, "_deleted": [],
        "projects": [{"uid": uid_p, "main_project_name": "Probe",
                      "status": "open", "last_started": None,
                      "tasks": [{"uid": uid_t, "id": 1, "task_name": "Entwurf",
                                 "status": "open", "priority": 9,
                                 "due_date": None, "today": False, "note": "",
                                 "recurring": False, "frequency": "daily",
                                 "userdefined_days": 1, "last_started": None,
                                 "time_entries": []}]}],
    }

    r = call("snapshot", document, token=a, params={"seq": head - 1})
    check("a device that is not at head is refused",
          r.get("error") == "not_at_head", r.get("error"))

    r = call("snapshot", {"projects": []}, token=a, params={"seq": head})
    check("a document with nothing in it is refused",
          r.get("error") == "snapshot_has_no_projects", r.get("error"))

    r = call("snapshot", document, token=a, params={"seq": head})
    check("accepted at head", r.get("ok"), r.get("error", ""))
    check("it covers the sequence number offered", r.get("snapshot_seq") == head,
          r.get("snapshot_seq"))

    r = call("snapshot", token=a)
    check("it comes back as the document that went in",
          r.get("ok") and r.get("document") == document,
          r.get("error") or "document differs")
    check("and names the point it covers", r.get("seq") == head, r.get("seq"))

    r = call("pull", token=b, params={"since": 0})
    check("a machine below the point is sent to the snapshot",
          r.get("needs_snapshot") is True, r.get("needs_snapshot"))
    check("and is given no operations to misread", r.get("ops") == [], len(r.get("ops", [])))
    check("it is told where the snapshot sits", r.get("snapshot_seq") == head,
          r.get("snapshot_seq"))

    r = call("push", {"base_seq": head, "ops": [
        {"op": "task.set", "uid": uid_t, "f": {"priority": 2}, "lc": 901}]}, token=b)
    check("work after the snapshot is still accepted", r.get("ok"), r.get("error", ""))

    r = call("pull", token=a, params={"since": head})
    check("and a machine at the point reads on as before",
          not r.get("needs_snapshot") and len(r.get("ops", [])) == 1,
          (r.get("needs_snapshot"), len(r.get("ops", []))))
    check("the tail starts just past the snapshot",
          r.get("ops") and r["ops"][0]["s"] == head + 1,
          r["ops"][0]["s"] if r.get("ops") else None)

    r = call("snapshot", document, token=a, params={"seq": head})
    check("the same snapshot is not accepted twice",
          r.get("error") == "not_newer", r.get("error"))

    print("\nSigning both devices out")
    call("logout", token=a)
    call("logout", token=b)
    check("token no longer works", call("head", token=a).get("error") == "invalid_token")

    print("\n%d passed, %d failed" % (passed, failed))
    if failed:
        print("\nThe account still holds these test operations. Delete the account\n"
              "in setup.php to remove them.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
