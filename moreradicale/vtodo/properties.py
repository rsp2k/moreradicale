"""
VTODO DAV Properties for Enhanced Task Support.

Provides CalDAV properties for task management:
- supported-vtodo-component-set: Task component capabilities
- calendar-task-relations: Task relationship information
- task-status-set: Allowed STATUS values
"""

from typing import Dict, List, Any
import vobject


# Task status values per RFC 5545
TASK_STATUS_VALUES = [
    "NEEDS-ACTION",  # Task needs action (default)
    "IN-PROCESS",    # Task is in progress
    "COMPLETED",     # Task is completed
    "CANCELLED",     # Task was cancelled
]

# Task priority range (RFC 5545)
# 0 = undefined, 1-4 = high, 5 = normal, 6-9 = low
PRIORITY_RANGE = range(0, 10)

# VTODO properties supported by enhanced implementation
TASK_SUPPORTED_PROPERTIES = [
    # Core identification
    "UID",
    "SUMMARY",
    "DESCRIPTION",

    # Temporal properties
    "DTSTART",
    "DUE",
    "DURATION",
    "COMPLETED",

    # Status and progress
    "STATUS",
    "PERCENT-COMPLETE",
    "PRIORITY",

    # Organization
    "CLASS",
    "CATEGORIES",
    "ORGANIZER",
    "ATTENDEE",

    # Relationships (RFC 5545 + RFC 9253)
    "RELATED-TO",

    # Recurrence
    "RRULE",
    "RDATE",
    "EXDATE",
    "RECURRENCE-ID",

    # Metadata
    "CREATED",
    "LAST-MODIFIED",
    "DTSTAMP",
    "SEQUENCE",

    # URLs and references
    "URL",
    "ATTACH",

    # Alarms
    "VALARM",

    # Geographic
    "GEO",
    "LOCATION",

    # Apple/iOS extensions
    "X-APPLE-SORT-ORDER",
]


def get_task_properties(vtodo: vobject.base.Component) -> Dict[str, Any]:
    """
    Extract all relevant properties from a VTODO component.

    Args:
        vtodo: A vobject VTODO component

    Returns:
        Dictionary of property name -> value mappings
    """
    props = {}

    # UID
    if hasattr(vtodo, "uid"):
        props["uid"] = str(vtodo.uid.value)

    # Summary
    if hasattr(vtodo, "summary"):
        props["summary"] = str(vtodo.summary.value)

    # Description
    if hasattr(vtodo, "description"):
        props["description"] = str(vtodo.description.value)

    # Status
    if hasattr(vtodo, "status"):
        props["status"] = str(vtodo.status.value).upper()
    else:
        props["status"] = "NEEDS-ACTION"

    # Priority (0-9)
    if hasattr(vtodo, "priority"):
        try:
            props["priority"] = int(vtodo.priority.value)
        except (ValueError, TypeError):
            props["priority"] = 0
    else:
        props["priority"] = 0

    # Percent complete (0-100)
    if hasattr(vtodo, "percent_complete"):
        try:
            value = int(vtodo.percent_complete.value)
            props["percent_complete"] = max(0, min(100, value))
        except (ValueError, TypeError):
            props["percent_complete"] = 0
    else:
        props["percent_complete"] = 0

    # Due date
    if hasattr(vtodo, "due"):
        props["due"] = vtodo.due.value

    # Start date
    if hasattr(vtodo, "dtstart"):
        props["dtstart"] = vtodo.dtstart.value

    # Completed date
    if hasattr(vtodo, "completed"):
        props["completed"] = vtodo.completed.value

    # Duration
    if hasattr(vtodo, "duration"):
        props["duration"] = vtodo.duration.value

    # Categories
    if hasattr(vtodo, "categories"):
        cats = vtodo.categories.value
        if isinstance(cats, list):
            props["categories"] = cats
        else:
            props["categories"] = [cats]
    else:
        props["categories"] = []

    # Location
    if hasattr(vtodo, "location"):
        props["location"] = str(vtodo.location.value)

    # URL
    if hasattr(vtodo, "url"):
        props["url"] = str(vtodo.url.value)

    # Organizer
    if hasattr(vtodo, "organizer"):
        props["organizer"] = str(vtodo.organizer.value)

    # Created/Modified timestamps
    if hasattr(vtodo, "created"):
        props["created"] = vtodo.created.value
    if hasattr(vtodo, "last_modified"):
        props["last_modified"] = vtodo.last_modified.value

    # Recurrence
    if hasattr(vtodo, "rrule"):
        props["rrule"] = str(vtodo.rrule.value)
    props["is_recurring"] = hasattr(vtodo, "rrule")

    # RELATED-TO (handled separately in relationships module)
    props["has_relationships"] = hasattr(vtodo, "related_to")

    return props


