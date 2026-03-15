"""kb_to_markdown.py -- Convert verified/draft KB JSON entries to markdown files.

Usage:
    python src/kb_to_markdown.py --dry-run
    python src/kb_to_markdown.py
    python src/kb_to_markdown.py --source-dir data/kb/verified --output-dir "C:\\path\\to\\output"
"""

import argparse
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_VERIFIED_DIR = PROJECT_ROOT / "data" / "kb" / "verified"
DEFAULT_DRAFTS_DIR = PROJECT_ROOT / "data" / "kb" / "drafts"
DEFAULT_OUTPUT_DIR = Path(r"C:\Users\1028120\Documents\corp_data\rfp_kb")

# Family code -> product display names
FAMILY_PRODUCTS = {
    "planning": ["Blue Yonder Demand Planning", "Blue Yonder Supply Planning"],
    "planning_ibp": ["Blue Yonder Integrated Business Planning"],
    "planning_pps": ["Blue Yonder Production Planning & Scheduling"],
    "wms": ["Blue Yonder WMS"],
    "logistics": ["Blue Yonder TMS"],
    "network": ["Blue Yonder Network"],
    "aiml": ["Blue Yonder Platform"],
    "catman": ["Blue Yonder Category Management"],
    "catman_assortment": ["Blue Yonder Assortment Management"],
    "catman_space": ["Blue Yonder Space Planning"],
    "commerce": ["Blue Yonder Commerce"],
    "commerce_orders": ["Blue Yonder Order Management"],
    "control_tower": ["Blue Yonder Control Tower"],
    "retail_ar": ["Blue Yonder Retail Analytics"],
    "retail_demand_edge": ["Blue Yonder Demand Edge"],
    "retail_mfp": ["Blue Yonder MFP"],
    "scp": ["Blue Yonder Supply Chain Planning"],
    "scp_sequencing": ["Blue Yonder Sequencing"],
}

# Directories to create under output
FAMILY_DIRS = ["planning", "wms", "logistics", "network", "aiml", "_staging"]


def _sanitize_id(entry_id: str) -> str:
    """Normalize ID for use in markdown frontmatter: lowercase, hyphens."""
    return re.sub(r"[_\s]+", "-", entry_id.strip().lower())


def _build_markdown_id(family: str, entry_id: str) -> str:
    """Build a prefixed markdown ID like kb-rfp-planning-0001."""
    sanitized = _sanitize_id(entry_id)
    # If the ID already starts with the family prefix, just add kb-rfp-
    if sanitized.startswith(f"{family}-"):
        return f"kb-rfp-{sanitized}"
    return f"kb-rfp-{family}-{sanitized}"


def _yaml_list(items: list) -> str:
    """Format a Python list as a YAML inline list."""
    if not items:
        return "[]"
    escaped = []
    for item in items:
        s = str(item)
        # Quote strings that contain special YAML characters
        if any(c in s for c in ":\",[]{}#&*!|>' "):
            s = f'"{s}"'
        escaped.append(s)
    return "[" + ", ".join(escaped) + "]"


def json_to_markdown(entry: dict, family: str, trust_level: str = "verified") -> str:
    """Convert a KB JSON entry dict to a markdown string with YAML frontmatter."""
    entry_id = entry.get("id") or entry.get("kb_id", "unknown")
    md_id = _build_markdown_id(family, entry_id)

    products = FAMILY_PRODUCTS.get(
        family, [f"Blue Yonder {family.replace('_', ' ').title()}"]
    )
    category = entry.get("category", "general")
    tags = entry.get("tags", [])
    source_rfps = entry.get("source_rfps", [])
    last_reviewed = entry.get("last_updated", "")

    question = entry.get("question") or entry.get("canonical_question", "")
    answer = entry.get("answer") or entry.get("canonical_answer", "")

    lines = [
        "---",
        f"id: {md_id}",
        "doc_type: rfp_response",
        f"trust_level: {trust_level}",
        f"products: {_yaml_list(products)}",
        f"topics: {_yaml_list([entry.get('subcategory', '')] if entry.get('subcategory') else [])}",
        f"category: {category}",
        f"tags: {_yaml_list(tags)}",
        f"source_rfps: {_yaml_list(source_rfps)}",
        f'last_reviewed: "{last_reviewed}"',
        "---",
        "",
        "## Question",
        "",
        question.strip() if question else "(no question)",
        "",
        "## Answer",
        "",
        answer.strip() if answer else "(no answer)",
        "",
    ]
    return "\n".join(lines)


