# AI Assistor 대상혁

텔레그램으로 툭툭 던진 기록을 AI가 분류·파일화하고, 브리프로 기록을 독려하며, 주기적 보고서로 동기부여를 제공하는 개인 어시스턴트 봇.

---

## 핵심 가치

- **마찰 없는 기록** — 텔레그램 하나로 메모/일정/할 일을 처리
- **자동 정리** — AI가 분류하고 알맞은 저장소에 저장
- **돌아보기** — 브리프와 보고서로 자신의 활동을 되돌아봄
- **지속성** — 브리프를 통한 꾸준한 기록 습관 형성

---

## 시스템 구성

```
[사용자]
    ├── 외출 중: 텔레그램으로 메모/일정/Todo 입력·조회 + 브리프 수신
    └── 집에서: Obsidian(PC)으로 메모 열람·편집
         │
[텔레그램 봇] ──(Polling)──▶ [Raspberry Pi 5]
                                  ├── 봇 서버 (Python)
                                  └── 스케줄러 (APScheduler)
                                       │
                          ┌────────────┼────────────┐
                    [Gemini API]  [Google Calendar]  [Google Drive]
                                                          │
                                                   PC Obsidian Vault
                                               (Google Drive 동기화 폴더)
```

---

## 기술 스택

| 구분 | 기술 | 비용 |
|------|------|------|
| 인터페이스 | Telegram Bot API | 무료 |
| 서버 | Raspberry Pi 5 8GB (자체 호스팅) | 전기세 월 ~2,000원 |
| 언어 | Python 3.11 | 무료 |
| AI | Google Gemini 3.1 Flash Lite Preview | 무료 (일 500회) |
| 메모/Todo 저장소 | Google Drive API (.md 파일) | 무료 (15GB) |
| 메모 편집 (PC) | Obsidian (Google Drive 동기화 폴더) | 무료 |
| 캘린더 | Google Calendar API | 무료 |
| 스케줄러 | APScheduler | 무료 |

**총 운영 비용: 월 ~2,000원 (전기세)**

---

## 채널 구성

텔레그램 채널 3개로 역할을 구분한다. 채널 자체가 카테고리 역할을 하므로 AI 분류 호출이 불필요하다.

| 채널 | 담당 | 저장 위치 |
|------|------|-----------|
| 📅 일정 | 날짜/시간이 포함된 약속 | Google Calendar |
| ✅ Todo | 반복 할 일(습관) 및 단순 할 일 | Google Drive / Todo |
| 📥 일상 메모 | 일기, 운동, 독서, 잡메모 등 | Google Drive / Inbox |

---

## 주요 기능

### 일상 메모

- 채팅하듯 여러 메시지를 보내면 5분 타이머 후 하나의 메모로 묶어 저장
- `/done` 또는 `/done 제목명` 으로 즉시 저장 및 미리보기
- AI가 자동으로 제목 생성 (`YYYY-MM-DD HH:MM 제목.md` 형식)
- `#태그명` 입력 시 자동 등록 → YAML frontmatter에 반영, 본문에서 제거
- 등록 태그 기반 AI 태그 최대 3개 추천

**태그 명령어**

| 명령어 | 설명 |
|--------|------|
| `!태그` | 등록된 태그 목록 조회 |
| `!태그삭제 태그명` | 태그 삭제 |

### 일정

- Gemini가 날짜/시간/제목/장소를 자연어 파싱 → Google Calendar 자동 등록
- 파싱 실패 시 재입력 요청

### Todo

**`!` 명령어** (Gemini 호출 없음, 즉시 처리)

| 명령어 | 설명 |
|--------|------|
| `!조회` | 오늘 할 일 + 습관 목록 표시 |
| `!할일 내용` | 오늘 할 일 추가 |
| `!습관 내용` | 습관 추가 |
| `!완료 번호` | 항목 완료 처리 |
| `!취소 번호` | 완료 항목을 미완료로 전환 |
| `!삭제 번호` | 미완료 항목 삭제 |
| `!수정 번호 새텍스트` | 항목 텍스트 수정 |
| `!help` / `!도움말` | 채널별 명령어 도움말 |

**자연어** — Gemini 1회 호출로 의도 파싱 + 대상혁 페르소나 코멘트 동시 생성

> 완료 항목은 기록 보존 원칙에 따라 직접 삭제 불가. `!취소`로 미완료 전환 후 삭제.

### 브리프 & 보고서 (APScheduler)

