"""v3: also strip auto-bracket & placeholder prefixes from within concatenated answers."""
import json, re

PLACEHOLDER_A_STANDALONE = {
    "미답변",
    "이 상품에 대해 무엇이 궁금하세요?",
    "무엇이 궁금하세요?",
    "이 상품에 대해 무엇이 궁금하신가요?",
    "네","넵","예",
}
PLACEHOLDER_SENTENCE_RE = re.compile(
    r"(?:이\s*상품에\s*대해\s*무엇이\s*궁금하[세신]요[?.]?"
    r"|무엇이\s*궁금하[세신]요[?.]?"
    r"|주문을\s*문의합니다\.?"
    r"|이전에\s*문의하신\s*내용에\s*대한\s*답변입니다\.?)"
)

AUTO_BRACKET_SENT_RE = re.compile(
    r"\[(?:배송|주문|결제|출고|입고|도착|교환|환불|반품|상품도착|배송도착예정|배송중|배송시작|배송완료|배송안내|발송준비|발송완료|결제완료|주문확인)[^\]]*\][^.!?\n]*[.!?]?"
)

# Closing template markers (match sentence containing these)
CLOSING_PAT = re.compile(
    r"(추가로\s*문의하실\s*사항이\s*있으실\s*경우"
    r"|추가\s*문의\s*사항이\s*있으실\s*경우"
    r"|언제든지\s*말씀\s*주십시오"
    r"|언제든지\s*네이버\s*톡톡으로\s*문의"
    r"|정직한\s*가격과\s*품질로\s*고객님을"
    r"|더욱\s*만족시켜\s*드릴\s*수\s*있는\s*랜스타"
    r"|빠르고\s*성실한\s*답변으로\s*고객님의\s*문제를"
    r"|구매해\s*주셔서\s*감사"
    r"|함께해\s*주셔서\s*감사"
    r"|랜스타\s*제품\s*이용\s*중\s*불편"
    r"|감사합니다\s*$"
    r"|감사드립니다\s*$)"
)

GREETING_INTRO_RE = re.compile(
    r"(^|[\s\n])안녕하세요[.,!]?\s*(?:고객님[.,!]?\s*)?랜스타(?:입니다)?[.,!]?\s*",
    re.MULTILINE,
)

GREETING_ONLY_Q_RE = re.compile(
    r"^(?:안녕하세요|안녕하시요|감사합니다|감사해요|감사|수고하세요|수고|네|넵+|예+|알겠습니다|알겠어요|고맙습니다|확인했습니다|확인|ㄴㄴ|ㅎㅇ|ㅇㅇ|좋아요|좋습니다)[\s.,!?~ㅎㅠ\-]*$",
    re.IGNORECASE,
)

ASK_INFO_RE = re.compile(r"성함[,/\s]*(?:연락처|구매처|구매\s*날짜|구매한\s*모델명)")

