"""Merge review_merged.json into products_json_consolidated/.

Matching strategy:
  1. Normalize review model name (extract LS- code from parens if present).
  2. Exact match against a consolidated rep.
  3. Match against any variants[].model_no inside consolidated files.
  4. Apply length/suffix stripping (same rules as apply_variants.py).
  5. Otherwise → unmatched bucket.

Appends reviews[] field to each consolidated JSON, plus _review_meta.
"""
import json, pathlib, re, collections, sys

ROOT = pathlib.Path(__file__).resolve().parent
DST = ROOT / "products_json_consolidated"
REVIEWS_SRC = ROOT / "review_merged.json"
UNMATCHED_OUT = ROOT / "reviews_unmatched.json"
REPORT_OUT = ROOT / "review_matching_report.json"

# Reuse suffix rules from apply_variants.py
LENGTH_RE = re.compile(r"-(?:\d+(?:\.\d+)?M|XXM)(?:[A-Z]{1,4})?$", re.IGNORECASE)
SHORT_M_RE = re.compile(r"-M(?:[A-Z]{1,4})?$")
SHOP_SUFFIXES = ["LANMART","TOSS","NAVER","COUPANG","SMARTSTORE","GMARKET","11ST","AUCTION","NTM"]
VARIANT_SUFFIXES = [
    "NEW","REVU","FINGER",
    "BREAK-DOWN","BREAK-LEFT","BREAK-RIGHT","BREAK","THUMBNAIL",
    "STRANDED-SS","STRANDED","SOLID","SS",
    "COLOR","NOTICE","CASCADING","FEMALE","MALE","NOZZLE",
    "COMMON","FOLD","SINGLE","SEAMLESS","CLEAN","CABLE","TRI","3IN1",
    "ADD","FIRST","SECOND","PASSWORD","RESET","PROGRAM","ALWAYS",
    "BLACK","WHITE","BLUE","RED","YELLOW","GREY","GRAY","GREEN",
    "LED1","LED2","LED",
    "MBK","MBL","MG","MR","MY","MW","MB",
    "LONG","SHORT","RO",
]
NUMERIC_TAIL_RE = re.compile(r"-\d{1,2}$")
VERSION_TAIL_RE = re.compile(r"_V\d+(?:\.\d+)?$", re.IGNORECASE)
STOP_TAG_RE = re.compile(r"(_STOP\d*|_\d+_NO|_NO)$", re.IGNORECASE)
PAREN_TAG_RE = re.compile(r"\([^)]*\)")

# Color-variant merges (from apply_variants.py)
COLOR_MAP = {
    "LS-ANDOOR-B": "LS-ANDOOR", "LS-ANDOOR-S": "LS-ANDOOR",
    "LS-CAP-G": "LS-CAP", "LS-CAP-S": "LS-CAP",
    "LS-MHEAT-B": "LS-MHEAT", "LS-MHEAT-S": "LS-MHEAT",
    "LS-USBLOCK-B": "LS-USBLOCK", "LS-USBLOCK-P": "LS-USBLOCK", "LS-USBLOCK-R": "LS-USBLOCK",
}

LS_CODE_IN_PAREN = re.compile(r"\((LS[^\)\s]+)\)")

def normalize_review_model(raw: str) -> str:
    """Extract the real LS-* code from a review model name.
    e.g. 'KYT826-PD65W-CCA (LS-GAN65W)' -> 'LS-GAN65W'
         'LS-1000H' -> 'LS-1000H'
    """
    m = LS_CODE_IN_PAREN.search(raw)
    if m:
        return m.group(1).strip()
    # Also strip trailing paren notes like "LS-FOO (bar)"
    base = PAREN_TAG_RE.sub("", raw).strip()
    return base


def strip_length(name):
    m = LENGTH_RE.search(name)
    if m: return name[:m.start()]
    m = SHORT_M_RE.search(name)
    if m and name[:m.start()].count("-") >= 1:
        return name[:m.start()]
    return name