| 브리프 | 시각 | 내용 |
|--------|------|------|
| 🌅 모닝 브리프 | 매일 08:00 | 오늘 일정 + Todo + 습관 연속 기록 |
| 🌙 데이 브리프 | 매일 22:00 | 오늘 기록 요약 + AI 맞춤 질문 + 하루 총평 (기록 없으면 생략) |
| 📊 주간 보고서 | 매주 일 21:00 | 주간 통계 + AI 총평 + Drive 저장 |

- `TELEGRAM_OWNER_ID` 설정 시 1:1 DM으로 전송, 미설정 시 📥 일상 메모 채널로 전송
- `/chatid` 명령어로 자신의 채팅 ID 확인 가능

### API 사용량 관리

- `rpd_counter.json`에 날짜별 호출 수 기록 (재시작해도 유지)
- 잔여 횟수 30회 미만 시 경고 표시
- `!통계` 명령어로 최근 7일 사용 현황 조회 (📥 메모 채널 또는 DM)

---

## 프로젝트 구조

```
ai-bot/
  ├── main.py                    # 텔레그램 봇 진입점
  ├── gemini_module.py           # Gemini API 호출 전담
  ├── google_calendar_module.py  # Google Calendar 저장/조회
  ├── drive_module.py            # Google Drive Todo/메모/태그 저장/조회
  ├── persona_daesanghyuk.md     # 페르소나 정의 (런타임 주입)
  ├── auth.py                    # OAuth2 재인증용 스크립트
  ├── requirements.txt           # Python 의존성
  ├── commands.md                # Pi 운영 명령어 모음
  └── .env                       # 환경변수 (gitignore)
```

### Google Drive 폴더 구조

```
{DRIVE_VAULT_FOLDER_ID}/     # Obsidian Vault 루트
  ├── Inbox/                 # 일상 메모
  │     └── tags.md          # 사용자 정의 태그 목록
  ├── Todo/
  │     ├── habits.md        # 습관 정의 + 날짜별 완료 이력
  │     └── YYYY-MM-DD.md    # 당일 할 일 + 습관 완료 현황
  └── AI Reports/
        ├── Daily/
        └── Weekly/
```

---

## 설치 및 실행

### 의존성 설치

```bash
pip install -r requirements.txt
```

### 환경변수 설정 (`.env`)

| 키 | 설명 |
|----|------|
| `TELEGRAM_BOT_TOKEN` | BotFather에서 발급받은 토큰 |
| `TELEGRAM_CH_SCHEDULE` | 📅 일정 채널 ID |
| `TELEGRAM_CH_TODO` | ✅ Todo 채널 ID |
| `TELEGRAM_CH_DAILY` | 📥 일상 메모 채널 ID |
| `TELEGRAM_OWNER_ID` | 브리프를 DM으로 받을 사용자 ID (선택) |
| `GEMINI_API_KEY` | Google AI Studio에서 발급 |
| `DRIVE_VAULT_FOLDER_ID` | Google Drive Obsidian Vault 폴더 ID |

### Google OAuth2 인증

```bash
# token.json 만료 또는 스코프 변경 시
rm token.json
python auth.py
```

### 서비스 등록 (Raspberry Pi)

```bash
# 서비스 시작
sudo systemctl start ai-bot.service

# 부팅 시 자동 시작 등록
sudo systemctl enable ai-bot.service

# 로그 확인
sudo journalctl -u ai-bot.service -f
```

---

## 페르소나: 대상혁

Todo 자연어 명령 처리 시 Faker(이상혁) 스타일의 코멘트를 1문장으로 덧붙인다.
페르소나 정의는 `persona_daesanghyuk.md`에서 코드 수정 없이 편집 가능하다.

> 존댓말 기본에 장난기 있는 반말을 살짝 섞는 말투. 짧고 진심 어린 응원형.

---

## 개발 로드맵

| Phase | 내용 | 상태 |
|-------|------|------|
| Phase 1 | Pi 세팅, 봇 기본 메시지 수신, Calendar 연동 | ✅ 완료 |
| Phase 2 | 일정 파싱 안정화, Drive API 연동 | ✅ 완료 |
| Phase 3 | Todo 기능 (!, 자연어, 습관) | ✅ 완료 |
| Phase 4 | 메모 기능 (묶음, 태그, AI 제목) | ✅ 완료 |
| Phase 5 | 브리프 & 보고서 (APScheduler) | ✅ 완료 |
| Phase 6 | 안정화 — Drive/Calendar/Gemini 오류 폴백 | 🔄 진행 중 |
