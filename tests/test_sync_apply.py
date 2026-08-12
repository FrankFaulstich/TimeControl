import copy
import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tt.sync_apply import apply_ops


def document(*projects):
    return {"projects": list(projects), "next_id": 1, "_deleted": [], "schema_version": 2}


def project(uid, name="P", tasks=None, status="open"):
    return {"uid": uid, "main_project_name": name, "tasks": tasks or [],
            "status": status, "last_started": None}


def task(uid, name="T", entries=None, tid=1, **extra):
    base = {"uid": uid, "id": tid, "task_name": name, "time_entries": entries or [],
            "status": "open", "due_date": None, "today": False, "note": "",
            "recurring": False, "frequency": "daily", "userdefined_days": 1,
            "priority": 0, "last_started": None}
    base.update(extra)
    return base


def entry(uid, start="2026-08-10 09:00:00", end=None):
    e = {"uid": uid, "start_time": start}
    if end:
        e["end_time"] = end
    return e


def find_task(doc, uid):
    for p in doc["projects"]:
        for t in p.get("tasks", []):
            if t["uid"] == uid:
                return t
    return None


def find_entry(doc, uid):
    for p in doc["projects"]:
        for t in p.get("tasks", []):
            for e in t.get("time_entries", []):
                if e["uid"] == uid:
                    return e, t
    return None, None


def uids(collection):
    return [x["uid"] for x in collection]


P1, P2 = "p" * 16, "q" * 16
T1, T2 = "t" * 16, "u" * 16
E1, E2 = "e" * 16, "f" * 16


class TestProjects(unittest.TestCase):

    def test_a_project_arrives(self):
        doc = document()
        apply_ops(doc, [{"s": 1, "op": "project.create", "uid": P1,
                         "f": {"name": "Website", "status": "open"}}])
        self.assertEqual(doc["projects"][0]["main_project_name"], "Website")
        self.assertEqual(doc["projects"][0]["uid"], P1)
        self.assertEqual(doc["projects"][0]["tasks"], [])

    def test_the_same_creation_twice_makes_one_project(self):
        """
        A push whose response was lost is sent again, and both machines may
        pull the same range twice. Applying an operation a second time has to
        be harmless or every dropped connection would duplicate data.
        """
        doc = document()
        op = {"s": 1, "op": "project.create", "uid": P1, "f": {"name": "Website"}}
        apply_ops(doc, [op])
        apply_ops(doc, [op])
        self.assertEqual(len(doc["projects"]), 1)

    def test_a_rename_changes_the_project_it_names(self):
        doc = document(project(P1, "Old"))
        apply_ops(doc, [{"s": 1, "op": "project.set", "uid": P1, "f": {"name": "New"}}])
        self.assertEqual(doc["projects"][0]["main_project_name"], "New")

    def test_deleting_a_project_takes_its_tasks_with_it(self):
        doc = document(project(P1, tasks=[task(T1), task(T2, tid=2)]))
        apply_ops(doc, [{"s": 1, "op": "project.delete", "uid": P1, "ts": "2026-08-10 12:00:00"}])
        self.assertEqual(doc["projects"], [])
        self.assertEqual({t["uid"] for t in doc["_deleted"]}, {P1, T1, T2})


