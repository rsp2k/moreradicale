"""
Enhanced VTODO Support for Radicale.

Implements RFC 9253 Task Extensions to iCalendar:
- Task relationships (PARENT, CHILD, SIBLING, DEPENDS-ON)
- Task hierarchy querying
- Enhanced task filtering

This module provides:
- TaskRelationship: Model for RELATED-TO relationships
- TaskManager: Query and manage task hierarchies
- Task filtering utilities for STATUS, PERCENT-COMPLETE, etc.
"""

from radicale.vtodo.relationships import (
    TaskRelationship,
    RelationType,
    extract_relationships,
    find_related_tasks,
)
from radicale.vtodo.properties import (
    get_task_properties,
    TASK_SUPPORTED_PROPERTIES,
)

__all__ = [
    "TaskRelationship",
    "RelationType",
    "extract_relationships",
    "find_related_tasks",
    "get_task_properties",
    "TASK_SUPPORTED_PROPERTIES",
]
