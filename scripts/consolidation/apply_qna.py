"""Merge smartstore-qna.json into products_json_consolidated/.

Matching uses same rules as apply_reviews.py:
  color-variant map, exact rep, exact variant, Excel fallback,
  length strip, suffix strip. Unmatched → reported separately.

Adds `qna[]` field to each JSON, plus `_qna_meta`.
"""
import json, pathlib, re, collections

ROOT = pathlib.Path(__file__).resolve().parent
DST = ROOT / "products_json_consolidated"
QNA_SRC = ROOT / "smartstore-qna.json"
UNMATCHED_OUT = ROOT / "qna_unmatched.json"
REPORT_OUT = ROOT / "qna_matching_report.json"

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

COLOR_MAP = {
    "LS-ANDOOR-B": "LS-ANDOOR", "LS-ANDOOR-S": "LS-ANDOOR",
    "LS-CAP-G": "LS-CAP", "LS-CAP-S": "LS-CAP",
    "LS-MHEAT-B": "LS-MHEAT", "LS-MHEAT-S": "LS-MHEAT",
    "LS-USBLOCK-B": "LS-USBLOCK", "LS-USBLOCK-P": "LS-USBLOCK", "LS-USBLOCK-R": "LS-USBLOCK",
}

LS_CODE_IN_PAREN = re.compile(r"\((LS[^\)\s]+)\)")


def normalize_model(raw: str) -> str:
    m = LS_CODE_IN_PAREN.search(raw)
    if m:
        return m.group(1).strip()
    return PAREN_TAG_RE.sub("", raw).strip()


def strip_length(name):
    m = LENGTH_RE.search(name)
    if m: return name[:m.start()]
    m = SHORT_M_RE.search(name)
    if m and name[:m.start()].count("-") >= 1:
        return name[:m.start()]
    return name


def strip_suffix_once(name):
    for sfx in SHOP_SUFFIXES + VARIANT_SUFFIXES:
        if re.search(rf"-{re.escape(sfx)}$", name, re.IGNORECASE):
            return re.sub(rf"-{re.escape(sfx)}$", "", name, flags=re.IGNORECASE)
    for pat in (STOP_TAG_RE, VERSION_TAIL_RE, NUMERIC_TAIL_RE):
        if pat.search(name):
            return pat.sub("", name)
    return None


def find_rep(norm, rep_set, variant_to_rep, excel_reps, excel_variant_to_rep):
    """Return (rep, match_type) or (None, None)."""
    if norm in COLOR_MAP:
        return COLOR_MAP[norm], "color_variant"
    if norm in rep_set:
        return norm, "exact_rep"
    if norm in variant_to_rep:
        return variant_to_rep[norm], "exact_variant"
    if norm in excel_reps:
        return norm, "excel_rep_no_json"
    if norm in excel_variant_to_rep:
        return excel_variant_to_rep[norm], "excel_variant_no_json"
    # length strip
    ls = strip_length(norm)
    if ls != norm:
        if ls in rep_set: return ls, "length_to_rep"
        if ls in variant_to_rep: return variant_to_rep[ls], "length_to_variant"
        if ls in COLOR_MAP: return COLOR_MAP[ls], "length_to_color_variant"
        if ls in excel_reps: return ls, "length_to_excel_rep_no_json"
        if ls in excel_variant_to_rep: return excel_variant_to_rep[ls], "length_to_excel_variant_no_json"
    # suffix strip
    ss = strip_suffix_once(norm)
    if ss:
        if ss in rep_set: return ss, "suffix_to_rep"
        if ss in variant_to_rep: return variant_to_rep[ss], "suffix_to_variant"
        if ss in excel_reps: return ss, "suffix_to_excel_rep_no_json"
        if ss in excel_variant_to_rep: return excel_variant_to_rep[ss], "suffix_to_excel_variant_no_json"
        ss_l = strip_length(ss)
        if ss_l in rep_set: return ss_l, "suffix+length_to_rep"
        if ss_l in variant_to_rep: return variant_to_rep[ss_l], "suffix+length_to_variant"
        if ss_l in excel_reps: return ss_l, "suffix+length_to_excel_rep_no_json"
    return None, None


