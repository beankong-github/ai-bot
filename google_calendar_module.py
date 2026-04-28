import logging
import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import json

load_dotenv()

# drive_module.py와 반드시 동일한 스코프 목록을 유지해야 한다.
# 두 모듈이 하나의 token.json을 공유하기 때문에,
# 스코프가 달라지면 한 쪽에서 인증 오류가 발생한다.
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/drive',
]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')    # OAuth2 토큰 (gitignore 대상)
CREDS_PATH = os.path.join(BASE_DIR, 'credentials.json')  # Google Cloud 앱 자격증명


def get_calendar_service():
    # 토큰이 만료됐을 때 refresh_token으로 자동 갱신한다.
    # 갱신된 토큰은 token.json에 즉시 덮어쓴다.
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)


def parse_schedule_with_gemini(text):
    """자연어 텍스트에서 일정 정보를 파싱해 dict로 반환한다.

    날짜를 특정할 수 없으면 None을 반환하고, 호출한 쪽에서 재입력을 요청한다.
    모델은 gemini-2.0-flash-lite — 일정 파싱은 경량 모델로 충분하고 무료 한도가 넉넉하다.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    api_key = os.getenv("GEMINI_API_KEY")

    # 오늘 날짜를 프롬프트에 포함시켜 "내일", "다음 주 화요일" 같은
    # 상대 날짜 표현도 절대 날짜로 변환되도록 유도한다.
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

    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}",
        json={"contents": [{"parts": [{"text": prompt}]}]}
    )

    resp_json = response.json()
    # Gemini API 무료 한도 초과나 안전 필터 차단 시 candidates 키 자체가 없는 응답이 온다.
    # 이 경우 None을 반환해서 사용자에게 재입력을 요청하도록 한다.
    if "candidates" not in resp_json:
        logging.error(f"Gemini 응답 이상: {resp_json}")
        return None

    raw = resp_json["candidates"][0]["content"]["parts"][0]["text"]
    # Gemini가 JSON 앞뒤에 마크다운 코드 블록을 붙이는 경우가 있어서 제거한다.
    raw = raw.strip().replace("```json", "").replace("```", "").strip()

    if raw == "null":
        # 날짜 특정 불가 — 사용자가 날짜 없이 입력한 경우
        return None

    parsed = json.loads(raw)
    if parsed.get("date") is None:
        return None
    return parsed


def add_event(text):
    """일정 채널 메시지를 파싱해서 Google Calendar에 등록한다.

    반환값: (성공 여부, 확인 메시지 문자열)
    파싱 실패 시 (False, None) 반환 → main.py에서 재입력 메시지 전송.
    """
    parsed = parse_schedule_with_gemini(text)
    if not parsed:
        return False, None

    service = get_calendar_service()
    title     = parsed.get("title", text[:30])
    date      = parsed.get("date")
    time      = parsed.get("time")
    location  = parsed.get("location")
    is_allday = parsed.get("is_allday", True)

    if not is_allday and time:
        # 시각이 있는 일정: 시작 시각에서 1시간을 기본 종료 시각으로 설정한다.
        # Google Calendar는 종료 시각이 필수이고, 사용자가 따로 지정하지 않으므로 1시간을 기본값으로 쓴다.
        start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        end_dt   = start_dt + timedelta(hours=1)
        event = {
            'summary': title,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Seoul'},
            'end':   {'dateTime': end_dt.isoformat(),   'timeZone': 'Asia/Seoul'},
        }
        time_str = start_dt.strftime('%m/%d %H:%M')
    else:
        # 종일 일정: Google Calendar API는 종료 날짜를 "exclusive" 로 받는다.
        # 4월 27일 하루짜리 일정이면 end.date를 4월 28일로 보내야 한다.
        end_date = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        event = {
            'summary': title,
            'start': {'date': date},
            'end':   {'date': end_date},
        }
        time_str = f"{date} (종일)"

    if location:
        event['location'] = location

    service.events().insert(calendarId='primary', body=event).execute()

    result_msg = f"📅 등록했습니다.\n제목: {title}\n일시: {time_str}"
    if location:
        result_msg += f"\n장소: {location}"
    return True, result_msg


def parse_todo_and_comment(text: str) -> dict:
    """Todo 채널 자연어 메시지를 파싱하고 대상혁 페르소나 코멘트를 함께 생성한다.

    일정 파싱과 달리 의도 분류 + 코멘트 생성을 한 번의 Gemini 호출로 처리한다.

    반환 예시:
      {"intent": "add_todo", "text": "헬스장 가기", "comment": "작은 습관이 큰 차이를 만든다 💪"}
      {"intent": "add_habit", "text": "독서 30분", "comment": "꾸준함이 전부다 📚"}
      {"intent": "query", "comment": ""}
      {"intent": "complete", "number": 2, "comment": "하나씩 해내는 거다 ✅"}
      {"intent": "unknown", "comment": ""}
    """
    api_key = os.getenv("GEMINI_API_KEY")

    prompt = f"""너는 '대상혁'이라는 엄격하지만 진심으로 응원하는 코치 페르소나야.
사용자가 Todo 채널에 메시지를 보냈어. 의도를 파악하고 짧은 코멘트도 함께 JSON으로만 응답해. 다른 말은 절대 하지 마.

메시지: "{text}"

intent 종류:
- add_todo: 오늘 할 일 추가 요청
- add_habit: 반복 습관 추가 요청
- query: 할 일 목록 조회 요청
- complete: 특정 번호 완료 처리 요청
- unknown: 위에 해당하지 않음

응답 형식 (JSON만):
{{"intent": "add_todo", "text": "추출한 할 일 내용", "comment": "10단어 이내 코멘트 + 이모지 1개"}}
{{"intent": "add_habit", "text": "추출한 습관 내용", "comment": "10단어 이내 코멘트 + 이모지 1개"}}
{{"intent": "query", "comment": ""}}
{{"intent": "complete", "number": 숫자, "comment": "10단어 이내 완료 격려 코멘트 + 이모지 1개"}}
{{"intent": "unknown", "comment": ""}}"""

    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}",
        json={"contents": [{"parts": [{"text": prompt}]}]}
    )

    resp_json = response.json()
    if "candidates" not in resp_json:
        logging.error(f"Gemini Todo 파싱 응답 이상: {resp_json}")
        return {"intent": "unknown", "comment": ""}

    raw = resp_json["candidates"][0]["content"]["parts"][0]["text"]
    raw = raw.strip().replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logging.error(f"Todo 파싱 JSON 오류: {raw}")
        return {"intent": "unknown", "comment": ""}
