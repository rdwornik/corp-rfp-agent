"""Unified CLI for corp-rfp-agent.

Usage:
    python -m corp_rfp_agent.cli kb stats
    python -m corp_rfp_agent.cli kb stats --json
    python -m corp_rfp_agent.cli overrides list
    python -m corp_rfp_agent.cli overrides test "text with JDA"
"""

import argparse
import sys


def cmd_kb_stats(args: argparse.Namespace) -> None:
    """Show KB statistics."""
    from corp_rfp_agent.pipelines.kb_stats import show_stats
    show_stats(kb_dir=args.kb_dir, as_json=args.json)


def cmd_kb_rebuild(args: argparse.Namespace) -> None:
    """Rebuild ChromaDB from canonical files."""
    from pathlib import Path
    from corp_rfp_agent.pipelines.kb_loader import KBLoader
    from corp_rfp_agent.pipelines.kb_builder import KBBuilder

    kb_dir = Path(args.kb_dir) if args.kb_dir else Path.cwd() / "data" / "kb"
    canonical_dir = kb_dir / "canonical"

    # Step 1: merge unified
    builder = KBBuilder(canonical_dir)
    total = builder.merge_unified()
    print(f"Merged {total} entries into UNIFIED canonical.")

    # Step 2: rebuild ChromaDB
    if not args.merge_only:
        loader = KBLoader(canonical_dir)
        entries = loader.load_all()
        from corp_rfp_agent.kb.chromadb_impl import ChromaKBClient
        chroma_path = str(kb_dir / "chroma_store")
        client = ChromaKBClient(chroma_path=chroma_path, create_if_missing=True)
        count = client.rebuild(entries)
        print(f"Rebuilt ChromaDB with {count} entries.")


def cmd_overrides(args: argparse.Namespace) -> None:
    """Delegate to overrides CLI."""
    from corp_rfp_agent.overrides.cli import main as overrides_main
    # Pass remaining args to override CLI
    override_argv = [args.override_command] + args.override_args
    overrides_main(override_argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="corp-rfp-agent",
        description="Corp RFP Agent -- Blue Yonder pre-sales toolkit",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    subparsers = parser.add_subparsers(dest="command")

    # kb group
    kb_parser = subparsers.add_parser("kb", help="Knowledge base management")
    kb_sub = kb_parser.add_subparsers(dest="kb_command")

    # kb stats
    stats_parser = kb_sub.add_parser("stats", help="Show KB statistics")
    stats_parser.add_argument("--kb-dir", default=None)
    stats_parser.add_argument("--json", action="store_true")

    # kb rebuild
    rebuild_parser = kb_sub.add_parser("rebuild", help="Rebuild ChromaDB from canonical files")
    rebuild_parser.add_argument("--kb-dir", default=None)
    rebuild_parser.add_argument("--merge-only", action="store_true",
                                help="Only merge canonical files, skip ChromaDB rebuild")

    # overrides group
    ovr_parser = subparsers.add_parser("overrides", help="Manage text overrides")
    ovr_parser.add_argument("override_command", choices=["list", "add", "remove", "test", "stats"])
    ovr_parser.add_argument("override_args", nargs="*", default=[])

    args = parser.parse_args(argv)

    if args.verbose:
        from corp_rfp_agent.core.logging import setup_logging
        setup_logging("DEBUG")

    if args.command == "kb":
        if args.kb_command == "stats":
            cmd_kb_stats(args)
        elif args.kb_command == "rebuild":
            cmd_kb_rebuild(args)
        else:
            kb_parser.print_help()
    elif args.command == "overrides":
        cmd_overrides(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
