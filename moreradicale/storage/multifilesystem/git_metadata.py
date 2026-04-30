# This file is part of Radicale - CalDAV and CardDAV server
# Copyright © 2025 RFC 3253 Versioning Implementation
#
# This library is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Radicale.  If not, see <http://www.gnu.org/licenses/>.

"""
Git metadata reader for RFC 3253 versioning support.

This module provides read-only access to git history for exposing
version-control properties via WebDAV. It requires git to be installed
and the storage folder to be a git repository.

Properties exposed (per RFC 3253):
- DAV:version-history - Link to version history resource
- DAV:checked-in - Current version URL
- DAV:version-name - Human-readable version identifier (git SHA)
- DAV:creator-displayname - Who made the change
- DAV:getlastmodified - Version timestamp
"""

import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class VersionInfo:
    """Information about a single version from git history."""
    sha: str  # Full git commit SHA
    short_sha: str  # Short SHA (8 chars)
    author: str  # Author name
    author_email: str  # Author email
    timestamp: datetime  # Commit timestamp
    message: str  # Commit message (first line)
    predecessor_sha: Optional[str] = None  # Parent commit SHA (RFC 3253 predecessor)
    successor_shas: Optional[List[str]] = None  # Child commit SHAs (RFC 3253 successor)
    labels: Optional[List[str]] = None  # RFC 3253 labels (git tags) pointing to this version

    @property
    def version_name(self) -> str:
        """Return human-readable version name (short SHA)."""
        return self.short_sha


