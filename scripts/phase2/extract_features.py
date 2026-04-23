#!/usr/bin/env python3
"""
Phase 2 배치 스크립트: 1,588개 제품 JSON에 'features' 필드를 추가한다.

입력:  서버의 aicc2/products/*.json (SFTP로 다운로드)
       /sessions/.../phase2/feature_schema.json
       /sessions/.../phase2/intent_synonyms.json
출력:  서버의 aicc2/products/*.json (features 필드 추가된 상태로 업로드)

특징:
- 재개 가능: progress.json에 성공한 모델 기록. 중단 후 재실행 시 skip.
- 병렬: asyncio.Semaphore(3)으로 API 동시 3개.
- 레이트 리밋: 429 응답 시 지수 백오프 (2s → 8s → 32s).
- 비파괴: 기존 필드는 그대로 두고 features + feature_evidence + feature_extracted_at 만 추가.
- 드라이런: --dry-run 으로 LLM 호출·SFTP 업로드 없이 JSON 변환만 확인.

실행:
  python extract_features.py --limit 5 --dry-run        # 5개 시범
  python extract_features.py --family kvm_switch        # 한 family만
  python extract_features.py                             # 전체 1,588개
"""

import os, sys, json, time, random, argparse, asyncio, hashlib
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# ================= CONFIG =================
HERE = Path(__file__).parent
SCHEMA_PATH = HERE / "feature_schema.json"
SYN_PATH    = HERE / "intent_synonyms.json"
FAMILY_MAP  = HERE.parent / "category_to_family.json"   # 102 cat → 26 family
PROGRESS    = HERE / "progress.json"
OUTDIR      = HERE / "products_with_features"           # 로컬 출력

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL   = "gemini-2.5-flash"  # 빠르고 싸고 충분히 정확함
GEMINI_URL     = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

# SFTP (고도몰 Pro 호스팅)
SFTP_HOST = "${GODOMALL_SFTP_HOST}"
SFTP_PORT = 17662
SFTP_USER = "${GODOMALL_SFTP_USER}"
SFTP_PASS = "${GODOMALL_SFTP_PASSWORD}"
REMOTE_DIR = "aicc2/products"

CONCURRENT = 12
MAX_RETRY  = 4
DEADLINE   = None  # set in main if --max-seconds

# ================= PROMPT TEMPLATE =================
SYSTEM_PROMPT = """너는 LANstar의 IT 주변기기 카탈로그 분석가다.
주어진 상품 원시 데이터(제품명, key_features, specifications, usage_instructions, raw_text)를 바탕으로
정해진 스키마대로 'features' 객체를 JSON으로 출력한다.

[규칙]
1. 오직 JSON만 출력. 설명·코드블록 금지.
2. 스키마에 정의되지 않은 키는 추가하지 말 것.
3. 근거가 명확하지 않으면 값은 null. false와 null을 엄격히 구분.
4. 각 태그마다 feature_evidence[태그명] = "근거 원문 조각(30자 이내)" 을 같이 제공.
5. bool/enum/number/list[enum] 타입을 정확히 지켜라.
6. 고객이 자주 쓰는 자연어(예: "키보드로 전환")와 canonical tag(예: switching_methods=["hotkey"])의 매핑 힌트는 synonyms를 참고하라.

[출력 스키마]
{
  "features": {
    "connectivity": {...},
    "power": {...},
    ...(universal)
    "family": {...}
  },
  "feature_evidence": {
    "<dotted.path>": "<원문 30자>"
  }
}
"""

def build_prompt(product, family, schema, synonyms):
    """ Gemini에 보낼 단일 프롬프트를 만든다. """
    fam_schema  = schema["family_specific"].get(family, {})
    uni_schema  = schema["universal"]
    compact_src = {
        "model_no":     product.get("model_no") or product.get("modelNo"),
        "product_name": product.get("product_name"),
        "category":     product.get("category"),
        "family":       family,
        "key_features": product.get("key_features", [])[:20],
        "specifications": dict(list(product.get("specifications", {}).items())[:25]),
        "usage_instructions": (product.get("usage_instructions") or "")[:1500],
        "raw_text_excerpt":  (product.get("raw_text") or "")[:3000],
    }
    prompt = {
        "schema_universal": uni_schema,
        "schema_family":    fam_schema,
        "intent_synonyms":  synonyms,
        "source_product":   compact_src,
    }
    return SYSTEM_PROMPT + "\n\n[입력]\n" + json.dumps(prompt, ensure_ascii=False, indent=2)

# ================= GEMINI HTTP =================
import httpx  # pip install httpx

