import io
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

load_dotenv()

# Calendar + Drive 두 스코프를 하나의 token.json에서 관리한다.
# google_calendar_module.py와 반드시 동일하게 유지해야 한다.
# 스코프가 달라지면 token.json을 삭제하고 Pi에서 재인증 필요.
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/drive',
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')  # OAuth2 인증 토큰 (gitignore 대상)


def get_drive_service():
    # token.json이 만료된 경우 refresh_token으로 자동 갱신한다.
    # refresh_token이 없으면 재인증 필요 → Pi에서 auth 스크립트 직접 실행.
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)


# ── 폴더 관리 ──────────────────────────────────────────────────────────────────

def _find_or_create_folder(service, name: str, parent_id: str | None = None) -> str:
    # 같은 이름의 폴더가 이미 있으면 새로 만들지 않고 기존 ID를 반환한다.
    # trashed=false 조건이 없으면 휴지통에 버린 폴더도 검색에 걸린다.
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def _get_folder_id(service, *path_parts: str) -> str:
    # ("notes", "Todo") 처럼 경로를 순서대로 전달하면
    # 각 단계를 재귀적으로 찾거나 만들어서 최종 폴더 ID를 반환한다.
    # 최초 실행 시 Drive에 폴더 구조가 자동 생성된다.
    parent = None
    for part in path_parts:
        parent = _find_or_create_folder(service, part, parent)
    return parent


# ── 파일 기본 헬퍼 ─────────────────────────────────────────────────────────────

def _find_file(service, name: str, parent_id: str) -> str | None:
    # 해당 폴더 안에서 파일명으로 검색한다. 없으면 None 반환.
    # trashed=false 없으면 삭제된 파일도 같은 이름으로 검색될 수 있음.
    query = f"name='{name}' and '{parent_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def _read_file(service, file_id: str) -> str:
    # Drive API는 파일 내용을 스트림으로 내려준다.
    # BytesIO 버퍼에 청크 단위로 받은 뒤 한 번에 디코딩한다.
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue().decode("utf-8")


def _write_file(service, file_id: str, content: str):
    # 기존 파일 내용을 통째로 덮어쓴다. 부분 수정은 지원하지 않는다.
    # 따라서 수정 전에 반드시 _read_file로 내용을 먼저 가져와야 한다.
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
    service.files().update(fileId=file_id, media_body=media).execute()


def _create_file(service, name: str, parent_id: str, content: str) -> str:
    # mimetype을 text/plain으로 지정해야 Obsidian에서 .md 파일로 정상 인식된다.
    # Google Docs 형식(application/vnd.google-apps.document)으로 만들면 안 됨.
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
    meta = {"name": name, "parents": [parent_id]}
    file = service.files().create(body=meta, media_body=media, fields="id").execute()
    return file["id"]


# ── 일상 메모 (Inbox) ──────────────────────────────────────────────────────────

def save_memo(content: str, tags: list[str] | None = None, status: str = "draft") -> str:
    """텔레그램 일상 메모 채널 메시지를 Inbox에 .md 파일로 저장한다.

    파일명은 저장 시각(초 단위)으로 자동 생성되어 중복을 방지한다.
    status=draft로 시작하며, 사용자 승인 후 confirmed로 변경된다 (Phase 4에서 구현).
    반환값: 생성된 파일의 Drive 파일 ID
    """
    service = get_drive_service()
    inbox_id = _get_folder_id(service, "notes", "Inbox")

    now = datetime.now()
    filename = now.strftime("%Y-%m-%d-%H%M%S") + ".md"
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    # 태그 없으면 빈 배열로 저장 — Obsidian YAML 파싱 호환
    tags_str = "[" + ", ".join(tags) + "]" if tags else "[]"

    # Obsidian이 인식하는 YAML frontmatter 형식으로 저장
    file_content = (
        f"---\n"
        f"date: {date_str}\n"
        f"time: {time_str}\n"
        f"tags: {tags_str}\n"
        f"status: {status}\n"
        f"---\n\n"
        f"{content}\n"
    )
    return _create_file(service, filename, inbox_id, file_content)


# ── Todo 내부 헬퍼 ─────────────────────────────────────────────────────────────

def _get_habits_file_id(service, todo_folder_id: str) -> str:
    # habits.md가 없으면 헤더만 있는 빈 파일을 새로 만든다.
    # 최초 실행 시 자동 생성되므로 수동으로 파일을 만들 필요 없다.
    file_id = _find_file(service, "habits.md", todo_folder_id)
    if not file_id:
        file_id = _create_file(service, "habits.md", todo_folder_id, "# 습관 목록\n\n")
    return file_id


def _get_daily_file_id(service, todo_folder_id: str, date_str: str) -> str:
    # 날짜별 할 일 파일(예: 2026-04-28.md)을 찾거나 없으면 새로 만든다.
    # 매일 첫 번째 할 일 추가 시 해당 날짜 파일이 자동 생성된다.
    filename = f"{date_str}.md"
    file_id = _find_file(service, filename, todo_folder_id)
    if not file_id:
        file_id = _create_file(service, filename, todo_folder_id, f"# {date_str} 할 일\n\n")
    return file_id


