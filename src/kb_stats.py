"""
kb_stats.py
Dashboard showing KB entry counts per family + archive stats.

Usage:
  python src/kb_stats.py
  python src/kb_stats.py --json        # machine-readable
  python src/kb_stats.py --family wms  # single family
"""

import json
import argparse
from pathlib import Path
from collections import Counter

PROJECT_ROOT  = Path(__file__).resolve().parents[1]
CANONICAL_DIR = PROJECT_ROOT / "data/kb/canonical"
HISTORICAL_DIR= PROJECT_ROOT / "data/kb/historical"
SCHEMA_DIR    = PROJECT_ROOT / "data/kb/schema"
FAMILY_CONFIG = SCHEMA_DIR / "family_config.json"
REGISTRY_PATH = PROJECT_ROOT / "data/kb/archive/archive_registry.json"

ORDERED_FAMILIES = [
    "planning","wms","logistics","scpo","catman",
    "workforce","commerce","flexis","network","doddle","aiml",
]


# ── helpers ──────────────────────────────────────────────────

def load_family_config() -> dict:
    if not FAMILY_CONFIG.exists():
        return {}
    with open(FAMILY_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f).get("families", {})


def count_canonical(canon_file: str) -> int:
    p = CANONICAL_DIR / canon_file
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else -1
    except Exception:
        return -1


def count_inbox(family_key: str) -> int:
    inbox = HISTORICAL_DIR / family_key / "inbox"
    if not inbox.exists():
        return 0
    files = list(inbox.glob("*.xlsx")) + list(inbox.glob("*.xls"))
    return len([f for f in files if not f.name.startswith("~$")])


def count_unified() -> int:
    p = CANONICAL_DIR / "RFP_Database_UNIFIED_CANONICAL.json"
    if not p.exists():
        return 0
    try:
        return len(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return 0


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"entries": []}
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def archive_stats(registry: dict) -> dict:
    entries = registry.get("entries", [])
    total_files  = len(entries)
    total_qa     = sum(e.get("extraction_stats", {}).get("accepted", 0) for e in entries)
    clients      = {e.get("client","") for e in entries if e.get("client")}
    by_family    = Counter(e.get("family_code","?") for e in entries)
    cat_totals   = Counter()
    for e in entries:
        for cat, n in e.get("extraction_stats",{}).get("categories",{}).items():
            cat_totals[cat] += n
    return {
        "total_files":  total_files,
        "total_qa":     total_qa,
        "unique_clients": len(clients),
        "by_family":    dict(by_family),
        "categories":   dict(cat_totals),
    }


# ── per-family stats ─────────────────────────────────────────

def get_stats(family_filter: str = None) -> dict:
    config  = load_family_config()
    registry = load_registry()
    arc_by_fam = Counter(e.get("family_code") for e in registry.get("entries",[]))
    stats   = {}

    families = [family_filter] if family_filter else ORDERED_FAMILIES
    for key in families:
        fam    = config.get(key, {})
        cfile  = fam.get("canonical_file",
                          f"RFP_Database_{key.title()}_CANONICAL.json")
        stats[key] = {
            "display_name":  fam.get("display_name", key),
            "canonical_file": cfile,
            "entry_count":   count_canonical(cfile),
            "inbox_files":   count_inbox(key),
            "archived_files":arc_by_fam.get(key, 0),
            "phase":         fam.get("phase", 1),
            "id_prefix":     fam.get("id_prefix", "???"),
            "cloud_native":  fam.get("cloud_native", True),
        }
    return stats


# ── display ──────────────────────────────────────────────────

def print_dashboard(stats: dict, registry: dict, show_totals: bool = True) -> None:
    arc = archive_stats(registry)

    print()
    print(" RFP Answer Engine - KB Stats")
    print("=" * 70)
    hdr = f"  {'Family':<28} {'Entries':>8} {'Phase':>6} {'Inbox':>6} {'Archived':>9}"
    print(hdr)
    print("  " + "-" * 66)

    total_entries = 0
    for key, d in stats.items():
        cnt   = d["entry_count"]
        phase = d["phase"]
        inbox = d["inbox_files"]
        arc_n = d["archived_files"]
        name  = f"{d['display_name']} ({d['id_prefix']})"

        cnt_s  = str(cnt)  if cnt  >= 0 else "ERR"
        inbox_s= str(inbox) if inbox else "-"
        arc_s  = str(arc_n) if arc_n else "-"
        phase_s= str(phase)

        alert = ""
        if inbox > 0:
            alert = f"  <- {inbox} file(s) ready in inbox"

        print(f"  {name:<28} {cnt_s:>8} {phase_s:>6} {inbox_s:>6} {arc_s:>9}{alert}")
        if cnt > 0:
            total_entries += cnt

    if show_totals:
        print()
        print("  " + "-" * 66)

        # Sum row
        unified = count_unified()
        print(f"  {'Sum (family canonicals)':<28} {total_entries:>8}")
        if unified:
            print(f"  {'UNIFIED (merged)':<28} {unified:>8}  "
                  f"(refresh: kb_merge_canonical.py)")

        print()
        print("=" * 70)
        print(f"  Archive: {arc['total_files']} files | "
              f"{arc['total_qa']} Q&A extracted | "
              f"{arc['unique_clients']} clients")

        if arc["categories"]:
            cats = arc["categories"]
            cat_str = "  ".join(f"{k}:{v}" for k, v in sorted(cats.items()))
            print(f"  Categories: {cat_str}")

        gaps = [k for k, v in stats.items() if v["entry_count"] == 0]
        ready= [k for k, v in stats.items()
                if v["inbox_files"] > 0 and v["entry_count"] == 0]

        if gaps:
            print()
            print(f"  Families with 0 entries: {', '.join(gaps)}")
        if ready:
            print(f"  Ready to extract:        {', '.join(ready)}")
            print(f"  Run: python src/kb_extract_historical.py --family <name>")
        print()


def print_json(stats: dict, registry: dict) -> None:
    arc = archive_stats(registry)
    output = {
        "families": stats,
        "unified_entries": count_unified(),
        "archive": arc,
        "summary": {
            "total_family_entries": sum(
                v["entry_count"] for v in stats.values() if v["entry_count"] > 0
            ),
            "families_with_entries":       sum(1 for v in stats.values() if v["entry_count"] > 0),
            "families_with_inbox_files":   sum(1 for v in stats.values() if v["inbox_files"] > 0),
        },
    }
    print(json.dumps(output, indent=2))


# ── main ─────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="KB entry counts and archive stats.")
    parser.add_argument("--json",   action="store_true")
    parser.add_argument("--family", default=None, choices=ORDERED_FAMILIES)
    args = parser.parse_args()

    stats    = get_stats(args.family)
    registry = load_registry()

    if args.json:
        print_json(stats, registry)
    else:
        print_dashboard(stats, registry, show_totals=(args.family is None))


if __name__ == "__main__":
    main()
