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

SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/drive',
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')

# Obsidian Vault 폴더 ID — Drive 웹에서 Vault 폴더 열기 → URL 마지막 경로
# 미설정 시 Drive 루트에 notes/ 폴더 생성
VAULT_FOLDER_ID = os.getenv("DRIVE_VAULT_FOLDER_ID")


def get_drive_service():
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)


# ── 폴더 / 파일 헬퍼 ───────────────────────────────────────────────────────────

def _find_or_create_folder(service, name: str, parent_id: str | None = None) -> str:
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
    return service.files().create(body=meta, fields="id").execute()["id"]


def _get_folder_id(service, *path_parts: str) -> str:
    # VAULT_FOLDER_ID 설정 시 Vault를 루트로 사용, 미설정 시 Drive 루트에서 탐색
    parent = VAULT_FOLDER_ID if VAULT_FOLDER_ID else None
    for part in path_parts:
        parent = _find_or_create_folder(service, part, parent)
    return parent


def _find_file(service, name: str, parent_id: str) -> str | None:
    query = f"name='{name}' and '{parent_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def _read_file(service, file_id: str) -> str:
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue().decode("utf-8")


def _write_file(service, file_id: str, content: str):
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
    service.files().update(fileId=file_id, media_body=media).execute()


def _create_file(service, name: str, parent_id: str, content: str) -> str:
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
    meta = {"name": name, "parents": [parent_id]}
    return service.files().create(body=meta, media_body=media, fields="id").execute()["id"]


# ── habits.md 파싱 ─────────────────────────────────────────────────────────────
#
# habits.md 형식:
#   # 습관 목록
#
#   ## 독서 30분
#   완료: 2026-04-27, 2026-04-28
#
#   ## 운동
#   완료:

def _parse_habits(content: str) -> list[dict]:
    """habits.md → [{"name": "독서 30분", "completed_dates": ["2026-04-28", ...]}, ...]"""
    habits = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            name = line[3:].strip()
            completed_dates = []
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("완료:"):
                dates_str = lines[i + 1].strip().removeprefix("완료:").strip()
                if dates_str:
                    completed_dates = [d.strip() for d in dates_str.split(",") if d.strip()]
                i += 1
            habits.append({"name": name, "completed_dates": completed_dates})
        i += 1
    return habits


def _habits_to_content(habits: list[dict]) -> str:
    """습관 목록 → habits.md 형식 문자열"""
    result = "# 습관 목록\n"
    for h in habits:
        dates_str = ", ".join(h["completed_dates"])
        result += f"\n## {h['name']}\n완료: {dates_str}\n"
    return result


# ── daily 파일 파싱 ────────────────────────────────────────────────────────────
#
# daily 파일 형식:
#   # 2026-04-29 할 일
#
#   ## 할 일
#   - [ ] 헬스장 예약
#   - [x] 이메일 답장 ✅ 14:30
#
#   ## 습관
#   - [ ] 독서 30분
#   - [x] 운동 ✅ 22:00

def _parse_daily_sections(content: str) -> dict:
    """daily 파일 → {"header": str, "todos": [str], "habits": [str]}"""
    header_lines = []
    todos = []
    habits = []
    current = None

    for line in content.splitlines():
        if line.startswith("# "):
            header_lines.append(line)
            current = None
        elif line.strip() == "## 할 일":
            current = "todos"
        elif line.strip() == "## 습관":
            current = "habits"
        elif current == "todos":
            todos.append(line)
        elif current == "habits":
            habits.append(line)
        elif current is None:
            header_lines.append(line)

    return {"header": "\n".join(header_lines), "todos": todos, "habits": habits}


def _build_daily_content(sections: dict) -> str:
    """섹션 dict → daily 파일 형식 문자열"""
    parts = [sections["header"], "", "## 할 일"]
    parts += [l for l in sections["todos"] if l.strip()]
    parts += ["", "## 습관"]
    parts += [l for l in sections["habits"] if l.strip()]
    parts.append("")
    return "\n".join(parts)


def _sync_habits_to_daily(sections: dict, habits: list[dict]) -> tuple[dict, bool]:
    """habits.md 기준으로 daily 파일 습관 섹션에 없는 습관을 추가한다.

    habits.md에 새 습관이 추가될 때 오늘 daily 파일에 자동 반영하는 용도.
    이미 있는 습관은 건드리지 않는다 (완료 여부 유지).
    """
    existing = set()
    for line in sections["habits"]:
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            name = stripped.replace("- [ ] ", "").replace("- [x] ", "").split(" ✅")[0].strip()
            existing.add(name)

    changed = False
    for h in habits:
        if h["name"] not in existing:
            sections["habits"].append(f"- [ ] {h['name']}")
            changed = True
    return sections, changed


