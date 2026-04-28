import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from notion_client_module import save_memo, save_diary, save_exercise, save_book_record
import os

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

CH_SCHEDULE = os.getenv("TELEGRAM_CH_SCHEDULE")
CH_BOOK     = os.getenv("TELEGRAM_CH_BOOK")
CH_DAILY    = os.getenv("TELEGRAM_CH_DAILY")

BOOK_FORM = (
    "좋습니다. 기록은 습관입니다. 아래 양식을 복사해서 작성해주세요.\n\n"
    "📚 독서 기록 양식\n"
    "제목: (필수)\n"
    "지은이: (필수)\n"
    "읽은 페이지: (예: 1~50 또는 50)\n"
    "감상평: \n"
    "완독: Y 또는 N"
)

def parse_book_form(text: str) -> dict:
    fields = {}
    for line in text.strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()
    return fields

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "안녕하세요! 대상혁입니다.\n열심히 기록하세요. 나태해질 생각 하지 마세요 😤\n\n"
        "기록 안 하면 발전 없습니다.\n\n"
        "📅 일정 채널 → 일정 저장\n"
        "📚 독서 기록 → '대상혁 독서 기록할래'라고 말하세요\n"
        "📥 일상 메모 채널 → #일기 #운동 #메모\n\n"
        "그냥 쓰세요. 생각하지 말고."
    )

async def handle_book_form_submit(msg, text: str, ctx: ContextTypes.DEFAULT_TYPE):
    fields = parse_book_form(text)
    title  = fields.get("제목", "").strip()
    author = fields.get("지은이", "").strip()

    if not title or not author:
        await msg.reply_text(
            "제목과 지은이는 필수입니다.\n양식을 다시 확인해주세요.\n\n"
            "제목: (필수)\n지은이: (필수)\n읽은 페이지: \n감상평: \n완독: Y 또는 N"
        )
        return

    pages    = fields.get("읽은 페이지", "")
    review   = fields.get("감상평", "")
    finished = fields.get("완독", "N").upper() == "Y"

    save_book_record(title, author, pages, review, finished)
    ctx.user_data.pop("waiting_book_form")

    await msg.reply_text(
        f"📚 저장했습니다.\n"
        f"책: {title} / {author}\n"
        f"완독: {'✅' if finished else '📖 읽는 중'}\n\n"
        "기록하는 사람이 성장합니다."
    )

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # 채널 포스트와 일반 메시지 구분
    is_channel = update.channel_post is not None
    msg = update.channel_post if is_channel else update.message

    if not msg or not msg.text:
        return

    text = msg.text
    chat_id = str(msg.chat_id)
    logging.info(f"메시지 수신 | chat_id={chat_id} | text={text}")

    try:
        # 채널이 아닌 DM에서만 독서 양식 플로우 사용
        if not is_channel:
            if ctx.user_data.get("waiting_book_form"):
                await handle_book_form_submit(msg, text, ctx)
                return
            if "대상혁" in text and "독서" in text:
                ctx.user_data["waiting_book_form"] = True
                await msg.reply_text(BOOK_FORM)
                return

        # 채널별 분기
        if chat_id == CH_DAILY:
            if text.startswith("#일기"):
                save_diary(text)
                await msg.reply_text("📔 저장했습니다. 오늘 하루 잘 보내셨길 바랍니다.")
            elif text.startswith("#운동"):
                save_exercise(text)
                await msg.reply_text("🏃 저장했습니다. 꾸준함이 전부입니다.")
            else:
                save_memo(text)
                await msg.reply_text("📝 저장했습니다.")
        elif chat_id == CH_SCHEDULE:
            from google_calendar_module import add_event
            success, result_msg = add_event(text)
            if success:
                await msg.reply_text(result_msg)
            else:
              await msg.reply_text("📅 날짜를 찾지못했습니다.\n다시 입력해주세요. (예: '내일 3시 강남역미팅')")
        elif chat_id == CH_BOOK:
            save_memo(text)
            await msg.reply_text("📚 독서 메모 저장했습니다.")
        else:
            save_memo(text)
            await msg.reply_text("📝 저장했습니다.")

    except Exception as e:
        logging.error(f"저장 실패: {e}")
        await msg.reply_text(f"❌ 저장 실패: {e}")

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS & filters.TEXT, handle_message))
    logging.info("대상혁 봇 시작!")
    app.run_polling()

if __name__ == "__main__":
    main()
