"""Override store -- YAML-based text override system.

Loads overrides from config/overrides.yaml and applies them to
generated answers. Used to correct stale KB content (e.g.,
Splunk -> CrowdStrike, JDA -> Blue Yonder).
"""

import logging
import re
from pathlib import Path
from typing import Optional

from corp_rfp_agent.overrides.models import Override, OverrideMatch, OverrideResult

logger = logging.getLogger(__name__)


class YAMLOverrideStore:
    """Loads overrides from YAML and applies text replacements.

    Satisfies the OverrideStore protocol from Phase 1.
    """

    def __init__(self, yaml_path: Optional[Path] = None):
        """Initialize from YAML file.

        Args:
            yaml_path: Path to overrides.yaml. If None, uses
                       config/overrides.yaml relative to project root.
        """
        self._overrides: list[Override] = []
        self._path = yaml_path

        if yaml_path and yaml_path.exists():
            self._load(yaml_path)

    def _load(self, path: Path) -> None:
        """Load overrides from YAML file."""
        import yaml

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        raw_list = data.get("overrides", [])
        for item in raw_list:
            ovr = Override(
                id=item.get("id", ""),
                find=item.get("find", ""),
                replace=item.get("replace", ""),
                description=item.get("description", ""),
                whole_word=item.get("whole_word", False),
                enabled=item.get("enabled", True),
                family=item.get("family", ""),
                added=item.get("added", ""),
            )
            if ovr.is_valid():
                self._overrides.append(ovr)
            else:
                logger.warning("Skipping invalid override: %s", item)

        logger.info("Loaded %d overrides from %s", len(self._overrides), path.name)

    def apply(
        self,
        text: str,
        *,
        family: Optional[str] = None,
    ) -> OverrideResult:
        """Apply all enabled overrides to text.

        Args:
            text: Text to process.
            family: If set, only apply overrides matching this family
                    (or overrides with no family restriction).

        Returns:
            OverrideResult with original text, modified text, and match details.
        """
        result_text = text
        matches: list[OverrideMatch] = []

        for ovr in self._overrides:
            if not ovr.enabled:
                continue

            # Family filter: apply if override has no family or matches
            if ovr.family and family and ovr.family != family:
                continue

            # Build pattern
            pattern = re.escape(ovr.find)
            if ovr.whole_word:
                pattern = r"\b" + pattern + r"\b"

            # Count matches before replacing
            found = re.findall(pattern, result_text, flags=re.IGNORECASE)
            if found:
                result_text = re.sub(
                    pattern, ovr.replace, result_text, flags=re.IGNORECASE
                )
                matches.append(OverrideMatch(
                    override_id=ovr.id,
                    find=ovr.find,
                    replace=ovr.replace,
                    count=len(found),
                ))

        return OverrideResult(
            original=text,
            modified=result_text,
            matches=matches,
        )

    # --- OverrideStore protocol methods ---

    def get_override(
        self,
        question: str,
        *,
        client: Optional[str] = None,
        family: Optional[str] = None,
    ) -> Optional[str]:
        """Apply overrides to question text and return modified if changed.

        This implements the OverrideStore protocol. For text overrides,
        we apply all matching overrides and return the result if anything changed.
        """
        result = self.apply(question, family=family)
        return result.modified if result.changed else None

    def set_override(
        self,
        question: str,
        answer: str,
        *,
        client: Optional[str] = None,
        family: Optional[str] = None,
    ) -> None:
        """Add a new override. Writes back to YAML file."""
        new_id = f"OVR-{len(self._overrides) + 1:04d}"
        ovr = Override(
            id=new_id,
            find=question,
            replace=answer,
            description=f"Added via set_override",
        )
        self._overrides.append(ovr)

        if self._path:
            self._save()

    def count(self) -> int:
        """Count total overrides."""
        return len(self._overrides)

    # --- Query methods ---

    def list_overrides(
        self, *, enabled_only: bool = False, family: Optional[str] = None
    ) -> list[Override]:
        """List overrides with optional filtering."""
        result = self._overrides
        if enabled_only:
            result = [o for o in result if o.enabled]
        if family:
            result = [o for o in result if not o.family or o.family == family]
        return result

    def get_by_id(self, override_id: str) -> Optional[Override]:
        """Find override by ID."""
        for ovr in self._overrides:
            if ovr.id == override_id:
                return ovr
        return None

    def add(self, override: Override) -> None:
        """Add an override to the store."""
        self._overrides.append(override)

    def remove(self, override_id: str) -> bool:
        """Remove an override by ID. Returns True if found."""
        for i, ovr in enumerate(self._overrides):
            if ovr.id == override_id:
                self._overrides.pop(i)
                return True
        return False

    def stats(self) -> dict:
        """Return summary statistics."""
        enabled = sum(1 for o in self._overrides if o.enabled)
        disabled = len(self._overrides) - enabled
        families = set(o.family for o in self._overrides if o.family)
        return {
            "total": len(self._overrides),
            "enabled": enabled,
            "disabled": disabled,
            "families": sorted(families),
        }

    def _save(self) -> None:
        """Write overrides back to YAML file."""
        if not self._path:
            return

        import yaml

        data = {"overrides": []}
        for ovr in self._overrides:
            entry = {
                "id": ovr.id,
                "description": ovr.description,
                "find": ovr.find,
                "replace": ovr.replace,
            }
            if ovr.whole_word:
                entry["whole_word"] = True
            if not ovr.enabled:
                entry["enabled"] = False
            if ovr.family:
                entry["family"] = ovr.family
            if ovr.added:
                entry["added"] = ovr.added
            data["overrides"].append(entry)

        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
