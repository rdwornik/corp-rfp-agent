"""Merge generated profiles + manual overrides -> effective profiles.

Reads:
  - config/product_profiles/_generated/*.yaml
  - config/product_profiles/_overrides/*.yaml (optional)

Writes:
  - config/product_profiles/_effective/*.yaml (pipeline reads ONLY here)

Override rules:
  - Override fields REPLACE generated fields
  - forbidden_claims_add:     APPENDED to generated list
  - forbidden_claims_remove:  REMOVED from generated list
  - platform_services_add:    APPENDED to service lists
  - platform_services_remove: REMOVED from service lists
  - Override always wins on conflict (logged as warning)

Usage:
  python src/merge_profiles.py
  python src/merge_profiles.py --show-overrides wms
  python src/merge_profiles.py --show-conflicts
  python src/merge_profiles.py --validate
"""

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = PROJECT_ROOT / "config" / "product_profiles"
GENERATED_DIR = PROFILES_DIR / "_generated"
OVERRIDES_DIR = PROFILES_DIR / "_overrides"
EFFECTIVE_DIR = PROFILES_DIR / "_effective"
CHANGELOG_PATH = PROFILES_DIR / "_changelog.jsonl"


# ---------------------------------------------------------------------------
# Special merge keys (extracted and processed separately)
# ---------------------------------------------------------------------------
SPECIAL_KEYS = frozenset({
    "forbidden_claims_add",
    "forbidden_claims_remove",
    "platform_services_add",
    "platform_services_remove",
    "review_notes",
    "last_reviewed",
    "status",
})

# Required fields for validation
REQUIRED_FIELDS = [
    "product",
    "display_name",
    "platform_services",
    "_meta",
]


# ---------------------------------------------------------------------------
# Core merge
# ---------------------------------------------------------------------------

def merge_profile(generated: dict, override: dict) -> dict:
    """Merge generated + override -> effective.

    Override fields replace generated fields.
    Special *_add/*_remove keys do list surgery.
    _meta is rebuilt with merge info.
    """
    if not override:
        return copy.deepcopy(generated)

    effective = copy.deepcopy(generated)
    override = copy.deepcopy(override)

    # Extract special merge fields
    fc_add = override.pop("forbidden_claims_add", [])
    fc_remove = override.pop("forbidden_claims_remove", [])
    ps_add = override.pop("platform_services_add", {})
    ps_remove = override.pop("platform_services_remove", {})
    review_notes = override.pop("review_notes", "")
    last_reviewed = override.pop("last_reviewed", "")
    override_status = override.pop("status", None)

    # Simple field replacement (override wins)
    override_count = 0
    for key, value in override.items():
        if key.startswith("_"):
            continue
        if key in effective and effective[key] != value:
            override_count += 1
        effective[key] = value

    # Merge forbidden_claims
    fc = effective.get("forbidden_claims", [])
    for item in fc_add:
        if item not in fc:
            fc.append(item)
    for item in fc_remove:
        if item in fc:
            fc.remove(item)
    effective["forbidden_claims"] = fc

    # Merge platform_services lists
    ps = effective.get("platform_services", {})
    for list_name, items in ps_add.items():
        if list_name not in ps:
            ps[list_name] = []
        for item in items:
            if item not in ps[list_name]:
                ps[list_name].append(item)
    for list_name, items in ps_remove.items():
        if list_name in ps:
            for item in items:
                if item in ps[list_name]:
                    ps[list_name].remove(item)
    effective["platform_services"] = ps

    # Recompute convenience booleans from merged services
    available = ps.get("available", [])
    effective["has_analytics"] = "analytics" in available
    effective["has_ml_studio"] = "ml_studio" in available
    effective["has_bdm"] = "bdm" in available
    effective["has_workflow"] = "workflow_orchestrator" in available
    effective["has_bulk_ingestion"] = "bulk_ingestion" in available
    effective["has_streaming"] = "streaming_ingestion" in available
    effective["has_data_share"] = "data_share_app" in available
    effective["has_daas"] = "daas_egress" in available

    # Update _meta
    meta = effective.setdefault("_meta", {})
    meta["last_override"] = last_reviewed or datetime.now().isoformat()
    meta["override_count"] = override_count + len(fc_add) + len(fc_remove)
    meta["review_notes"] = review_notes
    if override_status:
        meta["status"] = override_status

    return effective


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    """Load YAML file, return empty dict if not found."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(data: dict, path: Path) -> None:
    """Save dict as YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def _none_repr(dumper, _):
        return dumper.represent_scalar("tag:yaml.org,2002:null", "null")

    dumper = yaml.SafeDumper
    dumper.add_representer(type(None), _none_repr)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, Dumper=dumper, default_flow_style=False,
                  allow_unicode=True, sort_keys=False, width=120)


