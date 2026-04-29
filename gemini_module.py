import json
import logging
import os
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSONA_PATH = os.path.join(BASE_DIR, 'persona_daesanghyuk.md')

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/gemini-2.5-flash-lite:generateContent"
)


def _call_gemini(prompt: str) -> str | None:
    """Gemini API를 호출하고 텍스트 응답을 반환한다.

    candidates 키가 없는 비정상 응답(한도 초과, 안전 필터 등)이면 None을 반환한다.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    response = requests.post(
        f"{GEMINI_API_URL}?key={api_key}",
        json={"contents": [{"parts": [{"text": prompt}]}]},
    )
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


def generate_memo_title(content: str) -> str:
    """메모 내용에서 짧은 제목을 생성한다. 실패 시 현재 시각 문자열을 반환."""
    prompt = f"""다음 메모 내용을 보고 어울리는 짧은 제목을 한국어로 만들어줘.
20자 이내로, 핵심 내용이 담기게. 제목만 출력해. 따옴표나 기호 없이.

메모:
{content}"""
    result = _call_gemini(prompt)
    if result:
        return result.strip()[:30]
    return datetime.now().strftime("%Y-%m-%d %H:%M")