class TestTasks(unittest.TestCase):

    def test_a_task_arrives_with_a_local_id_of_its_own(self):
        """
        The integer id is a per-machine handle and is never sent. A task
        arriving here has to be given one from this machine's counter.
        """
        doc = document(project(P1))
        doc["next_id"] = 42
        apply_ops(doc, [{"s": 1, "op": "task.create", "uid": T1, "project": P1,
                         "f": {"task_name": "Write", "priority": 5}}])
        arrived = find_task(doc, T1)
        self.assertEqual(arrived["task_name"], "Write")
        self.assertEqual(arrived["priority"], 5)
        self.assertEqual(arrived["id"], 42)
        self.assertEqual(doc["next_id"], 43)

    def test_an_arriving_task_gets_every_field_the_app_expects(self):
        """
        The operation carries only what was set. Everything the rest of the
        app reads has to be present, or a view crashes on a task that came
        from the other machine but not on one made here.
        """
        doc = document(project(P1))
        apply_ops(doc, [{"s": 1, "op": "task.create", "uid": T1, "project": P1,
                         "f": {"task_name": "Sparse"}}])
        arrived = find_task(doc, T1)
        for field in ("status", "due_date", "today", "note", "recurring",
                      "frequency", "userdefined_days", "priority", "last_started"):
            self.assertIn(field, arrived)

    def test_two_machines_editing_different_fields_both_win(self):
        """
        The point of sending changed fields rather than whole objects. A
        priority set here and a due date set there must compose; sending
        whole tasks, the later one would silently undo the earlier.
        """
        doc = document(project(P1, tasks=[task(T1)]))
        apply_ops(doc, [
            {"s": 1, "op": "task.set", "uid": T1, "f": {"priority": 7}},
            {"s": 2, "op": "task.set", "uid": T1, "f": {"due_date": "2026-09-01"}},
        ])
        arrived = find_task(doc, T1)
        self.assertEqual(arrived["priority"], 7)
        self.assertEqual(arrived["due_date"], "2026-09-01")

    def test_the_later_sequence_number_wins_the_same_field(self):
        doc = document(project(P1, tasks=[task(T1)]))
        apply_ops(doc, [
            {"s": 2, "op": "task.set", "uid": T1, "f": {"priority": 9}},
            {"s": 1, "op": "task.set", "uid": T1, "f": {"priority": 1}},
        ])
        self.assertEqual(find_task(doc, T1)["priority"], 9)

    def test_order_comes_from_the_sequence_number_not_the_list(self):
        """
        Pushed and pulled operations are merged into one list, and nothing
        guarantees the caller interleaved them correctly. The server's
        numbering is the only thing that decides.
        """
        doc = document()
        apply_ops(doc, [
            {"s": 3, "op": "task.set", "uid": T1, "f": {"task_name": "third"}},
            {"s": 1, "op": "project.create", "uid": P1, "f": {"name": "P"}},
            {"s": 2, "op": "task.create", "uid": T1, "project": P1, "f": {"task_name": "second"}},
        ])
        self.assertEqual(find_task(doc, T1)["task_name"], "third")

    def test_a_moved_task_keeps_its_time(self):
        doc = document(project(P1), project(P2, "Other",
                                            tasks=[task(T1, entries=[entry(E1)])]))
        apply_ops(doc, [{"s": 1, "op": "task.move", "uid": T1, "project": P1}])
        self.assertEqual(uids(doc["projects"][0]["tasks"]), [T1])
        self.assertEqual(doc["projects"][1]["tasks"], [])
        self.assertEqual(uids(find_task(doc, T1)["time_entries"]), [E1])

    def test_an_unknown_field_is_not_written_into_the_task(self):
        """
        The server stores operations without understanding them, so this is
        the only place a malformed or hostile field is stopped.
        """
        doc = document(project(P1, tasks=[task(T1)]))
        apply_ops(doc, [{"s": 1, "op": "task.set", "uid": T1,
                         "f": {"priority": 3, "id": 999, "uid": "hijacked", "__proto__": 1}}])
        arrived = find_task(doc, T1)
        self.assertEqual(arrived["priority"], 3)
        self.assertEqual(arrived["id"], 1)
        self.assertEqual(arrived["uid"], T1)
        self.assertNotIn("__proto__", arrived)


class TestDeletionWins(unittest.TestCase):

    def test_an_edit_cannot_bring_back_something_deleted(self):
        """
        Without this, a deletion here plus any edit there resurrects the
        object - and does so again on every reconcile, for ever.
        """
        doc = document(project(P1))
        doc["_deleted"] = [{"uid": T1, "kind": "task", "at": "2026-08-10 10:00:00"}]
        report = apply_ops(doc, [
            {"s": 1, "op": "task.create", "uid": T1, "project": P1, "f": {"task_name": "Zombie"}},
            {"s": 2, "op": "task.set", "uid": T1, "f": {"priority": 5}},
        ])
        self.assertIsNone(find_task(doc, T1))
        self.assertEqual(report.ignored, 2)

    def test_a_deletion_arriving_later_still_wins(self):
        doc = document(project(P1, tasks=[task(T1)]))
        apply_ops(doc, [
            {"s": 1, "op": "task.set", "uid": T1, "f": {"priority": 5}},
            {"s": 2, "op": "task.delete", "uid": T1, "ts": "2026-08-10 11:00:00"},
            {"s": 3, "op": "task.set", "uid": T1, "f": {"priority": 9}},
        ])
        self.assertIsNone(find_task(doc, T1))
        self.assertEqual(uids(doc["_deleted"]), [T1])

    def test_a_deletion_is_remembered_so_it_can_be_passed_on(self):
        """
        The tombstone is what a third machine, or this one after a restore,
        learns the deletion from. Removing the object without recording why
        would let the next sync recreate it.
        """
        doc = document(project(P1, tasks=[task(T1)]))
        apply_ops(doc, [{"s": 1, "op": "task.delete", "uid": T1, "ts": "2026-08-10 11:00:00"}])
        self.assertEqual(doc["_deleted"],
                         [{"uid": T1, "kind": "task", "at": "2026-08-10 11:00:00"}])

    def test_the_same_deletion_twice_leaves_one_tombstone(self):
        doc = document(project(P1, tasks=[task(T1)]))
        op = {"s": 1, "op": "task.delete", "uid": T1, "ts": "2026-08-10 11:00:00"}
        apply_ops(doc, [op])
        apply_ops(doc, [op])
        self.assertEqual(len(doc["_deleted"]), 1)


