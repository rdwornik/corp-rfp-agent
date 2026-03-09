"""Override CLI -- manage text overrides from the command line.

Usage:
    python -m corp_rfp_agent.overrides.cli list
    python -m corp_rfp_agent.overrides.cli add --find "OldTerm" --replace "NewTerm" --description "Why"
    python -m corp_rfp_agent.overrides.cli remove OVR-0001
    python -m corp_rfp_agent.overrides.cli test "Some text with Splunk and JDA in it"
    python -m corp_rfp_agent.overrides.cli stats
"""

import argparse
import sys
from pathlib import Path

from corp_rfp_agent.overrides.models import Override
from corp_rfp_agent.overrides.store import YAMLOverrideStore


def _default_yaml_path() -> Path:
    """Find config/overrides.yaml relative to project root."""
    # Walk up from this file to find project root
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "config" / "overrides.yaml"
        if candidate.exists():
            return candidate
    # Fallback: CWD
    return Path.cwd() / "config" / "overrides.yaml"


def cmd_list(store: YAMLOverrideStore, args: argparse.Namespace) -> None:
    """List all overrides."""
    overrides = store.list_overrides(
        enabled_only=args.enabled_only,
        family=args.family,
    )
    if not overrides:
        print("No overrides found.")
        return

    for ovr in overrides:
        status = "ON " if ovr.enabled else "OFF"
        family_tag = f" [{ovr.family}]" if ovr.family else ""
        word_tag = " (whole-word)" if ovr.whole_word else ""
        print(f"  {status} {ovr.id}: \"{ovr.find}\" -> \"{ovr.replace}\"{word_tag}{family_tag}")
        if ovr.description:
            print(f"       {ovr.description}")


def cmd_add(store: YAMLOverrideStore, args: argparse.Namespace) -> None:
    """Add a new override."""
    new_id = f"OVR-{store.count() + 1:04d}"
    ovr = Override(
        id=new_id,
        find=args.find,
        replace=args.replace,
        description=args.description or "",
        whole_word=args.whole_word,
        family=args.family or "",
    )
    store.add(ovr)
    store._save()
    print(f"Added override {new_id}: \"{args.find}\" -> \"{args.replace}\"")


def cmd_remove(store: YAMLOverrideStore, args: argparse.Namespace) -> None:
    """Remove an override by ID."""
    if store.remove(args.override_id):
        store._save()
        print(f"Removed override {args.override_id}")
    else:
        print(f"Override {args.override_id} not found.")
        sys.exit(1)


def cmd_test(store: YAMLOverrideStore, args: argparse.Namespace) -> None:
    """Test overrides against sample text."""
    result = store.apply(args.text, family=args.family)
    if result.changed:
        print(f"Original:  {result.original}")
        print(f"Modified:  {result.modified}")
        print(f"Replacements: {result.total_replacements}")
        for m in result.matches:
            print(f"  {m.override_id}: \"{m.find}\" -> \"{m.replace}\" ({m.count}x)")
    else:
        print("No overrides matched.")


def cmd_stats(store: YAMLOverrideStore, args: argparse.Namespace) -> None:
    """Show override statistics."""
    s = store.stats()
    print(f"Total overrides: {s['total']}")
    print(f"  Enabled:  {s['enabled']}")
    print(f"  Disabled: {s['disabled']}")
    if s["families"]:
        print(f"  Families: {', '.join(s['families'])}")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="overrides",
        description="Manage text overrides for RFP answer generation",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to overrides.yaml (default: config/overrides.yaml)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List all overrides")
    p_list.add_argument("--enabled-only", action="store_true")
    p_list.add_argument("--family", default=None)

    # add
    p_add = subparsers.add_parser("add", help="Add a new override")
    p_add.add_argument("--find", required=True)
    p_add.add_argument("--replace", required=True)
    p_add.add_argument("--description", default="")
    p_add.add_argument("--whole-word", action="store_true")
    p_add.add_argument("--family", default="")

    # remove
    p_remove = subparsers.add_parser("remove", help="Remove an override")
    p_remove.add_argument("override_id")

    # test
    p_test = subparsers.add_parser("test", help="Test overrides on sample text")
    p_test.add_argument("text")
    p_test.add_argument("--family", default=None)

    # stats
    subparsers.add_parser("stats", help="Show override statistics")

    args = parser.parse_args(argv)

    yaml_path = args.config or _default_yaml_path()
    store = YAMLOverrideStore(yaml_path=yaml_path)

    commands = {
        "list": cmd_list,
        "add": cmd_add,
        "remove": cmd_remove,
        "test": cmd_test,
        "stats": cmd_stats,
    }
    commands[args.command](store, args)


if __name__ == "__main__":
    main()
