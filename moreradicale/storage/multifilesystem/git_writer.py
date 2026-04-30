# This file is part of Radicale - CalDAV and CardDAV server
# Copyright 2025 RFC 3253 Versioning Implementation
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
Git metadata writer for RFC 3253 versioning write support.

This module provides write operations for git-backed versioning:
- CHECKIN: Create new versions (git commits)
- UNCHECKOUT: Restore previous versions
- VERSION-CONTROL: Initialize version tracking

Works alongside git_metadata.py (read operations) to provide
full RFC 3253 DeltaV compliance.
"""

import logging
import os
import subprocess
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class GitMetadataWriter:
    """
    Write operations for git-backed versioning.

    This class handles git mutations for RFC 3253 write methods:
    - CHECKIN creates new commits
    - UNCHECKOUT restores from commits
    - VERSION-CONTROL initializes git tracking
    """

    def __init__(self, storage_folder: str):
        """
        Initialize git metadata writer.

        Args:
            storage_folder: Path to the storage folder (git repository root)
        """
        self.storage_folder = storage_folder
        self._git_available: Optional[bool] = None
        self._is_git_repo: Optional[bool] = None

    def _run_git(self, args: List[str], cwd: Optional[str] = None,
                 env: Optional[dict] = None) -> Tuple[bool, str]:
        """
        Run a git command and return output.

        Args:
            args: Git command arguments (without 'git' prefix)
            cwd: Working directory (defaults to storage_folder)
            env: Additional environment variables

        Returns:
            Tuple of (success, output_or_error)
        """
        if cwd is None:
            cwd = self.storage_folder

        git_env = os.environ.copy()
        if env:
            git_env.update(env)

        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30,  # 30 second timeout
                env=git_env
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
            success, _ = self._run_git(["--version"])
            self._git_available = success

        if not self._git_available:
            return False

        if self._is_git_repo is None:
            success, _ = self._run_git(
                ["rev-parse", "--git-dir"],
                cwd=self.storage_folder
            )
            self._is_git_repo = success

        return self._is_git_repo

    def is_version_controlled(self, relative_path: str) -> bool:
        """
        Check if a file is tracked by git.

        Args:
            relative_path: Path relative to storage folder

        Returns:
            True if file is under version control
        """
        if not self.is_available():
            return False

        success, _ = self._run_git([
            "ls-files", "--error-unmatch", relative_path
        ])
        return success

    def create_version(self, relative_path: str, author: str,
                       author_email: str, message: str) -> Optional[str]:
        """
        Create a new version (git commit) for a file.

        The file should already be modified on disk. This method
        stages and commits the changes.

        Args:
            relative_path: Path relative to storage folder
            author: Author name for the commit
            author_email: Author email for the commit
            message: Commit message

        Returns:
            New version SHA, or None on failure
        """
        if not self.is_available():
            logger.warning("Git not available for version creation")
            return None

        # Stage the file
        success, error = self._run_git(["add", relative_path])
        if not success:
            logger.warning("Failed to stage file %s: %s", relative_path, error)
            return None

        # Check if there are actually changes to commit
        success, diff_output = self._run_git([
            "diff", "--cached", "--quiet", relative_path
        ])
        if success:
            # No changes - file is already at this version
            logger.debug("No changes to commit for %s", relative_path)
            # Return current HEAD as the version
            success, head_sha = self._run_git(["rev-parse", "HEAD"])
            return head_sha if success else None

        # Create commit with author info
        author_str = f"{author} <{author_email}>"
        success, output = self._run_git([
            "commit",
            f"--author={author_str}",
            "-m", message,
            "--", relative_path
        ])

        if not success:
            logger.warning("Failed to commit %s: %s", relative_path, output)
            return None

        # Get the new commit SHA
        success, sha = self._run_git(["rev-parse", "HEAD"])
        if success:
            logger.info("Created version %s for %s", sha[:8], relative_path)
            return sha

        return None

    def restore_version(self, relative_path: str,
                        version_sha: str) -> Optional[str]:
        """
        Restore file to a specific version and return content.

        This restores the file content from the specified commit
        to the working directory. Does NOT create a new commit.

        Args:
            relative_path: Path relative to storage folder
            version_sha: Git commit SHA (short or full)

        Returns:
            Restored file content, or None on failure
        """
        if not self.is_available():
            return None

        # Validate SHA format (prevent command injection)
        if not version_sha.isalnum():
            logger.warning("Invalid version SHA: %s", version_sha)
            return None

        # Get content at that version
        success, content = self._run_git([
            "show", f"{version_sha}:{relative_path}"
        ])

        if not success:
            logger.warning(
                "Failed to get content at %s for %s: %s",
                version_sha, relative_path, content
            )
            return None

        # Write content to working directory
        full_path = os.path.join(self.storage_folder, relative_path)
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(
                "Restored %s to version %s",
                relative_path, version_sha[:8]
            )
            return content
        except OSError as e:
            logger.warning("Failed to write restored content: %s", e)
            return None

    def initialize_version_control(self, relative_path: str, author: str,
                                   author_email: str) -> Optional[str]:
        """
        Place a file under version control.

        Creates an initial commit for a file that isn't yet tracked.

        Args:
            relative_path: Path relative to storage folder
            author: Author name for the initial commit
            author_email: Author email

        Returns:
            Initial version SHA, or None on failure
        """
        if not self.is_available():
            logger.warning("Git not available")
            return None

        if self.is_version_controlled(relative_path):
            logger.debug("%s already under version control", relative_path)
            # Return current version
            success, sha = self._run_git([
                "log", "-1", "--format=%H", "--", relative_path
            ])
            return sha if success else None

        # Check if file exists
        full_path = os.path.join(self.storage_folder, relative_path)
        if not os.path.exists(full_path):
            logger.warning("Cannot version-control non-existent file: %s",
                           relative_path)
            return None

        # Stage and commit
        message = f"Initial version control for {os.path.basename(relative_path)}"
        return self.create_version(
            relative_path, author, author_email, message
        )

    def discard_changes(self, relative_path: str,
                        to_version: Optional[str] = None) -> bool:
        """
        Discard uncommitted changes to a file.

        Args:
            relative_path: Path relative to storage folder
            to_version: Version to restore to (default: HEAD)

        Returns:
            True on success
        """
        if not self.is_available():
            return False

        version = to_version or "HEAD"
        if to_version and not to_version.isalnum():
            logger.warning("Invalid version SHA: %s", to_version)
            return False

        # Use git checkout to restore file
        success, error = self._run_git([
            "checkout", version, "--", relative_path
        ])

        if success:
            logger.info("Discarded changes to %s (restored to %s)",
                        relative_path, version[:8] if to_version else "HEAD")
            return True

        logger.warning("Failed to discard changes: %s", error)
        return False

    def get_head_sha(self) -> Optional[str]:
        """Get the current HEAD commit SHA."""
        if not self.is_available():
            return None

        success, sha = self._run_git(["rev-parse", "HEAD"])
        return sha if success else None

    def add_label(self, label_name: str, commit_sha: str, relative_path: Optional[str] = None, 
                  force: bool = False) -> bool:
        """
        Add a label (git tag) to a specific commit.
        
        RFC 3253 §8.1: ADD operation adds a label to a version.
        
        Args:
            label_name: Name of the label to add
            commit_sha: Commit SHA to label
            relative_path: Optional file path (for namespacing tags)
            force: If True, move existing label (set operation)
        
        Returns:
            True if successful
        """
        if not self.is_available():
            logger.warning("Git not available for label operations")
            return False
        
        # Validate inputs
        if not all(c.isalnum() or c in "-_." for c in label_name):
            logger.warning("Invalid label name: %s", label_name)
            return False
        
        if not commit_sha or not commit_sha.isalnum():
            logger.warning("Invalid commit SHA: %s", commit_sha)
            return False
        
        # Build full tag name
        if relative_path:
            normalized_path = relative_path.strip("/")
            tag_name = f"{normalized_path}/{label_name}"
        else:
            tag_name = label_name
        
        # Create lightweight tag
        args = ["tag"]
        if force:
            args.append("-f")  # Force overwrites existing tag
        args.extend([tag_name, commit_sha])
        
        success, output = self._run_git(args)
        
        if success:
            logger.info(f"Label '{label_name}' added to commit {commit_sha[:8]} (path: {relative_path or 'global'})")
            return True
        else:
            logger.warning(f"Failed to add label '{label_name}': {output}")
            return False
    
    def remove_label(self, label_name: str, relative_path: Optional[str] = None) -> bool:
        """
        Remove a label (delete git tag).
        
        RFC 3253 §8.3: REMOVE operation removes a label from all versions.
        
        Args:
            label_name: Name of the label to remove
            relative_path: Optional file path (for namespaced tags)
        
        Returns:
            True if successful
        """
        if not self.is_available():
            logger.warning("Git not available for label operations")
            return False
        
        # Validate label name
        if not all(c.isalnum() or c in "-_." for c in label_name):
            logger.warning("Invalid label name: %s", label_name)
            return False
        
        # Build full tag name
        if relative_path:
            normalized_path = relative_path.strip("/")
            tag_name = f"{normalized_path}/{label_name}"
        else:
            tag_name = label_name
        
        # Delete tag
        success, output = self._run_git(["tag", "-d", tag_name])
        
        if success:
            logger.info(f"Label '{label_name}' removed (path: {relative_path or 'global'})")
            return True
        else:
            # Tag might not exist - that's okay for REMOVE
            if "not found" in output.lower():
                logger.debug(f"Label '{label_name}' does not exist (already removed)")
                return True
            logger.warning(f"Failed to remove label '{label_name}': {output}")
            return False
    
    def set_label(self, label_name: str, commit_sha: str, relative_path: Optional[str] = None) -> bool:
        """
        Set a label to a specific commit (remove from others, add to this one).
        
        RFC 3253 §8.2: SET operation moves a label to a different version.
        
        Args:
            label_name: Name of the label
            commit_sha: Commit SHA to assign label to
            relative_path: Optional file path (for namespacing tags)
        
        Returns:
            True if successful
        """
        # SET is just ADD with force=True (git tag -f)
        return self.add_label(label_name, commit_sha, relative_path, force=True)
    
    def get_labels_for_commit(self, commit_sha: str, relative_path: Optional[str] = None) -> List[str]:
        """
        Get all labels pointing to a commit.
        
        This is a convenience method - delegates to GitMetadataReader.
        
        Args:
            commit_sha: Commit SHA
            relative_path: Optional file path filter
        
        Returns:
            List of label names
        """
        if not self.is_available():
            return []
        
        # Build full tag name filter if path specified
        if relative_path:
            normalized_path = relative_path.strip("/")
            tag_pattern = f"{normalized_path}/*"
        else:
            tag_pattern = "*"
        
        # Get tags pointing to commit
        success, output = self._run_git([
            "tag", "--points-at", commit_sha, "-l", tag_pattern
        ])
        
        if not success or not output:
            return []
        
        tags = output.strip().splitlines()
        
        # Strip path prefix if present
        if relative_path:
            normalized_path = relative_path.strip("/")
            return [tag[len(normalized_path) + 1:] if tag.startswith(f"{normalized_path}/") else tag 
                    for tag in tags]
        
        return tags
