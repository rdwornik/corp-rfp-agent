"""Generate product profiles from CKE JSON extractions + Platform Services Matrix.

Reads:
  - CKE Service Description JSON (--svc)
  - CKE Architecture JSON (--arch)
  - Platform Matrix (config/platform_matrix.json, pre-parsed from Excel)

Writes:
  - config/product_profiles/_generated/{product}.yaml  (one per product)
  - config/product_profiles/_generation_report.json     (audit trail)

Usage:
  python src/generate_product_profiles.py --svc svc.json --arch arch.json
  python src/generate_product_profiles.py --svc svc.json --arch arch.json --product wms
  python src/generate_product_profiles.py --dry-run
  python src/generate_product_profiles.py --diff
"""

import argparse
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = PROJECT_ROOT / "config" / "platform_matrix.json"
PROFILES_DIR = PROJECT_ROOT / "config" / "product_profiles"
GENERATED_DIR = PROFILES_DIR / "_generated"
REPORT_PATH = PROFILES_DIR / "_generation_report.json"
CHANGELOG_PATH = PROFILES_DIR / "_changelog.jsonl"


# ---------------------------------------------------------------------------
# Product name mapping: canonical_key -> CKE JSON names
# ---------------------------------------------------------------------------
PRODUCT_NAME_MAP = {
    "platform":              ["Blue Yonder Platform"],
    "demand_planning":       ["Blue Yonder Demand Planning", "Blue Yonder Cognitive Demand Planning"],
    "supply_planning":       ["Blue Yonder Supply Planning", "Blue Yonder Enterprise Supply Planning (ESP)"],
    "ibp":                   ["Blue Yonder Integrated Business Planning"],
    "category_management":   ["Blue Yonder Category Management"],
    "assortment":            ["Blue Yonder Luminate Assortment", "Strategic Assortment"],
    "allocation_replenishment": ["Blue Yonder Allocation and Replenishment"],
    "merchandise_planning":  ["Blue Yonder Merchandise Financial Planning"],
    "replenishment_retail":  ["Blue Yonder Replenishment for Retail"],
    "forecasting_retail":    ["Blue Yonder Forecasting for Retail"],
    "wms":                   ["Blue Yonder WMS", "Warehouse Management"],
    "wms_native":            ["Platform Native Warehouse Management"],
    "wms_labor":             ["Warehouse Labor Management"],
    "tms":                   ["Blue Yonder TMS", "TM, TP, TMU, Archive"],
    "oms":                   ["Blue Yonder OMS", "Inventory & Commits Service", "Order Services"],
    "control_tower":         ["Blue Yonder Control Tower", "Control Tower Visibility"],
    "network":               ["Blue Yonder Network Design", "Blue Yonder Network", "Command Center"],
    "workforce":             ["Workforce Management"],
    "doddle":                ["Returns Management"],
    "flexis":                ["Order Sequencing", "Order Slotting"],
    "order_sequencing":      ["Blue Yonder Order Sequencing / Slotting"],
}

# Canonical key -> human display name
DISPLAY_NAMES = {
    "platform":              "Blue Yonder Platform",
    "demand_planning":       "Blue Yonder Demand Planning",
    "supply_planning":       "Blue Yonder Supply Planning",
    "ibp":                   "Blue Yonder Integrated Business Planning",
    "category_management":   "Blue Yonder Category Management",
    "assortment":            "Blue Yonder Strategic Assortment",
    "allocation_replenishment": "Blue Yonder Allocation and Replenishment",
    "merchandise_planning":  "Blue Yonder Merchandise Financial Planning",
    "replenishment_retail":  "Blue Yonder Replenishment for Retail",
    "forecasting_retail":    "Blue Yonder Forecasting for Retail",
    "wms":                   "Blue Yonder Warehouse Management",
    "wms_native":            "Blue Yonder Platform Native WMS",
    "wms_labor":             "Blue Yonder Warehouse Labor Management",
    "tms":                   "Blue Yonder Transportation Management",
    "oms":                   "Blue Yonder Order Management",
    "control_tower":         "Blue Yonder Control Tower",
    "network":               "Blue Yonder Network Design",
    "workforce":             "Blue Yonder Workforce Management",
    "doddle":                "Blue Yonder Returns Management",
    "flexis":                "Blue Yonder Order Sequencing",
    "order_sequencing":      "Blue Yonder Order Sequencing / Slotting",
}