# ── Todo / Habit 파일 초기화 ───────────────────────────────────────────────────

def _get_habits_file_id(service, todo_folder_id: str) -> str:
    file_id = _find_file(service, "habits.md", todo_folder_id)
    if not file_id:
        file_id = _create_file(service, "habits.md", todo_folder_id, "# 습관 목록\n")
    return file_id


def _get_daily_file_id(service, todo_folder_id: str, date_str: str) -> str:
    filename = f"{date_str}.md"
    file_id = _find_file(service, filename, todo_folder_id)
    if not file_id:
        # 새 daily 파일은 빈 섹션으로 시작 — 습관 sync는 각 함수에서 처리
        initial = f"# {date_str} 할 일\n\n## 할 일\n\n## 습관\n"
        file_id = _create_file(service, filename, todo_folder_id, initial)
    return file_id


# ── 일상 메모 (Inbox) ──────────────────────────────────────────────────────────

def save_memo(content: str, title: str | None = None, tags: list[str] | None = None, status: str = "draft") -> str:
    """일상 메모 채널 메시지를 Inbox에 .md 파일로 저장한다."""
    service = get_drive_service()
    inbox_id = _get_folder_id(service, "notes", "Inbox")

    now = datetime.now()
    filename = now.strftime("%Y-%m-%d-%H%M%S") + ".md"
    tags_str = "[" + ", ".join(tags) + "]" if tags else "[]"
    display_title = title or now.strftime("%Y-%m-%d %H:%M")

    file_content = (
        f"---\n"
        f"date: {now.strftime('%Y-%m-%d')}\n"
        f"time: {now.strftime('%H:%M')}\n"
        f"tags: {tags_str}\n"
        f"status: {status}\n"
        f"---\n\n"
        f"# {display_title}\n\n"
        f"{content}\n"
    )
    return _create_file(service, filename, inbox_id, file_content)


# ── 태그 관리 ──────────────────────────────────────────────────────────────────
#
# tags.md 형식:
#   # 태그 목록
#
#   - 운동
#   - 독서

def _get_tags_file_id(service, inbox_id: str) -> str:
    file_id = _find_file(service, "tags.md", inbox_id)
    if not file_id:
        file_id = _create_file(service, "tags.md", inbox_id, "# 태그 목록\n")
    return file_id


def _parse_tags(content: str) -> list[str]:
    return [l.strip()[2:].strip() for l in content.splitlines()
            if l.strip().startswith("- ")]


def _tags_to_content(tags: list[str]) -> str:
    result = "# 태그 목록\n"
    for tag in tags:
        result += f"\n- {tag}"
    return result + "\n"


def get_tags() -> str:
    """등록된 태그 목록을 텔레그램 메시지 형식으로 반환한다."""
    service = get_drive_service()
    inbox_id = _get_folder_id(service, "notes", "Inbox")
    tags_id = _get_tags_file_id(service, inbox_id)
    tags = _parse_tags(_read_file(service, tags_id))
    if not tags:
        return "등록된 태그가 없습니다.\n!태그추가 태그명 으로 추가해보세요."
    return "🏷️ 태그 목록\n\n" + "\n".join(f"• {t}" for t in tags)


def add_tag(tag: str) -> bool:
    """태그를 추가한다. 이미 존재하면 False 반환."""
    service = get_drive_service()
    inbox_id = _get_folder_id(service, "notes", "Inbox")
    tags_id = _get_tags_file_id(service, inbox_id)
    tags = _parse_tags(_read_file(service, tags_id))
    if tag in tags:
        return False
    tags.append(tag)
    _write_file(service, tags_id, _tags_to_content(tags))
    return True


def confirm_memo(file_id: str):
    """draft 메모의 status를 confirmed로 변경한다."""
    service = get_drive_service()
    content = _read_file(service, file_id)
    new_content = content.replace("status: draft", "status: confirmed", 1)
    _write_file(service, file_id, new_content)


def delete_tag(tag: str) -> bool:
    """태그를 삭제한다. 존재하지 않으면 False 반환."""
    service = get_drive_service()
    inbox_id = _get_folder_id(service, "notes", "Inbox")
    tags_id = _get_tags_file_id(service, inbox_id)
    tags = _parse_tags(_read_file(service, tags_id))
    if tag not in tags:
        return False
    tags.remove(tag)
    _write_file(service, tags_id, _tags_to_content(tags))
    return True


# ── Todo 공개 API ──────────────────────────────────────────────────────────────

