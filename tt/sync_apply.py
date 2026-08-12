"""
Applying operations that came from another machine.

A deliberately pure module: it takes a document and a list of operations and
changes the document. No network, no files, no clock it did not receive. That
is what makes the awkward cases - a deletion racing an edit, time booked
against a task somebody else removed - testable exhaustively rather than by
hoping.

THE RULES, IN ONE PLACE
-----------------------
*Order.* Operations are applied strictly in the server's sequence order. That
number, not any timestamp, decides who wins: the two machines' clocks are
allowed to disagree by minutes, but they cannot disagree about the order the
server put things in.

*Same object, different fields.* Both survive. Operations carry only the
fields that actually changed, so a priority set here and a due date set there
compose instead of overwriting each other.

*Same object, same field.* The later sequence number wins.

*Deletion beats editing, and takes the tracked time with it.* A deleted
object stays deleted, and any later operation naming it is dropped -
including a time entry booked against it. Without the first part, a deletion
here plus any edit there would resurrect the object on every reconcile, for
ever. The second part is what makes the two machines agree: deleting a task
in this application has always discarded its hours, so a machine that
receives the deletion must discard them too. Re-homing them somewhere safe
was tried and rejected - it left the machine that did the deleting with
nothing and the machine that received it with the hours, permanently, and
divergence that reports nothing is worse than the loss it was avoiding.

So: work booked on the other machine and not yet sent, against a task deleted
here, is gone. That is a deliberate choice, not an oversight.
"""

# Fields an incoming operation is allowed to set. Anything else is ignored:
# the server stores operations without understanding them, so this is where a
# malformed or hostile field would otherwise reach the document.
TASK_FIELDS = frozenset((
    "task_name", "status", "due_date", "today", "note",
    "recurring", "frequency", "userdefined_days", "priority", "last_started",
))
PROJECT_FIELDS = frozenset(("name", "status", "last_started"))
ENTRY_FIELDS = frozenset(("start_time", "end_time"))


class Report:
    """What happened, for the caller to log or show."""

    def __init__(self):
        self.applied = 0
        self.ignored = 0
        # Time entries discarded because the task they belong to had been
        # deleted. Counted separately from the rest of `ignored` because this
        # is the one kind of dropped operation that costs the user something.
        self.discarded_time = 0
        # Sessions this machine had left running that were closed because
        # work had since begun elsewhere: (entry_uid, end_time) pairs. The
        # caller is expected to report these back, or only this machine will
        # know they ended.
        self.auto_closed = []
        self.highest_seq = 0

    def __repr__(self):
        return ("<applied=%d ignored=%d discarded_time=%d auto_closed=%d "
                "highest_seq=%d>"
                % (self.applied, self.ignored, self.discarded_time,
                   len(self.auto_closed), self.highest_seq))


class _Index:
    """uid -> object lookups, kept current as operations are applied."""

    def __init__(self, document):
        self.document = document
        self.projects = {}
        self.tasks = {}
        self.task_parent = {}
        self.entries = {}
        self.entry_parent = {}
        for project in document.get("projects", []):
            if project.get("uid"):
                self.projects[project["uid"]] = project
            for task in project.get("tasks", []):
                if task.get("uid"):
                    self.tasks[task["uid"]] = task
                    self.task_parent[task["uid"]] = project
                for entry in task.get("time_entries", []):
                    if entry.get("uid"):
                        self.entries[entry["uid"]] = entry
                        self.entry_parent[entry["uid"]] = task

    def add_project(self, project):
        self.document.setdefault("projects", []).append(project)
        self.projects[project["uid"]] = project

    def add_task(self, task, project):
        project.setdefault("tasks", []).append(task)
        self.tasks[task["uid"]] = task
        self.task_parent[task["uid"]] = project

    def add_entry(self, entry, task):
        task.setdefault("time_entries", []).append(entry)
        self.entries[entry["uid"]] = entry
        self.entry_parent[entry["uid"]] = task

    def move_task(self, uid, project):
        task = self.tasks[uid]
        old = self.task_parent[uid]
        if old is project:
            return
        old["tasks"] = [t for t in old.get("tasks", []) if t.get("uid") != uid]
        project.setdefault("tasks", []).append(task)
        self.task_parent[uid] = project

    def move_entry(self, uid, task):
        entry = self.entries[uid]
        old = self.entry_parent[uid]
        if old is task:
            return
        old["time_entries"] = [e for e in old.get("time_entries", []) if e.get("uid") != uid]
        task.setdefault("time_entries", []).append(entry)
        self.entry_parent[uid] = task

    def drop_project(self, uid):
        project = self.projects.pop(uid, None)
        if project is None:
            return []
        self.document["projects"] = [p for p in self.document.get("projects", [])
                                     if p.get("uid") != uid]
        gone = []
        for task in project.get("tasks", []):
            gone.extend(self.drop_task_bookkeeping(task))
        return gone

    def drop_task_bookkeeping(self, task):
        uid = task.get("uid")
        self.tasks.pop(uid, None)
        self.task_parent.pop(uid, None)
        for entry in task.get("time_entries", []):
            self.entries.pop(entry.get("uid"), None)
            self.entry_parent.pop(entry.get("uid"), None)
        return [uid] if uid else []

    def drop_task(self, uid):
        task = self.tasks.get(uid)
        if task is None:
            return []
        parent = self.task_parent.get(uid)
        if parent is not None:
            parent["tasks"] = [t for t in parent.get("tasks", []) if t.get("uid") != uid]
        return self.drop_task_bookkeeping(task)

    def drop_entry(self, uid):
        entry = self.entries.pop(uid, None)
        if entry is None:
            return
        parent = self.entry_parent.pop(uid, None)
        if parent is not None:
            parent["time_entries"] = [e for e in parent.get("time_entries", [])
                                      if e.get("uid") != uid]


