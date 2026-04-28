import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import json

load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/calendar']
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')
CREDS_PATH = os.path.join(BASE_DIR, 'credentials.json')

def get_calendar_service():
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)

def parse_schedule_with_gemini(text):
    today = datetime.now().strftime("%Y-%m-%d")
    api_key = os.getenv("GEMINI_API_KEY")

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
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={api_key}",
        json={"contents": [{"parts": [{"text": prompt}]}]}
    )

    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    raw = raw.strip().replace("```json", "").replace("```", "").strip()

    if raw == "null":
        return None

    parsed = json.loads(raw)
    if parsed.get("date") is None:
        return None
    return parsed

def add_event(text):
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
        start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        end_dt   = start_dt + timedelta(hours=1)
        event = {
            'summary': title,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Seoul'},
            'end':   {'dateTime': end_dt.isoformat(),   'timeZone': 'Asia/Seoul'},
        }
        time_str = start_dt.strftime('%m/%d %H:%M')
    else:
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