class TestTimeEntries(unittest.TestCase):

    def test_a_session_arrives_and_is_closed(self):
        doc = document(project(P1, tasks=[task(T1)]))
        apply_ops(doc, [
            {"s": 1, "op": "entry.add", "uid": E1, "task": T1, "start": "2026-08-10 09:00:00"},
            {"s": 2, "op": "entry.close", "uid": E1, "end": "2026-08-10 10:30:00"},
        ])
        found, parent = find_entry(doc, E1)
        self.assertEqual(parent["uid"], T1)
        self.assertEqual(found["end_time"], "2026-08-10 10:30:00")

    def test_an_entry_cannot_end_before_it_began(self):
        """
        Duration is these two subtracted. The machines' clocks are allowed to
        disagree by minutes, so a close really can arrive dated earlier than
        the start - and a negative duration does not announce itself, it just
        makes every report wrong.
        """
        doc = document(project(P1, tasks=[task(T1, entries=[entry(E1, "2026-08-10 09:00:00")])]))
        apply_ops(doc, [{"s": 1, "op": "entry.close", "uid": E1, "end": "2026-08-10 08:58:00"}])
        found, _ = find_entry(doc, E1)
        self.assertEqual(found["end_time"], "2026-08-10 09:00:00")

    def test_a_corrected_entry_cannot_end_before_it_began_either(self):
        doc = document(project(P1, tasks=[task(T1, entries=[
            entry(E1, "2026-08-10 09:00:00", "2026-08-10 10:00:00")])]))
        apply_ops(doc, [{"s": 1, "op": "entry.set", "uid": E1,
                         "f": {"start_time": "2026-08-10 11:00:00"}}])
        found, _ = find_entry(doc, E1)
        self.assertEqual(found["end_time"], "2026-08-10 11:00:00")

    def test_a_correction_that_makes_sense_is_left_as_it_is(self):
        """
        The clamp exists for an end that precedes its start. It must not fire
        on an ordinary correction, or every edited entry would be flattened
        to nothing.
        """
        doc = document(project(P1, tasks=[task(T1, entries=[
            entry(E1, "2026-08-10 09:00:00", "2026-08-10 10:00:00")])]))
        apply_ops(doc, [{"s": 1, "op": "entry.set", "uid": E1,
                         "f": {"end_time": "2026-08-10 12:00:00"}}])
        found, _ = find_entry(doc, E1)
        self.assertEqual(found["start_time"], "2026-08-10 09:00:00")
        self.assertEqual(found["end_time"], "2026-08-10 12:00:00")

    def test_a_session_closed_at_a_sensible_time_keeps_it(self):
        doc = document(project(P1, tasks=[task(T1, entries=[entry(E1, "2026-08-10 09:00:00")])]))
        apply_ops(doc, [{"s": 1, "op": "entry.close", "uid": E1,
                         "end": "2026-08-10 17:30:00"}])
        found, _ = find_entry(doc, E1)
        self.assertEqual(found["end_time"], "2026-08-10 17:30:00")

    def test_an_entry_moves_without_being_copied(self):
        doc = document(project(P1, tasks=[task(T1, entries=[entry(E1)]), task(T2, tid=2)]))
        apply_ops(doc, [{"s": 1, "op": "entry.move", "uid": E1, "task": T2}])
        _, parent = find_entry(doc, E1)
        self.assertEqual(parent["uid"], T2)
        self.assertEqual(find_task(doc, T1)["time_entries"], [])

    def test_deleting_an_entry_leaves_the_task(self):
        doc = document(project(P1, tasks=[task(T1, entries=[entry(E1), entry(E2)])]))
        apply_ops(doc, [{"s": 1, "op": "entry.delete", "uid": E1}])
        self.assertEqual(uids(find_task(doc, T1)["time_entries"]), [E2])


