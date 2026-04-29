import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import os

from drive_module import save_memo, add_todo, add_habit, get_today_todos, complete_todo, edit_todo, delete_todo, uncomplete_todo, get_tags, add_tag, delete_tag
from gemini_module import parse_todo_and_comment, generate_memo_title, get_remaining_rpd, RPD_WARN_THRESHOLD
from google_calendar_module import add_event

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# 채널 ID는 .env에서 관리한다 (음수 숫자 형태, 예: -1001234567890)
CH_SCHEDULE = os.getenv("TELEGRAM_CH_SCHEDULE")  # 📅 일정 채널
CH_TODO     = os.getenv("TELEGRAM_CH_TODO")       # ✅ Todo 채널
CH_DAILY    = os.getenv("TELEGRAM_CH_DAILY")      # 📥 일상 메모 채널

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)


def _rpd_warning() -> str:
    remaining = get_remaining_rpd()
    if remaining < RPD_WARN_THRESHOLD:
        return f"\n\n⚠️ 오늘 AI 호출 가능 횟수: {remaining}회 남음"
    return ""


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

HELP_DAILY = (
    "📥 일상 메모 채널 사용법\n"
    "\n"
    "텍스트를 입력하면 Google Drive Inbox에 .md 파일로 저장됩니다.\n"
    "\n"
    "🏷️ 태그 명령어\n"
    "!태그              등록된 태그 목록 보기\n"
    "!태그추가 <태그명>   태그 추가\n"
    "!태그삭제 <태그명>   태그 삭제"
)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "안녕하세요! 대상혁입니다.\n열심히 기록하세요. 나태해질 생각 하지 마세요 😤\n\n"
        "📅 일정 채널 → 자연어로 일정 입력 (예: '내일 3시 강남역 미팅')\n"
        "✅ Todo 채널 → 할 일 관리\n"
        "  !조회 — 오늘 할 일 보기\n"
        "  !습관 내용 — 매일 반복 습관 추가\n"
        "  !완료 번호 — 항목 완료 처리\n"
        "  !취소 번호 — 완료 항목 미완료로 전환\n"
        "  !삭제 번호 — 미완료 항목 삭제\n"
        "  !수정 번호 새텍스트 — 항목 수정\n"
        "  그 외 텍스트 — 오늘 할 일로 바로 추가\n"
        "📥 일상 메모 채널 → 메모 저장\n\n"
        "그냥 쓰세요. 생각하지 말고."
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

            if text.startswith("!태그추가 "):
                tag = text[len("!태그추가 "):].strip()
                if not tag:
                    await msg.reply_text("태그명을 입력해주세요.\n예: !태그추가 운동")
                    return
                success = add_tag(tag)
                await msg.reply_text(f"🏷️ '{tag}' 태그를 추가했습니다." if success
                                     else f"이미 등록된 태그입니다: {tag}")
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

            title = generate_memo_title(text)
            save_memo(text, title=title)
            await msg.reply_text("📝 저장했습니다." + _rpd_warning())

        else:
            # 등록되지 않은 채널이나 DM은 일상 메모로 저장한다.
            save_memo(text)
            await msg.reply_text("📝 저장했습니다.")

    except Exception as e:
        logging.error(f"처리 실패: {e}")
        await msg.reply_text(f"❌ 오류가 발생했습니다: {e}")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS & filters.TEXT, handle_message))
    logging.info("대상혁 봇 시작!")
    app.run_polling()


if __name__ == "__main__":
    main()
