import asyncio
import logging
import re
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
import os

from drive_module import (
    save_memo, add_todo, add_habit, get_today_todos, complete_todo,
    edit_todo, delete_todo, uncomplete_todo, get_tags, get_tags_list,
    add_tag, delete_tag, confirm_memo,
    get_today_memos, get_week_memo_stats, get_week_habit_stats, get_habit_streaks, save_report,
)
from gemini_module import (
    parse_todo_and_comment, generate_memo_title, suggest_tags,
    get_remaining_rpd, RPD_LIMIT, RPD_WARN_THRESHOLD,
    generate_day_brief_content, generate_weekly_report_content,
    get_rpd_stats, generate_dm_reply,
)
from google_calendar_module import (
    add_event,
    get_today_events_text, get_tomorrow_events_text, get_week_events_text,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# 채널 ID는 .env에서 관리한다 (음수 숫자 형태, 예: -1001234567890)
CH_SCHEDULE = os.getenv("TELEGRAM_CH_SCHEDULE")  # 📅 일정 채널
CH_TODO     = os.getenv("TELEGRAM_CH_TODO")       # ✅ Todo 채널
CH_DAILY    = os.getenv("TELEGRAM_CH_DAILY")      # 📥 일상 메모 채널
CH_OWNER    = os.getenv("TELEGRAM_OWNER_ID")      # 👤 브리프 수신용 1:1 DM 채팅 ID
CH_REPORT   = os.getenv("TELEGRAM_CH_REPORT")     # 📊 보고서 전용 채널 (미설정 시 CH_OWNER → CH_DAILY 폴백)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)


# ── 메모 확인/취소 자연어 ────────────────────────────────────────────────────────
_CONFIRM_WORDS = {"ㄱㄱ", "ㅇㅇ", "응", "좋아", "네", "ok", "yes", "확인", "!확인", "저장"}
_CANCEL_WORDS  = {"ㄴㄴ", "취소", "아니", "no", "싫어", "!취소"}

# ── 메모 묶음 상태 ──────────────────────────────────────────────────────────────
_memo_buffers: dict[str, list[str]] = {}   # chat_id → 누적 메시지 리스트
_memo_timers:  dict[str, asyncio.Task] = {} # chat_id → 5분 자동 flush 태스크
_pending_drafts: dict[str, str] = {}        # chat_id → Drive 파일 ID (확인 대기 중)
_memo_titles: dict[str, str] = {}           # chat_id → 명시적 제목 (본문 제외)

_TITLE_PATTERNS = [
    re.compile(r'^(?:제목|title)\s*:\s*(.+)$', re.IGNORECASE),
    re.compile(r'^[""""](.+)[""""]$'),   # 영문/한국어 큰따옴표
    re.compile(r"^['''](.+)[''']$"),    # 영문/한국어 작은따옴표
    re.compile(r'^\[(.+)\]$'),           # 대괄호
]


def _extract_explicit_title(text: str) -> str | None:
    s = text.strip()
    for pat in _TITLE_PATTERNS:
        m = pat.match(s)
        if m:
            return m.group(1).strip()
    return None

MEMO_FLUSH_DELAY = 300  # 5분

_scheduler = AsyncIOScheduler(timezone='Asia/Seoul')


async def _flush_memo(bot, chat_id: str, title: str | None = None):
    """버퍼 메시지를 하나로 묶어 draft로 저장하고 미리보기를 전송한다.

    title을 넘기면 그대로 사용, 없으면 Gemini가 자동 생성한다.
    """
    messages = _memo_buffers.pop(chat_id, [])
    explicit_title = _memo_titles.pop(chat_id, None)
    if not messages:
        return

    combined = "\n\n".join(messages)
    final_title = title or explicit_title or generate_memo_title(combined)

    # 본문에 #태그 있으면 YAML에 반영 + 새 태그면 목록에도 자동 추가 + 본문에서 제거
    content_tags = list(dict.fromkeys(
        m.lstrip('#') for m in re.findall(r'#\S+', combined) if m.lstrip('#')
    ))
    if content_tags:
        for tag in content_tags:
            add_tag(tag)  # 이미 있으면 무시, 없으면 tags.md에 추가
        recommended_tags = content_tags
        combined = re.sub(r'#\S+', '', combined).strip()
    else:
        available_tags = get_tags_list()
        recommended_tags = suggest_tags(combined, available_tags) if available_tags else []

    file_id = save_memo(combined, title=final_title, tags=recommended_tags)
    _pending_drafts[chat_id] = file_id

    preview = combined if len(combined) <= 300 else combined[:300] + "..."
    reply = f"📋 미리보기\n\n제목: {final_title}\n\n{preview}"
    if recommended_tags:
        reply += "\n\n🏷️ 추천 태그: " + " ".join(f"#{t}" for t in recommended_tags)
    reply += "\n\n저장할까요?\n!확인 — 저장 확정  |  !취소 — 취소"
    reply += _rpd_warning()
    await bot.send_message(chat_id=int(chat_id), text=reply)


def _rpd_warning() -> str:
    remaining = get_remaining_rpd()
    if remaining < RPD_WARN_THRESHOLD:
        return f"\n\n⚠️ 오늘 AI 호출 가능 횟수: {remaining}회 남음"
    return ""


# ── 브리프 & 보고서 전송 ────────────────────────────────────────────────────────

async def send_morning_brief(bot, chat_id: str):
    """매일 08:00 모닝 브리프를 전송한다."""
    try:
        events_text = get_today_events_text()
        todos_text = get_today_todos()
        streaks_text = get_habit_streaks()
        brief = (
            f"🌅 좋은 아침입니다!\n\n"
            f"📅 오늘 일정\n{events_text}\n\n"
            f"{todos_text}\n\n"
        )
        if streaks_text:
            brief += f"🔥 습관 연속 기록\n{streaks_text}\n\n"
        brief += (
            f"📌 오늘 계획이 있으시면 각 채널로 알려주세요.\n"
            f"• 📅 일정 채널 — 새 일정 추가\n"
            f"• ✅ Todo 채널 — 할 일 추가"
        )
        await bot.send_message(chat_id=int(chat_id), text=brief)
    except Exception as e:
        logging.error(f"모닝 브리프 전송 실패: {e}")


async def send_day_brief(bot, chat_id: str):
    """매일 22:00 데이 브리프를 전송한다. 오늘 기록이 없으면 전송하지 않는다."""
    try:
        memos = get_today_memos()
        if not memos:
            return
        events_text = get_today_events_text()
        tomorrow_text = get_tomorrow_events_text()
        streaks_text = get_habit_streaks()
        ai_content = generate_day_brief_content(memos, events_text)

        tag_counts: dict[str, int] = {}
        for m in memos:
            for tag in m.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        tag_text = (
            "  ".join(f"#{k}({v})" for k, v in sorted(tag_counts.items(), key=lambda x: -x[1]))
            if tag_counts else "태그 없음"
        )

        brief = (
            f"🌙 오늘 하루 수고하셨습니다!\n\n"
            f"📝 오늘 기록 ({len(memos)}건)\n{tag_text}\n\n"
        )
        if streaks_text:
            brief += f"🔥 습관 연속 기록\n{streaks_text}\n\n"
        brief += (
            f"📅 오늘 일정\n{events_text}\n\n"
            f"📅 내일 일정\n{tomorrow_text}\n\n"
            f"💬 {ai_content['question']}\n\n"
            f"✨ {ai_content['summary']}\n\n"
            f"📌 기록을 남기고 싶으시면 각 채널로 알려주세요.\n"
            f"• 📥 메모 채널 — 오늘 하루 기록\n"
            f"• 📅 일정 채널 — 내일 일정 추가\n"
            f"• ✅ Todo 채널 — 할 일 추가"
        )
        brief += _rpd_warning()
        await bot.send_message(chat_id=int(chat_id), text=brief)
    except Exception as e:
        logging.error(f"데이 브리프 전송 실패: {e}")


async def send_weekly_report(bot, chat_id: str):
    """매주 일요일 21:00 주간 보고서를 전송하고 Drive에 저장한다."""
    try:
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        start_date = monday.strftime("%Y-%m-%d")
        end_date = sunday.strftime("%Y-%m-%d")
        week_label = f"{monday.strftime('%m/%d')} ~ {sunday.strftime('%m/%d')}"

        stats = get_week_memo_stats(start_date, end_date)
        habits_text = get_week_habit_stats(start_date, end_date)
        events_text = get_week_events_text()
        ai_content = generate_weekly_report_content(stats, habits_text, events_text)

        tag_text = (
            "\n".join(f"• {k}: {v}회"
                      for k, v in sorted(stats["by_tag"].items(), key=lambda x: -x[1]))
            or "없음"
        )

        report = (
            f"📊 주간 보고서 ({week_label})\n\n"
            f"📝 이번 주 기록 ({stats['total']}건)\n{tag_text}\n\n"
            f"📅 이번 주 일정\n{events_text}\n\n"
            f"🔁 습관 이행 현황\n{habits_text}\n\n"
            f"✨ {ai_content['summary']}\n\n"
            f"💡 {ai_content['suggestion']}"
        )
        report += _rpd_warning()

        save_report(f"# 주간 보고서 {week_label}\n\n{report}", "weekly", end_date)
        await bot.send_message(chat_id=int(chat_id), text=report)
    except Exception as e:
        logging.error(f"주간 보고서 전송 실패: {e}")


async def _setup_scheduler(application):
    """봇 초기화 후 브리프 스케줄러를 시작한다."""
    brief_target = CH_OWNER or CH_DAILY
    if not brief_target:
        logging.warning("TELEGRAM_OWNER_ID / CH_DAILY 미설정 — 브리프 스케줄러 비활성화")
        return
    report_target = CH_REPORT or CH_OWNER or CH_DAILY
    bot = application.bot
    _scheduler.add_job(send_morning_brief, 'cron', hour=8,  minute=0, args=[bot, brief_target])
    _scheduler.add_job(send_day_brief,     'cron', hour=22, minute=0, args=[bot, brief_target])
    _scheduler.add_job(
        send_weekly_report, 'cron',
        day_of_week='sun', hour=21, minute=0,
        args=[bot, report_target],
    )
    _scheduler.start()
    brief_label  = "DM (OWNER)" if CH_OWNER else "CH_DAILY"
    report_label = "CH_REPORT" if CH_REPORT else brief_label
    logging.info(f"브리프 스케줄러 시작 → {brief_label} (모닝 08:00 / 데이 22:00) / 주간 보고서 → {report_label} (일 21:00)")


HELP_TODO = (
    "✅ Todo 채널 명령어\n"
    "\n"
    "!조회              오늘 할 일 + 습관 목록 보기\n"
    "!할일 <내용>        할 일 추가\n"
    "!습관 <내용>        매일 반복 습관 추가\n"
    "!완료 <번호>        항목 완료 처리\n"
    "!취소 <번호>        완료 항목 미완료로 되돌리기\n"
    "!삭제 <번호>        미완료 항목 삭제\n"
    "!수정 <번호> <내용>  항목 텍스트 수정\n"
    "\n"
    "자연어도 됩니다.\n"
    "예) 헬스장 가기 추가해줘 / 3번 완료했어 / 오늘 할 일 보여줘"
)

HELP_SCHEDULE = (
    "📅 일정 채널 사용법\n"
    "\n"
    "자연어로 일정을 입력하면 Google Calendar에 자동 등록됩니다.\n"
    "\n"
    "예) 내일 3시 강남역 미팅\n"
    "예) 다음 주 화요일 치과 예약\n"
    "예) 5월 3일 종일 휴가"
)

HELP_DM = (
    "대상혁 봇 전체 사용 가이드\n"
    "\n"
    "📅 일정 채널\n"
    "자연어로 일정을 입력하면 Google Calendar에 자동 등록됩니다.\n"
    "예) 내일 3시 강남역 미팅 / 5월 3일 종일 휴가\n"
    "\n"
    "✅ Todo 채널\n"
    "할 일과 습관을 관리합니다.\n"
    "!조회              오늘 할 일 + 습관 목록\n"
    "!할일 <내용>        할 일 추가\n"
    "!습관 <내용>        반복 습관 추가\n"
    "!완료 <번호>        완료 처리\n"
    "!취소 <번호>        완료 → 미완료 전환\n"
    "!삭제 <번호>        미완료 항목 삭제\n"
    "!수정 <번호> <내용>  항목 수정\n"
    "자연어도 지원됩니다.\n"
    "\n"
    "📥 메모 채널\n"
    "자유롭게 입력하면 버퍼에 쌓이고, 5분 후 자동 저장됩니다.\n"
    "/done [제목]        즉시 저장 (제목 생략 시 AI 자동 생성)\n"
    "/끝 [제목]          /done 과 동일\n"
    "ㄱㄱ / ㅇㅇ          미리보기 확인 후 저장 확정\n"
    "ㄴㄴ / 취소          저장 취소\n"
    "#태그명             태그 사용 (없으면 자동 추가)\n"
    "!태그              태그 목록 조회\n"
    "!태그삭제 <태그명>   태그 삭제\n"
    "!통계              Gemini API 호출 통계\n"
    "\n"
    "💬 이 대화창 (DM)\n"
    "!help              이 도움말\n"
    "!통계              Gemini API 호출 통계\n"
    "그 외 메시지       대상혁과 자유 대화\n"
    "\n"
    "🌅 브리프 (자동 발송)\n"
    "모닝 브리프  매일 08:00 — 오늘 일정 + 할 일 + 습관 연속 기록\n"
    "데이 브리프  매일 22:00 — 오늘 기록 요약 + AI 질문·총평\n"
    "주간 보고서  매주 일요일 21:00 — 주간 통계 + AI 총평"
)

HELP_DAILY = (
    "📥 일상 메모 채널 사용법\n"
    "\n"
    "텍스트를 입력하면 버퍼에 쌓이고, /done 또는 5분 후 자동으로 묶어 저장됩니다.\n"
    "\n"
    "💾 메모 묶음\n"
    "/done [제목]        즉시 저장 및 미리보기 (제목 생략 시 AI 자동 생성)\n"
    "/끝 [제목]          /done 과 동일\n"
    "저장 확인: ㄱㄱ / 응 / 좋아 / 네 / !확인\n"
    "저장 취소: ㄴㄴ / 취소 / 아니 / !취소\n"
    "\n"
    "🏷️ 태그 명령어\n"
    "#태그명             태그 사용 (없으면 자동 추가)\n"
    "!태그              등록된 태그 목록 보기\n"
    "!태그삭제 <태그명>   태그 삭제"
)


async def chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"이 채팅 ID: {update.message.chat_id}")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "안녕하세요. 대상혁입니다.\n"
        "기록하는 습관, 제가 옆에서 도와드리겠습니다.\n"
        "\n"
        "📅 일정 채널 — 자연어로 Google Calendar에 자동 등록\n"
        "✅ Todo 채널 — 오늘 할 일과 반복 습관 관리\n"
        "📥 메모 채널 — 자유롭게 입력하면 Drive에 자동 저장\n"
        "💬 이 대화창 — 대상혁과 자유 대화\n"
        "\n"
        "🌅 매일 08:00 모닝 브리프 / 🌙 22:00 데이 브리프 / 📊 일요일 21:00 주간 보고서를 자동으로 보내드립니다.\n"
        "\n"
        "자세한 사용법은 이 대화창에서 !help 를 입력하세요."
    )