class TestTimeGoesWithTheTaskItBelongedTo(unittest.TestCase):
    """
    Deleting a task has always discarded its hours, so a machine receiving
    that deletion has to discard them too. Keeping them safe somewhere was
    tried and rejected: it left the machine that did the deleting with
    nothing and the machine that received it with the hours, permanently.
    Divergence that reports nothing is worse than the loss.
    """

    def test_time_booked_against_a_deleted_task_goes_with_it(self):
        doc = document(project(P1))
        doc["_deleted"] = [{"uid": T1, "kind": "task", "at": "2026-08-10 08:00:00"}]

        report = apply_ops(doc, [{"s": 1, "op": "entry.add", "uid": E1, "task": T1,
                                  "start": "2026-08-10 09:00:00"}])

        found, _parent = find_entry(doc, E1)
        self.assertIsNone(found)
        self.assertEqual(report.discarded_time, 1)
        self.assertEqual(uids(doc["projects"]), [P1],
                         "a container was invented to hold it after all")

    def test_time_moved_onto_a_deleted_task_goes_with_it(self):
        doc = document(project(P1, tasks=[task(T2, entries=[entry(E1)], tid=2)]))
        doc["_deleted"] = [{"uid": T1, "kind": "task", "at": "2026-08-10 08:00:00"}]

        report = apply_ops(doc, [{"s": 1, "op": "entry.move", "uid": E1, "task": T1,
                                  "ts": "2026-08-10 09:00:00"}])

        found, _parent = find_entry(doc, E1)
        self.assertIsNone(found, "the entry stayed where it was instead of going")
        self.assertEqual(report.discarded_time, 1)

    def test_a_deleted_project_takes_time_booked_to_it_afterwards(self):
        doc = document(project(P1, tasks=[task(T1)]))
        report = apply_ops(doc, [
            {"s": 1, "op": "project.delete", "uid": P1, "ts": "2026-08-10 08:00:00"},
            {"s": 2, "op": "entry.add", "uid": E1, "task": T1, "start": "2026-08-10 09:00:00"},
        ])
        found, _parent = find_entry(doc, E1)
        self.assertIsNone(found)
        self.assertEqual(report.discarded_time, 1)

    def test_both_machines_end_up_with_the_same_document(self):
        """
        The whole reason for discarding. One machine deletes the task; the
        other has already booked time to it. Replaying the same log, they must
        agree - whichever order each of them happened to learn things in.
        """
        deleted_first = document(project(P1))
        deleted_first["_deleted"] = [{"uid": T1, "kind": "task",
                                      "at": "2026-08-10 08:00:00"}]
        apply_ops(deleted_first, [
            {"s": 1, "op": "entry.add", "uid": E1, "task": T1, "start": "2026-08-10 09:00:00"},
            {"s": 2, "op": "task.delete", "uid": T1, "ts": "2026-08-10 08:00:00"},
        ])

        heard_later = document(project(P1, tasks=[task(T1)]))
        apply_ops(heard_later, [
            {"s": 1, "op": "entry.add", "uid": E1, "task": T1, "start": "2026-08-10 09:00:00"},
            {"s": 2, "op": "task.delete", "uid": T1, "ts": "2026-08-10 08:00:00"},
        ])

        self.assertEqual(deleted_first["projects"], heard_later["projects"])
        self.assertIsNone(find_entry(deleted_first, E1)[0])
        self.assertIsNone(find_entry(heard_later, E1)[0])

    def test_time_for_a_task_that_is_still_there_is_untouched(self):
        doc = document(project(P1, tasks=[task(T1)]))
        report = apply_ops(doc, [{"s": 1, "op": "entry.add", "uid": E1, "task": T1,
                                  "start": "2026-08-10 09:00:00"}])
        self.assertIsNotNone(find_entry(doc, E1)[0])
        self.assertEqual(report.discarded_time, 0)


class TestOneRunningSessionAtATime(unittest.TestCase):
    """
    What the user asked for directly: starting a task on the second machine
    should end the one still running on the first.
    """

    def test_starting_work_elsewhere_ends_the_session_left_running_here(self):
        doc = document(project(P1, tasks=[
            task(T1, "Here", entries=[entry(E1, "2026-08-10 09:00:00")]),
            task(T2, "There", tid=2)]))

        report = apply_ops(doc, [{"s": 1, "op": "entry.add", "uid": E2, "task": T2,
                                  "start": "2026-08-10 10:00:00"}])

        here, _ = find_entry(doc, E1)
        there, _ = find_entry(doc, E2)
        self.assertEqual(here["end_time"], "2026-08-10 10:00:00")
        self.assertNotIn("end_time", there)
        self.assertEqual(report.auto_closed, [(E1, "2026-08-10 10:00:00")])

    def test_no_stretch_of_time_is_counted_twice(self):
        """
        The earlier session ends exactly where the later one begins, so the
        two do not overlap and the day's total stays honest.
        """
        doc = document(project(P1, tasks=[task(T1, entries=[entry(E1, "2026-08-10 09:00:00")])]))
        apply_ops(doc, [{"s": 1, "op": "entry.add", "uid": E2, "task": T1,
                         "start": "2026-08-10 09:30:00"}])
        first, _ = find_entry(doc, E1)
        second, _ = find_entry(doc, E2)
        self.assertEqual(first["end_time"], second["start_time"])

    def test_a_running_session_stays_last_in_its_task(self):
        """
        Three places recognise the running session as the final entry rather
        than by searching for one. An entry finished elsewhere arrives after
        it and is appended, pushing it out of last place - and the session
        can then no longer be stopped, shown, or counted.
        """
        doc = document(project(P1, tasks=[task(T1, entries=[entry(E1, "2026-08-10 09:00:00")])]))
        apply_ops(doc, [
            {"s": 1, "op": "entry.add", "uid": E2, "task": T1, "start": "2026-08-10 07:00:00"},
            {"s": 2, "op": "entry.close", "uid": E2, "end": "2026-08-10 08:00:00"},
        ])
        entries = find_task(doc, T1)["time_entries"]
        self.assertNotIn("end_time", entries[-1],
                         "the running session is no longer last and cannot be stopped")
        self.assertEqual(entries[-1]["uid"], E1)

    def test_the_later_session_survives_even_if_it_is_listed_first(self):
        """
        Which of the two is closed is decided by when they began, not by
        where they happen to sit in the file. Operations arrive in the
        server's order, not in time order, so the two do come apart.
        """
        doc = document(project(P1, tasks=[task(T1, entries=[
            entry(E2, "2026-08-10 11:00:00"),      # later, but listed first
            entry(E1, "2026-08-10 09:00:00"),
        ])]))
        report = apply_ops(doc, [])

        first, _ = find_entry(doc, E1)
        second, _ = find_entry(doc, E2)
        self.assertEqual(first["end_time"], "2026-08-10 11:00:00",
                         "the earlier session should have been the one closed")
        self.assertNotIn("end_time", second)
        self.assertEqual(report.auto_closed, [(E1, "2026-08-10 11:00:00")])

    def test_a_single_running_session_is_left_alone(self):
        doc = document(project(P1, tasks=[task(T1, entries=[entry(E1)])]))
        report = apply_ops(doc, [])
        found, _ = find_entry(doc, E1)
        self.assertNotIn("end_time", found)
        self.assertEqual(report.auto_closed, [])