def _next_local_id(document):
    """
    Hands out the next integer id for a task arriving from elsewhere.

    These ids never travel. They are a local convenience - short handles for
    the interface and the MCP/REST/SOAP calls - and the same task is allowed
    to carry different ones on different machines.
    """
    nid = int(document.get("next_id", 1))
    document["next_id"] = nid + 1
    return nid


def _tombstones(document):
    return {t.get("uid") for t in document.get("_deleted", []) if t.get("uid")}


def _add_tombstone(document, uid, kind, when):
    for existing in document.setdefault("_deleted", []):
        if existing.get("uid") == uid:
            return
    document["_deleted"].append({"uid": uid, "kind": kind, "at": when})


def apply_ops(document, ops, on_conflict=None):
    """
    Applies operations from the server to a document, in place.

    :param document: A schema-2 document. Modified.
    :param ops: Operations as the server returned them, each with 's' (the
                sequence number that decides order) and 'op'.
    :param on_conflict: Optional callable, invoked as (kind, detail) whenever
                        something had to be decided rather than simply done:
                        'discarded_time' when a time entry went with a deleted
                        task, 'auto_closed' when a session left running here
                        was ended because work began elsewhere.
    :return: A Report.
    """
    report = Report()
    index = _Index(document)
    dead = _tombstones(document)

    for op in sorted(ops, key=lambda o: int(o.get("s", 0))):
        seq = int(op.get("s", 0))
        report.highest_seq = max(report.highest_seq, seq)
        kind = op.get("op")
        uid = op.get("uid")
        when = op.get("ts") or op.get("start") or op.get("end") or ""

        # An operation naming something already deleted is dropped. The one
        # exception is below: it is about time entries, and losing tracked
        # time silently is the one outcome worth complicating this for.
        if uid in dead and kind not in ("entry.add", "entry.move"):
            report.ignored += 1
            continue

        handled = True

        if kind == "project.create":
            if uid not in index.projects:
                fields = op.get("f") or {}
                index.add_project({
                    "uid": uid,
                    "main_project_name": fields.get("name", ""),
                    "tasks": [],
                    "status": fields.get("status", "open"),
                    "last_started": fields.get("last_started"),
                })

        elif kind == "project.set":
            project = index.projects.get(uid)
            if project is None:
                handled = False
            else:
                for key, value in (op.get("f") or {}).items():
                    if key not in PROJECT_FIELDS:
                        continue
                    project["main_project_name" if key == "name" else key] = value

        elif kind == "project.delete":
            for task_uid in index.drop_project(uid):
                _add_tombstone(document, task_uid, "task", when)
                dead.add(task_uid)
            _add_tombstone(document, uid, "project", when)
            dead.add(uid)

        elif kind == "task.create":
            if uid not in index.tasks:
                project = index.projects.get(op.get("project"))
                if project is None:
                    handled = False
                else:
                    fields = {k: v for k, v in (op.get("f") or {}).items() if k in TASK_FIELDS}
                    task = {
                        "uid": uid,
                        "id": _next_local_id(document),
                        "task_name": "",
                        "time_entries": [],
                        "status": "open",
                        "due_date": None,
                        "today": False,
                        "note": "",
                        "recurring": False,
                        "frequency": "daily",
                        "userdefined_days": 1,
                        "priority": 0,
                        "last_started": None,
                    }
                    task.update(fields)
                    index.add_task(task, project)

        elif kind == "task.set":
            task = index.tasks.get(uid)
            if task is None:
                handled = False
            else:
                for key, value in (op.get("f") or {}).items():
                    if key in TASK_FIELDS:
                        task[key] = value

        elif kind == "task.move":
            task = index.tasks.get(uid)
            project = index.projects.get(op.get("project"))
            if task is None or project is None:
                handled = False
            else:
                index.move_task(uid, project)

        elif kind == "task.delete":
            index.drop_task(uid)
            _add_tombstone(document, uid, "task", when)
            dead.add(uid)

        elif kind in ("entry.add", "entry.move"):
            target_uid = op.get("task")
            task = index.tasks.get(target_uid)
            if task is None or target_uid in dead:
                # The task this time belongs to is gone here - deleted on this
                # machine, or never created because its project was. The entry
                # goes with it, which is what deleting a task has always done
                # locally, and is the only answer that leaves both machines
                # holding the same document: the one that did the deleting
                # discarded these hours the moment the user asked it to, and
                # it has no way to get them back.
                index.drop_entry(uid)
                report.discarded_time += 1
                if on_conflict:
                    on_conflict('discarded_time',
                                {'entry': uid, 'task': target_uid})
                handled = False
            elif kind == "entry.add":
                if uid in index.entries:
                    index.move_entry(uid, task)
                else:
                    entry = {"uid": uid, "start_time": op.get("start")}
                    if op.get("end"):
                        entry["end_time"] = op["end"]
                    index.add_entry(entry, task)
            else:
                if uid in index.entries:
                    index.move_entry(uid, task)
                else:
                    handled = False

        elif kind == "entry.close":
            entry = index.entries.get(uid)
            if entry is None:
                handled = False
            else:
                end = op.get("end")
                start = entry.get("start_time")
                # Never before it began: durations are these two subtracted,
                # and a negative one does not announce itself, it just makes
                # the numbers wrong. Clocks on the two machines are allowed
                # to disagree, so this really can happen.
                if end and start and end < start:
                    end = start
                if end:
                    entry["end_time"] = end

        elif kind == "entry.set":
            entry = index.entries.get(uid)
            if entry is None:
                handled = False
            else:
                for key, value in (op.get("f") or {}).items():
                    if key in ENTRY_FIELDS:
                        entry[key] = value
                start, end = entry.get("start_time"), entry.get("end_time")
                if start and end and end < start:
                    entry["end_time"] = start

        elif kind == "entry.delete":
            index.drop_entry(uid)

        else:
            handled = False

        if handled:
            report.applied += 1
        else:
            report.ignored += 1

    _settle(document, report, on_conflict)
    return report


