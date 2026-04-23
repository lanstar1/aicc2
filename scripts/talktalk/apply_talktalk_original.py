"""Merge talktalk-qna-grouped.json into products_json_consolidated/.

Input structure:
  {
    "meta": {...},
    "byModel": { "LS-XXX": [ {chatUrl, date, qna: [{question, answer}, ...], ...}, ... ] },
    "unknownModelQna": [...]
  }

Matching uses same rules as apply_reviews.py / apply_qna.py.
Adds `talktalk_qna[]` field to each JSON (each item = one Q&A pair with session context),
plus `_talktalk_meta`.
"""
import json, pathlib, re, collections

ROOT = pathlib.Path(__file__).resolve().parent
DST = ROOT / "products_json_consolidated"
SRC = ROOT / "talktalk-qna-grouped.json"
UNMATCHED_OUT = ROOT / "talktalk_unmatched.json"
REPORT_OUT = ROOT / "talktalk_matching_report.json"

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
    if norm in COLOR_MAP: return COLOR_MAP[norm], "color_variant"
    if norm in rep_set: return norm, "exact_rep"
    if norm in variant_to_rep: return variant_to_rep[norm], "exact_variant"
    if norm in excel_reps: return norm, "excel_rep_no_json"
    if norm in excel_variant_to_rep: return excel_variant_to_rep[norm], "excel_variant_no_json"
    ls = strip_length(norm)
    if ls != norm:
        if ls in rep_set: return ls, "length_to_rep"
        if ls in variant_to_rep: return variant_to_rep[ls], "length_to_variant"
        if ls in COLOR_MAP: return COLOR_MAP[ls], "length_to_color_variant"
        if ls in excel_reps: return ls, "length_to_excel_rep_no_json"
        if ls in excel_variant_to_rep: return excel_variant_to_rep[ls], "length_to_excel_variant_no_json"
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
    # consolidated lookup
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

    # Load talktalk source
    data = json.loads(SRC.read_text(encoding="utf-8"))
    meta = data.get("meta", {})
    by_model = data.get("byModel", {})
    unknown = data.get("unknownModelQna", [])

    print(f"Consolidated reps: {len(consolidated)}, Excel reps: {len(excel_reps)}")
    print(f"Source models: {len(by_model)}, total records: {meta.get('totalRecords')}")
    print(f"  technical: {meta.get('technicalCount')}, non-technical: {meta.get('nonTechnicalCount')}")
    print(f"  unknown (no model): {len(unknown)}")

    # Match each model
    matches = []
    unmatched = []
    by_rep = collections.defaultdict(list)  # rep -> [session, ...]

    for raw, sessions in by_model.items():
        if not isinstance(sessions, list):
            continue
        norm = normalize_model(raw)
        rep, match_type = find_rep(norm, rep_set, variant_to_rep, excel_reps, excel_variant_to_rep)
        session_count = len(sessions)
        qna_pair_count = sum(len(s.get("qna", [])) for s in sessions)
        if rep:
            matches.append({"raw": raw, "normalized": norm, "rep": rep,
                           "match_type": match_type,
                           "session_count": session_count,
                           "qna_pair_count": qna_pair_count})
            by_rep[rep].append({"source_model": raw, "normalized": norm, "sessions": sessions})
        else:
            unmatched.append({"raw": raw, "normalized": norm,
                             "session_count": session_count,
                             "qna_pair_count": qna_pair_count})

    match_counts = collections.Counter(m["match_type"] for m in matches)
    total_matched_qna = sum(m["qna_pair_count"] for m in matches)
    total_unmatched_qna = sum(u["qna_pair_count"] for u in unmatched)
    print("\n=== TalkTalk Matching Summary ===")
    for k, v in match_counts.most_common():
        print(f"  {k}: {v}")
    print(f"  unmatched: {len(unmatched)}")
    print(f"\nModels matched: {len(matches)} / {len(by_model)}")
    print(f"QnA pairs matched:   {total_matched_qna}")
    print(f"QnA pairs unmatched: {total_unmatched_qna}")
    print(f"Source unknownModelQna records: {len(unknown)}")

    # Merge into JSONs
    updated = 0
    created = 0
    written_qna = 0
    for rep, bundles in by_rep.items():
        fp = DST / f"{rep}.json"
        if fp.exists():
            d = json.loads(fp.read_text(encoding="utf-8"))
        else:
            excel_entries = excel_map.get(rep, [])
            d = {
                "model_no": rep,
                "product_name": excel_entries[0]["품목명"] if excel_entries else "",
                "category": "", "key_features": [], "specifications": {},
                "compatibility": [], "package_contents": [],
                "variants": [{
                    "model_no": e["model_no"], "품목코드": e["품목코드"],
                    "품목명": e["품목명"],
                    "variant_suffix": e.get("length_tail") or "",
                    "source": "excel",
                } for e in excel_entries],
                "_merge_meta": {
                    "merged_from_jsons": [], "absorbed_excel_reps": [],
                    "excel_variant_count": len(excel_entries),
                    "json_contributor_count": 0,
                    "_note": "Excel-only stub — created by apply_talktalk.py",
                },
            }
            created += 1

        all_items = []
        source_models = []
        cat_counter = collections.Counter()
        for bundle in bundles:
            source_models.append(bundle["source_model"])
            for s in bundle["sessions"]:
                cat = s.get("category", "")
                cat_counter[cat] += 1
                for pair in s.get("qna", []):
                    q = (pair.get("question") or "").strip()
                    a = (pair.get("answer") or "").strip()
                    if not q and not a:
                        continue
                    all_items.append({
                        "source_model": bundle["source_model"],
                        "chatUrl": s.get("chatUrl", ""),
                        "dateIso": s.get("dateIso", ""),
                        "name": s.get("name", ""),
                        "isTechnical": s.get("isTechnical"),
                        "category": cat,
                        "modelConfidence": s.get("modelConfidence", ""),
                        "question": q,
                        "answer": a,
                    })
        if not all_items:
            continue
        d["talktalk_qna"] = all_items
        d["_talktalk_meta"] = {
            "source_models": source_models,
            "qna_pair_count": len(all_items),
            "category_breakdown": dict(cat_counter),
        }
        fp.write_text(json.dumps(d, ensure_ascii=False, indent=2))
        updated += 1
        written_qna += len(all_items)

    print(f"\nUpdated/created {updated} JSON files.")
    print(f"  (new Excel-only stubs: {created})")
    print(f"Total TalkTalk QnA pairs written: {written_qna}")

    REPORT_OUT.write_text(json.dumps({
        "source_meta": meta,
        "models_total": len(by_model),
        "models_matched": len(matches),
        "models_unmatched": len(unmatched),
        "qna_pairs_matched": total_matched_qna,
        "qna_pairs_unmatched_model_level": total_unmatched_qna,
        "unknown_model_qna_in_source": len(unknown),
        "match_counts": dict(match_counts),
        "matches": matches,
    }, ensure_ascii=False, indent=2))

    UNMATCHED_OUT.write_text(json.dumps({
        "model_level_unmatched": unmatched,
        "unknown_model_qna_in_source_preview": unknown[:20],
        "unknown_model_qna_in_source_count": len(unknown),
    }, ensure_ascii=False, indent=2))
    print(f"\nWrote: talktalk_matching_report.json, talktalk_unmatched.json")


if __name__ == "__main__":
    main()