class TestNothingIsQuietlyBroken(unittest.TestCase):

    def test_the_id_counter_stays_ahead_of_every_task(self):
        """
        Left behind, the next task created here reuses an id - and nothing
        anywhere checks for that.
        """
        doc = document(project(P1, tasks=[task(T1, tid=99)]))
        doc["next_id"] = 2
        apply_ops(doc, [])
        self.assertEqual(doc["next_id"], 100)

    def test_the_id_counter_is_raised_even_when_it_only_just_collides(self):
        """
        The boundary, and the one that bites: next_id equal to an id already
        in use. Left alone, the very next task created here is handed a
        number another task already has - and nothing anywhere checks.
        """
        doc = document(project(P1, tasks=[task(T1, tid=7)]))
        doc["next_id"] = 7
        apply_ops(doc, [])
        self.assertEqual(doc["next_id"], 8)

    def test_a_counter_already_ahead_is_left_alone(self):
        doc = document(project(P1, tasks=[task(T1, tid=7)]))
        doc["next_id"] = 20
        apply_ops(doc, [])
        self.assertEqual(doc["next_id"], 20)

    def test_an_operation_for_something_unknown_is_counted_not_applied(self):
        doc = document()
        report = apply_ops(doc, [{"s": 1, "op": "task.set", "uid": T1, "f": {"priority": 1}}])
        self.assertEqual(report.applied, 0)
        self.assertEqual(report.ignored, 1)

    def test_an_operation_this_version_does_not_know_is_skipped(self):
        """
        A newer version on the other machine may send verbs this one has
        never heard of. Skipping one is a missing change; failing on it would
        block every later operation for good.
        """
        doc = document(project(P1))
        report = apply_ops(doc, [
            {"s": 1, "op": "task.invented", "uid": T1},
            {"s": 2, "op": "project.set", "uid": P1, "f": {"name": "Applied"}},
        ])
        self.assertEqual(doc["projects"][0]["main_project_name"], "Applied")
        self.assertEqual(report.applied, 1)
        self.assertEqual(report.ignored, 1)

    def test_the_highest_sequence_number_is_reported(self):
        """The caller stores it and asks for everything after it next time."""
        doc = document(project(P1))
        report = apply_ops(doc, [
            {"s": 7, "op": "project.set", "uid": P1, "f": {"name": "A"}},
            {"s": 12, "op": "project.set", "uid": P1, "f": {"name": "B"}},
        ])
        self.assertEqual(report.highest_seq, 12)

    def test_applying_nothing_to_a_sound_document_changes_nothing(self):
        doc = document(project(P1, tasks=[task(T1, entries=[entry(E1, end="2026-08-10 10:00:00")])]))
        doc["next_id"] = 2
        before = copy.deepcopy(doc)
        apply_ops(doc, [])
        self.assertEqual(doc, before)


class TestTheResultIsUsable(unittest.TestCase):
    """
    Applying operations produces the file the whole application reads. A
    document that is internally consistent but that TimeTracker cannot open
    would be a bug found only in front of the user.
    """

    PATH = 'test_apply_data.json'

    def tearDown(self):
        if os.path.exists(self.PATH):
            os.remove(self.PATH)

    def test_a_document_built_only_from_operations_opens_and_works(self):
        import json
        from tt.TimeTracker import TimeTracker

        doc = document()
        apply_ops(doc, [
            {"s": 1, "op": "project.create", "uid": P1, "f": {"name": "Website"}},
            {"s": 2, "op": "task.create", "uid": T1, "project": P1,
             "f": {"task_name": "Relaunch", "priority": 4}},
            {"s": 3, "op": "entry.add", "uid": E1, "task": T1, "start": "2026-08-10 09:00:00"},
            {"s": 4, "op": "entry.close", "uid": E1, "end": "2026-08-10 10:00:00"},
        ])
        with open(self.PATH, 'w', encoding='utf-8') as f:
            json.dump(doc, f)

        tracker = TimeTracker(file_path=self.PATH)
        tasks = tracker.list_tasks("Website")
        self.assertEqual([t["task_name"] for t in tasks], ["Relaunch"])
        self.assertIn("1:00:00", tracker.generate_task_report("Website", "Relaunch"))

        # And it remains a document this machine can go on adding to.
        self.assertTrue(tracker.add_task("Website", "Next"))
        self.assertIsNotNone(tracker._get_task("Website", "Next"))


