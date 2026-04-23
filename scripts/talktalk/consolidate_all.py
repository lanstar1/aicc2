"""Run consolidation over all models, save per-model JSON snippets for merging."""
import json, re, os, pathlib

sys_path = '/tmp/tt_work'
import sys; sys.path.insert(0, sys_path)
from consolidate_v3 import consolidate_session

with open('/sessions/nice-clever-maxwell/mnt/uploads/talktalk-qna-grouped.json','r',encoding='utf-8') as f:
    raw = json.load(f)

# We need model-name -> rep resolution. Reuse apply_talktalk.py's matching.
# Simpler: just use raw model key = rep (most cases). For accurate, reload the mapping from local consolidated folder:
LOCAL_CONS = pathlib.Path('/sessions/nice-clever-maxwell/mnt/mac_handoff/products_json_consolidated')
LOCAL_REPS = {p.stem for p in LOCAL_CONS.glob('*.json')}
# Build variant -> rep map
variant_to_rep = {}
for fp in LOCAL_CONS.glob('*.json'):
    d = json.loads(fp.read_text(encoding='utf-8'))
    rep = fp.stem
    for v in d.get('variants', []):
        vm = v.get('model_no')
        if vm and vm not in variant_to_rep:
            variant_to_rep[vm] = rep

# Excel reps (from apply_talktalk.py)
EXCEL_PATH = pathlib.Path('/sessions/nice-clever-maxwell/mnt/mac_handoff/excel_models.json')
excel_map = json.loads(EXCEL_PATH.read_text(encoding='utf-8'))['map']
excel_reps = set(excel_map.keys())
excel_variant_to_rep = {}
for r, entries in excel_map.items():
    for e in entries:
        excel_variant_to_rep[e['model_no']] = r

# Copy the matching rules from apply_talktalk.py
LENGTH_RE = re.compile(r"-(?:\d+(?:\.\d+)?M|XXM)(?:[A-Z]{1,4})?$", re.IGNORECASE)
SHORT_M_RE = re.compile(r"-M(?:[A-Z]{1,4})?$")
SHOP_SUFFIXES = ["LANMART","TOSS","NAVER","COUPANG","SMARTSTORE","GMARKET","11ST","AUCTION","NTM"]
VARIANT_SUFFIXES = ["NEW","REVU","FINGER","BREAK-DOWN","BREAK-LEFT","BREAK-RIGHT","BREAK","THUMBNAIL","STRANDED-SS","STRANDED","SOLID","SS","COLOR","NOTICE","CASCADING","FEMALE","MALE","NOZZLE","COMMON","FOLD","SINGLE","SEAMLESS","CLEAN","CABLE","TRI","3IN1","ADD","FIRST","SECOND","PASSWORD","RESET","PROGRAM","ALWAYS","BLACK","WHITE","BLUE","RED","YELLOW","GREY","GRAY","GREEN","LED1","LED2","LED","MBK","MBL","MG","MR","MY","MW","MB","LONG","SHORT","RO"]
NUMERIC_TAIL_RE = re.compile(r"-\d{1,2}$")
VERSION_TAIL_RE = re.compile(r"_V\d+(?:\.\d+)?$", re.IGNORECASE)
STOP_TAG_RE = re.compile(r"(_STOP\d*|_\d+_NO|_NO)$", re.IGNORECASE)
PAREN_TAG_RE = re.compile(r"\([^)]*\)")
LS_CODE_IN_PAREN = re.compile(r"\((LS[^\)\s]+)\)")
COLOR_MAP = {"LS-ANDOOR-B":"LS-ANDOOR","LS-ANDOOR-S":"LS-ANDOOR","LS-CAP-G":"LS-CAP","LS-CAP-S":"LS-CAP","LS-MHEAT-B":"LS-MHEAT","LS-MHEAT-S":"LS-MHEAT","LS-USBLOCK-B":"LS-USBLOCK","LS-USBLOCK-P":"LS-USBLOCK","LS-USBLOCK-R":"LS-USBLOCK"}