async def call_gemini(session: httpx.AsyncClient, text: str) -> dict:
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        }
    }
    last_err = None
    for attempt in range(MAX_RETRY):
        try:
            r = await session.post(GEMINI_URL, json=body, timeout=90)
            if r.status_code == 200:
                data = r.json()
                out = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(out)
            if r.status_code in (429, 503):
                wait = 2 * (2 ** attempt) + random.uniform(0, 1)
                print(f"  [retry {attempt+1}] {r.status_code}, wait {wait:.1f}s", flush=True)
                await asyncio.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        except Exception as e:
            last_err = e
            if attempt == MAX_RETRY - 1:
                raise
            await asyncio.sleep(2 * (2 ** attempt))
    raise RuntimeError(f"Exhausted retries: {last_err}")

# ================= PROGRESS =================
def load_progress():
    if PROGRESS.exists():
        return json.loads(PROGRESS.read_text())
    return {"done": [], "failed": {}}

def save_progress(prog):
    PROGRESS.write_text(json.dumps(prog, ensure_ascii=False, indent=2))

# ================= WORKER =================
async def process_one(session, product, family, schema, synonyms, dry_run):
    model_no = product.get("model_no") or product.get("modelNo")
    prompt = build_prompt(product, family, schema, synonyms)
    if dry_run:
        print(f"  [dry] {model_no} ({family}) - prompt {len(prompt)} chars")
        return {"features": {"_dry_run": True}, "feature_evidence": {}}
    try:
        result = await call_gemini(session, prompt)
    except Exception as e:
        raise RuntimeError(f"{model_no}: {e}")
    # 비파괴 병합: 기존 필드 그대로 두고 features 만 추가
    product["features"] = result.get("features", {})
    product["feature_evidence"] = result.get("feature_evidence", {})
    product["feature_extracted_at"] = datetime.now().isoformat(timespec="seconds")
    product["feature_schema_version"] = "1.0.0"
    return product

# ================= MAIN =================
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0=전체, N>0이면 N개만")
    ap.add_argument("--family", help="특정 family만 (예: kvm_switch)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-seconds", type=int, default=0, help="N초 지나면 graceful exit")
    ap.add_argument("--source", default=str(HERE.parent / "aicc_handoff/aicc_consolidated.json"),
                    help="입력 JSON 경로 (기본: aicc_consolidated.json)")
    args = ap.parse_args()

    schema  = json.loads(SCHEMA_PATH.read_text())
    synonyms= json.loads(SYN_PATH.read_text())
    fam_map = json.loads(FAMILY_MAP.read_text())

    src = json.loads(Path(args.source).read_text())
    models = src["models"]
    print(f"Source models: {len(models)}")

    OUTDIR.mkdir(exist_ok=True)
    prog = load_progress()
    done = set(prog["done"])

    # 작업 목록 결정
    tasks = []
    for mno, p in models.items():
        cats = p.get("category") or []
        fam = None
        for c in cats:
            if c in fam_map:
                fam = fam_map[c]; break
        if not fam:
            continue  # family 모르면 skip
        if args.family and fam != args.family:
            continue
        if mno in done:
            continue
        tasks.append((mno, p, fam))

    if args.limit:
        tasks = tasks[:args.limit]

    print(f"Will process: {len(tasks)} products (dry={args.dry_run})")
    if not tasks:
        return

    deadline = (time.time() + args.max_seconds) if args.max_seconds else None
    stopped = False

    queue = asyncio.Queue()
    for t in tasks:
        queue.put_nowait(t)

    async with httpx.AsyncClient() as session:
        async def worker_loop(wid):
            nonlocal stopped
            while True:
                if deadline and time.time() > deadline:
                    stopped = True
                    return
                try:
                    mno, p, fam = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    out = await process_one(session, p, fam, schema, synonyms, args.dry_run)
                    (OUTDIR / f"{mno}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
                    prog["done"].append(mno)
                    if len(prog["done"]) % 10 == 0:
                        save_progress(prog)
                    print(f"  [ok] {mno} ({fam})", flush=True)
                except Exception as e:
                    prog["failed"][mno] = str(e)[:300]
                    print(f"  [FAIL] {mno}: {e}", flush=True)

        await asyncio.gather(*[worker_loop(i) for i in range(CONCURRENT)])

    save_progress(prog)
    remain = queue.qsize()
    tag = f" (time-limited exit, remaining={remain})" if stopped else ""
    print(f"Done{tag}. Success: {len(prog['done'])}, Failed: {len(prog['failed'])}")

if __name__ == "__main__":
    if not GEMINI_API_KEY and not any("--dry-run" in a for a in sys.argv):
        print("ERROR: GEMINI_API_KEY 환경변수를 설정해야 합니다. (--dry-run 만 예외)")
        sys.exit(2)
    asyncio.run(main())