class TestWhatTheCallerIsToldAsItHappens(unittest.TestCase):
    """
    Two things happen during a merge that the user did not ask for on this
    machine. Both are reported through the same callback, so the interface
    can say something rather than letting them pass unnoticed.
    """

    def test_discarded_time_is_announced(self):
        seen = []
        doc = document(project(P1))
        doc["_deleted"] = [{"uid": T1, "kind": "task", "at": "2026-08-10 08:00:00"}]

        apply_ops(doc, [{"s": 1, "op": "entry.add", "uid": E1, "task": T1,
                         "start": "2026-08-10 09:00:00"}],
                  on_conflict=lambda kind, detail: seen.append((kind, detail)))

        self.assertEqual([k for k, _ in seen], ['discarded_time'])
        self.assertEqual(seen[0][1]['entry'], E1)
        self.assertEqual(seen[0][1]['task'], T1)

    def test_an_auto_closed_session_is_announced(self):
        seen = []
        doc = document(project(P1, tasks=[
            task(T1, entries=[entry(E1, "2026-08-10 09:00:00")]),
            task(T2, tid=2)]))

        apply_ops(doc, [{"s": 1, "op": "entry.add", "uid": E2, "task": T2,
                         "start": "2026-08-10 10:00:00"}],
                  on_conflict=lambda kind, detail: seen.append((kind, detail)))

        self.assertEqual([k for k, _ in seen], ['auto_closed'])
        self.assertEqual(seen[0][1], {'entry': E1, 'end': "2026-08-10 10:00:00"})

    def test_nothing_is_announced_when_nothing_had_to_be_decided(self):
        seen = []
        doc = document(project(P1, tasks=[task(T1)]))
        apply_ops(doc, [{"s": 1, "op": "task.set", "uid": T1, "f": {"priority": 3}}],
                  on_conflict=lambda kind, detail: seen.append(kind))
        self.assertEqual(seen, [])


class TestOperationsThatFindNothingToActOn(unittest.TestCase):
    """
    Each of these names something this machine does not have. None may raise,
    and none may invent the missing object - a later operation would then be
    applied to something the other machine does not have.
    """

    def test_every_verb_survives_a_missing_target(self):
        doc = document()
        report = apply_ops(doc, [
            {"s": 1, "op": "project.set", "uid": P1, "f": {"name": "x"}},
            {"s": 2, "op": "task.set", "uid": T1, "f": {"priority": 1}},
            {"s": 3, "op": "task.move", "uid": T1, "project": P1},
            {"s": 4, "op": "task.create", "uid": T1, "project": P1, "f": {}},
            {"s": 5, "op": "entry.close", "uid": E1, "end": "2026-08-10 10:00:00"},
            {"s": 6, "op": "entry.set", "uid": E1, "f": {"start_time": "x"}},
            {"s": 7, "op": "entry.move", "uid": E1, "task": T1},
            {"s": 8, "op": "entry.delete", "uid": E1},
            {"s": 9, "op": "project.delete", "uid": P1},
            {"s": 10, "op": "task.delete", "uid": T1},
        ])
        self.assertEqual(doc["projects"], [])
        self.assertGreaterEqual(report.ignored, 7)

    def test_an_entry_arriving_already_finished_keeps_its_end(self):
        """
        How a rebuild from the log delivers past work: the add carries the
        end, rather than a close following it.
        """
        doc = document(project(P1, tasks=[task(T1)]))
        apply_ops(doc, [{"s": 1, "op": "entry.add", "uid": E1, "task": T1,
                         "start": "2026-08-10 09:00:00", "end": "2026-08-10 10:00:00"}])
        found, _ = find_entry(doc, E1)
        self.assertEqual(found["end_time"], "2026-08-10 10:00:00")

    def test_an_entry_that_arrives_twice_is_not_duplicated(self):
        doc = document(project(P1, tasks=[task(T1), task(T2, tid=2)]))
        op = {"s": 1, "op": "entry.add", "uid": E1, "task": T1,
              "start": "2026-08-10 09:00:00"}
        apply_ops(doc, [op])
        apply_ops(doc, [dict(op, s=2, task=T2)])

        self.assertEqual(find_task(doc, T1)["time_entries"], [])
        self.assertEqual(uids(find_task(doc, T2)["time_entries"]), [E1])

    def test_a_move_to_a_project_this_machine_lacks_is_dropped(self):
        """
        Only half of what the move names is missing, which is the case a test
        with both missing never reaches: acting on it would hand the mover a
        project that is not there.
        """
        doc = document(project(P2, "Other", tasks=[task(T1)]))
        report = apply_ops(doc, [{"s": 1, "op": "task.move", "uid": T1, "project": P1}])

        self.assertEqual(uids(doc["projects"][0]["tasks"]), [T1],
                         "the task was moved somewhere that does not exist")
        self.assertEqual(report.ignored, 1)

    def test_time_for_a_task_that_was_never_created_here_is_dropped(self):
        """
        Not deleted - simply absent, because the operation that would have
        created it was itself dropped. Nothing may be invented to hold it.
        """
        doc = document(project(P1))
        report = apply_ops(doc, [{"s": 1, "op": "entry.add", "uid": E1, "task": T1,
                                  "start": "2026-08-10 09:00:00"}])

        self.assertIsNone(find_entry(doc, E1)[0])
        self.assertEqual(uids(doc["projects"]), [P1])
        self.assertEqual(report.discarded_time, 1)

    def test_moving_an_entry_this_machine_does_not_have_is_dropped(self):
        """
        The task is here, the entry is not - it was deleted here, or its
        creation never arrived. Nothing may be conjured up to move.
        """
        doc = document(project(P1, tasks=[task(T1)]))
        report = apply_ops(doc, [{"s": 1, "op": "entry.move", "uid": E1, "task": T1}])
        self.assertEqual(find_task(doc, T1)["time_entries"], [])
        self.assertEqual(report.ignored, 1)
        self.assertEqual(report.discarded_time, 0,
                         "counted as lost time when there was no time to lose")

    def test_a_task_with_a_missing_project_is_not_given_one(self):
        doc = document()
        report = apply_ops(doc, [{"s": 1, "op": "task.create", "uid": T1,
                                  "project": P1, "f": {"task_name": "T"}}])
        self.assertEqual(doc["projects"], [])
        self.assertEqual(report.ignored, 1)