# Service name -> normalized key
SERVICE_KEY_MAP = {
    "Access Management - Authentication": "authentication",
    "Access Management - Authorization": "authorization",
    "API Management": "api_management",
    "Runtime Environment": "runtime_environment",
    "User Experience (Portal)": "user_experience_portal",
    "Application Lifecycle Management": "alm",
    "Automated Environment Provisioning": "automated_provisioning",
    "Workflow Orchestrator": "workflow_orchestrator",
    "Business Data Management": "bdm",
    "Blue Yonder Data Share App": "data_share_app",
    "Bulk Ingestion": "bulk_ingestion",
    "Bulk Distribution": "bulk_distribution",
    "Curated Data": "curated_data",
    "Streaming Ingestion": "streaming_ingestion",
    "Streaming Distribution": "streaming_distribution",
    "DaaS - Egress": "daas_egress",
    "DaaS - Snowflake share": "daas_snowflake_share",
    "DaaS - Reader account": "daas_reader_account",
    "DaaS - Archive": "daas_archive",
    "ML Studio": "ml_studio",
    "Analytics": "analytics",
}


# ---------------------------------------------------------------------------
# CKE parsing
# ---------------------------------------------------------------------------

def load_cke_json(path: Path) -> dict:
    """Load CKE extraction JSON. Returns products dict."""
    if not path or not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("products", data)


def _resolve_cke_key(cke_products: dict, canonical_key: str) -> Optional[str]:
    """Find the CKE product key matching a canonical key."""
    aliases = PRODUCT_NAME_MAP.get(canonical_key, [])
    for alias in aliases:
        if alias in cke_products:
            return alias
        # Case-insensitive fallback
        for k in cke_products:
            if k.lower() == alias.lower():
                return k
    return None


def merge_cke_facts(svc_products: dict, arch_products: dict, canonical_key: str) -> dict:
    """Merge facts from both CKE sources for one product. Dedup by fact text."""
    merged: dict[str, list] = {}

    for source_label, products in [("service_description", svc_products),
                                    ("architecture", arch_products)]:
        cke_key = _resolve_cke_key(products, canonical_key)
        if not cke_key:
            continue
        product_data = products[cke_key]

        for category, items in product_data.items():
            if isinstance(items, list):
                if category not in merged:
                    merged[category] = []
                for item in items:
                    if isinstance(item, dict):
                        fact = item.get("fact", "")
                    else:
                        fact = str(item)
                    # Dedup by normalized fact text
                    existing_facts = {
                        (f.get("fact", "") if isinstance(f, dict) else str(f)).lower().strip()
                        for f in merged[category]
                    }
                    if fact.lower().strip() not in existing_facts:
                        if isinstance(item, dict):
                            item_copy = dict(item)
                            item_copy.setdefault("_source", source_label)
                            merged[category].append(item_copy)
                        else:
                            merged[category].append({"fact": fact, "_source": source_label})
            elif isinstance(items, str):
                # Single value (e.g. not_supported as string)
                if category not in merged:
                    merged[category] = []
                merged[category].append({"fact": items, "_source": source_label})

    return merged


def _flat_facts(items: list) -> list[str]:
    """Extract plain fact strings from a CKE items list."""
    result = []
    for item in items:
        if isinstance(item, dict):
            result.append(item.get("fact", ""))
        elif isinstance(item, str):
            result.append(item)
    return [f for f in result if f]


# ---------------------------------------------------------------------------
# Inference helpers (pure logic, no LLM)
# ---------------------------------------------------------------------------

def _infer_bool(facts: list[str], *, positive: list[str], negative: list[str]) -> Optional[bool]:
    """Infer boolean from facts. Returns True/False/None (unknown)."""
    lower_facts = " ".join(facts).lower()
    for neg in negative:
        if neg.lower() in lower_facts:
            return False
    for pos in positive:
        if pos.lower() in lower_facts:
            return True
    return None


def _check_contains(facts: list[str], *keywords: str) -> Optional[bool]:
    """Check if any keyword appears in facts."""
    lower_facts = " ".join(facts).lower()
    for kw in keywords:
        if kw.lower() in lower_facts:
            return True
    return None


