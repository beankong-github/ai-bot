import json
import logging
import os
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSONA_PATH = os.path.join(BASE_DIR, 'persona_daesanghyuk.md')
RPD_COUNTER_PATH = os.path.join(BASE_DIR, 'rpd_counter.json')

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/gemini-3.1-flash-lite-preview:generateContent"
)

RPD_LIMIT = 500
RPD_WARN_THRESHOLD = 30

_last_remaining_rpd: int = RPD_LIMIT


_HISTORY_KEEP_DAYS = 30  # 최대 보관 일수


def _load_counter() -> dict:
    if os.path.exists(RPD_COUNTER_PATH):
        try:
            with open(RPD_COUNTER_PATH, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            pass
    return {"date": "", "count": 0, "history": {}}


def _save_counter(data: dict):
    with open(RPD_COUNTER_PATH, 'w') as f:
        json.dump(data, f, indent=2)


def _increment_rpd() -> int:
    """오늘 Gemini 호출 수를 1 증가시키고 남은 횟수를 반환한다.

    날짜가 바뀌면 전날 카운트를 history에 저장하고 초기화한다.
    history는 최근 _HISTORY_KEEP_DAYS일 분만 유지한다.
    """
    global _last_remaining_rpd
    today = datetime.now().strftime("%Y-%m-%d")
    data = _load_counter()

    if data.get("date") != today:
        # 날짜 바뀜 — 어제 카운트를 history에 보존
        prev_date = data.get("date", "")
        prev_count = data.get("count", 0)
        history: dict = data.get("history", {})
        if prev_date and prev_count > 0:
            history[prev_date] = prev_count
        # 오래된 기록 정리
        cutoff = sorted(history)[-_HISTORY_KEEP_DAYS:] if len(history) > _HISTORY_KEEP_DAYS else list(history)
        history = {k: history[k] for k in cutoff}
        data = {"date": today, "count": 0, "history": history}

    data["count"] = data.get("count", 0) + 1
    _save_counter(data)

    _last_remaining_rpd = max(0, RPD_LIMIT - data["count"])
    return _last_remaining_rpd


def get_remaining_rpd() -> int:
    """마지막 Gemini 호출 이후 남은 오늘 RPD를 반환한다."""
    return _last_remaining_rpd


def get_rpd_stats(days: int = 7) -> dict:
    """최근 N일간 Gemini 호출 통계를 반환한다.

    반환: {"today": N, "avg": N, "max": N, "days_with_data": N}
    """
    data = _load_counter()
    today = datetime.now().strftime("%Y-%m-%d")
    today_count = data.get("count", 0) if data.get("date") == today else 0
    history: dict = data.get("history", {})

    # 최근 days일 기록만 추출 (오늘 제외)
    recent = {k: v for k, v in history.items() if k < today}
    recent_counts = sorted(recent.items())[-days:]

    all_counts = [c for _, c in recent_counts] + ([today_count] if today_count > 0 else [])

    if not all_counts:
        return {"today": today_count, "avg": 0.0, "max": 0, "days_with_data": 0}

    return {
        "today": today_count,
        "avg": round(sum(all_counts) / len(all_counts), 1),
        "max": max(all_counts),
        "days_with_data": len(all_counts),
    }


def _call_gemini(prompt: str) -> str | None:
    """Gemini API를 호출하고 텍스트 응답을 반환한다.

    네트워크 오류, HTTP 오류, candidates 없는 응답이면 None을 반환한다.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    try:
        response = requests.post(
            f"{GEMINI_API_URL}?key={api_key}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        logging.error(f"Gemini 네트워크 오류: {e}")
        return None
    _increment_rpd()
    if response.status_code == 429:
        logging.warning("Gemini RPD 한도 초과 (429)")
        return None
    if not response.ok:
        logging.error(f"Gemini HTTP {response.status_code}: {response.text[:200]}")
        return None
    resp_json = response.json()
    if "candidates" not in resp_json:
        logging.error(f"Gemini 응답 이상: {resp_json}")
        return None
    raw = resp_json["candidates"][0]["content"]["parts"][0]["text"]
    return raw.strip().replace("```json", "").replace("```", "").strip()


def _load_persona() -> str:
    # 페르소나 파일을 런타임에 읽어서 프롬프트에 주입한다.
    # 페르소나 수정 시 persona_daesanghyuk.md만 편집하면 코드 변경 없이 반영된다.
    try:
        with open(PERSONA_PATH, encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        logging.warning("persona_daesanghyuk.md 파일을 찾을 수 없습니다.")
        return ""


def parse_schedule(text: str) -> dict | None:
    """자연어 텍스트에서 일정 정보를 파싱해 dict로 반환한다.

    날짜를 특정할 수 없으면 None을 반환하고, 호출한 쪽에서 재입력을 요청한다.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""오늘 날짜: {today}
다음 텍스트에서 일정 정보를 추출해서 JSON으로만 응답해. 다른 말은 절대 하지 마.

텍스트: "{text}"

응답 형식:
{{
  "title": "일정 제목",
  "date": "YYYY-MM-DD",
  "time": "HH:MM 또는 null",
  "location": "장소 또는 null",
  "is_allday": false
}}

날짜를 알 수 없으면 null만 반환."""

    raw = _call_gemini(prompt)
    if raw is None or raw == "null":
        return None

    parsed = json.loads(raw)
    if parsed.get("date") is None:
        return None
    return parsed


def parse_todo_and_comment(text: str) -> dict:
    """Todo 채널 자연어 메시지를 파싱하고 대상혁 페르소나 코멘트를 함께 생성한다.

    의도 분류 + 코멘트 생성을 한 번의 Gemini 호출로 처리한다.
    """
    persona = _load_persona()
    prompt = f"""아래는 네가 따라야 할 페르소나 정의다.

{persona}

---

위 페르소나로서 사용자의 Todo 채널 메시지를 분석해라.
의도를 파악하고 페르소나에 맞는 코멘트를 JSON으로만 응답해라. 다른 말은 절대 하지 마.

메시지: "{text}"

intent 종류:
- add_todo: 오늘 할 일 추가 요청
- add_habit: 반복 습관 추가 요청
- query: 할 일 목록 조회 요청
- complete: 특정 번호 완료 처리 요청
- edit_todo: 특정 번호 할 일 수정 요청
- delete_todo: 특정 번호 할 일 삭제 요청
- uncomplete: 특정 번호 완료 항목을 미완료로 전환 요청
- unknown: 위에 해당하지 않음

응답 형식 (JSON만):
{{"intent": "add_todo", "texts": ["할 일1", "할 일2"], "comment": "1문장 이내 코멘트"}}
{{"intent": "add_habit", "text": "추출한 습관 내용", "comment": "1문장 이내 코멘트"}}
{{"intent": "query", "comment": ""}}
{{"intent": "complete", "number": 숫자, "comment": "1문장 이내 코멘트"}}
{{"intent": "edit_todo", "number": 숫자, "text": "수정할 내용", "comment": "1문장 이내 코멘트"}}
{{"intent": "delete_todo", "number": 숫자, "comment": "1문장 이내 코멘트"}}
{{"intent": "uncomplete", "number": 숫자, "comment": "1문장 이내 코멘트"}}
{{"intent": "unknown", "comment": ""}}

add_todo의 texts는 반드시 리스트여야 한다. 메시지에 할 일이 여러 개 담겨 있으면 각각 분리해서 배열에 담아라.
예: "씻고 자야지" → {{"intent": "add_todo", "texts": ["씻기", "잠들기"], "comment": "..."}}"""

    raw = _call_gemini(prompt)
    if raw is None:
        return {"intent": "unknown", "comment": ""}

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logging.error(f"Todo 파싱 JSON 오류: {raw}")
        return {"intent": "unknown", "comment": ""}


def suggest_tags(content: str, available_tags: list[str]) -> list[str]:
    """메모 내용에서 등록된 태그 중 적합한 것을 최대 3개 추천한다.

    태그 목록이 비어 있으면 Gemini 호출 없이 빈 리스트를 반환한다.
    """
    if not available_tags:
        return []

    tags_str = ", ".join(available_tags)
    prompt = f"""다음 메모 내용에 어울리는 태그를 아래 목록에서 골라줘.
최대 3개, 없으면 빈 배열. JSON 배열로만 응답해. 다른 말 하지 마.

태그 목록: {tags_str}

메모:
{content}

응답 형식: ["태그1", "태그2"]"""

    raw = _call_gemini(prompt)
    if not raw:
        return []
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return [t for t in result if t in available_tags]
    except json.JSONDecodeError:
        logging.error(f"태그 추천 JSON 오류: {raw}")
    return []


def generate_day_brief_content(memos: list[dict], events_text: str) -> dict:
    """데이 브리프 AI 맞춤 질문과 하루 총평을 생성한다.

    반환: {"question": "...", "summary": "..."}
    """
    persona = _load_persona()
    if memos:
        memo_lines = "\n".join(
            f"- {m['title']} (태그: {', '.join(m['tags']) or '없음'})" for m in memos
        )
    else:
        memo_lines = "기록 없음"
    prompt = f"""{persona}

---

오늘 하루 데이 브리프입니다. JSON으로만 응답해줘. 다른 말 하지 마.

오늘 기록:
{memo_lines}

오늘 일정: {events_text}

응답 형식:
{{"question": "오늘 기록 기반 맞춤 질문 (30자 이내, 기록 없으면 '오늘 하루 어떠셨나요?')", "summary": "대상혁 페르소나로 하루 격려 총평 (50자 이내)"}}"""

    raw = _call_gemini(prompt)
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logging.error(f"데이 브리프 JSON 오류: {raw}")
    return {"question": "오늘 하루 어떠셨나요?", "summary": "오늘도 수고했어요. 꾸준히 하는 게 가장 중요합니다."}


def generate_weekly_report_content(stats: dict, habits_text: str, events_text: str) -> dict:
    """주간 보고서 AI 총평과 다음 주 제안을 생성한다.

    반환: {"summary": "...", "suggestion": "..."}
    """
    persona = _load_persona()
    total = stats.get("total", 0)
    tag_text = ", ".join(
        f"{k}:{v}회" for k, v in sorted(stats.get("by_tag", {}).items(), key=lambda x: -x[1])
    ) or "없음"
    prompt = f"""{persona}

---

이번 주 보고서입니다. JSON으로만 응답해줘. 다른 말 하지 마.

이번 주 기록: {total}건 (태그별: {tag_text})
이번 주 습관:
{habits_text}
이번 주 일정: {events_text}

응답 형식:
{{"summary": "대상혁 페르소나로 이번 주 격려 총평 (70자 이내)", "suggestion": "다음 주를 위한 구체적인 제안 한 줄 (50자 이내)"}}"""

    raw = _call_gemini(prompt)
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logging.error(f"주간 보고서 JSON 오류: {raw}")
    return {
        "summary": "이번 주도 꾸준히 기록했네요. 이 습관을 유지해봐요.",
        "suggestion": "다음 주도 오늘처럼 열심히 해봅시다."
    }


def generate_dm_reply(text: str) -> str:
    """DM 자유 대화에 대상혁 페르소나로 짧은 리액션을 생성한다."""
    persona = _load_persona()
    prompt = f"""{persona}

---

사용자가 말을 걸어왔다. 페르소나에 맞게 짧게 리액션해줘.
2문장 이내, 자연스럽게. 다른 말 하지 마.

사용자: "{text}"
"""
    return _call_gemini(prompt) or "..."


def generate_memo_title(content: str) -> str | None:
    """메모 내용에서 짧은 제목을 생성한다. 실패 시 None 반환."""
    prompt = f"""다음 메모 내용을 보고 어울리는 짧은 제목을 한국어로 만들어줘.
20자 이내로, 핵심 내용이 담기게. 제목만 출력해. 따옴표나 기호 없이.

메모:
{content}"""
    result = _call_gemini(prompt)
    if result:
        return result.strip()[:30]
    return None