class TestReconcile(unittest.TestCase):
    """
    Incoming work first, then this machine's own unsent work on top - because
    that is the order the server will put them in.
    """

    def test_an_unsent_local_change_survives_an_incoming_one(self):
        doc = document(project(P1, tasks=[task(T1, priority=5)]))
        from tt.sync_apply import reconcile

        reconcile(doc,
                  incoming=[{"s": 10, "op": "task.set", "uid": T1, "f": {"priority": 9}}],
                  local=[{"lc": 1, "op": "task.set", "uid": T1, "f": {"priority": 5}}])

        self.assertEqual(find_task(doc, T1)["priority"], 5)

    def test_an_incoming_change_to_another_field_is_kept(self):
        doc = document(project(P1, tasks=[task(T1, priority=5)]))
        from tt.sync_apply import reconcile

        reconcile(doc,
                  incoming=[{"s": 10, "op": "task.set", "uid": T1, "f": {"due_date": "2026-09-01"}}],
                  local=[{"lc": 1, "op": "task.set", "uid": T1, "f": {"priority": 5}}])

        arrived = find_task(doc, T1)
        self.assertEqual(arrived["priority"], 5)
        self.assertEqual(arrived["due_date"], "2026-09-01")

    def test_unsent_work_is_replayed_in_the_order_it_was_made(self):
        doc = document(project(P1))
        from tt.sync_apply import reconcile

        reconcile(doc, incoming=[], local=[
            {"lc": 3, "op": "task.set", "uid": T1, "f": {"task_name": "last"}},
            {"lc": 1, "op": "project.create", "uid": P1, "f": {"name": "P"}},
            {"lc": 2, "op": "task.create", "uid": T1, "project": P1, "f": {"task_name": "first"}},
        ])

        self.assertEqual(find_task(doc, T1)["task_name"], "last")

    def test_unsent_time_goes_too_when_the_task_was_deleted_elsewhere(self):
        """
        The hard edge of the rule, and the reason it is written down: work
        booked here and not yet sent is discarded if the task it belongs to
        was deleted on the other machine. The alternative keeps the hours here
        and nowhere else, which is the divergence this design exists to avoid.
        """
        doc = document(project(P1, tasks=[task(T1)]))
        from tt.sync_apply import reconcile

        report = reconcile(
            doc,
            incoming=[{"s": 10, "op": "task.delete", "uid": T1, "ts": "2026-08-10 08:00:00"}],
            local=[{"lc": 1, "op": "entry.add", "uid": E1, "task": T1,
                    "start": "2026-08-10 09:00:00"}])

        self.assertIsNone(find_entry(doc, E1)[0])
        self.assertEqual(report.discarded_time, 1,
                         "it was dropped without the user being told")

    def test_two_machines_reach_the_same_document(self):
        """
        The whole point, end to end. Two machines edit the same task while out
        of contact; one reaches the server first. Both must finish identical -
        two files that quietly stopped matching is the failure nothing reports.
        """
        from tt.sync_apply import reconcile

        start = document(project(P1, tasks=[task(T1, "Shared", priority=1)]))
        start["next_id"] = 2
        here, there = copy.deepcopy(start), copy.deepcopy(start)

        # Each machine makes its change locally, before either has synced.
        first = [{"lc": 1, "op": "task.set", "uid": T1, "f": {"priority": 8}}]
        second = [{"lc": 1, "op": "task.set", "uid": T1, "f": {"due_date": "2026-09-01"}},
                  {"lc": 2, "op": "task.set", "uid": T1, "f": {"priority": 3}}]
        find_task(here, T1)["priority"] = 8
        find_task(there, T1).update({"due_date": "2026-09-01", "priority": 3})

        # This machine gets there first: nothing waiting, its own work numbered 1.
        reconcile(here, incoming=[], local=first)
        # The other pushes next. It sees the first machine's work, and the
        # server puts its own after it.
        reconcile(there,
                  incoming=[dict(first[0], s=1)],
                  local=second)
        # And the first machine catches up, now with an empty queue.
        reconcile(here,
                  incoming=[dict(second[0], s=2), dict(second[1], s=3)],
                  local=[])

        self.assertEqual(here, there)
        self.assertEqual(find_task(here, T1)["priority"], 3)
        self.assertEqual(find_task(here, T1)["due_date"], "2026-09-01")


