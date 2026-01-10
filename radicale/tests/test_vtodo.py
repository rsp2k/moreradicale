"""
Tests for Enhanced VTODO Support.

Tests task relationships (RFC 9253), filtering, and property extraction.
"""

import datetime
import pytest
import vobject


class TestTaskRelationships:
    """Tests for RELATED-TO relationship handling."""

    def test_parse_reltype_parent(self):
        """Test parsing RELTYPE=PARENT."""
        from radicale.vtodo.relationships import parse_reltype, RelationType

        assert parse_reltype("PARENT") == RelationType.PARENT
        assert parse_reltype("parent") == RelationType.PARENT
        assert parse_reltype("Parent") == RelationType.PARENT

    def test_parse_reltype_child(self):
        """Test parsing RELTYPE=CHILD."""
        from radicale.vtodo.relationships import parse_reltype, RelationType

        assert parse_reltype("CHILD") == RelationType.CHILD

    def test_parse_reltype_depends_on(self):
        """Test parsing RELTYPE=DEPENDS-ON (RFC 9253)."""
        from radicale.vtodo.relationships import parse_reltype, RelationType

        assert parse_reltype("DEPENDS-ON") == RelationType.DEPENDS_ON
        assert parse_reltype("depends-on") == RelationType.DEPENDS_ON

    def test_parse_reltype_default(self):
        """Test default RELTYPE when not specified."""
        from radicale.vtodo.relationships import parse_reltype, RelationType

        # RFC 5545: Default is PARENT
        assert parse_reltype(None) == RelationType.PARENT
        assert parse_reltype("") == RelationType.PARENT

    def test_parse_reltype_unknown(self):
        """Test parsing unknown RELTYPE."""
        from radicale.vtodo.relationships import parse_reltype, RelationType

        assert parse_reltype("CUSTOM-TYPE") == RelationType.X_UNKNOWN

    def test_extract_relationships_basic(self):
        """Test extracting RELATED-TO from VTODO."""
        from radicale.vtodo.relationships import extract_relationships, RelationType

        ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:subtask-1
SUMMARY:Write code
RELATED-TO;RELTYPE=PARENT:project-1
END:VTODO
END:VCALENDAR"""

        cal = vobject.readOne(ics)
        relationships = extract_relationships(cal.vtodo)

        assert len(relationships) == 1
        assert relationships[0].task_uid == "subtask-1"
        assert relationships[0].related_uid == "project-1"
        assert relationships[0].rel_type == RelationType.PARENT

    def test_extract_relationships_multiple(self):
        """Test extracting multiple RELATED-TO properties."""
        from radicale.vtodo.relationships import extract_relationships, RelationType

        ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:task-1
SUMMARY:Complex task
RELATED-TO;RELTYPE=PARENT:project-1
RELATED-TO;RELTYPE=DEPENDS-ON:prerequisite-1
END:VTODO
END:VCALENDAR"""

        cal = vobject.readOne(ics)
        relationships = extract_relationships(cal.vtodo)

        assert len(relationships) == 2

        parent_rels = [r for r in relationships if r.rel_type == RelationType.PARENT]
        dep_rels = [r for r in relationships if r.rel_type == RelationType.DEPENDS_ON]

        assert len(parent_rels) == 1
        assert len(dep_rels) == 1
        assert parent_rels[0].related_uid == "project-1"
        assert dep_rels[0].related_uid == "prerequisite-1"

    def test_task_relationship_is_blocking(self):
        """Test that DEPENDS-ON relationships are blocking."""
        from radicale.vtodo.relationships import TaskRelationship, RelationType

        dep_rel = TaskRelationship("task-1", "prereq-1", RelationType.DEPENDS_ON)
        parent_rel = TaskRelationship("task-1", "parent-1", RelationType.PARENT)

        assert dep_rel.is_blocking()
        assert not parent_rel.is_blocking()

    def test_build_task_hierarchy(self):
        """Test building complete task hierarchy."""
        from radicale.vtodo.relationships import build_task_hierarchy

        parent_ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:parent-task
SUMMARY:Parent Task
END:VTODO
END:VCALENDAR"""

        child_ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:child-task
SUMMARY:Child Task
RELATED-TO;RELTYPE=PARENT:parent-task
END:VTODO
END:VCALENDAR"""

        parent = vobject.readOne(parent_ics)
        child = vobject.readOne(child_ics)

        hierarchy = build_task_hierarchy([parent, child])

        assert "parent-task" in hierarchy
        assert "child-task" in hierarchy
        assert "child-task" in hierarchy["parent-task"]["children"]
        assert "parent-task" in hierarchy["child-task"]["parents"]

    def test_get_root_tasks(self):
        """Test finding root tasks (no parents)."""
        from radicale.vtodo.relationships import build_task_hierarchy, get_root_tasks

        parent_ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:root-task
