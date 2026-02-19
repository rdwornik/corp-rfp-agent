"""
kb_stats.py
Dashboard showing KB entry counts per family and historical file counts.

Usage:
  python src/kb_stats.py
  python src/kb_stats.py --json        # machine-readable output
  python src/kb_stats.py --family wms  # show only one family
"""

import json
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CANONICAL_DIR = PROJECT_ROOT / "data/kb/canonical"
HISTORICAL_DIR = PROJECT_ROOT / "data/kb/historical"
SCHEMA_DIR = PROJECT_ROOT / "data/kb/schema"
FAMILY_CONFIG = SCHEMA_DIR / "family_config.json"

ORDERED_FAMILIES = [
    "planning", "wms", "logistics", "scpo", "catman",
    "workforce", "commerce", "flexis", "network", "doddle", "aiml",
]

PHASE_LABELS = {1: "Phase 1 - create mode", 2: "Phase 2 - improve mode"}


def load_family_config() -> dict:
    if not FAMILY_CONFIG.exists():
        return {}
    with open(FAMILY_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f).get("families", {})


def count_canonical_entries(canonical_file: str) -> int:
    path = CANONICAL_DIR / canonical_file
    if not path.exists():
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data) if isinstance(data, list) else 0
    except (json.JSONDecodeError, OSError):
        return -1  # error indicator


def count_historical_files(family_key: str) -> int:
    folder = HISTORICAL_DIR / family_key
    if not folder.exists():
        return 0
    files = list(folder.glob("*.xlsx")) + list(folder.glob("*.xls"))
    # Exclude temp files
    files = [f for f in files if not f.name.startswith("~$")]
    return len(files)


def count_unified_entries() -> int:
    unified = CANONICAL_DIR / "RFP_Database_UNIFIED_CANONICAL.json"
    if not unified.exists():
        return 0
    try:
        with open(unified, "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data) if isinstance(data, list) else 0
    except (json.JSONDecodeError, OSError):
        return 0


def get_stats(family_filter: str = None) -> dict:
    config = load_family_config()
    stats = {}

    families_to_check = [family_filter] if family_filter else ORDERED_FAMILIES

    for family_key in families_to_check:
        family = config.get(family_key, {})
        canonical_file = family.get("canonical_file", f"RFP_Database_{family_key.title()}_CANONICAL.json")
        entry_count = count_canonical_entries(canonical_file)
        hist_count = count_historical_files(family_key)
        phase = family.get("phase", 1)

        stats[family_key] = {
            "display_name": family.get("display_name", family_key),
            "canonical_file": canonical_file,
            "entry_count": entry_count,
            "historical_files": hist_count,
            "phase": phase,
            "phase_label": PHASE_LABELS.get(phase, f"Phase {phase}"),
            "id_prefix": family.get("id_prefix", "???"),
            "cloud_native": family.get("cloud_native", True),
        }

    return stats


def print_dashboard(stats: dict, show_unified: bool = True):
    print()
    print("KB Statistics")
    print("=" * 60)

    name_width = 28
    count_width = 8
    hist_width = 10

    header = (
        f"  {'Family':<{name_width}} {'Entries':>{count_width}} "
        f"{'Hist.Files':>{hist_width}}  {'Mode'}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    total_family_entries = 0
    for family_key, data in stats.items():
        count = data["entry_count"]
        hist = data["historical_files"]
        display = f"{data['display_name']} ({data['id_prefix']})"
        phase_label = data["phase_label"]

        count_str = str(count) if count >= 0 else "ERROR"
        hist_str = str(hist) if hist > 0 else "-"
        hist_note = f" [{hist} file(s) ready]" if hist > 0 else ""

        print(
            f"  {display:<{name_width}} {count_str:>{count_width}} "
            f"{hist_str:>{hist_width}}  {phase_label}{hist_note}"
        )
        if count > 0:
            total_family_entries += count

    if show_unified and len(stats) == len(ORDERED_FAMILIES):
        print()
        unified_count = count_unified_entries()
        print("  " + "-" * 58)
        print(f"  {'Sum (family canonicals)':<{name_width}} {total_family_entries:>{count_width}}")
        if unified_count:
            print(f"  {'UNIFIED (merged)':<{name_width}} {unified_count:>{count_width}}  (run kb_merge_canonical.py to refresh)")
        print()

        # Gaps summary
        gaps = [k for k, v in stats.items() if v["entry_count"] == 0]
        ready = [k for k, v in stats.items() if v["historical_files"] > 0 and v["entry_count"] == 0]

        if gaps:
            print(f"  Families with 0 entries:  {', '.join(gaps)}")
        if ready:
            print(f"  Ready to extract (have historical files):  {', '.join(ready)}")
            print(f"  Run: python src/kb_extract_historical.py --family <name> --mode create --model gemini")
        print()


def print_json(stats: dict):
    output = {
        "families": stats,
        "unified_entries": count_unified_entries(),
        "summary": {
            "total_family_entries": sum(
                v["entry_count"] for v in stats.values() if v["entry_count"] > 0
            ),
            "families_with_entries": sum(
                1 for v in stats.values() if v["entry_count"] > 0
            ),
            "families_with_historical_files": sum(
                1 for v in stats.values() if v["historical_files"] > 0
            ),
        },
    }
    print(json.dumps(output, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Show KB entry counts and file status.")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--family", default=None,
                        choices=ORDERED_FAMILIES,
                        help="Show stats for a single family only")
    args = parser.parse_args()

    stats = get_stats(args.family)

    if args.json:
        print_json(stats)
    else:
        print_dashboard(stats, show_unified=(args.family is None))


if __name__ == "__main__":
    main()
