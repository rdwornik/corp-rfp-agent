"""Validate product profiles — detect contradictions, missing data, suspicious inferences.

Reads effective profiles from config/product_profiles/_effective/ and checks:
  1. CONTRADICTIONS: field value vs forbidden_claims (ERROR)
  2. MISSING DATA: empty fields that need overrides (WARNING)
  3. SUSPICIOUS INFERENCES: field claims not backed by key_facts (SUSPICIOUS)
  4. PLATFORM SERVICE CONSISTENCY: available vs not_available overlap (ERROR)

Usage:
  python src/validate_profiles.py
  python src/validate_profiles.py --auto-fix
  python src/validate_profiles.py --auto-fix --merge
  python src/validate_profiles.py --product wms
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = PROJECT_ROOT / "config" / "product_profiles"
EFFECTIVE_DIR = PROFILES_DIR / "_effective"
OVERRIDES_DIR = PROFILES_DIR / "_overrides"

# ---------------------------------------------------------------------------
# Issue levels
# ---------------------------------------------------------------------------
ERROR = "ERROR"
WARNING = "WARNING"
SUSPICIOUS = "SUSPICIOUS"


# ---------------------------------------------------------------------------
# Convenience-boolean → service key mapping
# ---------------------------------------------------------------------------
BOOL_TO_SERVICE_KEY = {
    "has_analytics": "analytics",
    "has_ml_studio": "ml_studio",
    "has_bdm": "bdm",
    "has_workflow": "workflow_orchestrator",
    "has_bulk_ingestion": "bulk_ingestion",
    "has_streaming": "streaming_ingestion",
    "has_data_share": "data_share_app",
    "has_daas": "daas_egress",
}

# Field → (forbidden-claim substring patterns that contradict True)
FIELD_FORBIDDEN_PATTERNS = {
    "cloud_native": ["NOT cloud-native", "not cloud-native"],
    "uses_snowflake": ["NOT use Snowflake", "Does NOT use Snowflake", "not use snowflake"],
    "microservices": ["NOT microservice", "not microservice"],
}


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------

def validate_profile(profile: dict) -> list[dict]:
    """Validate one effective profile. Returns list of issues.

    Each issue: {"level": ERROR|WARNING|SUSPICIOUS, "message": str, "field": str}
    """
    issues: list[dict] = []
    fc = profile.get("forbidden_claims", [])
    fc_lower = [c.lower() for c in fc]
    key_facts = profile.get("key_facts", [])
    kf_lower = " ".join(key_facts).lower()
    ps = profile.get("platform_services", {})
    available = set(ps.get("available", []))
    not_available = set(ps.get("not_available", []))
    coming_soon = set(ps.get("coming_soon", []))

    # --- 1. CONTRADICTIONS: field vs forbidden_claims ---
    for field, patterns in FIELD_FORBIDDEN_PATTERNS.items():
        value = profile.get(field)
        if value is True:
            for pat in patterns:
                if any(pat.lower() in c for c in fc_lower):
                    issues.append({
                        "level": ERROR,
                        "field": field,
                        "message": (
                            f"{field}=true contradicts "
                            f"forbidden claim containing '{pat}'"
                        ),
                    })
                    break  # One error per field is enough

    # --- 1b. CONTRADICTIONS: has_X: true but X in not_available ---
    for bool_field, svc_key in BOOL_TO_SERVICE_KEY.items():
        value = profile.get(bool_field)
        if value is True and svc_key in not_available:
            issues.append({
                "level": ERROR,
                "field": bool_field,
                "message": (
                    f"{bool_field}=true but '{svc_key}' is in "
                    f"platform_services.not_available"
                ),
            })

    # --- 2. MISSING DATA ---
    for field, label in [
        ("database", "database unknown, needs override"),
        ("security_protocols", "security unknown"),
        ("deployment", "deployment unknown"),
    ]:
        val = profile.get(field)
        if val is None or val == []:
            issues.append({
                "level": WARNING,
                "field": field,
                "message": label,
            })

    if profile.get("multi_tenant") is None:
        issues.append({
            "level": WARNING,
            "field": "multi_tenant",
            "message": "multi_tenant unknown, needs override",
        })

    # --- 3. SUSPICIOUS INFERENCES ---
    if profile.get("cloud_native") is True:
        if "microservice" not in kf_lower and "cloud-native" not in kf_lower:
            # Only suspicious if there are key_facts at all (otherwise just missing data)
            if key_facts:
                issues.append({
                    "level": SUSPICIOUS,
                    "field": "cloud_native",
                    "message": (
                        "cloud_native=true but no 'microservice' or 'cloud-native' "
                        "found in key_facts"
                    ),
                })

    if profile.get("uses_snowflake") is True:
        if any("snowflake" in c for c in fc_lower):
            issues.append({
                "level": ERROR,
                "field": "uses_snowflake",
                "message": "uses_snowflake=true but 'Snowflake' appears in a forbidden claim",
            })

    if profile.get("microservices") is True:
        if any("not microservice" in c for c in fc_lower):
            issues.append({
                "level": ERROR,
                "field": "microservices",
                "message": "microservices=true but 'NOT microservice' in a forbidden claim",
            })

    # --- 4. PLATFORM SERVICE CONSISTENCY ---
    overlap = available & not_available
    for svc in sorted(overlap):
        issues.append({
            "level": ERROR,
            "field": "platform_services",
            "message": f"'{svc}' in both available AND not_available",
        })

    already_available = available & coming_soon
    for svc in sorted(already_available):
        issues.append({
            "level": WARNING,
            "field": "platform_services",
            "message": f"'{svc}' in both available AND coming_soon (already shipped?)",
        })

    return issues


# ---------------------------------------------------------------------------
# Auto-fix: generate override YAML for ERROR contradictions
# ---------------------------------------------------------------------------

def build_auto_fix(profile: dict, issues: list[dict]) -> Optional[dict]:
    """Build an override dict that fixes ERROR-level contradictions.

    Returns None if no fixes needed.
    """
    fixes: dict = {}
    product = profile.get("product", "unknown")

    for issue in issues:
        if issue["level"] != ERROR:
            continue
        field = issue["field"]

        # Boolean field contradicts forbidden claim → set to False
        if field in FIELD_FORBIDDEN_PATTERNS and profile.get(field) is True:
            fixes[field] = False

        # has_X contradicts not_available → override to False
        if field in BOOL_TO_SERVICE_KEY and profile.get(field) is True:
            svc_key = BOOL_TO_SERVICE_KEY[field]
            # Also move service from available to not_available via override
            fixes[field] = False
            fixes.setdefault("platform_services_remove", {}).setdefault("available", [])
            if svc_key not in fixes["platform_services_remove"]["available"]:
                fixes["platform_services_remove"]["available"].append(svc_key)

    if not fixes:
        return None

    fixes["review_notes"] = f"Auto-fix generated {datetime.now().strftime('%Y-%m-%d')}"
    fixes["last_reviewed"] = datetime.now().strftime("%Y-%m-%d")
    return fixes


def save_override(product_key: str, override: dict,
                  overrides_dir: Path = OVERRIDES_DIR) -> Path:
    """Save override YAML, merging with any existing override file."""
    overrides_dir.mkdir(parents=True, exist_ok=True)
    path = overrides_dir / f"{product_key}.yaml"

    existing = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    # Merge: new fixes layer on top of existing overrides
    for key, value in override.items():
        if key == "platform_services_remove":
            ex_ps = existing.setdefault("platform_services_remove", {})
            for list_name, items in value.items():
                ex_list = ex_ps.setdefault(list_name, [])
                for item in items:
                    if item not in ex_list:
                        ex_list.append(item)
        elif key in ("review_notes", "last_reviewed"):
            existing[key] = value
        else:
            existing[key] = value

    def _none_repr(dumper, _):
        return dumper.represent_scalar("tag:yaml.org,2002:null", "null")

    dumper = yaml.SafeDumper
    dumper.add_representer(type(None), _none_repr)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, Dumper=dumper, default_flow_style=False,
                  allow_unicode=True, sort_keys=False, width=120)

    return path


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(results: dict[str, list[dict]]) -> None:
    """Print validation report to console."""
    border = "=" * 66
    print(f"\n{border}")
    print(f"  Product Profile Validation")
    print(f"{border}")
    print(f"  {'Profile':<24}{'Errors':>8}{'Warnings':>10}  Status")
    print(f"  {'-'*24}{'-'*8}{'-'*10}  {'-'*16}")

    total_profiles = 0
    with_errors = 0
    with_warnings = 0

    for product in sorted(results.keys()):
        issues = results[product]
        errors = sum(1 for i in issues if i["level"] == ERROR)
        warnings = sum(1 for i in issues if i["level"] in (WARNING, SUSPICIOUS))
        status = "CLEAN" if not issues else ("ERRORS" if errors else "WARNINGS")

        total_profiles += 1
        if errors:
            with_errors += 1
        elif warnings:
            with_warnings += 1

        print(f"  {product:<24}{errors:>8}{warnings:>10}  {status}")

    print(f"  {'-'*24}{'-'*8}{'-'*10}  {'-'*16}")
    print(f"  Total: {total_profiles} profiles, "
          f"{with_errors} with errors, {with_warnings} with warnings")
    print(f"{border}")

    # Detail sections
    all_errors = [(p, i) for p, issues in results.items() for i in issues if i["level"] == ERROR]
    all_warnings = [(p, i) for p, issues in results.items()
                    for i in issues if i["level"] in (WARNING, SUSPICIOUS)]

    if all_errors:
        print(f"\nErrors (must fix):")
        for product, issue in sorted(all_errors, key=lambda x: x[0]):
            print(f"  {product}: {issue['message']}")

    if all_warnings:
        print(f"\nWarnings (should review):")
        for product, issue in sorted(all_warnings, key=lambda x: x[0]):
            print(f"  {product}: {issue['message']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def validate_all(
    effective_dir: Path = EFFECTIVE_DIR,
    product_filter: Optional[str] = None,
) -> dict[str, list[dict]]:
    """Validate all effective profiles. Returns {product: [issues]}."""
    results: dict[str, list[dict]] = {}

    files = sorted(effective_dir.glob("*.yaml"))
    if not files:
        print("[WARN] No effective profiles found. Run merge_profiles.py first.")
        return results

    for path in files:
        product_key = path.stem
        if product_filter and product_key != product_filter:
            continue
        with open(path, "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f) or {}
        issues = validate_profile(profile)
        results[product_key] = issues

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Validate product profiles -- detect contradictions and missing data",
    )
    parser.add_argument("--product", type=str, default=None,
                        help="Validate only one product")
    parser.add_argument("--auto-fix", action="store_true",
                        help="Generate override YAML files for ERROR contradictions")
    parser.add_argument("--merge", action="store_true",
                        help="After auto-fix, run merge_profiles to update effective")
    parser.add_argument("--effective-dir", type=str, default=str(EFFECTIVE_DIR))
    parser.add_argument("--overrides-dir", type=str, default=str(OVERRIDES_DIR))
    args = parser.parse_args()

    eff_dir = Path(args.effective_dir)
    ovr_dir = Path(args.overrides_dir)

    results = validate_all(eff_dir, args.product)
    print_report(results)

    if args.auto_fix:
        fix_count = 0
        for product, issues in results.items():
            errors = [i for i in issues if i["level"] == ERROR]
            if not errors:
                continue
            profile_path = eff_dir / f"{product}.yaml"
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = yaml.safe_load(f) or {}
            override = build_auto_fix(profile, issues)
            if override:
                path = save_override(product, override, ovr_dir)
                print(f"  [FIX] {product}: override written to {path.name}")
                fix_count += 1

        if fix_count:
            print(f"\n[OK] {fix_count} override file(s) generated")
        else:
            print("\n[OK] No auto-fixes needed")

        if args.merge and fix_count:
            print("\n[INFO] Running merge_profiles...")
            from merge_profiles import merge_all
            merge_all()


if __name__ == "__main__":
    main()