def _extract_keywords(facts: list[str], keyword_map: dict[str, list[str]]) -> list[str]:
    """Extract matching keywords from facts."""
    lower_facts = " ".join(facts).lower()
    found = []
    for key, patterns in keyword_map.items():
        for p in patterns:
            if p.lower() in lower_facts:
                if key not in found:
                    found.append(key)
                break
    return found


def _build_forbidden_claims(cke_facts: dict) -> list[str]:
    """Build forbidden claims from CKE not_supported + architecture facts."""
    forbidden = []
    not_supported = _flat_facts(cke_facts.get("not_supported", []))
    for item in not_supported:
        claim = item.strip()
        if claim and claim not in forbidden:
            forbidden.append(claim)
    return forbidden


def _extract_key_facts(cke_facts: dict) -> list[str]:
    """Extract high-confidence key facts across all categories."""
    key_facts = []
    for category in ("platform_integration", "architecture", "data_layer"):
        items = cke_facts.get(category, [])
        for item in items:
            if isinstance(item, dict):
                conf = item.get("confidence", "")
                fact = item.get("fact", "")
                if conf == "high" and fact and len(fact) < 200:
                    if fact not in key_facts:
                        key_facts.append(fact)
    return key_facts[:20]  # Cap at 20


# ---------------------------------------------------------------------------
# Platform services from matrix
# ---------------------------------------------------------------------------

