"""
Task Relationship Handling for RFC 9253 Task Extensions.

Supports RELATED-TO property with relationship types:
- PARENT: This task is a subtask of the related task
- CHILD: The related task is a subtask of this task
- SIBLING: Tasks share the same parent
- DEPENDS-ON: This task depends on the related task (RFC 9253)
- REFID: Reference identifier relationship (RFC 9253)

Example VTODO with relationships:
    BEGIN:VTODO
    UID:subtask-1
    SUMMARY:Write code
    RELATED-TO;RELTYPE=PARENT:project-1
    RELATED-TO;RELTYPE=DEPENDS-ON:review-1
    END:VTODO
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set
import vobject


class RelationType(Enum):
    """RFC 5545/9253 relationship types."""
    PARENT = "PARENT"       # This task is subtask of related
    CHILD = "CHILD"         # Related task is subtask of this
    SIBLING = "SIBLING"     # Tasks share same parent
    DEPENDS_ON = "DEPENDS-ON"  # RFC 9253: Dependency relationship
    REFID = "REFID"         # RFC 9253: Reference ID
    X_UNKNOWN = "X-UNKNOWN"  # Unknown/custom relationship


@dataclass
class TaskRelationship:
    """
    Represents a task relationship.

    Attributes:
        task_uid: UID of the task containing this relationship
        related_uid: UID of the related task
        rel_type: Type of relationship (PARENT, CHILD, DEPENDS-ON, etc.)
    """
    task_uid: str
    related_uid: str
    rel_type: RelationType

    def is_blocking(self) -> bool:
        """Check if this relationship blocks the task."""
        return self.rel_type == RelationType.DEPENDS_ON

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "task_uid": self.task_uid,
            "related_uid": self.related_uid,
            "rel_type": self.rel_type.value
        }


def parse_reltype(reltype_str: Optional[str]) -> RelationType:
    """
    Parse RELTYPE parameter value to RelationType enum.

    Args:
        reltype_str: The RELTYPE parameter value (e.g., "PARENT", "DEPENDS-ON")

    Returns:
        RelationType enum value, defaults to PARENT if not specified
    """
    if not reltype_str:
        return RelationType.PARENT  # RFC 5545 default

    reltype_upper = reltype_str.upper().strip()

    # Handle hyphenated values
    if reltype_upper == "DEPENDS-ON":
        return RelationType.DEPENDS_ON

    try:
        return RelationType[reltype_upper.replace("-", "_")]
    except KeyError:
        return RelationType.X_UNKNOWN


def extract_relationships(vtodo: vobject.base.Component) -> List[TaskRelationship]:
    """
    Extract all RELATED-TO relationships from a VTODO component.

    Args:
        vtodo: A vobject VTODO component

    Returns:
        List of TaskRelationship objects
    """
    relationships = []

    uid = getattr(vtodo, "uid", None)
    if not uid:
        return relationships

    task_uid = str(uid.value)

    # Handle multiple RELATED-TO properties
    related_tos = []
    if hasattr(vtodo, "related_to"):
        related = vtodo.related_to
        # Handle both single and multiple RELATED-TO properties
        if hasattr(related, "__iter__") and not isinstance(related, str):
            related_tos = list(related)
        else:
            related_tos = [related]
    if hasattr(vtodo, "related_to_list"):
        related_tos = vtodo.related_to_list

    for related_to in related_tos:
        if not related_to:
            continue

        related_uid = str(related_to.value) if hasattr(related_to, "value") else str(related_to)

        # Extract RELTYPE parameter
        reltype_str = None
        if hasattr(related_to, "params") and related_to.params:
            reltype_param = related_to.params.get("RELTYPE", [])
            if reltype_param:
                reltype_str = reltype_param[0] if isinstance(reltype_param, list) else reltype_param

        rel_type = parse_reltype(reltype_str)

        relationships.append(TaskRelationship(
            task_uid=task_uid,
            related_uid=related_uid,
            rel_type=rel_type
        ))

    return relationships


def find_related_tasks(
    items: List[vobject.base.Component],
    target_uid: str,
    rel_type: Optional[RelationType] = None,
    direction: str = "both"
) -> List[TaskRelationship]:
    """
    Find all tasks related to a target task.

    Args:
        items: List of vobject items to search
        target_uid: UID of the task to find relationships for
        rel_type: Optional filter by relationship type
        direction: "outgoing" (from target), "incoming" (to target), or "both"

    Returns:
        List of TaskRelationship objects
    """
    results = []

    for item in items:
        # Extract VTODOs from calendar items
        vtodos = []
        if hasattr(item, "vtodo"):
            vtodos.append(item.vtodo)
        if hasattr(item, "vtodo_list"):
            vtodos.extend(item.vtodo_list)
        if item.name == "VTODO":
            vtodos.append(item)

        for vtodo in vtodos:
            relationships = extract_relationships(vtodo)

            for rel in relationships:
                matches_type = rel_type is None or rel.rel_type == rel_type

                if matches_type:
                    # Outgoing: target task has RELATED-TO pointing elsewhere
                    if direction in ("outgoing", "both") and rel.task_uid == target_uid:
                        results.append(rel)

                    # Incoming: another task has RELATED-TO pointing to target
                    if direction in ("incoming", "both") and rel.related_uid == target_uid:
                        # Create inverse relationship
                        inverse_type = _invert_relationship_type(rel.rel_type)
                        results.append(TaskRelationship(
                            task_uid=rel.related_uid,
                            related_uid=rel.task_uid,
                            rel_type=inverse_type
                        ))

    return results


def _invert_relationship_type(rel_type: RelationType) -> RelationType:
    """
    Get the inverse relationship type.

    PARENT <-> CHILD, others remain the same.
    """
    if rel_type == RelationType.PARENT:
        return RelationType.CHILD
    if rel_type == RelationType.CHILD:
        return RelationType.PARENT
    return rel_type


def build_task_hierarchy(
    items: List[vobject.base.Component]
) -> Dict[str, Dict[str, Set[str]]]:
    """
    Build a complete task hierarchy from a collection.

    Returns a structure:
    {
        "task-uid-1": {
            "parents": {"parent-uid"},
            "children": {"child-uid-1", "child-uid-2"},
            "dependencies": {"dep-uid-1"},
            "dependents": {"dependent-uid-1"}
        }
    }
    """
    hierarchy: Dict[str, Dict[str, Set[str]]] = {}

    def ensure_task(uid: str):
        if uid not in hierarchy:
            hierarchy[uid] = {
                "parents": set(),
                "children": set(),
                "dependencies": set(),
                "dependents": set()
            }

    for item in items:
        vtodos = []
        if hasattr(item, "vtodo"):
            vtodos.append(item.vtodo)
        if hasattr(item, "vtodo_list"):
            vtodos.extend(item.vtodo_list)
        if hasattr(item, "name") and item.name == "VTODO":
            vtodos.append(item)

        for vtodo in vtodos:
            uid_prop = getattr(vtodo, "uid", None)
            if not uid_prop:
                continue

            task_uid = str(uid_prop.value)
            ensure_task(task_uid)

            for rel in extract_relationships(vtodo):
                ensure_task(rel.related_uid)

                if rel.rel_type == RelationType.PARENT:
                    hierarchy[task_uid]["parents"].add(rel.related_uid)
                    hierarchy[rel.related_uid]["children"].add(task_uid)
                elif rel.rel_type == RelationType.CHILD:
                    hierarchy[task_uid]["children"].add(rel.related_uid)
                    hierarchy[rel.related_uid]["parents"].add(task_uid)
                elif rel.rel_type == RelationType.DEPENDS_ON:
                    hierarchy[task_uid]["dependencies"].add(rel.related_uid)
                    hierarchy[rel.related_uid]["dependents"].add(task_uid)

    return hierarchy


def get_root_tasks(hierarchy: Dict[str, Dict[str, Set[str]]]) -> List[str]:
    """Get tasks with no parents (top-level tasks)."""
    return [uid for uid, info in hierarchy.items() if not info["parents"]]


def get_leaf_tasks(hierarchy: Dict[str, Dict[str, Set[str]]]) -> List[str]:
    """Get tasks with no children (lowest-level tasks)."""
    return [uid for uid, info in hierarchy.items() if not info["children"]]


def get_blocked_tasks(hierarchy: Dict[str, Dict[str, Set[str]]]) -> List[str]:
    """Get tasks that have incomplete dependencies."""
    # This would need to check STATUS of dependencies
    return [uid for uid, info in hierarchy.items() if info["dependencies"]]


def validate_no_cycles(hierarchy: Dict[str, Dict[str, Set[str]]]) -> bool:
    """
    Validate that the task hierarchy has no cycles.

    Cycles in parent/child or dependency relationships would be invalid.
    """
    visited = set()
    rec_stack = set()

    def dfs(uid: str, edges_key: str) -> bool:
        visited.add(uid)
        rec_stack.add(uid)

        for related in hierarchy.get(uid, {}).get(edges_key, set()):
            if related not in visited:
                if not dfs(related, edges_key):
                    return False
            elif related in rec_stack:
                return False

        rec_stack.remove(uid)
        return True

    for uid in hierarchy:
        if uid not in visited:
            # Check parent-child cycles
            if not dfs(uid, "children"):
                return False

    visited.clear()
    rec_stack.clear()

    for uid in hierarchy:
        if uid not in visited:
            # Check dependency cycles
            if not dfs(uid, "dependencies"):
                return False

    return True