def strip_suffix_once(name):
    """Strip one non-length suffix. Returns stripped or None."""
    for sfx in SHOP_SUFFIXES + VARIANT_SUFFIXES:
        if re.search(rf"-{re.escape(sfx)}$", name, re.IGNORECASE):
            return re.sub(rf"-{re.escape(sfx)}$", "", name, flags=re.IGNORECASE)
    for pat in (STOP_TAG_RE, VERSION_TAIL_RE, NUMERIC_TAIL_RE):
        if pat.search(name):
            return pat.sub("", name)
    return None


def main():
    # Load consolidated files — build model lookup
    consolidated = {}
    variant_to_rep = {}
    for fp in sorted(DST.glob("*.json")):
        d = json.loads(fp.read_text(encoding="utf-8"))
        rep = fp.stem
        consolidated[rep] = d
        for v in d.get("variants", []):
            vm = v.get("model_no")
            if vm and vm not in variant_to_rep:
                variant_to_rep[vm] = rep

    print(f"Consolidated reps: {len(consolidated)}")
    print(f"Excel variants indexed (via consolidated): {len(variant_to_rep)}")

    rep_set = set(consolidated.keys())

    # Excel fallback: reps & variants from Excel directly (covers reps with no JSON)
    excel_map = json.loads((ROOT / "excel_models.json").read_text(encoding="utf-8"))["map"]
    excel_reps = set(excel_map.keys())
    excel_variant_to_rep = {}
    for r, entries in excel_map.items():
        for e in entries:
            excel_variant_to_rep[e["model_no"]] = r
    print(f"Excel reps (truth source): {len(excel_reps)}")

    # Load reviews
    reviews_raw = json.loads(REVIEWS_SRC.read_text(encoding="utf-8"))
    print(f"Review entries: {len(reviews_raw)}")

    # ---- Matching ----
    matches = []      # list of (raw, normalized, rep, match_type)
    unmatched = []
    by_rep = collections.defaultdict(list)  # rep -> list of review entries

    for entry in reviews_raw:
        raw = entry.get("모델명", "").strip()
        if not raw:
            continue
        norm = normalize_review_model(raw)
        reviews = entry.get("리뷰", [])

        match_type = None
        rep = None

        # 1. Color-variant remap
        if norm in COLOR_MAP:
            rep = COLOR_MAP[norm]
            match_type = "color_variant"
        # 2. Exact rep
        elif norm in rep_set:
            rep = norm
            match_type = "exact_rep"
        # 3. Exact variant (in existing consolidated)
        elif norm in variant_to_rep:
            rep = variant_to_rep[norm]
            match_type = "exact_variant"
        # 3b. Exact match against Excel (creates new JSON if no consolidated file yet)
        elif norm in excel_reps:
            rep = norm
            match_type = "excel_rep_no_json"
        elif norm in excel_variant_to_rep:
            rep = excel_variant_to_rep[norm]
            match_type = "excel_variant_no_json"
        else:
            # 4. Length strip
            ls = strip_length(norm)
            if ls != norm:
                if ls in rep_set:
                    rep, match_type = ls, "length_to_rep"
                elif ls in variant_to_rep:
                    rep, match_type = variant_to_rep[ls], "length_to_variant"
                elif ls in COLOR_MAP:
                    rep, match_type = COLOR_MAP[ls], "length_to_color_variant"
                elif ls in excel_reps:
                    rep, match_type = ls, "length_to_excel_rep_no_json"
                elif ls in excel_variant_to_rep:
                    rep, match_type = excel_variant_to_rep[ls], "length_to_excel_variant_no_json"
            # 5. Suffix strip (one level)
            if rep is None:
                ss = strip_suffix_once(norm)
                if ss and ss in rep_set:
                    rep, match_type = ss, "suffix_to_rep"
                elif ss and ss in variant_to_rep:
                    rep, match_type = variant_to_rep[ss], "suffix_to_variant"
                elif ss and ss in excel_reps:
                    rep, match_type = ss, "suffix_to_excel_rep_no_json"
                elif ss and ss in excel_variant_to_rep:
                    rep, match_type = excel_variant_to_rep[ss], "suffix_to_excel_variant_no_json"
                elif ss:
                    # 6. Length after suffix
                    ss_l = strip_length(ss)
                    if ss_l in rep_set:
                        rep, match_type = ss_l, "suffix+length_to_rep"
                    elif ss_l in variant_to_rep:
                        rep, match_type = variant_to_rep[ss_l], "suffix+length_to_variant"

        if rep:
            matches.append({"raw": raw, "normalized": norm, "rep": rep,
                            "match_type": match_type, "review_count": len(reviews)})
            by_rep[rep].append({
                "source_model": raw,
                "normalized": norm,
                "reviews": reviews,
            })
        else:
            unmatched.append({"raw": raw, "normalized": norm, "review_count": len(reviews)})

    # ---- Summary ----
    match_counts = collections.Counter(m["match_type"] for m in matches)
    print("\n=== Review Matching Summary ===")
    for k, v in match_counts.most_common():
        print(f"  {k}: {v}")
    print(f"  unmatched: {len(unmatched)}")
    total_reviews_matched = sum(m["review_count"] for m in matches)
    total_reviews_unmatched = sum(u["review_count"] for u in unmatched)
    print(f"\nReview model entries matched:   {len(matches)} / {len(reviews_raw)}")
    print(f"Individual reviews matched:     {total_reviews_matched}")
    print(f"Individual reviews unmatched:   {total_reviews_unmatched}")
    print(f"Reps with at least one review:  {len(by_rep)}")

    # ---- Merge reviews into consolidated JSONs (create Excel-only stubs if missing) ----
    updated_files = 0
    created_files = 0
    total_reviews_written = 0
    for rep, review_bundles in by_rep.items():
        fp = DST / f"{rep}.json"
        if fp.exists():
            d = json.loads(fp.read_text(encoding="utf-8"))
        else:
            # Excel-backed but no OCR content → create minimal stub from Excel
            excel_entries = excel_map.get(rep, [])
            d = {
                "model_no": rep,
                "product_name": excel_entries[0]["품목명"] if excel_entries else "",
                "category": "",
                "key_features": [],
                "specifications": {},
                "compatibility": [],
                "package_contents": [],
                "variants": [
                    {
                        "model_no": e["model_no"],
                        "품목코드": e["품목코드"],
                        "품목명": e["품목명"],
                        "variant_suffix": e.get("length_tail") or "",
                        "source": "excel",
                    }
                    for e in excel_entries
                ],
                "_merge_meta": {
                    "merged_from_jsons": [],
                    "absorbed_excel_reps": [],
                    "excel_variant_count": len(excel_entries),
                    "json_contributor_count": 0,
                    "_note": "Excel-only stub — created by apply_reviews.py because reviews exist but no OCR JSON",
                },
            }
            created_files += 1

        all_reviews = []
        source_models = []
        for bundle in review_bundles:
            source_models.append(bundle["source_model"])
            for r in bundle["reviews"]:
                rec = {
                    "source_model": bundle["source_model"],
                    "내용": r.get("리뷰상세내용", "").strip(),
                }
                related = r.get("관련리뷰상세내용")
                if related:
                    rec["관련내용"] = related.strip()
                if rec["내용"] or rec.get("관련내용"):
                    all_reviews.append(rec)
        if not all_reviews:
            continue
        d["reviews"] = all_reviews
        d["_review_meta"] = {
            "source_models": source_models,
            "review_count": len(all_reviews),
        }
        fp.write_text(json.dumps(d, ensure_ascii=False, indent=2))
        if fp.exists():
            updated_files += 1
        total_reviews_written += len(all_reviews)

    print(f"\nUpdated/created {updated_files} consolidated JSON files.")
    print(f"  (new Excel-only stubs created: {created_files})")
    print(f"Total reviews written: {total_reviews_written}")

    # ---- Report files ----
    REPORT_OUT.write_text(json.dumps({
        "review_entries_total": len(reviews_raw),
        "review_entries_matched": len(matches),
        "review_entries_unmatched": len(unmatched),
        "individual_reviews_matched": total_reviews_matched,
        "individual_reviews_unmatched": total_reviews_unmatched,
        "reps_with_reviews": len(by_rep),
        "match_counts": dict(match_counts),
        "matches": matches,
    }, ensure_ascii=False, indent=2))

    UNMATCHED_OUT.write_text(json.dumps(unmatched, ensure_ascii=False, indent=2))
    print(f"\nWrote: review_matching_report.json, reviews_unmatched.json")

if __name__ == "__main__":
    main()