def load_platform_matrix(path: Path = MATRIX_PATH) -> dict:
    """Load pre-parsed platform_matrix.json."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_services_for_solution(matrix: dict, solution_code: str) -> dict:
    """Extract platform services for one solution from the matrix.

    Returns:
        {
            "available": ["authentication", ...],
            "not_available": ["bdm", ...],
            "coming_soon": ["streaming_ingestion"],
            "via_other": {}
        }
    """
    solution = matrix.get("solutions", {}).get(solution_code)
    if not solution:
        return {"available": [], "not_available": [], "coming_soon": [], "via_other": {}}

    available = []
    not_available = []
    coming_soon = []
    via_other = {}

    for svc_name, status_info in solution.get("services", {}).items():
        svc_key = SERVICE_KEY_MAP.get(svc_name)
        if not svc_key:
            continue

        status = status_info.get("status", "infrastructure")
        is_available = status_info.get("available", False)
        note = status_info.get("note")

        if status == "native" and is_available:
            available.append(svc_key)
        elif status == "coming":
            coming_soon.append(svc_key)
        elif note and "via" in str(note).lower():
            via_other[svc_key] = note
        else:
            not_available.append(svc_key)

    return {
        "available": sorted(available),
        "not_available": sorted(not_available),
        "coming_soon": sorted(coming_soon),
        "via_other": via_other,
    }


# ---------------------------------------------------------------------------
# Profile generation
# ---------------------------------------------------------------------------

def generate_profile(
    canonical_key: str,
    cke_facts: dict,
    services: dict,
    matrix_solution: Optional[dict] = None,
) -> dict:
    """Generate a single product profile from CKE facts + platform services."""
    not_supported = _flat_facts(cke_facts.get("not_supported", []))
    arch_facts = _flat_facts(cke_facts.get("architecture", []))
    all_arch = not_supported + arch_facts
    deploy_facts = _flat_facts(cke_facts.get("deployment", []))
    data_facts = _flat_facts(cke_facts.get("data_layer", []))
    platform_facts = _flat_facts(cke_facts.get("platform_integration", []))
    api_facts = _flat_facts(cke_facts.get("apis", []))
    security_facts = _flat_facts(cke_facts.get("security", []))
    scalability_facts = _flat_facts(cke_facts.get("scalability", []))

    cloud_native = matrix_solution.get("cloud_native") if matrix_solution else None
    if cloud_native is None:
        cloud_native = _infer_bool(all_arch,
                                    positive=["cloud-native", "microservice"],
                                    negative=["NOT cloud-native", "not cloud-native"])

    available = services.get("available", [])

    profile = {
        "product": canonical_key,
        "display_name": DISPLAY_NAMES.get(canonical_key, canonical_key),

        # Architecture (from CKE)
        "cloud_native": cloud_native,
        "deployment": _extract_keywords(deploy_facts + arch_facts, {
            "azure": ["azure"], "aws": ["aws"], "on_prem": ["on-prem", "on premise"],
        }),
        "multi_tenant": _infer_bool(all_arch,
                                     positive=["multi-tenant"],
                                     negative=["NOT multi-tenant", "dedicated"]),
        "microservices": _infer_bool(all_arch,
                                      positive=["microservice"],
                                      negative=["NOT microservice"]),
        "uses_snowflake": _check_contains(data_facts + platform_facts, "snowflake"),
        "uses_pdc": _check_contains(data_facts + platform_facts,
                                     "platform data cloud", "pdc"),
        "database": [],  # Left for manual override
        "apis": _extract_keywords(api_facts, {
            "rest": ["rest"], "json": ["json"], "sftp": ["sftp"],
            "kafka": ["kafka"], "azure_blob": ["azure blob"],
            "soap": ["soap"], "graphql": ["graphql"],
        }),
        "security_protocols": _extract_keywords(security_facts, {
            "saml2": ["saml"], "oauth2": ["oauth"], "oidc": ["openid connect", "oidc"],
        }),

        # Platform services (from matrix)
        "platform_services": services,

        # Convenience booleans
        "has_analytics": "analytics" in available,
        "has_ml_studio": "ml_studio" in available,
        "has_bdm": "bdm" in available,
        "has_workflow": "workflow_orchestrator" in available,
        "has_bulk_ingestion": "bulk_ingestion" in available,
        "has_streaming": "streaming_ingestion" in available,
        "has_data_share": "data_share_app" in available,
        "has_daas": "daas_egress" in available,

        # Guardrails
        "forbidden_claims": _build_forbidden_claims(cke_facts),
        "key_facts": _extract_key_facts(cke_facts),
        "scalability_notes": scalability_facts,

        # Metadata
        "_meta": {
            "generated_at": datetime.now().isoformat(),
            "cke_sources": [],
            "matrix_source": "platform_matrix.json",
            "cke_fact_count": sum(len(v) for v in cke_facts.values() if isinstance(v, list)),
            "platform_services_count": len(available),
            "status": "draft",
        },
    }

    # Add forbidden claims for missing platform services
    for svc_key in services.get("not_available", []):
        svc_display = svc_key.replace("_", " ").title()
        claim = f"Platform service '{svc_display}' is not available for this product"
        if claim not in profile["forbidden_claims"]:
            profile["forbidden_claims"].append(claim)

    return profile


# ---------------------------------------------------------------------------
# YAML output
# ---------------------------------------------------------------------------

def _yaml_representer_none(dumper, _):
    return dumper.represent_scalar("tag:yaml.org,2002:null", "null")


def save_profile_yaml(profile: dict, path: Path) -> None:
    """Save profile as YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)

    dumper = yaml.SafeDumper
    dumper.add_representer(type(None), _yaml_representer_none)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(profile, f, Dumper=dumper, default_flow_style=False,
                  allow_unicode=True, sort_keys=False, width=120)


# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------

def append_changelog(entry: dict) -> None:
    """Append one entry to _changelog.jsonl."""
    CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CHANGELOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Main generation pipeline
# ---------------------------------------------------------------------------

