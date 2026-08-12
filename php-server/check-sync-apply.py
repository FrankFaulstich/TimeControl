#!/usr/bin/env python3
"""
Two machines against the running server, using the real applier.

The unit tests prove the merge rules in isolation. This proves the same code
against the real op log: that what one machine sends is what the other can
rebuild, that concurrent edits land the same way on both, and that a task
deleted on one machine takes its recorded hours with it on the other too.

Run this against a THROWAWAY account, not your real one - it writes into that
account's log, which cannot be selectively cleaned up afterwards. Create one
in setup.php, run this, delete it again in setup.php.

requests rather than urllib on purpose: a python.org install on macOS ships
with an empty certificate store until "Install Certificates" has been run,
and urllib then cannot verify TLS at all.
"""

import copy
import getpass
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import requests
except ImportError:
    sys.exit("requests is missing - pip install -r requirements.txt")

from tt.sync_apply import apply_ops, reconcile, seed_operations

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
        return {"ok": False, "error": "not_json", "raw": r.text[:200]}


class Machine:
    """A client: a token, a queue with its own numbering, and a document."""

    def __init__(self, label, token):
        self.label = label
        self.token = token
        self.lc = 0
        self.seq = 0
        self.outbox = []
        self.doc = {"projects": [], "next_id": 1, "_deleted": [], "schema_version": 2}

    def queue(self, op, **fields):
        self.lc += 1
        entry = {"op": op, "lc": self.lc}
        entry.update({k: v for k, v in fields.items() if v is not None})
        self.outbox.append(entry)
        return entry

    def sync(self):
        """One cycle: send what is queued, take in what is not, merge both."""
        sending = list(self.outbox)
        if sending:
            r = call("push", {"base_seq": self.seq, "ops": sending}, token=self.token)
        else:
            r = call("pull", token=self.token, params={"since": self.seq})
        if not r.get("ok"):
            raise SystemExit("%s: sync refused: %s" % (self.label, r))
        if r.get("more"):
            # These logs are a handful of operations long. If the server is
            # holding some back, the comparisons below are meaningless.
            raise SystemExit("%s: the log is longer than one batch - use a "
                             "fresh account" % self.label)

        report = reconcile(self.doc, r.get("ops", []), sending)
        self.seq = max(self.seq, int(r.get("head", 0)))
        self.outbox = []
        return r, report


def uid():
    return secrets.token_hex(8)


def find_task(doc, name):
    for p in doc["projects"]:
        for t in p.get("tasks", []):
            if t["task_name"] == name:
                return t
    return None


def find_entry(doc, entry_uid):
    for p in doc["projects"]:
        for t in p.get("tasks", []):
            for e in t.get("time_entries", []):
                if e["uid"] == entry_uid:
                    return e, t
    return None, None