def add_todo(text: str):
    """오늘 daily 파일의 '## 할 일' 섹션에 항목 추가."""
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")
    date_str = datetime.now().strftime("%Y-%m-%d")

    habits_id = _get_habits_file_id(service, todo_folder_id)
    habits = _parse_habits(_read_file(service, habits_id))

    daily_id = _get_daily_file_id(service, todo_folder_id, date_str)
    sections = _parse_daily_sections(_read_file(service, daily_id))

    # 새 습관이 있으면 daily 파일에 자동 반영
    sections, _ = _sync_habits_to_daily(sections, habits)
    sections["todos"].append(f"- [ ] {text}")
    _write_file(service, daily_id, _build_daily_content(sections))


def add_habit(text: str) -> bool:
    """habits.md에 습관 정의 추가 + 오늘 daily 파일에 즉시 반영.

    이미 존재하는 습관이면 False 반환.
    """
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")
    date_str = datetime.now().strftime("%Y-%m-%d")

    habits_id = _get_habits_file_id(service, todo_folder_id)
    habits = _parse_habits(_read_file(service, habits_id))

    if any(h["name"] == text for h in habits):
        return False  # 중복 방지

    habits.append({"name": text, "completed_dates": []})
    _write_file(service, habits_id, _habits_to_content(habits))

    # 오늘 daily 파일에 즉시 추가 — 습관 추가 당일부터 표시됨
    daily_id = _get_daily_file_id(service, todo_folder_id, date_str)
    sections = _parse_daily_sections(_read_file(service, daily_id))
    sections, _ = _sync_habits_to_daily(sections, habits)
    _write_file(service, daily_id, _build_daily_content(sections))
    return True


def get_today_todos() -> str:
    """오늘의 습관 + 할 일을 텔레그램 메시지 형식으로 반환.

    habits.md에서 습관 정의를 읽고, daily 파일에서 완료 여부를 확인한다.
    daily 파일에 누락된 습관이 있으면 자동으로 추가한다.
    complete_todo()의 번호 체계와 반드시 동일하게 유지해야 한다.
    """
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")
    date_str = datetime.now().strftime("%Y-%m-%d")

    habits_id = _get_habits_file_id(service, todo_folder_id)
    habits = _parse_habits(_read_file(service, habits_id))

    daily_id = _get_daily_file_id(service, todo_folder_id, date_str)
    sections = _parse_daily_sections(_read_file(service, daily_id))

    sections, changed = _sync_habits_to_daily(sections, habits)
    if changed:
        _write_file(service, daily_id, _build_daily_content(sections))

    result = f"📋 오늘의 할 일 ({date_str})\n\n"

    # 습관: habits.md 정의 기준, 완료 여부는 daily 파일에서 확인
    if habits:
        result += "🔁 습관\n"
        completed_habit_names = set()
        for line in sections["habits"]:
            if line.strip().startswith("- [x]"):
                name = line.strip().replace("- [x] ", "").split(" ✅")[0].strip()
                completed_habit_names.add(name)
        for i, h in enumerate(habits, 1):
            icon = "✅" if h["name"] in completed_habit_names else "⬜"
            result += f"{i}. {icon} {h['name']}\n"
        result += "\n"

    # 할 일: daily 파일 기준
    todo_lines = [l for l in sections["todos"] if l.strip().startswith("- [ ]") or l.strip().startswith("- [x]")]
    if todo_lines:
        result += "✅ 오늘 할 일\n"
        offset = len(habits) + 1
        for i, line in enumerate(todo_lines, offset):
            stripped = line.strip()
            icon = "✅" if stripped.startswith("- [x]") else "⬜"
            label = stripped.replace("- [ ] ", "").replace("- [x] ", "").strip()
            result += f"{i}. {icon} {label}\n"

    if not habits and not todo_lines:
        result += "등록된 할 일이 없습니다."

    return result