def generate_all(
    svc_path: Optional[Path] = None,
    arch_path: Optional[Path] = None,
    matrix_path: Path = MATRIX_PATH,
    output_dir: Path = GENERATED_DIR,
    product_filter: Optional[str] = None,
    dry_run: bool = False,
    diff: bool = False,
) -> dict:
    """Generate all product profiles.

    Returns report dict with counts and details.
    """
    # Load sources
    svc_products = load_cke_json(svc_path) if svc_path else {}
    arch_products = load_cke_json(arch_path) if arch_path else {}
    matrix = load_platform_matrix(matrix_path)

    solutions = matrix.get("solutions", {})
    families = matrix.get("product_families", {})

    report = {
        "timestamp": datetime.now().isoformat(),
        "sources": {
            "svc": str(svc_path) if svc_path else None,
            "arch": str(arch_path) if arch_path else None,
            "matrix": str(matrix_path),
        },
        "products_generated": [],
        "products_skipped": [],
        "total": 0,
        "cke_matches": 0,
        "matrix_only": 0,
    }

    # Build list of products to generate
    product_keys = []
    if product_filter:
        if product_filter in solutions:
            product_keys = [product_filter]
        else:
            print(f"[ERROR] Unknown product: {product_filter}")
            print(f"  Available: {', '.join(sorted(solutions.keys()))}")
            return report
    else:
        product_keys = list(solutions.keys())

    output_dir.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now().isoformat()

    for key in sorted(product_keys):
        solution_info = solutions.get(key, {})

        # Merge CKE facts
        cke_facts = merge_cke_facts(svc_products, arch_products, key)

        # Extract platform services
        services = extract_services_for_solution(matrix, key)

        # Generate profile
        profile = generate_profile(key, cke_facts, services, solution_info)

        # Track CKE source files used
        cke_sources = []
        if svc_path and _resolve_cke_key(svc_products, key):
            cke_sources.append("service_description")
        if arch_path and _resolve_cke_key(arch_products, key):
            cke_sources.append("architecture")
        profile["_meta"]["cke_sources"] = cke_sources

        has_cke = bool(cke_sources)

        if has_cke:
            report["cke_matches"] += 1
        else:
            report["matrix_only"] += 1

        out_path = output_dir / f"{key}.yaml"

        if diff and out_path.exists():
            # Load existing and compare
            with open(out_path, "r", encoding="utf-8") as f:
                old = yaml.safe_load(f)
            if old == profile:
                report["products_skipped"].append(key)
                continue
            else:
                print(f"  [DIFF] {key}: changed")

        if dry_run:
            print(f"  [DRY] {key}: {profile['_meta']['cke_fact_count']} CKE facts, "
                  f"{profile['_meta']['platform_services_count']} services")
        else:
            save_profile_yaml(profile, out_path)
            append_changelog({
                "timestamp": now_iso,
                "action": "generated",
                "product": key,
                "cke_fact_count": profile["_meta"]["cke_fact_count"],
                "platform_services_count": profile["_meta"]["platform_services_count"],
                "cke_sources": cke_sources,
            })

        report["products_generated"].append(key)
        report["total"] += 1

    # Save generation report
    if not dry_run:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] Generated {report['total']} profiles "
          f"({report['cke_matches']} with CKE, {report['matrix_only']} matrix-only)")

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate product profiles from CKE extractions + Platform Matrix",
    )
    parser.add_argument("--svc", type=str, default=None,
                        help="Path to CKE Service Description JSON")
    parser.add_argument("--arch", type=str, default=None,
                        help="Path to CKE Architecture JSON")
    parser.add_argument("--matrix", type=str, default=str(MATRIX_PATH),
                        help="Path to platform_matrix.json")
    parser.add_argument("--output-dir", type=str, default=str(GENERATED_DIR),
                        help="Output directory for generated profiles")
    parser.add_argument("--product", type=str, default=None,
                        help="Generate only one product (solution code)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be generated without writing files")
    parser.add_argument("--diff", action="store_true",
                        help="Only regenerate profiles that have changed")
    parser.add_argument("--full", action="store_true",
                        help="Generate + merge in one step")
    args = parser.parse_args()

    svc_path = Path(args.svc) if args.svc else None
    arch_path = Path(args.arch) if args.arch else None
    matrix_path = Path(args.matrix)
    output_dir = Path(args.output_dir)

    if svc_path and not svc_path.exists():
        print(f"[ERROR] Service Description file not found: {svc_path}")
        sys.exit(1)
    if arch_path and not arch_path.exists():
        print(f"[ERROR] Architecture file not found: {arch_path}")
        sys.exit(1)
    if not matrix_path.exists():
        print(f"[ERROR] Platform matrix not found: {matrix_path}")
        sys.exit(1)

    report = generate_all(
        svc_path=svc_path,
        arch_path=arch_path,
        matrix_path=matrix_path,
        output_dir=output_dir,
        product_filter=args.product,
        dry_run=args.dry_run,
        diff=args.diff,
    )

    if args.full and not args.dry_run:
        print("\n[INFO] Running merge...")
        from merge_profiles import merge_all
        merge_all()


if __name__ == "__main__":
    main()