def is_task_completed(vtodo: vobject.base.Component) -> bool:
    """Check if a VTODO is completed."""
    if hasattr(vtodo, "status"):
        return str(vtodo.status.value).upper() == "COMPLETED"
    return False


def is_task_overdue(vtodo: vobject.base.Component) -> bool:
    """Check if a VTODO is overdue (not completed and past due date)."""
    import datetime

    if is_task_completed(vtodo):
        return False

    if not hasattr(vtodo, "due"):
        return False

    due = vtodo.due.value
    now = datetime.datetime.now(datetime.timezone.utc)

    # Handle date vs datetime
    if hasattr(due, "tzinfo"):
        if due.tzinfo is None:
            due = due.replace(tzinfo=datetime.timezone.utc)
        return due < now
    else:
        # date object - compare to today
        today = datetime.date.today()
        return due < today


def get_task_progress_category(vtodo: vobject.base.Component) -> str:
    """
    Categorize task by progress level.

    Returns: "not-started", "in-progress", "nearly-done", "completed"
    """
    props = get_task_properties(vtodo)

    if props["status"] == "COMPLETED":
        return "completed"

    pct = props["percent_complete"]

    if pct == 0:
        return "not-started"
    elif pct < 75:
        return "in-progress"
    else:
        return "nearly-done"


def filter_tasks_by_status(
    vtodos: List[vobject.base.Component],
    statuses: List[str]
) -> List[vobject.base.Component]:
    """
    Filter VTODOs by status values.

    Args:
        vtodos: List of VTODO components
        statuses: List of status values to include (e.g., ["NEEDS-ACTION", "IN-PROCESS"])

    Returns:
        Filtered list of VTODOs
    """
    statuses_upper = [s.upper() for s in statuses]

    def matches(vtodo):
        if hasattr(vtodo, "status"):
            return str(vtodo.status.value).upper() in statuses_upper
        else:
            # Default status is NEEDS-ACTION
            return "NEEDS-ACTION" in statuses_upper

    return [v for v in vtodos if matches(v)]


def filter_tasks_by_percent_range(
    vtodos: List[vobject.base.Component],
    min_percent: int = 0,
    max_percent: int = 100
) -> List[vobject.base.Component]:
    """
    Filter VTODOs by percent complete range.

    Args:
        vtodos: List of VTODO components
        min_percent: Minimum percent complete (inclusive)
        max_percent: Maximum percent complete (inclusive)

    Returns:
        Filtered list of VTODOs
    """
    def matches(vtodo):
        pct = 0
        if hasattr(vtodo, "percent_complete"):
            try:
                pct = int(vtodo.percent_complete.value)
            except (ValueError, TypeError):
                pct = 0
        return min_percent <= pct <= max_percent

    return [v for v in vtodos if matches(v)]


def filter_tasks_by_priority(
    vtodos: List[vobject.base.Component],
    max_priority: int = 9
) -> List[vobject.base.Component]:
    """
    Filter VTODOs by priority (1=highest, 9=lowest, 0=undefined).

    Args:
        vtodos: List of VTODO components
        max_priority: Maximum priority value to include

    Returns:
        Filtered list of VTODOs with priority <= max_priority
    """
    def matches(vtodo):
        if hasattr(vtodo, "priority"):
            try:
                pri = int(vtodo.priority.value)
                if pri == 0:
                    return True  # Undefined always matches
                return pri <= max_priority
            except (ValueError, TypeError):
                return True
        return True  # No priority defined

    return [v for v in vtodos if matches(v)]


def sort_tasks_by_priority(
    vtodos: List[vobject.base.Component],
    descending: bool = True
) -> List[vobject.base.Component]:
    """
    Sort VTODOs by priority (highest first by default).

    Priority 1 is highest, 9 is lowest, 0 is undefined (sorted last).
    """
    def priority_key(vtodo):
        if hasattr(vtodo, "priority"):
            try:
                pri = int(vtodo.priority.value)
                # 0 (undefined) should sort last
                return 10 if pri == 0 else pri
            except (ValueError, TypeError):
                return 10
        return 10  # No priority = undefined = last

    return sorted(vtodos, key=priority_key, reverse=not descending)


def sort_tasks_by_due(
    vtodos: List[vobject.base.Component],
    ascending: bool = True
) -> List[vobject.base.Component]:
    """
    Sort VTODOs by due date (earliest first by default).

    Tasks without due dates are sorted last.
    """
    import datetime

    def due_key(vtodo):
        if not hasattr(vtodo, "due"):
            return datetime.datetime.max

        due = vtodo.due.value
        if isinstance(due, datetime.date) and not isinstance(due, datetime.datetime):
            due = datetime.datetime.combine(due, datetime.time.min)

        return due

    return sorted(vtodos, key=due_key, reverse=not ascending)