def append_changelog(entry: dict) -> None:
    """Append one entry to _changelog.jsonl."""
    CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CHANGELOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_profile(profile: dict) -> list[str]:
    """Validate an effective profile. Returns list of issues."""
    issues = []
    for field in REQUIRED_FIELDS:
        if field not in profile:
            issues.append(f"Missing required field: {field}")

    ps = profile.get("platform_services", {})
    if not isinstance(ps, dict):
        issues.append("platform_services should be a dict")
    elif "available" not in ps:
        issues.append("platform_services.available missing")

    meta = profile.get("_meta", {})
    if not meta.get("generated_at"):
        issues.append("_meta.generated_at missing")

    return issues


# ---------------------------------------------------------------------------
# Merge all
# ---------------------------------------------------------------------------

def merge_all(
    generated_dir: Path = GENERATED_DIR,
    overrides_dir: Path = OVERRIDES_DIR,
    effective_dir: Path = EFFECTIVE_DIR,
) -> dict:
    """Merge all generated profiles with any overrides.

    Returns summary dict.
    """
    generated_dir.mkdir(parents=True, exist_ok=True)
    overrides_dir.mkdir(parents=True, exist_ok=True)
    effective_dir.mkdir(parents=True, exist_ok=True)

    gen_files = sorted(generated_dir.glob("*.yaml"))
    if not gen_files:
        print("[WARN] No generated profiles found.")
        return {"total": 0, "with_overrides": 0, "plain": 0}

    summary = {
        "total": 0,
        "with_overrides": 0,
        "plain": 0,
        "conflicts": [],
        "validation_issues": {},
    }

    now_iso = datetime.now().isoformat()

    for gen_path in gen_files:
        product_key = gen_path.stem
        generated = load_yaml(gen_path)

        override_path = overrides_dir / gen_path.name
        override = load_yaml(override_path)

        effective = merge_profile(generated, override)

        # Validate
        issues = validate_profile(effective)
        if issues:
            summary["validation_issues"][product_key] = issues
            print(f"  [WARN] {product_key}: {len(issues)} validation issue(s)")

        # Detect conflicts (fields overridden that changed since last gen)
        if override:
            for key in override:
                if key in SPECIAL_KEYS or key.startswith("_"):
                    continue
                if key in generated and generated[key] != override[key]:
                    summary["conflicts"].append({
                        "product": product_key,
                        "field": key,
                        "generated": str(generated[key])[:100],
                        "override": str(override[key])[:100],
                    })

        # Save effective
        out_path = effective_dir / gen_path.name
        save_yaml(effective, out_path)

        has_override = bool(override)
        if has_override:
            summary["with_overrides"] += 1
        else:
            summary["plain"] += 1
        summary["total"] += 1

        append_changelog({
            "timestamp": now_iso,
            "action": "merged",
            "product": product_key,
            "has_override": has_override,
            "override_fields": len(override) if override else 0,
        })

    print(f"\n[OK] Merged {summary['total']} profiles "
          f"({summary['with_overrides']} with overrides, {summary['plain']} plain)")

    if summary["conflicts"]:
        print(f"\n[INFO] {len(summary['conflicts'])} conflict(s) (override wins):")
        for c in summary["conflicts"][:5]:
            print(f"  {c['product']}.{c['field']}: "
                  f"generated={c['generated'][:40]} | override={c['override'][:40]}")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Merge generated + override profiles -> effective",
    )
    parser.add_argument("--show-overrides", type=str, default=None, metavar="PRODUCT",
                        help="Show override details for a product")
    parser.add_argument("--show-conflicts", action="store_true",
                        help="Show all field conflicts")
    parser.add_argument("--validate", action="store_true",
                        help="Validate all effective profiles")
    parser.add_argument("--generated-dir", type=str, default=str(GENERATED_DIR))
    parser.add_argument("--overrides-dir", type=str, default=str(OVERRIDES_DIR))
    parser.add_argument("--effective-dir", type=str, default=str(EFFECTIVE_DIR))
    args = parser.parse_args()

    gen_dir = Path(args.generated_dir)
    ovr_dir = Path(args.overrides_dir)
    eff_dir = Path(args.effective_dir)

    if args.show_overrides:
        ovr_path = ovr_dir / f"{args.show_overrides}.yaml"
        if ovr_path.exists():
            print(ovr_path.read_text(encoding="utf-8"))
        else:
            print(f"No override file for '{args.show_overrides}'")
        return

    if args.validate:
        eff_files = sorted(eff_dir.glob("*.yaml"))
        total_issues = 0
        for p in eff_files:
            profile = load_yaml(p)
            issues = validate_profile(profile)
            if issues:
                print(f"  {p.stem}: {', '.join(issues)}")
                total_issues += len(issues)
        if total_issues == 0:
            print(f"[OK] All {len(eff_files)} profiles valid")
        else:
            print(f"\n[WARN] {total_issues} issue(s) across {len(eff_files)} profiles")
        return

    summary = merge_all(gen_dir, ovr_dir, eff_dir)

    if args.show_conflicts and summary["conflicts"]:
        print(f"\nAll conflicts ({len(summary['conflicts'])}):")
        for c in summary["conflicts"]:
            print(f"  {c['product']}.{c['field']}:")
            print(f"    generated: {c['generated']}")
            print(f"    override:  {c['override']}")


if __name__ == "__main__":
    main()