class GitMetadataReader:
    """
    Read git history for version-control properties.

    This class provides read-only access to git metadata for implementing
    RFC 3253 versioning. It does not modify the repository.
    """

    def __init__(self, storage_folder: str, max_history: int = 100):
        """
        Initialize git metadata reader.

        Args:
            storage_folder: Path to the storage folder (git repository root)
            max_history: Maximum number of versions to return per item
        """
        self.storage_folder = storage_folder
        self.max_history = max_history
        self._git_available: Optional[bool] = None
        self._is_git_repo: Optional[bool] = None

    def _run_git(self, args: List[str], cwd: Optional[str] = None
                 ) -> Tuple[bool, str]:
        """
        Run a git command and return output.

        Args:
            args: Git command arguments (without 'git' prefix)
            cwd: Working directory (defaults to storage_folder)

        Returns:
            Tuple of (success, output_or_error)
        """
        if cwd is None:
            cwd = self.storage_folder

        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30  # 30 second timeout
            )
            if result.returncode == 0:
                return True, result.stdout.strip()
            else:
                return False, result.stderr.strip()
        except subprocess.TimeoutExpired:
            logger.warning("Git command timed out: git %s", " ".join(args))
            return False, "Command timed out"
        except FileNotFoundError:
            return False, "git command not found"
        except Exception as e:
            logger.warning("Git command failed: %s", e)
            return False, str(e)

    def is_available(self) -> bool:
        """
        Check if git is available and storage is a git repository.

        Returns:
            True if versioning is available
        """
        if self._git_available is None:
            # Check if git command exists
            success, _ = self._run_git(["--version"])
            self._git_available = success

        if not self._git_available:
            return False

        if self._is_git_repo is None:
            # Check if storage folder is a git repository
            success, _ = self._run_git(
                ["rev-parse", "--git-dir"],
                cwd=self.storage_folder
            )
            self._is_git_repo = success

        return self._is_git_repo

    def get_item_history(self, relative_path: str) -> List[VersionInfo]:
        """
        Get version history for a specific item.

        Args:
            relative_path: Path relative to storage folder (e.g.,
                          "collection-root/user/calendar.ics/event.ics")

        Returns:
            List of VersionInfo objects, most recent first
        """
        if not self.is_available():
            return []

        # Use git log with --follow for rename tracking
        # Format: SHA|author|email|timestamp|subject
        format_str = "%H|%an|%ae|%aI|%s"

        success, output = self._run_git([
            "log",
            "--follow",
            f"--max-count={self.max_history}",
            f"--format={format_str}",
            "--",
            relative_path
        ])

        if not success or not output:
            return []

        versions = []
        for line in output.splitlines():
            if not line.strip():
                continue

            parts = line.split("|", 4)
            if len(parts) < 5:
                logger.debug("Skipping malformed git log line: %s", line)
                continue

            sha, author, email, timestamp_str, message = parts

            try:
                # Parse ISO 8601 timestamp
                timestamp = datetime.fromisoformat(timestamp_str)
            except ValueError:
                logger.debug("Failed to parse timestamp: %s", timestamp_str)
                timestamp = datetime.now()

            versions.append(VersionInfo(
                sha=sha,
                short_sha=sha[:8],
                author=author,
                author_email=email,
                timestamp=timestamp,
                message=message
            ))

        return versions

    def get_current_version(self, relative_path: str) -> Optional[VersionInfo]:
        """
        Get the current (most recent) version of an item.

        Args:
            relative_path: Path relative to storage folder

        Returns:
            VersionInfo for current version, or None if no history
        """
        history = self.get_item_history(relative_path)
        return history[0] if history else None

    def get_version_content(self, relative_path: str,
                            version_sha: str) -> Optional[str]:
        """
        Get item content at a specific version.

        Args:
            relative_path: Path relative to storage folder
            version_sha: Git commit SHA (short or full)

        Returns:
            File content at that version, or None if not found
        """
        if not self.is_available():
            return None

        # Validate SHA format (prevent command injection)
        if not version_sha.isalnum():
            logger.warning("Invalid version SHA: %s", version_sha)
            return None

        success, content = self._run_git([
            "show",
            f"{version_sha}:{relative_path}"
        ])

        if success:
            return content
        return None

    def get_collection_history(self, collection_path: str
                               ) -> List[Tuple[str, VersionInfo]]:
        """
        Get combined history for all items in a collection.

        Args:
            collection_path: Path to collection folder

        Returns:
            List of (relative_item_path, VersionInfo) tuples, most recent first
        """
        if not self.is_available():
            return []

        # Get log for entire collection directory
        format_str = "%H|%an|%ae|%aI|%s"

        success, output = self._run_git([
            "log",
            f"--max-count={self.max_history}",
            f"--format={format_str}",
            "--name-only",
            "--",
            collection_path
        ])

        if not success or not output:
            return []

        results = []
        current_version = None
        current_files = []

        for line in output.splitlines():
            if not line.strip():
                # Empty line separates commits - save previous if any
                if current_version and current_files:
                    for f in current_files:
                        if f.startswith(collection_path):
                            results.append((f, current_version))
                current_files = []
                continue

            if "|" in line:
                # This is a commit line
                parts = line.split("|", 4)
                if len(parts) >= 5:
                    sha, author, email, timestamp_str, message = parts
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str)
                    except ValueError:
                        timestamp = datetime.now()

                    current_version = VersionInfo(
                        sha=sha,
                        short_sha=sha[:8],
                        author=author,
                        author_email=email,
                        timestamp=timestamp,
                        message=message
                    )
            else:
                # This is a file path
                current_files.append(line)

        # Handle last commit
        if current_version and current_files:
            for f in current_files:
                if f.startswith(collection_path):
                    results.append((f, current_version))

        return results

    def version_exists(self, version_sha: str) -> bool:
        """
        Check if a version (commit) exists.

        Args:
            version_sha: Git commit SHA to check

        Returns:
            True if version exists
        """
        if not self.is_available():
            return False

        if not version_sha.isalnum():
            return False

        success, _ = self._run_git([
            "cat-file", "-t", version_sha
        ])
        return success

    def get_predecessor(self, version_sha: str) -> Optional[str]:
        """
        Get the predecessor (parent commit) of a version.

        Per RFC 3253 §3.3.2, DAV:predecessor-set identifies the
        versions from which this version was derived.

        Args:
            version_sha: Git commit SHA

        Returns:
            Parent commit SHA, or None if no parent (initial commit)
        """
        if not self.is_available():
            return None

        if not version_sha.isalnum():
            return None

        # Get parent commit SHA
        success, output = self._run_git([
            "rev-parse", f"{version_sha}^"
        ])

        if success and output:
            return output.strip()
        return None

    def get_successors(self, version_sha: str,
                       relative_path: Optional[str] = None) -> List[str]:
        """
        Get the successors (child commits) of a version.

        Per RFC 3253 §3.3.3, DAV:successor-set identifies the
        versions that were derived from this version.

        Args:
            version_sha: Git commit SHA
            relative_path: Optional path to filter commits that modified this file

        Returns:
            List of child commit SHAs
        """
        if not self.is_available():
            return []

        if not version_sha.isalnum():
            return []

        # Find commits whose parent is this version
        # Use --ancestry-path to find all descendants, then filter to direct children
        args = [
            "rev-list",
            "--children",
            f"{version_sha}..HEAD"
        ]

        if relative_path:
            args.extend(["--", relative_path])

        success, output = self._run_git(args)

        if not success or not output:
            return []

        # Parse output to find direct children of version_sha
        # Format: "commit_sha child1 child2 ..."
        successors = []
        for line in output.splitlines():
            parts = line.split()
            if parts and parts[0] == version_sha and len(parts) > 1:
                # This line lists version_sha and its children
                successors.extend(parts[1:])

        return successors

    def get_version_with_relationships(self, relative_path: str,
                                       version_sha: str) -> Optional[VersionInfo]:
        """
        Get version info with predecessor/successor relationships populated.

        Args:
            relative_path: Path to the item
            version_sha: Git commit SHA

        Returns:
            VersionInfo with predecessor_sha and successor_shas populated
        """
        if not self.is_available():
            return None

        if not version_sha.isalnum():
            return None

        # Get basic version info
        format_str = "%H|%an|%ae|%aI|%s"
        success, output = self._run_git([
            "log",
            "-1",
            f"--format={format_str}",
            version_sha
        ])

        if not success or not output:
            return None

        parts = output.split("|", 4)
        if len(parts) < 5:
            return None

        sha, author, email, timestamp_str, message = parts

        try:
            timestamp = datetime.fromisoformat(timestamp_str)
        except ValueError:
            timestamp = datetime.now()

        # Get predecessor and successors
        predecessor = self.get_predecessor(version_sha)
        successors = self.get_successors(version_sha, relative_path)

        return VersionInfo(
            sha=sha,
            short_sha=sha[:8],
            author=author,
            author_email=email,
            timestamp=timestamp,
            message=message,
            predecessor_sha=predecessor,
            successor_shas=successors if successors else None
        )

    def get_labels_for_commit(self, commit_sha: str, relative_path: Optional[str] = None) -> List[str]:
        """
        Get all labels (git tags) pointing to a specific commit.

        RFC 3253 §8: Labels are names that can be used to select a version.
        We implement this using git tags.

        Args:
            commit_sha: Git commit SHA
            relative_path: Optional file path to filter labels

        Returns:
            List of label names
        """
        if not self.is_available():
            return []

        # Validate SHA
        if not commit_sha or not commit_sha.isalnum():
            return []

        # Get all tags pointing to this commit
        success, output = self._run_git([
            "tag", "--points-at", commit_sha
        ])

        if not success or not output:
            return []

        tags = output.strip().splitlines()

        # If relative_path specified, filter to tags for this file
        # We use tag naming convention: <relative-path>/<label-name>
        # Example: user/calendar.ics/event.ics/production
        if relative_path:
            normalized_path = relative_path.strip("/")
            filtered_tags = []
            for tag in tags:
                # Check if tag starts with file path
                if tag.startswith(f"{normalized_path}/"):
                    # Extract label name (part after path)
                    label_name = tag[len(normalized_path) + 1:]
                    filtered_tags.append(label_name)
            return filtered_tags

        return tags

    def get_commit_for_label(self, label_name: str, relative_path: Optional[str] = None) -> Optional[str]:
        """
        Get the commit SHA for a given label.

        Args:
            label_name: Label name to look up
            relative_path: Optional file path (tags are namespaced by path)

        Returns:
            Commit SHA or None if label not found
        """
        if not self.is_available():
            return None

        # Validate label name (alphanumeric, dash, underscore, dot)
        if not all(c.isalnum() or c in "-_." for c in label_name):
            logger.warning("Invalid label name: %s", label_name)
            return None

        # Build full tag name
        if relative_path:
            normalized_path = relative_path.strip("/")
            tag_name = f"{normalized_path}/{label_name}"
        else:
            tag_name = label_name

        # Get commit for tag
        success, output = self._run_git([
            "rev-list", "-n", "1", tag_name
        ])

        if success and output:
            return output.strip()
        return None

    def list_all_labels(self, relative_path: Optional[str] = None) -> List[Tuple[str, str]]:
        """
        List all labels in the repository.

        Args:
            relative_path: Optional file path to filter labels

        Returns:
            List of (label_name, commit_sha) tuples
        """
        if not self.is_available():
            return []

        # Get all tags with commit SHAs
        success, output = self._run_git([
            "tag", "-l", "--format=%(refname:short)|%(objectname)"
        ])

        if not success or not output:
            return []

        results = []
        for line in output.strip().splitlines():
            if "|" not in line:
                continue

            tag_name, commit_sha = line.split("|", 1)

            # Filter by relative_path if specified
            if relative_path:
                normalized_path = relative_path.strip("/")
                if tag_name.startswith(f"{normalized_path}/"):
                    label_name = tag_name[len(normalized_path) + 1:]
                    results.append((label_name, commit_sha))
            else:
                results.append((tag_name, commit_sha))

        return results
