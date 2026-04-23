"""Consolidate 1,843 JSONs using Excel (ESA009M) as truth source.

Usage:
  python3 apply_variants.py --dry-run     # analysis only, no writes
  python3 apply_variants.py               # write products_json_consolidated/
"""
import argparse, json, pathlib, re, collections, copy, sys

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "products_json_hybrid"
DST = ROOT / "products_json_consolidated"
EXCEL_MAP = ROOT / "excel_models.json"

# ---------- suffix detection rules ----------
LENGTH_RE = re.compile(r"-(?:\d+(?:\.\d+)?M|XXM)(?:[A-Z]{1,4})?$", re.IGNORECASE)
SHORT_M_RE = re.compile(r"-M(?:[A-Z]{1,4})?$")

SHOP_SUFFIXES = [
    "LANMART", "TOSS", "NAVER", "COUPANG", "SMARTSTORE",
    "GMARKET", "11ST", "AUCTION", "NTM",
]
VARIANT_SUFFIXES = [
    # user-specified (ANDOOR family etc.)
    "NEW", "REVU", "FINGER",
    # photo/break/thumbnail tags
    "BREAK-DOWN", "BREAK-LEFT", "BREAK-RIGHT", "BREAK",
    "THUMBNAIL",
    # cable construction descriptors
    "STRANDED-SS", "STRANDED", "SOLID", "SS",
    # placeholder / descriptor words
    "COLOR", "NOTICE", "CASCADING", "FEMALE", "MALE", "NOZZLE",
    "COMMON", "FOLD", "SINGLE", "SEAMLESS", "CLEAN", "CABLE",
    "TRI", "3IN1",
    # rev/version markers
    "ADD", "FIRST", "SECOND", "PASSWORD", "RESET", "PROGRAM", "ALWAYS",
    # colors (English full)
    "BLACK", "WHITE", "BLUE", "RED", "YELLOW", "GREY", "GRAY", "GREEN",
    # LED variants
    "LED1", "LED2", "LED",
    # cable color short codes (stand-alone)
    "MBK", "MBL", "MG", "MR", "MY", "MW", "MB",
    # shape/finish
    "LONG", "SHORT", "RO",
]
# trailing numeric channel marker like "-01", "-02" (usually before/after a shop suffix)
NUMERIC_TAIL_RE = re.compile(r"-\d{1,2}$")
VERSION_TAIL_RE = re.compile(r"_V\d+(?:\.\d+)?$", re.IGNORECASE)
STOP_TAG_RE = re.compile(r"(_STOP\d*|_\d+_NO|_NO)$", re.IGNORECASE)
PAREN_RE = re.compile(r"\([^)]*\)")

def strip_length(name):
    m = LENGTH_RE.search(name)
    if m:
        return name[:m.start()], m.group(0)
    m = SHORT_M_RE.search(name)
    if m and name[:m.start()].count("-") >= 1:
        return name[:m.start()], m.group(0)
    return name, None

def strip_suffixes(name, shop=True, variant=True, stop=True, paren=True, numeric=True, version=True):
    """Strip non-length suffixes. Returns (stripped, list_of_applied_rules)."""
    applied = []
    cur = name
    changed = True
    while changed:
        changed = False
        if paren:
            m = PAREN_RE.search(cur)
            if m:
                cur = PAREN_RE.sub("", cur).rstrip("-_ ")
                applied.append(f"paren:{m.group(0)}")
                changed = True
                continue
        if stop:
            m = STOP_TAG_RE.search(cur)
            if m:
                cur = STOP_TAG_RE.sub("", cur)
                applied.append(f"stop:{m.group(0)}")
                changed = True
                continue
        if version:
            m = VERSION_TAIL_RE.search(cur)
            if m:
                cur = VERSION_TAIL_RE.sub("", cur)
                applied.append(f"ver:{m.group(0)}")
                changed = True
                continue
        if shop:
            matched = False
            for sfx in SHOP_SUFFIXES:
                if re.search(rf"-{sfx}$", cur, re.IGNORECASE):
                    cur = re.sub(rf"-{sfx}$", "", cur, flags=re.IGNORECASE)
                    applied.append(f"shop:{sfx}")
                    changed = True
                    matched = True
                    break
            if matched:
                continue
        if variant:
            matched = False
            for sfx in VARIANT_SUFFIXES:
                if re.search(rf"-{sfx}$", cur, re.IGNORECASE):
                    cur = re.sub(rf"-{sfx}$", "", cur, flags=re.IGNORECASE)
                    applied.append(f"variant:{sfx}")
                    changed = True
                    matched = True
                    break
            if matched:
                continue
        if numeric:
            m = NUMERIC_TAIL_RE.search(cur)
            if m:
                cur = NUMERIC_TAIL_RE.sub("", cur)
                applied.append(f"num:{m.group(0)}")
                changed = True
                continue
    return cur, applied