def reconcile(document, incoming, local=None, on_conflict=None):
    """
    One merge: what came from elsewhere, then this machine's own unsent work.

    WHY THE SECOND PASS EXISTS
    --------------------------
    Local changes are written into the document the moment they are made -
    the app cannot wait for a server to agree before showing the user their
    own edit. But the server decides the order, and it puts everything this
    machine sends AFTER everything already in the log. So a value set here
    and not yet acknowledged has to end up on top of an incoming change to
    the same field, even though it was applied to the document first.

    Without the second pass this machine keeps the incoming value while the
    other machine, replaying both in the server's order, keeps ours - and the
    two never notice they disagree. Two identical files that quietly stopped
    being identical is the worst outcome this design has to avoid, worse than
    an error, because nothing reports it.

    Replaying is safe because every operation here is written to be repeatable
    - a create for something that exists does nothing, a set writes the same
    value again - so an operation the document already reflects costs nothing.

    :param document: The document to bring up to date. Modified.
    :param incoming: Operations from the server, each carrying its sequence.
    :param local: This machine's queued operations, carrying the 'lc' they
                  were queued under. They are ordered by that alone: the
                  server appends a batch in the order it was sent, so 'lc'
                  order is already the order the server will give them.
    :return: A Report covering both passes.
    """
    report = apply_ops(document, incoming, on_conflict=on_conflict)
    if not local:
        return report

    floor = report.highest_seq
    replay = []
    for position, op in enumerate(sorted(local, key=lambda o: int(o.get('lc', 0))), 1):
        op = dict(op)
        op['s'] = floor + position
        replay.append(op)

    second = apply_ops(document, replay, on_conflict=on_conflict)
    report.applied += second.applied
    report.ignored += second.ignored
    report.discarded_time += second.discarded_time
    report.auto_closed.extend(second.auto_closed)
    report.highest_seq = max(report.highest_seq, second.highest_seq)
    return report