async def handle_todo_channel(msg, text: str):
    """Todo 채널 메시지 처리.

    !명령어는 Gemini 호출 없이 즉시 처리한다 (빠름, API 절약).
    그 외 자연어는 Gemini로 의도 파싱 + 페르소나 코멘트 생성.
    """
    # ── ! 명령어 직접 처리 ──────────────────────────────────────────────────────
    if text.strip() in ("!help", "!도움말"):
        await msg.reply_text(HELP_TODO)
        return

    if text.strip() == "!조회":
        await msg.reply_text(get_today_todos())
        return

    if text.startswith("!습관 "):
        habit_text = text[len("!습관 "):].strip()
        if not habit_text:
            await msg.reply_text("습관 내용을 입력해주세요.\n예: !습관 운동 30분")
            return
        success = add_habit(habit_text)
        await msg.reply_text(f"🔁 습관 추가했습니다.\n{habit_text}" if success
                              else f"이미 등록된 습관입니다.\n{habit_text}")
        return

    if text.startswith("!완료 "):
        num_str = text[len("!완료 "):].strip()
        if not num_str.isdigit():
            await msg.reply_text("번호를 입력해주세요.\n예: !완료 2")
            return
        success = complete_todo(int(num_str))
        await msg.reply_text(f"✅ {num_str}번 완료했습니다." if success
                              else f"❌ {num_str}번 항목을 찾지 못했습니다.\n할 일 목록을 확인해주세요.")
        return

    if text.startswith("!할일 "):
        todo_text = text[len("!할일 "):].strip()
        add_todo(todo_text)
        await msg.reply_text(f"✅ 할 일 추가했습니다.\n{todo_text}")
        return

    if text.startswith("!수정 "):
        # 형식: !수정 번호 새텍스트 (예: !수정 3 헬스장 예약 취소)
        rest = text[len("!수정 "):].strip()
        parts = rest.split(" ", 1)
        if len(parts) < 2 or not parts[0].isdigit():
            await msg.reply_text("형식: !수정 번호 새텍스트\n예: !수정 3 헬스장 예약 취소")
            return
        num, new_text = int(parts[0]), parts[1].strip()
        success = edit_todo(num, new_text)
        await msg.reply_text(f"✏️ {num}번을 수정했습니다.\n{new_text}" if success
                              else f"❌ {num}번 항목을 찾지 못했습니다.\n할 일 목록을 확인해주세요.")
        return

    if text.startswith("!삭제 "):
        num_str = text[len("!삭제 "):].strip()
        if not num_str.isdigit():
            await msg.reply_text("번호를 입력해주세요.\n예: !삭제 3")
            return
        num = int(num_str)
        result = delete_todo(num)
        if result is True:
            await msg.reply_text(f"🗑️ {num}번을 삭제했습니다.")
        elif result == "has_history":
            await msg.reply_text(
                f"❌ {num}번 습관은 완료 기록이 있어 삭제할 수 없습니다.\n"
                "이름 수정은 가능합니다. (!수정 번호 새이름)"
            )
        else:
            await msg.reply_text(
                f"❌ {num}번을 삭제할 수 없습니다.\n"
                "완료된 항목이거나 존재하지 않는 번호입니다.\n"
                "완료 항목은 !취소로 미완료 전환 후 삭제해주세요."
            )
        return

    if text.startswith("!취소 "):
        num_str = text[len("!취소 "):].strip()
        if not num_str.isdigit():
            await msg.reply_text("번호를 입력해주세요.\n예: !취소 3")
            return
        num = int(num_str)
        success = uncomplete_todo(num)
        await msg.reply_text(f"↩️ {num}번을 미완료로 되돌렸습니다." if success
                              else f"❌ {num}번은 이미 미완료 상태이거나 존재하지 않습니다.")
        return

    # ── 자연어 → Gemini 파싱 ────────────────────────────────────────────────────
    parsed = parse_todo_and_comment(text)
    intent = parsed.get("intent", "unknown")
    comment = parsed.get("comment", "")
    rpd_warn = _rpd_warning()

    if intent == "query":
        await msg.reply_text(get_today_todos())

    elif intent == "add_todo":
        # 여러 할 일이 한 문장에 담긴 경우 texts 리스트로 받아 각각 저장
        texts = parsed.get("texts", [parsed.get("text", text)])
        for t in texts:
            add_todo(t)
        items = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))
        reply = f"✅ 할 일 추가했습니다.\n{items}"
        if comment:
            reply += f"\n\n{comment}"
        await msg.reply_text(reply + rpd_warn)

    elif intent == "add_habit":
        habit_text = parsed.get("text", text)
        success = add_habit(habit_text)
        if success:
            reply = f"🔁 습관 추가했습니다.\n{habit_text}"
            if comment:
                reply += f"\n\n{comment}"
        else:
            # 이미 등록된 습관은 코멘트 없이 안내만
            reply = f"이미 등록된 습관입니다.\n{habit_text}"
        await msg.reply_text(reply + rpd_warn)

    elif intent == "complete":
        num = parsed.get("number")
        if num:
            success = complete_todo(int(num))
            if success:
                reply = f"✅ {num}번 완료했습니다."
                if comment:
                    reply += f"\n\n{comment}"
            else:
                reply = f"❌ {num}번 항목을 찾지 못했습니다.\n할 일 목록을 확인해주세요."
            await msg.reply_text(reply + rpd_warn)
        else:
            await msg.reply_text("몇 번을 완료할까요?\n예: 2번 완료해줘")

    elif intent == "edit_todo":
        num = parsed.get("number")
        new_text = parsed.get("text", "").strip()
        if num and new_text:
            success = edit_todo(int(num), new_text)
            if success:
                reply = f"✏️ {num}번을 수정했습니다.\n{new_text}"
                if comment:
                    reply += f"\n\n{comment}"
            else:
                reply = f"❌ {num}번 항목을 찾지 못했습니다.\n할 일 목록을 확인해주세요."
            await msg.reply_text(reply + rpd_warn)
        else:
            await msg.reply_text("몇 번을 어떻게 수정할까요?\n예: 3번 헬스장 예약 취소로 바꿔줘")

    elif intent == "delete_todo":
        num = parsed.get("number")
        if num:
            result = delete_todo(int(num))
            if result is True:
                reply = f"🗑️ {num}번을 삭제했습니다."
                if comment:
                    reply += f"\n\n{comment}"
            elif result == "has_history":
                reply = (
                    f"❌ {num}번 습관은 완료 기록이 있어 삭제할 수 없습니다.\n"
                    "이름 수정은 가능합니다. (!수정 번호 새이름)"
                )
            else:
                reply = (
                    f"❌ {num}번을 삭제할 수 없습니다.\n"
                    "완료된 항목이거나 존재하지 않는 번호입니다.\n"
                    "완료 항목은 먼저 미완료로 전환해주세요."
                )
            await msg.reply_text(reply + rpd_warn)
        else:
            await msg.reply_text("몇 번을 삭제할까요?\n예: 3번 삭제해줘")

    elif intent == "uncomplete":
        num = parsed.get("number")
        if num:
            success = uncomplete_todo(int(num))
            if success:
                reply = f"↩️ {num}번을 미완료로 되돌렸습니다."
                if comment:
                    reply += f"\n\n{comment}"
            else:
                reply = f"❌ {num}번은 이미 미완료 상태이거나 존재하지 않습니다."
            await msg.reply_text(reply + rpd_warn)
        else:
            await msg.reply_text("몇 번을 미완료로 되돌릴까요?\n예: 3번 완료 취소해줘")

    else:
        await msg.reply_text(
            "이해하지 못했습니다.\n\n"
            "예시:\n"
            "• 오늘 할 일 보여줘\n"
            "• 헬스장 가기 추가해줘\n"
            "• 독서 30분 습관으로 등록해줘\n"
            "• 2번 완료했어"
        )


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # 채널 포스트와 일반 DM 메시지를 구분해서 처리한다.
    is_channel = update.channel_post is not None
    msg = update.channel_post if is_channel else update.message

    if not msg or not msg.text:
        return

    text = msg.text
    chat_id = str(msg.chat_id)
    logging.info(f"메시지 수신 | chat_id={chat_id} | text={text}")

    try:
        if chat_id == CH_SCHEDULE:
            if text.strip() in ("!help", "!도움말"):
                await msg.reply_text(HELP_SCHEDULE)
                return
            # 자연어를 Gemini로 파싱해서 Google Calendar에 등록한다.
            success, result_msg = add_event(text)
            if success:
                await msg.reply_text(result_msg + _rpd_warning())
            else:
                await msg.reply_text(
                    "📅 날짜를 찾지 못했습니다.\n"
                    "다시 입력해주세요. (예: '내일 3시 강남역 미팅')"
                )

        elif chat_id == CH_TODO:
            await handle_todo_channel(msg, text)

        elif chat_id == CH_DAILY:
            if text.strip() in ("!help", "!도움말"):
                await msg.reply_text(HELP_DAILY)
                return

            if text.strip() == "!태그":
                await msg.reply_text(get_tags())
                return

            if text.strip() == "!통계":
                s = get_rpd_stats(days=7)
                if s["days_with_data"] == 0:
                    await msg.reply_text("아직 통계 데이터가 없습니다. 며칠 사용 후 다시 확인해주세요.")
                else:
                    await msg.reply_text(
                        f"📊 Gemini API 호출 통계\n\n"
                        f"오늘: {s['today']}회\n"
                        f"최근 7일 평균: {s['avg']}회/일\n"
                        f"최근 7일 최고: {s['max']}회\n"
                        f"(데이터 {s['days_with_data']}일치 기준)\n\n"
                        f"일일 한도: {RPD_LIMIT}회"
                    )
                return

            if text.startswith("!태그삭제 "):
                tag = text[len("!태그삭제 "):].strip()
                if not tag:
                    await msg.reply_text("태그명을 입력해주세요.\n예: !태그삭제 운동")
                    return
                success = delete_tag(tag)
                await msg.reply_text(f"🗑️ '{tag}' 태그를 삭제했습니다." if success
                                     else f"등록되지 않은 태그입니다: {tag}")
                return

            # ── #태그명 단독 입력: 없으면 추가, 있으면 기존 태그 확인 ────────────────
            stripped = text.strip()
            if stripped and all(w.startswith('#') for w in stripped.split()):
                results = []
                for word in stripped.split():
                    tag = word.lstrip('#')
                    if tag:
                        is_new = add_tag(tag)
                        results.append(f"#{tag} {'추가됨' if is_new else '(기존)'}")
                # 진행 중인 메모 버퍼가 있으면 태그를 버퍼에도 추가 → flush 시 YAML에 반영됨
                if _memo_buffers.get(chat_id):
                    _memo_buffers[chat_id].append(text)
                await msg.reply_text("🏷️ " + "  ".join(results))
                return

            # ── 미리보기 승인 흐름 (자연어 지원) ─────────────────────────────────────
            if chat_id in _pending_drafts:
                clean = text.strip().lower()
                if clean in _CONFIRM_WORDS:
                    confirm_memo(_pending_drafts.pop(chat_id))
                    await msg.reply_text("✅ 메모가 저장됐습니다.")
                    return
                if clean in _CANCEL_WORDS:
                    _pending_drafts.pop(chat_id)
                    await msg.reply_text("취소했습니다. 파일은 draft 상태로 남아있습니다.")
                    return
                await msg.reply_text(
                    "이전 메모가 아직 확인되지 않았습니다.\n"
                    "저장: ㄱㄱ / 응 / 좋아  |  취소: ㄴㄴ / 취소"
                )
                return

            # ── /done — 즉시 flush (브리프 답변 대기 상태도 해제) ──────────────────
            if text.strip() in ("/done", "/끝") or text.startswith("/done ") or text.startswith("/끝 "):
                if not _memo_buffers.get(chat_id):
                    _memo_titles.pop(chat_id, None)  # 내용 없이 /done 시 제목도 정리
                    await msg.reply_text("묶을 메모가 없습니다.")
                    return
                if chat_id in _memo_timers:
                    _memo_timers.pop(chat_id).cancel()
                cmd = "/끝" if text.startswith("/끝") else "/done"
                manual_title = text[len(cmd):].strip() or None
                await _flush_memo(ctx.bot, chat_id, title=manual_title)
                return

            # ── 메모 버퍼에 누적 ─────────────────────────────────────────────────
            is_new_bundle = not _memo_buffers.get(chat_id)

            # 첫 메시지에서 명시적 제목 패턴 감지 — 본문에는 추가하지 않음
            if is_new_bundle and not _memo_titles.get(chat_id):
                extracted = _extract_explicit_title(text)
                if extracted:
                    _memo_titles[chat_id] = extracted
                    bot = ctx.bot
                    async def _auto_flush_title():
                        await asyncio.sleep(MEMO_FLUSH_DELAY)
                        await _flush_memo(bot, chat_id)
                        _memo_timers.pop(chat_id, None)
                    _memo_timers[chat_id] = asyncio.create_task(_auto_flush_title())
                    await msg.reply_text(
                        f"📝 제목: {extracted}\n"
                        "메모 내용을 입력하세요. /done 또는 5분 후 자동 저장됩니다."
                    )
                    return

            _memo_buffers.setdefault(chat_id, []).append(text)

            # 타이머 리셋
            if chat_id in _memo_timers:
                _memo_timers.pop(chat_id).cancel()

            bot = ctx.bot

            async def _auto_flush():
                await asyncio.sleep(MEMO_FLUSH_DELAY)
                await _flush_memo(bot, chat_id)
                _memo_timers.pop(chat_id, None)

            _memo_timers[chat_id] = asyncio.create_task(_auto_flush())

            if is_new_bundle:
                await msg.reply_text(
                    "📝 메모 받았습니다.\n"
                    "계속 입력하거나 /done 으로 즉시 저장하세요. (5분 후 자동 저장)"
                )

        else:
            # DM — 명령어 처리 후 일반 텍스트는 대상혁 자유 대화
            stripped = text.strip()
            if stripped in ("!help", "!도움말"):
                await msg.reply_text(HELP_DM)
            elif stripped == "!통계":
                s = get_rpd_stats(days=7)
                if s["days_with_data"] == 0:
                    await msg.reply_text("아직 통계 데이터가 없습니다. 며칠 사용 후 다시 확인해주세요.")
                else:
                    await msg.reply_text(
                        f"📊 Gemini API 호출 통계\n\n"
                        f"오늘: {s['today']}회\n"
                        f"최근 7일 평균: {s['avg']}회/일\n"
                        f"최근 7일 최고: {s['max']}회\n"
                        f"(데이터 {s['days_with_data']}일치 기준)\n\n"
                        f"일일 한도: {RPD_LIMIT}회"
                    )
            elif stripped.startswith("!"):
                await msg.reply_text("알 수 없는 명령어입니다. !help 로 도움말을 확인하세요.")
            else:
                reply = generate_dm_reply(text)
                await msg.reply_text(reply + _rpd_warning())

    except Exception as e:
        logging.error(f"처리 실패: {e}", exc_info=True)
        await msg.reply_text("❌ 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")


def main():
    app = ApplicationBuilder().token(TOKEN).post_init(_setup_scheduler).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS & filters.TEXT, handle_message))
    logging.info("대상혁 봇 시작!")
    app.run_polling()


if __name__ == "__main__":
    main()