# ---------- merge helpers ----------
def _norm(s):
    if not isinstance(s, str):
        return s
    return re.sub(r"\s+", " ", s.strip().lower())

def merge_list_unique(target, extra):
    if not isinstance(target, list):
        target = []
    seen = {_norm(x) for x in target if isinstance(x, str)}
    for item in (extra or []):
        key = _norm(item) if isinstance(item, str) else repr(item)
        if key not in seen:
            target.append(item)
            seen.add(key)
    return target

def merge_specs(target, extra):
    if not isinstance(target, dict):
        target = {}
    for k, v in (extra or {}).items():
        if k not in target or target[k] is None or target[k] == "":
            target[k] = v
        elif target[k] == v:
            pass
        else:
            tv = str(target[k])
            ev = str(v)
            if ev in tv:
                continue
            target[k] = f"{tv} / {ev}"
    return target

def merge_json(base, extra):
    """Merge extra JSON dict into base, preserving & enriching."""
    if base is None:
        return copy.deepcopy(extra)
    for field in ("key_features", "compatibility", "package_contents"):
        if field in extra:
            base[field] = merge_list_unique(base.get(field, []), extra[field])
    if "specifications" in extra:
        base["specifications"] = merge_specs(base.get("specifications", {}), extra["specifications"])
    # keep other fields if base lacks them
    for field in ("product_name", "category"):
        if field in extra and not base.get(field):
            base[field] = extra[field]
    return base

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    excel = json.loads(EXCEL_MAP.read_text(encoding="utf-8"))
    excel_map = excel["map"]  # {rep: [variant entries]}
    excel_reps = set(excel_map.keys())

    # Build reverse index: any full model_no (variant) -> rep
    variant_to_rep = {}
    for rep, entries in excel_map.items():
        for e in entries:
            variant_to_rep[e["model_no"]] = rep

    json_files = sorted(p.stem for p in SRC.glob("*.json"))
    print(f"Excel reps: {len(excel_reps)}   JSON files: {len(json_files)}")

    # ---------- match each JSON to a rep ----------
    matches = {}          # json_stem -> {rep, match_type, strip_chain}
    orphans = []
    match_counts = collections.Counter()

    for j in json_files:
        if j in excel_reps:
            matches[j] = {"rep": j, "match_type": "exact_rep", "strip_chain": []}
            match_counts["exact_rep"] += 1
            continue
        if j in variant_to_rep:
            rep = variant_to_rep[j]
            matches[j] = {"rep": rep, "match_type": "exact_variant", "strip_chain": []}
            match_counts["exact_variant"] += 1
            continue
        # try length strip
        s, tail = strip_length(j)
        if tail and s in excel_reps:
            matches[j] = {"rep": s, "match_type": "length_to_rep", "strip_chain": [f"length:{tail}"]}
            match_counts["length_to_rep"] += 1
            continue
        if tail and s in variant_to_rep:
            rep = variant_to_rep[s]
            matches[j] = {"rep": rep, "match_type": "length_to_variant", "strip_chain": [f"length:{tail}"]}
            match_counts["length_to_variant"] += 1
            continue
        # try other suffixes
        s2, chain = strip_suffixes(j)
        if s2 != j:
            if s2 in excel_reps:
                matches[j] = {"rep": s2, "match_type": "suffix_to_rep", "strip_chain": chain}
                match_counts["suffix_to_rep"] += 1
                continue
            if s2 in variant_to_rep:
                rep = variant_to_rep[s2]
                matches[j] = {"rep": rep, "match_type": "suffix_to_variant", "strip_chain": chain}
                match_counts["suffix_to_variant"] += 1
                continue
            # after suffix strip, try length strip on top
            s3, tail3 = strip_length(s2)
            if tail3 and s3 in excel_reps:
                matches[j] = {"rep": s3, "match_type": "suffix+length_to_rep",
                             "strip_chain": chain + [f"length:{tail3}"]}
                match_counts["suffix+length_to_rep"] += 1
                continue
            if tail3 and s3 in variant_to_rep:
                rep = variant_to_rep[s3]
                matches[j] = {"rep": rep, "match_type": "suffix+length_to_variant",
                             "strip_chain": chain + [f"length:{tail3}"]}
                match_counts["suffix+length_to_variant"] += 1
                continue
        orphans.append(j)
        match_counts["orphan_pending"] += 1

    # ---------- orphan clustering: group orphans by deep-stripped base ----------
    # For each orphan, compute its most aggressively stripped form.
    # Orphans that share the same stripped base cluster together,
    # with the shortest member as the representative.
    orphan_deep_base = {}
    for o in orphans:
        s, tail = strip_length(o)
        s, _ = strip_suffixes(s)
        # one more length pass after suffix stripping reveals inner length (e.g. -MG after stripping STRANDED)
        s2, _ = strip_length(s)
        orphan_deep_base[o] = s2 if s2 else o

    # group orphans by their deep_base
    orphan_groups = collections.defaultdict(list)
    for o, base in orphan_deep_base.items():
        orphan_groups[base].append(o)

    # Designate rep per orphan group.
    # CONSERVATIVE RULE: only cluster if the deep_base is itself a JSON file (as member).
    # Otherwise each member stays as its own orphan_self (different SKUs, merging would
    # lose information — e.g., LS-T1394-44M vs LS-T1394-46M are different products).
    json_file_set = set(json_files)
    orphan_cluster_count = 0
    for base, members in orphan_groups.items():
        # Only cluster when the bare base exists as a JSON file (acts as anchor)
        if base in json_file_set and len(members) >= 2:
            rep = base
            for m in members:
                matches[m] = {"rep": rep, "match_type": "orphan_cluster",
                              "strip_chain": [f"cluster:{base}"], "excel_backed": False}
                match_counts["orphan_cluster"] += 1
            orphan_cluster_count += 1
        else:
            for m in members:
                matches[m] = {"rep": m, "match_type": "orphan_self",
                              "strip_chain": [], "excel_backed": False}
                match_counts["orphan_self"] += 1

    # remove from pending count
    match_counts.pop("orphan_pending", None)

    # ---------- build per-rep groups ----------
    rep_to_jsons = collections.defaultdict(list)
    for j, info in matches.items():
        rep_to_jsons[info["rep"]].append(j)

    excel_backed_reps = set(rep_to_jsons.keys()) & excel_reps
    orphan_reps = set(rep_to_jsons.keys()) - excel_reps
    reps_without_json = excel_reps - excel_backed_reps

    # ---------- POST-PASS: EXPLICIT color-variant allowlist ----------
    # User-confirmed color-variant groups (same product, different color only).
    # Content for all siblings MUST be identical — merge everything under `base`.
    # Format: base -> list of Excel reps to absorb as color variants.
    # Note: if `base` itself exists in Excel with its own variants, those are kept.
    COLOR_VARIANT_GROUPS = {
        "LS-ANDOOR":  ["LS-ANDOOR-B", "LS-ANDOOR-S"],                       # 블랙 / 실버 도어락
        "LS-CAP":     ["LS-CAP-G",    "LS-CAP-S"],                          # 골드 / 실버 키캡
        "LS-MHEAT":   ["LS-MHEAT-B",  "LS-MHEAT-S"],                        # 블랙 / 실버 방열판
        "LS-USBLOCK": ["LS-USBLOCK-B", "LS-USBLOCK-P", "LS-USBLOCK-R"],     # 블루 / 핑크 / 레드 USB 잠금
    }

    absorbed_excel_for_base = collections.defaultdict(list)  # base -> [excel reps absorbed]
    absorbed = []
    for base, child_reps in COLOR_VARIANT_GROUPS.items():
        # Ensure base exists as a rep (create empty if needed — content comes from children)
        if base not in rep_to_jsons:
            rep_to_jsons[base] = []
        for cr in child_reps:
            if cr not in rep_to_jsons and cr not in excel_reps:
                continue  # nothing to absorb
            moved_jsons = list(rep_to_jsons.get(cr, []))
            for j in moved_jsons:
                matches[j] = {
                    "rep": base,
                    "match_type": "color_variant_merge",
                    "strip_chain": [f"color:{cr}"],
                    "excel_backed": False,
                    "absorbed_from": cr,
                }
                rep_to_jsons[base].append(j)
            if cr in rep_to_jsons:
                del rep_to_jsons[cr]
            absorbed.append({
                "from": cr, "to": base,
                "json_count": len(moved_jsons),
                "excel_variants": len(excel_map.get(cr, [])),
            })
            absorbed_excel_for_base[base].append(cr)
            match_counts["color_variant_merge"] += len(moved_jsons)

    if absorbed:
        print(f"\n=== Post-pass: absorbed {len(absorbed)} color-variant reps ===")
        for a in absorbed:
            print(f"  {a['from']}  ->  {a['to']}  "
                  f"(+{a['json_count']} JSON, +{a['excel_variants']} Excel variants)")
        excel_backed_reps = set(rep_to_jsons.keys()) & excel_reps
        orphan_reps = set(rep_to_jsons.keys()) - excel_reps

    # ---------- print summary ----------
    print("\n=== Matching Summary ===")
    for k, v in match_counts.most_common():
        print(f"  {k}: {v}")
    total_reps = len(rep_to_jsons)
    print(f"\nTotal reps with JSON content: {total_reps}")
    print(f"  - Excel-backed reps: {len(excel_backed_reps)}")
    print(f"  - orphan clusters (Excel 미포함): {orphan_cluster_count}")
    print(f"  - single orphans (자기 자신만): {sum(1 for j,info in matches.items() if info.get('match_type')=='orphan_self')}")
    print(f"Reps with NO JSON (image was missing): {len(reps_without_json)}")
    print(f"\nFinal consolidated file count: {total_reps}")
    print(f"Reduction: {len(json_files)} -> {total_reps} "
          f"({round((1-total_reps/len(json_files))*100,1)}% fewer)")

    # ---------- report files ----------
    report = {
        "excel_reps": len(excel_reps),
        "json_files": len(json_files),
        "match_counts": dict(match_counts),
        "total_reps": total_reps,
        "excel_backed_reps": len(excel_backed_reps),
        "orphan_cluster_reps": orphan_cluster_count,
        "single_orphans": sum(1 for j,info in matches.items() if info.get('match_type')=='orphan_self'),
        "reps_without_json_sample": sorted(reps_without_json)[:50],
        "reps_without_json_total": len(reps_without_json),
        "matches": matches,
    }
    (ROOT / "matching_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2))

    # orphan cluster details
    cluster_details = {}
    for rep in orphan_reps:
        members = rep_to_jsons[rep]
        cluster_details[rep] = members
    (ROOT / "orphan_clusters.json").write_text(
        json.dumps(cluster_details, ensure_ascii=False, indent=2))
    print(f"\nWrote: matching_report.json, orphan_clusters.json")

    if args.dry_run:
        print("\n[dry-run] stopping before write. Remove --dry-run to execute merge.")
        return

    # ---------- actually build consolidated files ----------
    DST.mkdir(exist_ok=True)
    for f in DST.glob("*.json"):
        f.unlink()

    for rep, json_stems in rep_to_jsons.items():
        merged = None
        source_files = []
        for stem in json_stems:
            d = json.loads((SRC / f"{stem}.json").read_text(encoding="utf-8"))
            merged = merge_json(merged, d)
            source_files.append(f"{stem}.json")
        if merged is None:
            continue
        merged["model_no"] = rep
        # Collect Excel variants from rep itself AND any absorbed sub-reps
        absorbed_reps = absorbed_excel_for_base.get(rep, [])
        all_excel_entries = list(excel_map.get(rep, []))
        for ar in absorbed_reps:
            all_excel_entries.extend(excel_map.get(ar, []))
        merged["variants"] = [
            {
                "model_no": e["model_no"],
                "품목코드": e["품목코드"],
                "품목명": e["품목명"],
                "variant_suffix": e.get("length_tail") or "",
                "source": "excel",
            }
            for e in all_excel_entries
        ]
        merged["_merge_meta"] = {
            "merged_from_jsons": source_files,
            "absorbed_excel_reps": absorbed_reps,
            "excel_variant_count": len(all_excel_entries),
            "json_contributor_count": len(json_stems),
        }
        (DST / f"{rep}.json").write_text(
            json.dumps(merged, ensure_ascii=False, indent=2))

    print(f"\nWrote {len(rep_to_jsons)} consolidated files to: {DST}")

if __name__ == "__main__":
    main()
