#!/usr/bin/env python3
"""
Two machines running the real sync engine against the running server.

check-sync-apply.py proved the merge rules against the real log using a
hand-written client. This proves the shipped engine: the queue, the cycle,
the cursor, the inbox and the applying, exactly as the app runs them - only
with the two machines' configuration directories side by side in /tmp
instead of on two computers.

Run this against a THROWAWAY account, not your real one. It writes into that
account's log, which cannot be selectively cleaned up. Create one in
setup.php, run this, delete it again in setup.php.
"""

import getpass
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import requests                                          # noqa: F401
except ImportError:
    sys.exit("requests is missing - pip install -r requirements.txt")

from tt import sync_client, sync_engine
from tt.sync_outbox import Outbox
from tt.TimeTracker import TimeTracker

SERVER = None      # set in main(), see server_address()


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


class Machine:
    """
    One computer: its own configuration directory, its own data file, and
    therefore its own device identity, queue, cursor and inbox.
    """

    def __init__(self, name, root):
        self.name = name
        self.config = os.path.join(root, name, 'config')
        self.data = os.path.join(root, name, 'data.json')
        os.makedirs(self.config, exist_ok=True)
        with self:
            self.tracker = TimeTracker(file_path=self.data, op_outbox=Outbox())

    # Swapping the directory is what makes two machines out of one process.
    # Everything in the engine reaches for it through this one function.
    def __enter__(self):
        self._saved = sync_client.config_dir
        sync_client.config_dir = lambda: self.config
        return self

    def __exit__(self, *exc):
        sync_client.config_dir = self._saved
        return False

    def sign_in(self, user, password):
        with self:
            return sync_client.login(SERVER, user, password)

    def sync(self):
        """One full round: the worker's half, then the interface's half."""
        with self:
            outcome = sync_engine.run_cycle(self.tracker.op_outbox)
            summary = sync_engine.apply_pending(self.tracker)
            return outcome, summary

    def offer(self):
        with self:
            return sync_engine.offer_document(self.tracker)

    def state(self):
        with self:
            return sync_engine.read_state()

    def reopen(self):
        """As if the application had been restarted on this machine."""
        with self:
            self.tracker = TimeTracker(file_path=self.data, op_outbox=Outbox())


def task_of(machine, name):
    for project in machine.tracker.data['projects']:
        for task in project.get('tasks', []):
            if task['task_name'] == name:
                return task
    return None


def entry_of(machine, uid):
    for project in machine.tracker.data['projects']:
        for task in project.get('tasks', []):
            for entry in task.get('time_entries', []):
                if entry['uid'] == uid:
                    return entry, task
    return None, None


def running_entries(machine):
    return [e for p in machine.tracker.data['projects']
            for t in p.get('tasks', [])
            for e in t.get('time_entries', []) if 'end_time' not in e]