def normalize_model(raw):
    m = LS_CODE_IN_PAREN.search(raw)
    if m: return m.group(1).strip()
    return PAREN_TAG_RE.sub("", raw).strip()

def strip_length(name):
    m = LENGTH_RE.search(name)
    if m: return name[:m.start()]
    m = SHORT_M_RE.search(name)
    if m and name[:m.start()].count("-") >= 1: return name[:m.start()]
    return name

def strip_suffix_once(name):
    for sfx in SHOP_SUFFIXES + VARIANT_SUFFIXES:
        if re.search(rf"-{re.escape(sfx)}$", name, re.IGNORECASE):
            return re.sub(rf"-{re.escape(sfx)}$", "", name, flags=re.IGNORECASE)
    for pat in (STOP_TAG_RE, VERSION_TAIL_RE, NUMERIC_TAIL_RE):
        if pat.search(name):
            return pat.sub("", name)
    return None

def find_rep(norm):
    if norm in COLOR_MAP: return COLOR_MAP[norm]
    if norm in LOCAL_REPS: return norm
    if norm in variant_to_rep: return variant_to_rep[norm]
    if norm in excel_reps: return norm
    if norm in excel_variant_to_rep: return excel_variant_to_rep[norm]
    ls = strip_length(norm)
    if ls != norm:
        if ls in LOCAL_REPS: return ls
        if ls in variant_to_rep: return variant_to_rep[ls]
        if ls in COLOR_MAP: return COLOR_MAP[ls]
        if ls in excel_reps: return ls
        if ls in excel_variant_to_rep: return excel_variant_to_rep[ls]
    ss = strip_suffix_once(norm)
    if ss:
        if ss in LOCAL_REPS: return ss
        if ss in variant_to_rep: return variant_to_rep[ss]
        if ss in excel_reps: return ss
        if ss in excel_variant_to_rep: return excel_variant_to_rep[ss]
        ssl = strip_length(ss)
        if ssl in LOCAL_REPS: return ssl
        if ssl in variant_to_rep: return variant_to_rep[ssl]
        if ssl in excel_reps: return ssl
    return None

# Run consolidation per model, bucketed by rep
by_rep = {}  # rep -> list of consolidated entries (sorted by dateIso desc, capped)
stats_total_raw = 0
stats_total_kept = 0
for raw_key, sessions in raw['byModel'].items():
    norm = normalize_model(raw_key)
    rep = find_rep(norm)
    if not rep: continue
    for s in sessions:
        stats_total_raw += 1
        c = consolidate_session(s)
        if c is None: continue
        stats_total_kept += 1
        by_rep.setdefault(rep, []).append(c)

# Sort each rep's entries by date desc, keep up to 30
for rep in list(by_rep.keys()):
    by_rep[rep].sort(key=lambda x: x.get('dateIso',''), reverse=True)
    by_rep[rep] = by_rep[rep][:30]

# Save
out = {
    'reps': by_rep,
    'stats': {
        'total_raw_sessions': stats_total_raw,
        'total_kept_sessions': stats_total_kept,
        'drop_rate': round(1 - (stats_total_kept / stats_total_raw), 3) if stats_total_raw else 0,
        'rep_count': len(by_rep),
    }
}
with open('/tmp/tt_work/consolidated_by_rep.json','w',encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"raw sessions: {stats_total_raw}")
print(f"kept (consolidated entries): {stats_total_kept}")
print(f"drop rate: {out['stats']['drop_rate']}")
print(f"unique reps with TT: {len(by_rep)}")
print(f"output: /tmp/tt_work/consolidated_by_rep.json")
print(f"top reps by entry count:")
for rep, items in sorted(by_rep.items(), key=lambda x: -len(x[1]))[:15]:
    print(f"  {rep}: {len(items)}")