def _extract_tasks(content: str) -> list[tuple[int, str]]:
    # 파일 전체에서 Obsidian Tasks 체크박스 라인만 추출한다.
    # (파일 내 줄 번호, 줄 텍스트) 형태로 반환하는 이유:
    # complete_todo에서 해당 줄을 정확히 찾아 수정할 때 줄 번호가 필요하다.
    return [
        (i, line)
        for i, line in enumerate(content.splitlines())
        if line.strip().startswith("- [ ]") or line.strip().startswith("- [x]")
    ]


# ── Todo 공개 API ──────────────────────────────────────────────────────────────

def add_todo(text: str):
    """오늘 날짜 파일(예: 2026-04-28.md)에 단순 할 일 한 줄을 추가한다."""
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")
    date_str = datetime.now().strftime("%Y-%m-%d")

    file_id = _get_daily_file_id(service, todo_folder_id, date_str)
    content = _read_file(service, file_id)
    # rstrip()으로 파일 끝 공백/개행을 제거한 뒤 새 항목을 붙인다.
    # 이렇게 하지 않으면 빈 줄이 계속 늘어난다.
    _write_file(service, file_id, content.rstrip() + f"\n- [ ] {text}\n")


def add_habit(text: str):
    """habits.md에 매일 반복 습관을 추가한다.

    Obsidian Tasks 플러그인이 '🔁 every day' 이모지를 반복 일정으로 인식한다.
    """
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")

    file_id = _get_habits_file_id(service, todo_folder_id)
    content = _read_file(service, file_id)
    _write_file(service, file_id, content.rstrip() + f"\n- [ ] {text} 🔁 every day\n")


def get_today_todos() -> str:
    """습관(habits.md) + 오늘 할 일(날짜 파일)을 합쳐서 텔레그램 메시지 형식으로 반환한다.

    번호는 습관부터 순서대로 매긴다.
    complete_todo()에 넘기는 번호와 동일하게 맞춰야 한다.
    """
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")
    date_str = datetime.now().strftime("%Y-%m-%d")

    habits_content = _read_file(service, _get_habits_file_id(service, todo_folder_id))
    daily_content = _read_file(service, _get_daily_file_id(service, todo_folder_id, date_str))

    habit_tasks = [line for _, line in _extract_tasks(habits_content)]
    daily_tasks = [line for _, line in _extract_tasks(daily_content)]

    result = f"📋 오늘의 할 일 ({date_str})\n\n"
    if habit_tasks:
        result += "🔁 습관\n"
        for i, t in enumerate(habit_tasks, 1):
            done = "✅" if "- [x]" in t else "⬜"
            # 표시할 때는 체크박스 문법과 반복 이모지를 제거하고 순수 텍스트만 보여준다
            label = t.replace("- [ ] ", "").replace("- [x] ", "").replace(" 🔁 every day", "").strip()
            result += f"{i}. {done} {label}\n"
        result += "\n"
    if daily_tasks:
        result += "✅ 오늘 할 일\n"
        # 오늘 할 일 번호는 습관 개수 다음부터 시작해야 complete_todo 번호와 일치한다
        offset = len(habit_tasks) + 1
        for i, t in enumerate(daily_tasks, offset):
            done = "✅" if "- [x]" in t else "⬜"
            label = t.replace("- [ ] ", "").replace("- [x] ", "").strip()
            result += f"{i}. {done} {label}\n"
    if not habit_tasks and not daily_tasks:
        result += "등록된 할 일이 없습니다."

    return result


def complete_todo(item_number: int) -> bool:
    """get_today_todos()에서 보여준 번호로 항목을 완료 처리한다.

    습관과 오늘 할 일을 매번 새로 읽어서 번호를 재계산하기 때문에
    get_today_todos() 출력과 항상 동기화된다.
    완료 시 체크박스를 [x]로 바꾸고 '✅ 날짜'를 줄 끝에 추가한다.
    이미 ✅가 있으면 날짜를 중복 추가하지 않는다.
    """
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")
    date_str = datetime.now().strftime("%Y-%m-%d")

    habits_id = _get_habits_file_id(service, todo_folder_id)
    habits_content = _read_file(service, habits_id)
    daily_id = _get_daily_file_id(service, todo_folder_id, date_str)
    daily_content = _read_file(service, daily_id)

    # 파일 구분자("habits"/"daily")와 파일 내 줄 번호를 함께 보관한다.
    # 수정 시 어느 파일의 몇 번째 줄인지 알아야 하기 때문이다.
    habit_tasks = [("habits", i, line) for i, line in _extract_tasks(habits_content)]
    daily_tasks = [("daily", i, line) for i, line in _extract_tasks(daily_content)]
    all_tasks = habit_tasks + daily_tasks

    if not (1 <= item_number <= len(all_tasks)):
        return False  # 범위 초과 → 잘못된 번호

    file_type, line_idx, line_text = all_tasks[item_number - 1]
    new_line = line_text.replace("- [ ]", "- [x]", 1)
    if "✅" not in new_line:
        new_line = new_line.rstrip() + f" ✅ {date_str}"

    # 해당 줄만 교체하고 나머지는 그대로 유지한 뒤 파일 전체를 덮어쓴다.
    if file_type == "habits":
        lines = habits_content.splitlines(keepends=True)
        lines[line_idx] = new_line + "\n"
        _write_file(service, habits_id, "".join(lines))
    else:
        lines = daily_content.splitlines(keepends=True)
        lines[line_idx] = new_line + "\n"
        _write_file(service, daily_id, "".join(lines))

    return True