def seed_operations(document):
    """
    Describes an existing document as the operations that would build it.

    Used once, by whichever machine reaches an empty server first. Everything
    after that is incremental; this is the only time the whole document is
    sent, and it is sent as operations rather than as a file so the server
    never has to understand the format.

    The order matters and is the same order the app itself would have
    produced: a project before its tasks, a task before its time.
    """
    ops = []
    for project in document.get("projects", []):
        if not project.get("uid"):
            continue
        ops.append({
            'op': 'project.create',
            'uid': project["uid"],
            'f': {'name': project.get("main_project_name", ""),
                  'status': project.get("status", "open"),
                  'last_started': project.get("last_started")},
        })
        for task in project.get("tasks", []):
            if not task.get("uid"):
                continue
            ops.append({
                'op': 'task.create',
                'uid': task["uid"],
                'project': project["uid"],
                'f': {k: task.get(k) for k in sorted(TASK_FIELDS) if k in task},
            })
            for entry in task.get("time_entries", []):
                if not entry.get("uid") or not entry.get("start_time"):
                    continue
                ops.append({'op': 'entry.add', 'uid': entry["uid"],
                            'task': task["uid"], 'start': entry["start_time"]})
                if entry.get("end_time"):
                    ops.append({'op': 'entry.close', 'uid': entry["uid"],
                                'end': entry["end_time"]})

    # Deletions travel too, or a machine that seeds from a document still
    # carrying tombstones would hand the others no way to know those objects
    # are meant to stay gone.
    for stone in document.get("_deleted", []):
        kind = stone.get("kind")
        if kind in ("project", "task", "entry") and stone.get("uid"):
            ops.append({'op': kind + '.delete', 'uid': stone["uid"],
                        'ts': stone.get("at")})
    return ops


def _settle(document, report, on_conflict=None):
    """
    Puts the document back into a shape the rest of the app relies on.

    Two invariants that applying operations can break, and that nothing else
    would notice until much later:

    The id counter must stay above every id in use, or the next task created
    here reuses one - and nothing anywhere checks for that.

    At most one time entry may be open. A running session is recognised as
    "the entry with no end_time"; two of them and the app stops the wrong
    one, leaving the other running for ever. Whichever started earlier is
    closed at the later one's start, so no stretch of time is counted twice.
    This is also what the user asked for in so many words: starting a task on
    the second machine should end the one still running on the first.

    And an open entry must be the LAST one in its task. Three places in the
    application - stopping work, showing what is running, and the per-task
    report - all recognise a running session as the final element of the
    list rather than by searching for one. An entry finished on the other
    machine can arrive after a session started here and is simply appended,
    which pushes the open one out of last place: the session then cannot be
    stopped, does not appear as running, and its hours are never counted.
    """
    highest = 0
    open_entries = []
    for project in document.get("projects", []):
        for task in project.get("tasks", []):
            try:
                highest = max(highest, int(task.get("id")))
            except (TypeError, ValueError):
                pass
            for entry in task.get("time_entries", []):
                if "end_time" not in entry and entry.get("start_time"):
                    open_entries.append(entry)

    if int(document.get("next_id", 1)) <= highest:
        document["next_id"] = highest + 1

    if len(open_entries) > 1:
        open_entries.sort(key=lambda e: e.get("start_time") or "")
        for earlier, later in zip(open_entries, open_entries[1:]):
            end = later.get("start_time")
            earlier["end_time"] = end
            report.auto_closed.append((earlier.get("uid"), end))
            if on_conflict:
                on_conflict('auto_closed', {'entry': earlier.get("uid"), 'end': end})

    for project in document.get("projects", []):
        for task in project.get("tasks", []):
            entries = task.get("time_entries")
            if not entries or "end_time" not in entries[-1]:
                continue
            for position, entry in enumerate(entries):
                if "end_time" not in entry:
                    entries.append(entries.pop(position))
                    break