def main():
    global SERVER
    SERVER = server_address('')
    user = input("Throwaway account name: ").strip()
    if not user:
        sys.exit("No account given.")
    password = getpass.getpass("Password: ")

    root = tempfile.mkdtemp(prefix='tc-sync-check-')
    print("\nTwo machines under %s\n" % root)
    try:
        return run(user, password, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run(user, password, root):
    a = Machine('machine-a', root)
    b = Machine('machine-b', root)

    for machine in (a, b):
        result = machine.sign_in(user, password)
        if not result.get('ok'):
            sys.exit("Sign-in failed for %s: %s" % (machine.name, result))
    check("both machines signed in with identities of their own",
          a.state() is not None and b.state() is not None)

    # -- 1. the first machine offers what it already had --------------------
    print("\n1. A document that existed before synchronisation was switched on")

    a.tracker.add_main_project("Website")
    a.tracker.add_task("Website", "Relaunch", priority=4)
    a.tracker.start_work("Website", "Relaunch")
    a.tracker.stop_work()

    offered = a.offer()
    check("the existing document was offered", offered >= 4, "queued %s" % offered)
    a.sync()
    b.sync()

    check("the second machine has the project", task_of(b, "Relaunch") is not None)
    check("with the priority that was set", task_of(b, "Relaunch") and
          task_of(b, "Relaunch")['priority'] == 4)
    check("and the hour that was worked",
          task_of(b, "Relaunch") and len(task_of(b, "Relaunch")['time_entries']) == 1)
    check("the file on disk holds it too, not just the object in memory",
          TimeTracker(file_path=b.data)._get_project("Website") is not None)

    # -- 2. the queue empties and keeps counting ----------------------------
    print("\n2. The queue empties, and the numbering carries on")

    check("nothing of A's is still waiting", a.tracker.op_outbox.count() == 0,
          "%s left" % a.tracker.op_outbox.count())

    a.tracker.update_task("Website", "Relaunch", priority=7)
    a.sync()
    b.sync()
    check("a change made after the queue emptied still arrives",
          task_of(b, "Relaunch") and task_of(b, "Relaunch")['priority'] == 7,
          "this is the failure that would end synchronisation silently")

    # -- 3. both machines edit at once --------------------------------------
    print("\n3. Both edit the same task before either syncs")

    # A due date nobody touched stays as it is, so only the field each machine
    # actually changes travels - A's priority here, B's due date below. That is
    # what makes the last two checks meaningful.
    a.tracker.update_task("Website", "Relaunch", priority=8)
    b.tracker.update_task("Website", "Relaunch", due_date="2026-09-01")
    b.tracker.update_task("Website", "Relaunch", priority=3)

    a.sync()
    b.sync()
    a.sync()

    left, right = task_of(a, "Relaunch"), task_of(b, "Relaunch")
    check("the two machines agree", left == right,
          "\n     a=%s\n     b=%s" % (left, right))
    check("the later change won the field both touched", left and left['priority'] == 3)
    check("the change only one of them made survived",
          left and left['due_date'] == "2026-09-01")

    # -- 4. work booked against a task deleted elsewhere --------------------
    print("\n4. One deletes a task while the other is still booking time to it")

    a.tracker.add_task("Website", "Doomed")
    a.sync()
    b.sync()
    doomed = task_of(b, "Doomed")
    check("both have the task", task_of(a, "Doomed") and doomed)

    a.tracker.delete_task("Website", "Doomed")
    b.tracker.start_work("Website", "Doomed")
    b.tracker.stop_work()
    discarded_uid = doomed['time_entries'][0]['uid']

    a.sync()
    _outcome, summary_b = b.sync()
    a.sync()

    found_b, _parent_b = entry_of(b, discarded_uid)
    found_a, _parent_a = entry_of(a, discarded_uid)
    # The hour goes with the task, on both machines. Keeping it on one and not
    # the other is the divergence this rule exists to prevent.
    check("the time went with the task where it was booked", found_b is None)
    check("and where the task was deleted", found_a is None)
    check("the machine that had to discard it said so",
          summary_b and summary_b['discarded_time'] == 1, str(summary_b))
    check("the task itself stayed deleted",
          task_of(a, "Doomed") is None and task_of(b, "Doomed") is None)

    # -- 5. a session left running on the other machine ---------------------
    print("\n5. Starting work here ends the session left running there")

    a.tracker.start_work("Website", "Relaunch")
    a.sync()
    b.sync()
    check("B sees A's session running", len(running_entries(b)) == 1)

    b.tracker.start_work("Website", "Relaunch")
    b.sync()
    a.sync()

    check("only one session is running on each",
          len(running_entries(a)) == 1 and len(running_entries(b)) == 1,
          "a=%d b=%d" % (len(running_entries(a)), len(running_entries(b))))
    check("and it is the same one on both",
          running_entries(a)[0]['uid'] == running_entries(b)[0]['uid'])

    b.tracker.stop_work()
    b.sync()
    a.sync()
    check("stopping it stops it on both",
          not running_entries(a) and not running_entries(b))

    # -- 6. the ending of a session travels ---------------------------------
    print("\n6. The end that one machine worked out for itself reaches the other")

    third = Machine('machine-c', root)
    result = third.sign_in(user, password)
    check("a third machine can join", result.get('ok'), str(result))
    third.sync()
    while third.state()['base_seq'] < a.state()['base_seq']:
        outcome, _ = third.sync()
        if not outcome.get('ok'):
            break

    check("it rebuilt the same projects",
          [p['main_project_name'] for p in sorted(third.tracker.data['projects'],
                                                  key=lambda p: p['uid'])]
          == [p['main_project_name'] for p in sorted(a.tracker.data['projects'],
                                                     key=lambda p: p['uid'])],
          str([p['main_project_name'] for p in third.tracker.data['projects']]))
    check("with no session left running", not running_entries(third))
    check("and the same tracked time",
          _total_entries(third) == _total_entries(a),
          "c=%d a=%d" % (_total_entries(third), _total_entries(a)))

    # -- 7. restarting changes nothing --------------------------------------
    print("\n7. Restarting the application picks up where it left off")

    a.tracker.add_task("Website", "After restart")
    before = a.state()['base_seq']
    a.reopen()
    check("the cursor survived the restart", a.state()['base_seq'] == before)
    a.sync()
    b.sync()
    check("work queued before the restart still arrives",
          task_of(b, "After restart") is not None)

    # -- 8. an idle cycle costs nothing -------------------------------------
    print("\n8. With nothing to do, a cycle files nothing")

    a.sync()
    with a:
        idle_inbox = sync_engine.read_inbox()
    outcome, summary = a.sync()
    check("the cycle succeeded", outcome.get('ok'), str(outcome))
    check("and had nothing to apply", summary is None, str(summary))
    check("the inbox stayed empty", idle_inbox == [])

    for machine in (a, b, third):
        with machine:
            sync_client.logout()

    print("\n%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


def _total_entries(machine):
    return sum(len(t.get('time_entries', []))
               for p in machine.tracker.data['projects']
               for t in p.get('tasks', []))


if __name__ == "__main__":
    sys.exit(main())