def main():
    global BASE
    BASE = server_address('index.php')
    user = input("Throwaway account name: ").strip()
    if not user:
        sys.exit("No account given.")
    password = getpass.getpass("Password: ")

    def login(name):
        r = call("login", {"username": user, "password": password,
                           "device_uid": uid(), "device_name": name})
        if not r.get("ok"):
            sys.exit("Sign-in failed for %s: %s" % (name, r))
        return Machine(name, r["token"])

    a, b = login("machine-a"), login("machine-b")
    print("\nSigned in twice - two devices, one account.\n")

    tag = secrets.token_hex(3)
    p_uid, t_uid, t2_uid = uid(), uid(), uid()

    # -- 1. one machine fills an empty account -------------------------------
    print("1. The first machine seeds, the second rebuilds")

    a.doc = {
        "projects": [{
            "uid": p_uid, "main_project_name": "Website " + tag, "status": "open",
            "last_started": None,
            "tasks": [{
                "uid": t_uid, "id": 1, "task_name": "Relaunch", "status": "open",
                "due_date": None, "today": False, "note": "", "recurring": False,
                "frequency": "daily", "userdefined_days": 1, "priority": 4,
                "last_started": "2026-08-10 09:00:00",
                "time_entries": [{"uid": uid(), "start_time": "2026-08-10 09:00:00",
                                  "end_time": "2026-08-10 10:00:00"}],
            }],
        }],
        "next_id": 2, "_deleted": [], "schema_version": 2,
    }
    seeded = copy.deepcopy(a.doc)
    expected = len(seed_operations(a.doc))
    for op in seed_operations(a.doc):
        a.queue(op.pop("op"), **op)
    r, _ = a.sync()
    check("the seed was accepted",
          r.get("ok") and len(r.get("assigned", [])) == expected,
          "%s of %d" % (len(r.get("assigned", [])), expected))
    check("the document is unchanged by seeding it", a.doc["projects"] == seeded["projects"])

    r, _ = b.sync()
    check("the second machine rebuilt the same projects",
          b.doc["projects"] == a.doc["projects"],
          "\n     got %s" % json.dumps(b.doc["projects"])[:300])
    check("and the same id counter", b.doc["next_id"] == a.doc["next_id"],
          "%s vs %s" % (b.doc["next_id"], a.doc["next_id"]))

    # -- 2. concurrent edits to the same task --------------------------------
    print("\n2. Both machines edit the same task before either syncs")

    find_task(a.doc, "Relaunch")["priority"] = 8
    a.queue("task.set", uid=t_uid, f={"priority": 8})

    task_b = find_task(b.doc, "Relaunch")
    task_b["due_date"] = "2026-09-01"
    task_b["priority"] = 3
    b.queue("task.set", uid=t_uid, f={"due_date": "2026-09-01"})
    b.queue("task.set", uid=t_uid, f={"priority": 3})

    a.sync()          # a reaches the server first
    b.sync()          # b sees a's change and puts its own after it
    a.sync()          # a catches up

    check("both machines agree on the task", find_task(a.doc, "Relaunch") == find_task(b.doc, "Relaunch"),
          "\n     a=%s\n     b=%s" % (find_task(a.doc, "Relaunch"), find_task(b.doc, "Relaunch")))
    check("the later change won the field both touched",
          find_task(a.doc, "Relaunch")["priority"] == 3,
          str(find_task(a.doc, "Relaunch")["priority"]))
    check("the change only one of them made survived",
          find_task(a.doc, "Relaunch")["due_date"] == "2026-09-01")

    # -- 3. time booked against a task deleted elsewhere ---------------------
    print("\n3. One machine deletes a task while the other books time on it")

    a.queue("task.create", uid=t2_uid, project=p_uid, f={"task_name": "Doomed"})
    a.sync()
    b.sync()
    check("both machines have the new task",
          find_task(a.doc, "Doomed") and find_task(b.doc, "Doomed"))

    e_uid = uid()
    # a deletes it; b, not yet knowing, starts working on it.
    a.doc["_deleted"].append({"uid": t2_uid, "kind": "task", "at": "2026-08-10 12:00:00"})
    for p in a.doc["projects"]:
        p["tasks"] = [t for t in p.get("tasks", []) if t["uid"] != t2_uid]
    a.queue("task.delete", uid=t2_uid, ts="2026-08-10 12:00:00")

    b.queue("entry.add", uid=e_uid, task=t2_uid, start="2026-08-10 12:30:00")
    b.queue("entry.close", uid=e_uid, end="2026-08-10 13:30:00")
    entry_b = {"uid": e_uid, "start_time": "2026-08-10 12:30:00",
               "end_time": "2026-08-10 13:30:00"}
    find_task(b.doc, "Doomed")["time_entries"].append(entry_b)

    a.sync()
    _, report_b = b.sync()
    a.sync()

    found_b, _parent_b = find_entry(b.doc, e_uid)
    found_a, _parent_a = find_entry(a.doc, e_uid)
    # The hour goes with the task. That is what deleting a task has always
    # done locally, and it is the only answer both machines can reach.
    check("the hour went with the deleted task where it was booked", found_b is None)
    check("and on the machine that deleted it", found_a is None)
    check("the machine that had to discard it said so",
          report_b.discarded_time == 1, str(report_b))
    check("the deleted task itself stayed deleted",
          find_task(a.doc, "Doomed") is None and find_task(b.doc, "Doomed") is None)

    # -- 4. a session left running elsewhere ---------------------------------
    print("\n4. Starting work on one machine ends the session left running on the other")

    running, other = uid(), uid()
    a.queue("entry.add", uid=running, task=t_uid, start="2026-08-11 09:00:00")
    find_task(a.doc, "Relaunch")["time_entries"].append(
        {"uid": running, "start_time": "2026-08-11 09:00:00"})
    a.sync()
    b.sync()

    open_on_b, _ = find_entry(b.doc, running)
    check("the second machine sees it running", "end_time" not in (open_on_b or {"end_time": 1}))

    b.queue("entry.add", uid=other, task=t_uid, start="2026-08-11 10:00:00")
    find_task(b.doc, "Relaunch")["time_entries"].append(
        {"uid": other, "start_time": "2026-08-11 10:00:00"})
    _, report_b = b.sync()
    _, report_a = a.sync()

    closed_a, _ = find_entry(a.doc, running)
    closed_b, _ = find_entry(b.doc, running)
    check("the earlier session was ended on both",
          closed_a.get("end_time") == "2026-08-11 10:00:00"
          and closed_b.get("end_time") == "2026-08-11 10:00:00",
          "\n     a=%s b=%s" % (closed_a.get("end_time"), closed_b.get("end_time")))
    check("ended exactly where the new one began, so no time is counted twice",
          closed_a.get("end_time") == "2026-08-11 10:00:00")
    check("only one session is still running",
          sum(1 for p in a.doc["projects"] for t in p["tasks"]
              for e in t["time_entries"] if "end_time" not in e) == 1)

    # -- 5. the documents are the same ---------------------------------------
    print("\n5. After everything, the two machines hold the same document")

    a.sync()
    b.sync()
    check("projects match", a.doc["projects"] == b.doc["projects"],
          "\n     a=%s\n     b=%s" % (json.dumps(a.doc["projects"])[:400],
                                      json.dumps(b.doc["projects"])[:400]))
    check("deletions match", sorted(t["uid"] for t in a.doc["_deleted"])
          == sorted(t["uid"] for t in b.doc["_deleted"]))

    # -- 6. repeating a sync must be harmless --------------------------------
    print("\n6. A lost answer means the same push arrives twice")

    before = copy.deepcopy(a.doc)
    replayed = [{"op": "task.set", "lc": 1, "uid": t_uid, "f": {"priority": 3}}]
    r = call("push", {"base_seq": a.seq, "ops": replayed}, token=a.token)
    check("the server recognised the repeat", r.get("dups") == [1], str(r))
    reconcile(a.doc, r.get("ops", []), replayed)
    check("and nothing in the document changed", a.doc["projects"] == before["projects"])

    for m in (a, b):
        call("logout", {}, token=m.token)

    print("\n%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