class TestSeeding(unittest.TestCase):
    """
    The one time a whole document is sent: the first machine to reach an empty
    server. It goes as operations, not as a file, so the server never has to
    understand the format.
    """

    def test_a_document_is_described_as_the_operations_that_would_build_it(self):
        from tt.sync_apply import seed_operations

        doc = document(project(P1, "Website", tasks=[
            task(T1, "Relaunch", entries=[entry(E1, "2026-08-10 09:00:00", "2026-08-10 10:00:00")])]))
        ops = seed_operations(doc)

        self.assertEqual([o["op"] for o in ops],
                         ["project.create", "project.set",
                          "task.create", "task.set", "task.move",
                          "entry.add", "entry.set"])
        create = next(o for o in ops if o["op"] == "task.create")
        self.assertEqual(create["project"], P1)
        self.assertNotIn("id", create["f"])
        self.assertNotIn("time_entries", create["f"])

    def test_it_also_states_the_current_value_of_everything(self):
        """
        A machine re-introducing itself is talking to machines that already
        have these objects, where a create does nothing. Without the set,
        everything that changed while it was out of contact would be
        announced and silently dropped.
        """
        from tt.sync_apply import seed_operations

        doc = document(project(P1, "Renamed", tasks=[
            task(T1, "Also renamed", priority=6,
                 entries=[entry(E1, "2026-08-10 09:00:00", "2026-08-10 10:00:00")])]))
        ops = seed_operations(doc)

        # Apply to a machine that already holds the old values.
        elsewhere = document(project(P1, "Old", tasks=[
            task(T1, "Old name", priority=0,
                 entries=[entry(E1, "2026-08-10 09:00:00")])]))
        apply_ops(elsewhere, [dict(o, s=i) for i, o in enumerate(ops, 1)])

        self.assertEqual(elsewhere["projects"][0]["main_project_name"], "Renamed")
        arrived = find_task(elsewhere, T1)
        self.assertEqual(arrived["task_name"], "Also renamed")
        self.assertEqual(arrived["priority"], 6)
        self.assertEqual(arrived["time_entries"][0]["end_time"], "2026-08-10 10:00:00")

    def test_a_seeded_document_rebuilds_exactly(self):
        """
        Replayed on an empty machine the result has to be the same document,
        or the second machine starts out already disagreeing with the first.
        """
        from tt.sync_apply import seed_operations

        original = document(
            project(P1, "Website", tasks=[
                task(T1, "Relaunch", tid=1, priority=4, due_date="2026-09-01",
                     entries=[entry(E1, "2026-08-10 09:00:00", "2026-08-10 10:00:00"),
                              entry(E2, "2026-08-10 11:00:00")])]),
            project(P2, "Admin", tasks=[task(T2, "Invoices", tid=2)]))
        original["next_id"] = 3

        ops = seed_operations(original)
        rebuilt = document()
        apply_ops(rebuilt, [dict(op, s=i) for i, op in enumerate(ops, 1)])

        self.assertEqual(rebuilt["projects"], original["projects"])
        self.assertEqual(rebuilt["next_id"], original["next_id"])

    def test_an_entry_with_nothing_to_identify_it_is_left_out(self):
        """
        The server checks every uid against a 16-character pattern and
        rejects the whole batch if one fails. A single malformed entry -
        from a hand-edited file, or an interrupted write - would therefore
        stop this machine's document being offered at all.
        """
        from tt.sync_apply import seed_operations

        doc = document(project(P1, tasks=[task(T1, entries=[
            {"uid": E1, "start_time": "2026-08-10 09:00:00"},
            {"start_time": "2026-08-10 11:00:00"},          # no uid
            {"uid": E2},                                     # no start
        ])]))
        ops = seed_operations(doc)
        adds = [o for o in ops if o["op"] == "entry.add"]
        self.assertEqual([o["uid"] for o in adds], [E1])

    def test_a_tombstone_that_makes_no_sense_is_left_out(self):
        from tt.sync_apply import seed_operations

        doc = document(project(P1))
        doc["_deleted"] = [
            {"uid": T1, "kind": "task", "at": "2026-08-01 09:00:00"},
            {"uid": T2, "kind": "sideways", "at": "2026-08-01 09:00:00"},
            {"kind": "task", "at": "2026-08-01 09:00:00"},
        ]
        ops = seed_operations(doc)
        deletes = [o for o in ops if o["op"].endswith(".delete")]
        self.assertEqual(deletes, [{"op": "task.delete", "uid": T1,
                                    "ts": "2026-08-01 09:00:00"}])

    def test_deletions_are_seeded_too(self):
        """
        A machine seeding from a document that still carries tombstones has to
        pass them on, or the others are given no way to know those objects are
        meant to stay gone.
        """
        from tt.sync_apply import seed_operations

        doc = document(project(P1))
        doc["_deleted"] = [{"uid": T1, "kind": "task", "at": "2026-08-01 09:00:00"}]
        ops = seed_operations(doc)
        self.assertIn({"op": "task.delete", "uid": T1, "ts": "2026-08-01 09:00:00"}, ops)


if __name__ == '__main__':
    unittest.main()