def _md_filename(entry_id: str) -> str:
    """Generate markdown filename from entry ID."""
    return _sanitize_id(entry_id) + ".md"


def ensure_directories(
    output_dir: Path, families: list[str], dry_run: bool = False
) -> list[Path]:
    """Create output directory structure. Returns list of created dirs."""
    dirs_to_create = set(FAMILY_DIRS)
    # Add any families found in source that aren't in the default list
    for f in families:
        dirs_to_create.add(f)

    created = []
    for d in sorted(dirs_to_create):
        p = output_dir / d
        if not p.exists():
            if not dry_run:
                p.mkdir(parents=True, exist_ok=True)
            created.append(p)
    # Ensure root exists
    if not output_dir.exists() and not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    return created


def convert_directory(
    source_dir: Path, output_dir: Path, trust_level: str, dry_run: bool = False
) -> dict:
    """Convert all JSON files in source_dir/{family}/ to markdown.

    Returns dict with counts: {family: count, ...} and total.
    """
    stats = {}
    if not source_dir.exists():
        return stats

    for family_dir in sorted(source_dir.iterdir()):
        if not family_dir.is_dir():
            continue
        family = family_dir.name
        count = 0

        # Determine output subfolder
        if trust_level == "draft":
            out_family_dir = output_dir / "_staging" / family
        else:
            out_family_dir = output_dir / family

        for json_file in sorted(family_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                print(f"  SKIP (invalid JSON): {json_file.name}")
                continue

            md_content = json_to_markdown(data, family, trust_level)
            entry_id = data.get("id") or data.get("kb_id", json_file.stem)
            md_file = out_family_dir / _md_filename(entry_id)

            if not dry_run:
                out_family_dir.mkdir(parents=True, exist_ok=True)
                md_file.write_text(md_content, encoding="utf-8")

            count += 1

        if count > 0:
            stats[family] = count

    return stats


def migrate(
    verified_dir: Path = None,
    drafts_dir: Path = None,
    output_dir: Path = None,
    dry_run: bool = False,
) -> dict:
    """Run full migration. Returns summary dict."""
    verified_dir = verified_dir or DEFAULT_VERIFIED_DIR
    drafts_dir = drafts_dir or DEFAULT_DRAFTS_DIR
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    # Discover families
    families = set()
    for d in [verified_dir, drafts_dir]:
        if d.exists():
            families.update(f.name for f in d.iterdir() if f.is_dir())

    ensure_directories(output_dir, list(families), dry_run)

    verified_stats = convert_directory(verified_dir, output_dir, "verified", dry_run)
    draft_stats = convert_directory(drafts_dir, output_dir, "draft", dry_run)

    total_verified = sum(verified_stats.values())
    total_drafts = sum(draft_stats.values())

    return {
        "verified": verified_stats,
        "drafts": draft_stats,
        "total_verified": total_verified,
        "total_drafts": total_drafts,
        "output_dir": str(output_dir),
        "dry_run": dry_run,
    }


def print_summary(result: dict):
    """Print migration summary to stdout."""
    mode = "[DRY RUN] " if result["dry_run"] else ""
    print(f"\n{mode}KB to Markdown Migration Summary")
    print("=" * 50)

    if result["verified"]:
        print("\nVerified entries:")
        for family, count in sorted(result["verified"].items()):
            print(f"  {family:.<30} {count:>5}")
    if result["drafts"]:
        print("\nDraft entries (-> _staging/):")
        for family, count in sorted(result["drafts"].items()):
            print(f"  {family:.<30} {count:>5}")

    print(
        f"\nMigrated: {result['total_verified']} verified + {result['total_drafts']} drafts"
    )
    print(f"Output:   {result['output_dir']}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert KB JSON entries to markdown files"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without writing files",
    )
    parser.add_argument(
        "--source-dir",
        type=str,
        default=None,
        help="Override verified source directory",
    )
    parser.add_argument(
        "--output-dir", type=str, default=None, help="Override output directory"
    )
    args = parser.parse_args()

    verified_dir = Path(args.source_dir) if args.source_dir else None
    output_dir = Path(args.output_dir) if args.output_dir else None

    result = migrate(
        verified_dir=verified_dir,
        output_dir=output_dir,
        dry_run=args.dry_run,
    )
    print_summary(result)


if __name__ == "__main__":
    main()
