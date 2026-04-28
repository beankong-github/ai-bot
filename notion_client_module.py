from notion_client import Client
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

notion = Client(auth=os.getenv("NOTION_API_KEY"))

DB_MEMO     = os.getenv("NOTION_DB_MEMO")
DB_DIARY    = os.getenv("NOTION_DB_DIARY")
DB_BOOK     = os.getenv("NOTION_DB_BOOK")
DB_EXERCISE = os.getenv("NOTION_DB_EXERCISE")
DB_SCHEDULE = os.getenv("NOTION_DB_SCHEDULE")

now = lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def save_memo(text: str):
    notion.pages.create(
        parent={"database_id": DB_MEMO},
        properties={
            "제목": {"title": [{"text": {"content": text[:50]}}]},
            "날짜": {"date": {"start": now()}},
            "내용": {"rich_text": [{"text": {"content": text}}]},
        }
    )

def save_diary(text: str):
    notion.pages.create(
        parent={"database_id": DB_DIARY},
        properties={
            "제목": {"title": [{"text": {"content": text[:50]}}]},
            "날짜": {"date": {"start": now()}},
            "태그": {"select": {"name": "#일기"}},
            "내용": {"rich_text": [{"text": {"content": text}}]},
        }
    )

def save_exercise(text: str):
    notion.pages.create(
        parent={"database_id": DB_EXERCISE},
        properties={
            "제목": {"title": [{"text": {"content": text[:50]}}]},
            "날짜": {"date": {"start": now()}},
            "메모": {"rich_text": [{"text": {"content": text}}]},
        }
    )

def save_book_record(title: str, author: str, pages: str, review: str, finished: bool):
    notion.pages.create(
        parent={"database_id": DB_BOOK},
        properties={
            "제목":        {"title": [{"text": {"content": title}}]},
            "날짜":        {"date": {"start": now()}},
            "지은이":      {"rich_text": [{"text": {"content": author}}]},
            "읽은 페이지": {"rich_text": [{"text": {"content": pages}}]},
            "감상평":      {"rich_text": [{"text": {"content": review}}]},
            "완독":        {"checkbox": finished},
        }
    )
