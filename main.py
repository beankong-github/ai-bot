import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import os

from drive_module import save_memo, add_todo, add_habit, get_today_todos, complete_todo
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


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "안녕하세요! 대상혁입니다.\n열심히 기록하세요. 나태해질 생각 하지 마세요 😤\n\n"
        "📅 일정 채널 → 자연어로 일정 입력 (예: '내일 3시 강남역 미팅')\n"
        "✅ Todo 채널 → 할 일 관리\n"
        "  !조회 — 오늘 할 일 보기\n"
        "  !습관 내용 — 매일 반복 습관 추가\n"
        "  !완료 번호 — 항목 완료 처리\n"
        "  그 외 텍스트 — 오늘 할 일로 바로 추가\n"
        "📥 일상 메모 채널 → 메모 저장\n\n"
        "그냥 쓰세요. 생각하지 말고."
    )


async def handle_todo_channel(msg, text: str):
    """Todo 채널 메시지를 명령어에 따라 분기 처리한다."""

    if text.strip() == "!조회":
        # 습관 + 오늘 할 일 목록 조회
        result = get_today_todos()
        await msg.reply_text(result)

    elif text.startswith("!습관 "):
        # !습관 뒤의 텍스트를 habits.md에 추가
        habit_text = text[len("!습관 "):].strip()
        if not habit_text:
            await msg.reply_text("습관 내용을 입력해주세요.\n예: !습관 운동 30분")
            return
        add_habit(habit_text)
        await msg.reply_text(f"🔁 습관 추가했습니다.\n{habit_text}")

    elif text.startswith("!완료 "):
        # !완료 뒤의 번호로 해당 항목을 완료 처리
        num_str = text[len("!완료 "):].strip()
        if not num_str.isdigit():
            await msg.reply_text("번호를 입력해주세요.\n예: !완료 2")
            return
        success = complete_todo(int(num_str))
        if success:
            await msg.reply_text(f"✅ {num_str}번 완료했습니다.")
        else:
            await msg.reply_text(f"❌ {num_str}번 항목을 찾지 못했습니다.\n!조회로 번호를 확인해주세요.")

    else:
        # 명령어 없이 입력한 텍스트는 오늘 할 일로 바로 추가
        add_todo(text)
        await msg.reply_text(f"✅ 할 일 추가했습니다.\n{text}")


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
            # 자연어를 Gemini로 파싱해서 Google Calendar에 등록한다.
            success, result_msg = add_event(text)
            if success:
                await msg.reply_text(result_msg)
            else:
                await msg.reply_text(
                    "📅 날짜를 찾지 못했습니다.\n"
                    "다시 입력해주세요. (예: '내일 3시 강남역 미팅')"
                )

        elif chat_id == CH_TODO:
            await handle_todo_channel(msg, text)

        elif chat_id == CH_DAILY:
            # 태그 파싱과 묶음 저장(Phase 4)은 미구현 — 현재는 단건 즉시 저장
            save_memo(text)
            await msg.reply_text("📝 저장했습니다.")

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
