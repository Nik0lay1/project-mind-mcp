import json
import shutil
from datetime import datetime
from pathlib import Path

from config import MEMORY_FILE, MEMORY_HISTORY_DIR
from logger import get_logger

logger = get_logger()


class MemoryManager:
    """
    Manages project memory operations.
    Encapsulates reading, updating, versioning of memory.md file.
    """

    def __init__(self, memory_file: Path = MEMORY_FILE):
        """
        Initialize memory manager.

        Args:
            memory_file: Path to memory file
        """
        self.memory_file = memory_file
        self.history_dir = MEMORY_HISTORY_DIR

    def read(self, max_lines: int | None = 100) -> str:
        """
        Reads current memory content.

        Args:
            max_lines: Maximum number of lines to return. None for full content.
                      Default is 100 to avoid overwhelming context windows.

        Returns:
            Memory content or error message
        """
        if not self.memory_file.exists():
            return "Memory file not found."

        try:
            content = self.memory_file.read_text()
            
            if max_lines is None:
                return content
            
            lines = content.split('\n')
            if len(lines) <= max_lines:
                return content
            
            truncated = '\n'.join(lines[:max_lines])
            remaining = len(lines) - max_lines
            return f"{truncated}\n\n... ({remaining} more lines truncated. Use read_memory(max_lines=None) for full content)"
        except Exception as e:
            logger.error(f"Error reading memory: {e}")
            return f"Error reading memory: {e}"

    def update(self, content: str, section: str = "Recent Decisions") -> str:
        """
        Appends new content to memory file.

        Args:
            content: Content to append
            section: Section name for the update

        Returns:
            Success message or error
        """
        if not self.memory_file.exists():
            return "Memory file not found."

        if not content or not content.strip():
            return "Error: Content cannot be empty."

        try:
            new_entry = f"\n\n### Update ({section})\n{content}"

            with open(self.memory_file, "a") as f:
                f.write(new_entry)

            logger.info(f"Memory updated: {section}")
            return "Memory updated successfully."
        except Exception as e:
            logger.error(f"Error updating memory: {e}")
            return f"Error updating memory: {e}"

    def clear(self, keep_template: bool = True) -> str:
        """
        Clears memory file content.

        Args:
            keep_template: If True, preserves template structure

        Returns:
            Success message or error
        """
        if not self.memory_file.exists():
            return "Memory file not found."

        try:
            if keep_template:
                template = """# Project Memory

## Status
- [ ] Initial Setup

## Tech Stack
- Language: Python
- Framework:

## Recent Decisions
- Memory cleared.
"""
                self.memory_file.write_text(template)
                logger.info("Memory cleared (template preserved)")
                return "Memory cleared (template preserved)."
            else:
                self.memory_file.write_text("")
                logger.info("Memory completely cleared")
                return "Memory completely cleared."
        except Exception as e:
            logger.error(f"Error clearing memory: {e}")
            return f"Error clearing memory: {e}"

    def delete_section(self, section_name: str) -> str:
        """
        Deletes a specific section from memory.

        Args:
            section_name: Name of section to delete

        Returns:
            Success message or error
        """
        if not self.memory_file.exists():
            return "Memory file not found."

        if not section_name or not section_name.strip():
            return "Error: Section name cannot be empty."

        try:
            content = self.memory_file.read_text()
            lines = content.split("\n")
            new_lines = []
            skip = False

            for line in lines:
                if line.startswith("##") and section_name.lower() in line.lower():
                    skip = True
                    continue
                elif line.startswith("##") and skip:
                    skip = False

                if not skip:
                    new_lines.append(line)

            self.memory_file.write_text("\n".join(new_lines))
            logger.info(f"Section '{section_name}' deleted")
            return f"Section '{section_name}' deleted successfully."
        except Exception as e:
            logger.error(f"Error deleting section: {e}")
            return f"Error deleting section: {e}"

    def save_version(self, description: str = "") -> str:
        """
        Saves a versioned copy of the current memory.

        Args:
            description: Optional description of this version

        Returns:
            Success message with version name or error
        """
        if not self.memory_file.exists():
            return "Memory file not found"

        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            version_file = self.history_dir / f"memory_{timestamp}.md"

            shutil.copy2(self.memory_file, version_file)

            metadata_file = self.history_dir / f"memory_{timestamp}.meta.json"
            metadata = {
                "timestamp": timestamp,
                "description": description,
                "created_at": datetime.now().isoformat(),
            }

            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"Memory version saved: {version_file.name}")
            return f"Memory version saved: {version_file.name}"
        except Exception as e:
            logger.error(f"Error saving version: {e}")
            return f"Error saving version: {e}"

    def list_versions(self) -> str:
        """
        Lists all saved memory versions.

        Returns:
            Formatted list of versions or error
        """
        if not self.history_dir.exists():
            return "No memory versions found"

        try:
            versions = []
            for meta_file in sorted(self.history_dir.glob("*.meta.json"), reverse=True):
                try:
                    with open(meta_file) as f:
                        meta = json.load(f)

                    timestamp = meta.get("timestamp", "unknown")
                    description = meta.get("description", "")
                    created = meta.get("created_at", "")

                    version_info = f"- **{timestamp}**"
                    if description:
                        version_info += f": {description}"
                    if created:
                        version_info += f" ({created})"

                    versions.append(version_info)
                except Exception:
                    continue

            if not versions:
                return "No memory versions found"

            return "# MEMORY VERSIONS\n\n" + "\n".join(versions)
        except Exception as e:
            logger.error(f"Error listing versions: {e}")
            return f"Error listing versions: {e}"

    def restore_version(self, timestamp: str) -> str:
        """
        Restores a specific memory version.

        Args:
            timestamp: Timestamp of version to restore

        Returns:
            Success message or error
        """
        if not self.history_dir.exists():
            return "No memory versions found"

        try:
            version_file = self.history_dir / f"memory_{timestamp}.md"

            if not version_file.exists():
                return f"Version not found: {timestamp}"

            self.save_version(description="Auto-backup before restore")

            shutil.copy2(version_file, self.memory_file)

            logger.info(f"Memory restored from version: {timestamp}")
            return f"Memory restored from version: {timestamp}"
        except Exception as e:
            logger.error(f"Error restoring version: {e}")
            return f"Error restoring version: {e}"