def main():
    # Load consolidated
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
    rep_set = set(consolidated.keys())

    excel_map = json.loads((ROOT / "excel_models.json").read_text(encoding="utf-8"))["map"]
    excel_reps = set(excel_map.keys())
    excel_variant_to_rep = {}
    for r, entries in excel_map.items():
        for e in entries:
            excel_variant_to_rep[e["model_no"]] = r

    print(f"Consolidated reps: {len(consolidated)}")
    print(f"Excel reps: {len(excel_reps)}")

    # Load QnA
    qna_data = json.loads(QNA_SRC.read_text(encoding="utf-8"))
    meta = qna_data.get("_meta", {})
    unmatched_in_source = qna_data.get("_unmatched", [])
    model_keys = [k for k in qna_data.keys() if not k.startswith("_")]
    print(f"\nQnA source meta: totalRawComments={meta.get('totalRawComments')}, "
          f"matched={meta.get('totalMatchedToModel')}, unmatched_in_source={len(unmatched_in_source)}")
    print(f"Model entries in source: {len(model_keys)}")

    # ---- Match each model key to a rep ----
    matches = []
    unmatched = []
    by_rep = collections.defaultdict(list)  # rep -> list of {source_model, qna[]}

    for raw in model_keys:
        norm = normalize_model(raw)
        qna_list = qna_data[raw]
        if not isinstance(qna_list, list):
            continue
        rep, match_type = find_rep(norm, rep_set, variant_to_rep, excel_reps, excel_variant_to_rep)
        if rep:
            matches.append({"raw": raw, "normalized": norm, "rep": rep,
                           "match_type": match_type, "qna_count": len(qna_list)})
            by_rep[rep].append({"source_model": raw, "normalized": norm, "qna": qna_list})
        else:
            unmatched.append({"raw": raw, "normalized": norm, "qna_count": len(qna_list)})

    match_counts = collections.Counter(m["match_type"] for m in matches)
    print("\n=== QnA Matching Summary ===")
    for k, v in match_counts.most_common():
        print(f"  {k}: {v}")
    print(f"  unmatched: {len(unmatched)}")

    total_qna_matched = sum(m["qna_count"] for m in matches)
    total_qna_unmatched = sum(u["qna_count"] for u in unmatched)
    print(f"\nModel keys matched: {len(matches)} / {len(model_keys)}")
    print(f"Individual QnA matched: {total_qna_matched}")
    print(f"Individual QnA unmatched (model-level): {total_qna_unmatched}")
    print(f"QnA unmatched in source file (_unmatched list): {len(unmatched_in_source)}")

    # ---- Merge into consolidated JSONs ----
    updated_files = 0
    created_files = 0
    total_qna_written = 0
    for rep, bundles in by_rep.items():
        fp = DST / f"{rep}.json"
        if fp.exists():
            d = json.loads(fp.read_text(encoding="utf-8"))
        else:
            # Create Excel-only stub
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
                    } for e in excel_entries
                ],
                "_merge_meta": {
                    "merged_from_jsons": [],
                    "absorbed_excel_reps": [],
                    "excel_variant_count": len(excel_entries),
                    "json_contributor_count": 0,
                    "_note": "Excel-only stub — created by apply_qna.py",
                },
            }
            created_files += 1

        all_qna = []
        source_models = []
        for bundle in bundles:
            source_models.append(bundle["source_model"])
            for q in bundle["qna"]:
                rec = {
                    "source_model": bundle["source_model"],
                    "id": q.get("id"),
                    "date": q.get("date", ""),
                    "answerDate": q.get("answerDate", ""),
                    "user": q.get("user", ""),
                    "productName": q.get("productName", ""),
                    "question": (q.get("question") or "").strip(),
                    "answer": (q.get("answer") or "").strip(),
                }
                if rec["question"] or rec["answer"]:
                    all_qna.append(rec)
        if not all_qna:
            continue
        d["qna"] = all_qna
        d["_qna_meta"] = {
            "source_models": source_models,
            "qna_count": len(all_qna),
        }
        fp.write_text(json.dumps(d, ensure_ascii=False, indent=2))
        updated_files += 1
        total_qna_written += len(all_qna)

    print(f"\nUpdated/created {updated_files} consolidated JSON files.")
    print(f"  (new Excel-only stubs created: {created_files})")
    print(f"Total QnA records written: {total_qna_written}")

    # ---- Reports ----
    REPORT_OUT.write_text(json.dumps({
        "source_meta": meta,
        "model_keys_total": len(model_keys),
        "model_keys_matched": len(matches),
        "model_keys_unmatched": len(unmatched),
        "qna_total_matched": total_qna_matched,
        "qna_total_unmatched_model_level": total_qna_unmatched,
        "qna_total_unmatched_in_source_file": len(unmatched_in_source),
        "match_counts": dict(match_counts),
        "matches": matches,
    }, ensure_ascii=False, indent=2))

    UNMATCHED_OUT.write_text(json.dumps({
        "model_level_unmatched": unmatched,
        "source_file_unmatched_preview": unmatched_in_source[:20],
        "source_file_unmatched_count": len(unmatched_in_source),
    }, ensure_ascii=False, indent=2))

    print(f"\nWrote: qna_matching_report.json, qna_unmatched.json")


if __name__ == "__main__":
    main()
