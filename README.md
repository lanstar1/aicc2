# AICC2 — 랜스타 AI 고객 상담 센터

단일 HTML 프론트엔드 + 정적 JSON 제품 DB 기반 AI 상담 도구. Gemini API를 직접 호출해 상품 문의(제품별 답변)와 톡톡 멀티턴 상담을 모두 지원한다.

**Live**: https://lanstar.co.kr/aicc2/index.html

## 구조

```
public/aicc2/                  # 실제 서빙되는 정적 자산 (서버 루트 /aicc2/ 에 그대로 동기화)
  index.html                   # 단일 파일 SPA (HTML + CSS + JS 인라인, ~159 KB)
  products/LS-*.json           # 1,588개 제품 consolidated JSON
  features_index.json          # 제품별 feature 태그 인덱스
  models_index.json            # 모델명 → rep 매핑
  categories_index.json        # 카테고리 인덱스
  templates.json               # 카테고리별 질문 템플릿

scripts/
  talktalk/                    # 톡톡 상담 원본 → consolidated 가공 파이프라인
  consolidation/               # 리뷰/QnA/variants 병합 스크립트 (원본 데이터는 미포함)
  phase2/                      # Gemini Flash 기반 feature 추출 배치
```

## 주요 기능

- **상품문의 탭**: 모델명 → 제품 로드 → AI 프롬프트(OCR + 리뷰 + QnA + 톡톡 few-shot) → JSON 구조화 답변 + 추론경로 표시
- **톡톡 멀티턴 탭**: 제품 전환/핸드오프/요약, 이미지 첨부 지원
- **이력(문의관리) 탭**: 세션 저장, 검색, 드래그 정렬, 삭제
- **설정 모달 (⚙️)**: 첫 인사말 / 마무리 멘트 / 마무리 단축키(기본 `/마무리`)
- **사이드바 드래그 정렬**: 이전 상담 재정렬 + 삭제

## 데이터 파이프라인 (상위 요약)

1. 품목 마스터(엑셀) + Document AI OCR → 제품 raw JSON 1,843건
2. 리뷰/스마트스토어 QnA/톡톡 상담 로그를 모델명으로 매칭, 각 제품 JSON에 `reviews`, `qna`, `talktalk_qna`로 merge
3. Gemini Flash로 feature 태그(Phase 2) 추출 → `features`, `feature_evidence` 필드 추가
4. 1,588개 rep 파일로 정제 후 `public/aicc2/products/`에 배포

### 톡톡 consolidation (v1 — 2026-04)

원본 `talktalk-qna-grouped.json`은 한 상담 스레드를 턴 단위로 쪼개면서 Q/A가 어긋나 있음(예: "미답변", "이 상품에 대해 무엇이 궁금하세요?" 등의 placeholder가 A에 섞여 들어감).

해결:
- `scripts/talktalk/consolidate_v3.py` — 스레드별 점수제로 substantive Q/A 재조합, placeholder·자동 알림·마감 템플릿 제거
- `scripts/talktalk/consolidate_all.py` — 전체 byModel 루프, rep별 consolidated 세트 생성
- `scripts/talktalk/apply_to_server.py` — 서버의 product JSON에 `talktalk_qna` 교체

결과: 1,120 raw sessions → 759 consolidated entries across 266 reps.

## 배포

```bash
# SFTP 환경변수 설정 후
cp .env.example .env && vim .env

# index.html 변경
python3 scripts/deploy_index.py   # (SFTP로 aicc2/index.html 업로드)

# 제품 JSON 부분 업데이트
python3 scripts/talktalk/apply_to_server.py
```

## 인증

- `.env` 파일은 절대 커밋 금지 (`.gitignore`로 차단).
- SFTP: godomall Pro 호스팅 계정. SSH 터널은 차단되어 있어 paramiko SFTP + blank.php 엔드포인트 우회 패턴 사용.
- 프론트엔드에서는 사용자가 세팅한 Gemini API 키를 brower localStorage에 저장(서버로 안 감).

## 라이선스

사내 도구. 외부 배포 금지.
