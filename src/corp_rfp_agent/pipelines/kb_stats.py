"""KB statistics -- uses KBLoader and archive registry."""

import json
import logging
from pathlib import Path
from typing import Optional

from corp_rfp_agent.pipelines.kb_loader import KBLoader, _load_family_config

logger = logging.getLogger(__name__)


def _get_kb_path(kb_dir: Optional[str] = None) -> Path:
    """Resolve KB directory path."""
    if kb_dir:
        return Path(kb_dir)
    return Path(__file__).resolve().parents[3] / "data" / "kb"


def gather_stats(kb_dir: Optional[str] = None) -> dict:
    """Gather KB statistics into a dict for display or JSON output.

    Returns:
        {
            "families": [{name, entries, phase, inbox, archived, id_prefix}, ...],
            "total_entries": int,
            "archive": {total_files, total_qa, unique_clients, by_family},
        }
    """
    kb_path = _get_kb_path(kb_dir)
    canonical_dir = kb_path / "canonical"

    loader = KBLoader(canonical_dir)
    family_stats = loader.stats()
    family_config = _load_family_config()

    families = []
    total = 0
    for family_key, config in family_config.items():
        display_name = config.get("display_name", family_key)
        # Match stats key -- the loader uses the stem fragment which may differ
        # from family_key (e.g., "Cognitive_Planning" vs "planning")
        entry_count = 0
        canonical_file = config.get("canonical_file", "")
        if canonical_file:
            canon_path = canonical_dir / canonical_file
            if canon_path.exists():
                try:
                    with open(canon_path, encoding="utf-8") as f:
                        entry_count = len(json.load(f))
                except Exception:
                    entry_count = -1

        # Count inbox files
        inbox_dir = kb_path / "historical" / family_key / "inbox"
        inbox_count = len(list(inbox_dir.glob("*.xlsx"))) if inbox_dir.exists() else 0

        # Count archived files for this family
        archived_count = 0
        archive_registry_path = kb_path / "archive" / "archive_registry.json"
        if archive_registry_path.exists():
            try:
                with open(archive_registry_path, encoding="utf-8") as f:
                    registry = json.load(f)
                archived_count = sum(
                    1 for e in registry.get("entries", [])
                    if e.get("family_code") == family_key
                )
            except Exception:
                pass

        # Count categories within this family
        cat_counts = {"technical": 0, "functional": 0, "customer_executive": 0, "consulting": 0}
        if canonical_file and entry_count > 0:
            canon_path = canonical_dir / canonical_file
            try:
                with open(canon_path, encoding="utf-8") as cf:
                    for item in json.load(cf):
                        cat = item.get("category", "").lower()
                        if cat in cat_counts:
                            cat_counts[cat] += 1
            except Exception:
                pass

        families.append({
            "family": family_key,
            "display_name": display_name,
            "entries": entry_count,
            "phase": config.get("phase", 1),
            "inbox": inbox_count,
            "archived": archived_count,
            "id_prefix": config.get("id_prefix", ""),
            "categories": cat_counts,
        })
        total += max(entry_count, 0)

    # Archive summary
    archive_summary = {"total_files": 0, "total_qa": 0, "unique_clients": set()}
    archive_path = kb_path / "archive" / "archive_registry.json"
    if archive_path.exists():
        try:
            with open(archive_path, encoding="utf-8") as f:
                registry = json.load(f)
            entries = registry.get("entries", [])
            archive_summary["total_files"] = len(entries)
            archive_summary["total_qa"] = sum(
                e.get("extraction_stats", {}).get("total_qa_extracted", 0) for e in entries
            )
            archive_summary["unique_clients"] = len(set(
                e.get("client", "") for e in entries if e.get("client")
            ))
        except Exception:
            pass

    return {
        "families": families,
        "total_entries": total,
        "archive": {
            "total_files": archive_summary["total_files"],
            "total_qa": archive_summary["total_qa"],
            "unique_clients": archive_summary.get("unique_clients", 0),
        },
    }


def show_stats(kb_dir: Optional[str] = None, as_json: bool = False) -> None:
    """Display KB statistics."""
    data = gather_stats(kb_dir)

    if as_json:
        print(json.dumps(data, indent=2))
        return

    # ASCII table output (Windows cp1252 compatible)
    print("\nRFP Answer Engine -- KB Stats")
    print("=" * 95)
    print(f"{'Family':<25} {'Entries':>7} {'Tech':>5} {'Func':>5} {'Exec':>5} {'Cons':>5} {'Phase':>5} {'Inbox':>5} {'Arch':>5}")
    print("-" * 95)

    for fam in sorted(data["families"], key=lambda x: -x["entries"]):
        cats = fam.get("categories", {})
        print(
            f"{fam['display_name']:<25} {fam['entries']:>7} "
            f"{cats.get('technical', 0):>5} {cats.get('functional', 0):>5} "
            f"{cats.get('customer_executive', 0):>5} {cats.get('consulting', 0):>5} "
            f"{fam['phase']:>5} {fam['inbox']:>5} {fam['archived']:>5}"
        )

    print("-" * 95)
    print(f"{'TOTAL':<25} {data['total_entries']:>7}")

    arc = data["archive"]
    if arc["total_files"]:
        print(f"\nArchive: {arc['total_files']} files, "
              f"{arc['total_qa']} Q&A extracted, "
              f"{arc['unique_clients']} unique clients")