def clean_text(s: str) -> str:
    if not s: return ""
    s = s.replace("\u200b","").replace("\ufeff","")
    s = re.sub(r"[ \t\r]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def is_greeting_only_q(q: str) -> bool:
    c = clean_text(q)
    return len(c) <= 15 and bool(GREETING_ONLY_Q_RE.match(c))

def is_contact_info_q(q: str) -> bool:
    c = clean_text(q)
    if ASK_INFO_RE.search(c):
        return True
    if len(re.findall(r"01[016789]-?\d{3,4}-?\d{4}", c)) >= 1 and len(c) <= 160:
        return True
    if re.match(r"^[가-힣]{2,4}\s*/\s*0\d{9,10}", c):  # name/phone/…
        return True
    return False

def strip_auto_brackets(s: str) -> str:
    return AUTO_BRACKET_SENT_RE.sub(" ", s)

def strip_placeholder_phrases(s: str) -> str:
    return PLACEHOLDER_SENTENCE_RE.sub(" ", s)

def strip_greeting_intros(s: str) -> str:
    return GREETING_INTRO_RE.sub(" ", s)

def strip_closing_sentences(s: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+|\n+", s)
    kept = [p for p in parts if p.strip() and not CLOSING_PAT.search(p)]
    return " ".join(kept).strip()

def clean_answer(a: str) -> str:
    """Full cleaning pipeline for an answer."""
    c = clean_text(a)
    c = strip_auto_brackets(c)
    c = strip_placeholder_phrases(c)
    c = strip_greeting_intros(c)
    c = strip_closing_sentences(c)
    c = re.sub(r"\s{2,}", " ", c).strip()
    return c

def score_q(q: str) -> int:
    c = clean_text(q)
    if is_greeting_only_q(c): return 0
    if is_contact_info_q(c): return 0
    if len(c) < 8: return 0
    return len(c)

def score_a(a_raw: str) -> int:
    """Score cleaned answer."""
    c = clean_answer(a_raw)
    if not c: return 0
    if c in PLACEHOLDER_A_STANDALONE: return 0
    if len(c) < 20: return 0
    # is_ask_info_only check
    is_ask_info = (len(c) <= 180 and ASK_INFO_RE.search(c))
    score = len(c)
    if is_ask_info: score = min(score, 40)  # penalize
    if re.search(r"LS-|LSN-|LSP-|MPP-|HT-|ZOT-", c): score += 50
    if re.search(r"(호환|지원|가능|불가능|사용법|설치|설정|모드|동작|속도|해상도|연결|USB|HDMI|DP|4K|8K|규격|길이|mm\b|m\b|불량|교환|환불|테스트|수거|재발송)", c): score += 30
    return score

def consolidate_session(session):
    turns = session.get("qna", [])
    transcript = []
    q_candidates = []
    a_candidates = []  # list of (score, raw_a, cleaned_a)
    for t in turns:
        q = clean_text(t.get("question") or "")
        a = clean_text(t.get("answer") or "")
        if q:
            transcript.append({"role":"customer","text":q})
            s = score_q(q)
            if s > 0:
                q_candidates.append((s, q))
        if a:
            transcript.append({"role":"agent","text":a})
            cleaned = clean_answer(a)
            s = score_a(a)
            if s > 0 and cleaned:
                a_candidates.append((s, cleaned))
    if not q_candidates or not a_candidates:
        return None
    q_candidates.sort(key=lambda x: -x[0])
    a_candidates.sort(key=lambda x: -x[0])

    def dedup(items):
        seen, out = set(), []
        for _, x in items:
            key = re.sub(r"\s+", "", x)[:60]
            if key in seen: continue
            seen.add(key); out.append(x)
        return out
    qs = dedup(q_candidates)
    as_ = dedup(a_candidates)

    # Compose question
    if len(qs) == 1:
        question = qs[0]
    else:
        extras = [q for q in qs[1:] if len(q) > 15][:3]
        question = qs[0] + ("\n" + "\n".join(f"(추가 문의) {q}" for q in extras) if extras else "")

    # Compose answer: take top-ranked substantive ones
    answer = as_[0] if len(as_) == 1 else "\n\n".join(as_[:3])

    return {
        "chatUrl": session.get("chatUrl",""),
        "dateIso": session.get("dateIso",""),
        "name": session.get("name",""),
        "category": session.get("category",""),
        "isTechnical": session.get("isTechnical"),
        "modelConfidence": session.get("modelConfidence",""),
        "question": question,
        "answer": answer,
        "turn_count": len(turns),
        "source_model": session.get("originalModel",""),
        "transcript": transcript,
    }

if __name__ == "__main__":
    with open('/sessions/nice-clever-maxwell/mnt/uploads/talktalk-qna-grouped.json','r',encoding='utf-8') as f:
        raw = json.load(f)

    for model_key in ["LS-HDM402KVM-4K", "LS-UH319-W", "LS-UC202"]:
        sessions = raw["byModel"].get(model_key, [])
        print(f"\n========== {model_key} ({len(sessions)} sessions) ==========")
        out = []
        dropped = 0
        for s in sessions:
            c = consolidate_session(s)
            if c is None: dropped += 1; continue
            out.append(c)
        print(f"kept: {len(out)}, dropped: {dropped}")
        for i, c in enumerate(out[:6]):
            print(f"\n--- #{i+1} {c['chatUrl']} [{c['category']}] ({c['turn_count']}turns) ---")
            print(f"Q: {c['question'][:400]}")
            print(f"A: {c['answer'][:400]}")