SUMMARY:Root
END:VTODO
END:VCALENDAR"""

        child_ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:child-task
SUMMARY:Child
RELATED-TO;RELTYPE=PARENT:root-task
END:VTODO
END:VCALENDAR"""

        parent = vobject.readOne(parent_ics)
        child = vobject.readOne(child_ics)

        hierarchy = build_task_hierarchy([parent, child])
        roots = get_root_tasks(hierarchy)

        assert "root-task" in roots
        assert "child-task" not in roots

    def test_validate_no_cycles(self):
        """Test cycle detection in task hierarchy."""
        from radicale.vtodo.relationships import validate_no_cycles

        # Valid hierarchy (no cycles)
        valid_hierarchy = {
            "task-1": {"children": {"task-2"}, "parents": set(), "dependencies": set(), "dependents": set()},
            "task-2": {"children": set(), "parents": {"task-1"}, "dependencies": set(), "dependents": set()}
        }
        assert validate_no_cycles(valid_hierarchy)

        # Invalid hierarchy (cycle)
        cyclic_hierarchy = {
            "task-1": {"children": {"task-2"}, "parents": {"task-2"}, "dependencies": set(), "dependents": set()},
            "task-2": {"children": {"task-1"}, "parents": {"task-1"}, "dependencies": set(), "dependents": set()}
        }
        assert not validate_no_cycles(cyclic_hierarchy)


class TestTaskProperties:
    """Tests for VTODO property extraction."""

    def test_get_task_properties_basic(self):
        """Test extracting basic task properties."""
        from radicale.vtodo.properties import get_task_properties

        ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:task-1
SUMMARY:Test Task
DESCRIPTION:A test task
STATUS:IN-PROCESS
PRIORITY:3
PERCENT-COMPLETE:50
END:VTODO
END:VCALENDAR"""

        cal = vobject.readOne(ics)
        props = get_task_properties(cal.vtodo)

        assert props["uid"] == "task-1"
        assert props["summary"] == "Test Task"
        assert props["description"] == "A test task"
        assert props["status"] == "IN-PROCESS"
        assert props["priority"] == 3
        assert props["percent_complete"] == 50

    def test_get_task_properties_defaults(self):
        """Test default values for missing properties."""
        from radicale.vtodo.properties import get_task_properties

        ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:minimal-task
SUMMARY:Minimal
END:VTODO
END:VCALENDAR"""

        cal = vobject.readOne(ics)
        props = get_task_properties(cal.vtodo)

        assert props["status"] == "NEEDS-ACTION"
        assert props["priority"] == 0
        assert props["percent_complete"] == 0
        assert props["categories"] == []

    def test_is_task_completed(self):
        """Test checking if task is completed."""
        from radicale.vtodo.properties import is_task_completed

        completed_ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:done-task
STATUS:COMPLETED
END:VTODO
END:VCALENDAR"""

        incomplete_ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:todo-task
STATUS:NEEDS-ACTION
END:VTODO
END:VCALENDAR"""

        completed = vobject.readOne(completed_ics)
        incomplete = vobject.readOne(incomplete_ics)

        assert is_task_completed(completed.vtodo)
        assert not is_task_completed(incomplete.vtodo)

    def test_get_task_progress_category(self):
        """Test progress categorization."""
        from radicale.vtodo.properties import get_task_progress_category

        not_started = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:t1
PERCENT-COMPLETE:0
END:VTODO
END:VCALENDAR"""

        in_progress = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:t2
PERCENT-COMPLETE:50
END:VTODO
END:VCALENDAR"""

        nearly_done = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:t3
PERCENT-COMPLETE:90
END:VTODO
END:VCALENDAR"""

        completed = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:t4
STATUS:COMPLETED
END:VTODO
END:VCALENDAR"""

        assert get_task_progress_category(vobject.readOne(not_started).vtodo) == "not-started"
        assert get_task_progress_category(vobject.readOne(in_progress).vtodo) == "in-progress"
        assert get_task_progress_category(vobject.readOne(nearly_done).vtodo) == "nearly-done"
        assert get_task_progress_category(vobject.readOne(completed).vtodo) == "completed"


