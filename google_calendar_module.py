import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from gemini_module import parse_schedule

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


_KST = timezone(timedelta(hours=9))


def get_events(start_date: str, end_date: str) -> list[dict]:
    """'YYYY-MM-DD' 형식 날짜 범위의 캘린더 이벤트를 반환한다."""
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=_KST)
    end_dt = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=_KST)
    service = get_calendar_service()
    result = service.events().list(
        calendarId='primary',
        timeMin=start_dt.isoformat(),
        timeMax=end_dt.isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    return result.get('items', [])


def format_events_text(events: list[dict], show_date: bool = False) -> str:
    """이벤트 목록을 텍스트로 포매팅한다."""
    if not events:
        return "일정 없음"
    lines = []
    for e in events:
        summary = e.get('summary', '(제목 없음)')
        start = e['start'].get('dateTime', e['start'].get('date'))
        location = e.get('location', '')
        if 'T' in start:
            dt = datetime.fromisoformat(start)
            time_str = dt.strftime('%m/%d %H:%M') if show_date else dt.strftime('%H:%M')
        else:
            time_str = start if show_date else "종일"
        loc_str = f" ({location})" if location else ""
        lines.append(f"• {time_str} {summary}{loc_str}")
    return "\n".join(lines)


def get_today_events_text() -> str:
    """오늘 일정을 텍스트로 반환한다."""
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    return format_events_text(get_events(today, today))


def get_tomorrow_events_text() -> str:
    """내일 일정을 텍스트로 반환한다."""
    tomorrow = (datetime.now(_KST) + timedelta(days=1)).strftime("%Y-%m-%d")
    return format_events_text(get_events(tomorrow, tomorrow))


def get_week_events_text() -> str:
    """이번 주(월~일) 일정을 텍스트로 반환한다."""
    today = datetime.now(_KST)
    monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    sunday = (today + timedelta(days=6 - today.weekday())).strftime("%Y-%m-%d")
    return format_events_text(get_events(monday, sunday), show_date=True)


def add_event(text):
    """일정 채널 메시지를 파싱해서 Google Calendar에 등록한다.

    반환값: (성공 여부, 확인 메시지 문자열)
    파싱 실패 시 (False, None) 반환 → main.py에서 재입력 메시지 전송.
    """
    parsed = parse_schedule(text)
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