def complete_todo(item_number: int) -> bool:
    """get_today_todos() 기준 번호로 항목을 완료 처리한다.

    습관 완료: daily 파일에 완료 시각 기록 + habits.md 완료 날짜 append.
    할 일 완료: daily 파일에 완료 시각 기록.
    """
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M")

    habits_id = _get_habits_file_id(service, todo_folder_id)
    habits = _parse_habits(_read_file(service, habits_id))

    daily_id = _get_daily_file_id(service, todo_folder_id, date_str)
    sections = _parse_daily_sections(_read_file(service, daily_id))
    sections, _ = _sync_habits_to_daily(sections, habits)

    # get_today_todos()와 동일한 번호 체계
    all_items = [("habit", h["name"]) for h in habits]
    todo_lines = [(i, l) for i, l in enumerate(sections["todos"])
                  if l.strip().startswith("- [ ]") or l.strip().startswith("- [x]")]
    all_items += [("todo", i, l) for i, l in todo_lines]

    if not (1 <= item_number <= len(all_items)):
        return False

    target = all_items[item_number - 1]

    if target[0] == "habit":
        habit_name = target[1]

        # daily 파일: 해당 습관 라인을 완료로 업데이트
        for i, line in enumerate(sections["habits"]):
            stripped = line.strip()
            if (stripped.startswith("- [ ]") or stripped.startswith("- [x]")):
                name = stripped.replace("- [ ] ", "").replace("- [x] ", "").split(" ✅")[0].strip()
                if name == habit_name:
                    sections["habits"][i] = f"- [x] {habit_name} ✅ {time_str}"
                    break
        _write_file(service, daily_id, _build_daily_content(sections))

        # habits.md: 완료 날짜 추가 (같은 날 중복 방지)
        habits_list = _parse_habits(_read_file(service, habits_id))
        for h in habits_list:
            if h["name"] == habit_name and date_str not in h["completed_dates"]:
                h["completed_dates"].append(date_str)
                break
        _write_file(service, habits_id, _habits_to_content(habits_list))

    else:
        # 할 일: 해당 줄 완료 처리 + 완료 시각 추가
        _, line_idx, line_text = target
        new_line = line_text.strip().replace("- [ ]", "- [x]", 1)
        if "✅" not in new_line:
            new_line = new_line.rstrip() + f" ✅ {time_str}"
        sections["todos"][line_idx] = new_line
        _write_file(service, daily_id, _build_daily_content(sections))

    return True


def edit_todo(item_number: int, new_text: str) -> bool:
    """get_today_todos() 기준 번호로 할 일 항목의 텍스트를 수정한다.

    습관 번호는 수정할 수 없다. 할 일(todo) 항목만 수정 가능.
    완료된 항목은 텍스트를 바꾸되 완료 상태(✅ 시각)를 유지한다.
    """
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")
    date_str = datetime.now().strftime("%Y-%m-%d")

    habits_id = _get_habits_file_id(service, todo_folder_id)
    habits = _parse_habits(_read_file(service, habits_id))

    daily_id = _get_daily_file_id(service, todo_folder_id, date_str)
    sections = _parse_daily_sections(_read_file(service, daily_id))
    sections, _ = _sync_habits_to_daily(sections, habits)

    # get_today_todos()와 동일한 번호 체계
    num_habits = len(habits)
    todo_lines = [(i, l) for i, l in enumerate(sections["todos"])
                  if l.strip().startswith("- [ ]") or l.strip().startswith("- [x]")]

    if not (1 <= item_number <= num_habits + len(todo_lines)):
        return False

    if item_number <= num_habits:
        habit_name = habits[item_number - 1]["name"]

        # habits.md에서 이름 변경
        for h in habits:
            if h["name"] == habit_name:
                h["name"] = new_text
                break
        _write_file(service, habits_id, _habits_to_content(habits))

        # daily 파일 습관 섹션에서 이름 변경 (완료 상태·시각 유지)
        for i, line in enumerate(sections["habits"]):
            stripped = line.strip()
            if not (stripped.startswith("- [ ]") or stripped.startswith("- [x]")):
                continue
            name = stripped.replace("- [ ] ", "").replace("- [x] ", "").split(" ✅")[0].strip()
            if name == habit_name:
                if stripped.startswith("- [x]"):
                    time_suffix = (" ✅" + stripped.split("✅", 1)[1]) if "✅" in stripped else ""
                    sections["habits"][i] = f"- [x] {new_text}{time_suffix}"
                else:
                    sections["habits"][i] = f"- [ ] {new_text}"
                break
        _write_file(service, daily_id, _build_daily_content(sections))
        return True

    todo_idx = item_number - num_habits - 1
    line_idx, line_text = todo_lines[todo_idx]
    stripped = line_text.strip()

    if stripped.startswith("- [x]"):
        # 완료 상태 유지 — ✅ 이후 시각 부분도 그대로 보존
        time_suffix = ""
        if "✅" in stripped:
            time_suffix = " ✅" + stripped.split("✅", 1)[1]
        sections["todos"][line_idx] = f"- [x] {new_text}{time_suffix}"
    else:
        sections["todos"][line_idx] = f"- [ ] {new_text}"

    _write_file(service, daily_id, _build_daily_content(sections))
    return True