class TestTaskFiltering:
    """Tests for VTODO filtering."""

    def _make_vtodo(self, uid, status="NEEDS-ACTION", percent=0, priority=0):
        """Helper to create VTODO components."""
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:{uid}
STATUS:{status}
PERCENT-COMPLETE:{percent}
PRIORITY:{priority}
END:VTODO
END:VCALENDAR"""
        return vobject.readOne(ics).vtodo

    def test_filter_tasks_by_status(self):
        """Test filtering by status."""
        from radicale.vtodo.properties import filter_tasks_by_status

        todos = [
            self._make_vtodo("t1", "NEEDS-ACTION"),
            self._make_vtodo("t2", "IN-PROCESS"),
            self._make_vtodo("t3", "COMPLETED"),
            self._make_vtodo("t4", "CANCELLED"),
        ]

        active = filter_tasks_by_status(todos, ["NEEDS-ACTION", "IN-PROCESS"])
        assert len(active) == 2

        completed = filter_tasks_by_status(todos, ["COMPLETED"])
        assert len(completed) == 1

    def test_filter_tasks_by_percent_range(self):
        """Test filtering by percent complete range."""
        from radicale.vtodo.properties import filter_tasks_by_percent_range

        todos = [
            self._make_vtodo("t1", percent=0),
            self._make_vtodo("t2", percent=25),
            self._make_vtodo("t3", percent=50),
            self._make_vtodo("t4", percent=75),
            self._make_vtodo("t5", percent=100),
        ]

        # Tasks between 25-75%
        mid_progress = filter_tasks_by_percent_range(todos, 25, 75)
        assert len(mid_progress) == 3

        # Not started
        not_started = filter_tasks_by_percent_range(todos, 0, 0)
        assert len(not_started) == 1

    def test_filter_tasks_by_priority(self):
        """Test filtering by priority."""
        from radicale.vtodo.properties import filter_tasks_by_priority

        todos = [
            self._make_vtodo("t1", priority=1),  # High
            self._make_vtodo("t2", priority=5),  # Medium
            self._make_vtodo("t3", priority=9),  # Low
            self._make_vtodo("t4", priority=0),  # Undefined
        ]

        # High priority only (1-4)
        high = filter_tasks_by_priority(todos, max_priority=4)
        assert len(high) == 2  # t1 and t4 (undefined matches all)

    def test_sort_tasks_by_priority(self):
        """Test sorting by priority."""
        from radicale.vtodo.properties import sort_tasks_by_priority

        todos = [
            self._make_vtodo("low", priority=9),
            self._make_vtodo("high", priority=1),
            self._make_vtodo("medium", priority=5),
        ]

        sorted_desc = sort_tasks_by_priority(todos, descending=True)
        uids = [t.uid.value for t in sorted_desc]
        assert uids == ["high", "medium", "low"]

    def test_sort_tasks_by_due(self):
        """Test sorting by due date."""
        from radicale.vtodo.properties import sort_tasks_by_due

        ics1 = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:later
DUE:20250115
END:VTODO
END:VCALENDAR"""

        ics2 = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:sooner
DUE:20250110
END:VTODO
END:VCALENDAR"""

        ics3 = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:no-due
END:VTODO
END:VCALENDAR"""

        todos = [
            vobject.readOne(ics1).vtodo,
            vobject.readOne(ics2).vtodo,
            vobject.readOne(ics3).vtodo,
        ]

        sorted_asc = sort_tasks_by_due(todos, ascending=True)
        uids = [t.uid.value for t in sorted_asc]
        assert uids == ["sooner", "later", "no-due"]


class TestTaskOverdue:
    """Tests for overdue task detection."""

    def test_is_task_overdue_past_due(self):
        """Test task is overdue when past due date."""
        from radicale.vtodo.properties import is_task_overdue

        past_ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:overdue
STATUS:NEEDS-ACTION
DUE:20200101
END:VTODO
END:VCALENDAR"""

        past = vobject.readOne(past_ics)
        assert is_task_overdue(past.vtodo)

    def test_is_task_overdue_completed(self):
        """Test completed task is never overdue."""
        from radicale.vtodo.properties import is_task_overdue

        past_completed = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:done
STATUS:COMPLETED
DUE:20200101
END:VTODO
END:VCALENDAR"""

        done = vobject.readOne(past_completed)
        assert not is_task_overdue(done.vtodo)

    def test_is_task_overdue_no_due(self):
        """Test task without due date is never overdue."""
        from radicale.vtodo.properties import is_task_overdue

        no_due = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:no-due
STATUS:NEEDS-ACTION
END:VTODO
END:VCALENDAR"""

        task = vobject.readOne(no_due)
        assert not is_task_overdue(task.vtodo)


class TestRelationshipSerialization:
    """Tests for relationship serialization."""

    def test_relationship_to_dict(self):
        """Test TaskRelationship to_dict conversion."""
        from radicale.vtodo.relationships import TaskRelationship, RelationType

        rel = TaskRelationship("task-1", "parent-1", RelationType.PARENT)
        d = rel.to_dict()

        assert d["task_uid"] == "task-1"
        assert d["related_uid"] == "parent-1"
        assert d["rel_type"] == "PARENT"

    def test_relationship_depends_on_to_dict(self):
        """Test DEPENDS-ON relationship serialization."""
        from radicale.vtodo.relationships import TaskRelationship, RelationType

        rel = TaskRelationship("task-1", "prereq-1", RelationType.DEPENDS_ON)
        d = rel.to_dict()

        assert d["rel_type"] == "DEPENDS-ON"
