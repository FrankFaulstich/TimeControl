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
import json
import secrets
import sys

try:
    import requests
except ImportError:
    sys.exit("requests is missing - pip install -r requirements.txt")

BASE = "https://www.familiefaulstich.de/tc/index.php"

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