def delete_todo(item_number: int) -> bool | str:
    """get_today_todos() 기준 번호로 항목을 삭제한다.

    반환값:
      True          — 삭제 성공
      False         — 존재하지 않는 번호 또는 완료된 할 일 항목
      "has_history" — 완료 기록이 있는 습관 (삭제 불가, 수정만 가능)

    습관은 완료 기록이 없으면 삭제 가능.
    완료된 할 일 항목은 삭제 불가.
    """
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")
    date_str = datetime.now().strftime("%Y-%m-%d")

    habits_id = _get_habits_file_id(service, todo_folder_id)
    habits = _parse_habits(_read_file(service, habits_id))

    daily_id = _get_daily_file_id(service, todo_folder_id, date_str)
    sections = _parse_daily_sections(_read_file(service, daily_id))
    sections, _ = _sync_habits_to_daily(sections, habits)

    num_habits = len(habits)
    todo_lines = [(i, l) for i, l in enumerate(sections["todos"])
                  if l.strip().startswith("- [ ]") or l.strip().startswith("- [x]")]

    if not (1 <= item_number <= num_habits + len(todo_lines)):
        return False

    if item_number <= num_habits:
        habit = habits[item_number - 1]
        if habit["completed_dates"]:
            return "has_history"

        # 완료 기록 없는 습관: habits.md + daily에서 제거
        habit_name = habit["name"]
        updated = [h for h in habits if h["name"] != habit_name]
        _write_file(service, habits_id, _habits_to_content(updated))

        sections["habits"] = [
            line for line in sections["habits"]
            if not (
                (line.strip().startswith("- [ ]") or line.strip().startswith("- [x]")) and
                line.strip().replace("- [ ] ", "").replace("- [x] ", "").split(" ✅")[0].strip() == habit_name
            )
        ]
        _write_file(service, daily_id, _build_daily_content(sections))
        return True

    todo_idx = item_number - num_habits - 1
    line_idx, line_text = todo_lines[todo_idx]

    if line_text.strip().startswith("- [x]"):
        return False  # 완료 항목 삭제 불가

    del sections["todos"][line_idx]
    _write_file(service, daily_id, _build_daily_content(sections))
    return True


def uncomplete_todo(item_number: int) -> bool:
    """get_today_todos() 기준 번호로 완료 항목을 미완료로 전환한다.

    습관: daily 파일 완료 상태 제거 + habits.md 해당 날짜 완료 기록 제거.
    할 일: daily 파일에서 완료 상태와 시각을 제거.
    미완료 항목에 호출하면 False 반환.
    """
    service = get_drive_service()
    todo_folder_id = _get_folder_id(service, "notes", "Todo")
    date_str = datetime.now().strftime("%Y-%m-%d")

    habits_id = _get_habits_file_id(service, todo_folder_id)
    habits = _parse_habits(_read_file(service, habits_id))

    daily_id = _get_daily_file_id(service, todo_folder_id, date_str)
    sections = _parse_daily_sections(_read_file(service, daily_id))
    sections, _ = _sync_habits_to_daily(sections, habits)

    num_habits = len(habits)
    todo_lines = [(i, l) for i, l in enumerate(sections["todos"])
                  if l.strip().startswith("- [ ]") or l.strip().startswith("- [x]")]

    all_items = [("habit", h["name"]) for h in habits]
    all_items += [("todo", i, l) for i, l in todo_lines]

    if not (1 <= item_number <= len(all_items)):
        return False

    target = all_items[item_number - 1]

    if target[0] == "habit":
        habit_name = target[1]

        # daily 파일: 완료 → 미완료
        completed = False
        for i, line in enumerate(sections["habits"]):
            stripped = line.strip()
            if stripped.startswith("- [x]"):
                name = stripped.replace("- [x] ", "").split(" ✅")[0].strip()
                if name == habit_name:
                    sections["habits"][i] = f"- [ ] {habit_name}"
                    completed = True
                    break
        if not completed:
            return False  # 이미 미완료 상태
        _write_file(service, daily_id, _build_daily_content(sections))

        # habits.md: 오늘 날짜 완료 기록 제거
        habits_list = _parse_habits(_read_file(service, habits_id))
        for h in habits_list:
            if h["name"] == habit_name and date_str in h["completed_dates"]:
                h["completed_dates"].remove(date_str)
                break
        _write_file(service, habits_id, _habits_to_content(habits_list))

    else:
        _, line_idx, line_text = target
        stripped = line_text.strip()
        if not stripped.startswith("- [x]"):
            return False  # 이미 미완료 상태
        # ✅ 이후 시각 제거하고 미완료로 전환
        new_text = stripped.replace("- [x] ", "- [ ] ", 1).split(" ✅")[0]
        sections["todos"][line_idx] = new_text
        _write_file(service, daily_id, _build_daily_content(sections))

    return True
