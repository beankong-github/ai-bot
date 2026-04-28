"""
Google OAuth2 인증 스크립트 — 최초 실행 또는 token.json 재발급 시 사용.

Pi처럼 브라우저가 없는 환경에서는 run_console()을 사용한다.
URL을 PC 브라우저에서 열고, 발급된 코드를 터미널에 붙여넣으면 된다.
"""

import os
from google_auth_oauthlib.flow import InstalledAppFlow

# drive_module.py, google_calendar_module.py와 동일한 스코프 유지
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/drive',
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDS_PATH = os.path.join(BASE_DIR, 'credentials.json')
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')

flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)

# Pi(헤드리스) 환경용 — SSH 포트 포워딩 방식
# PC에서 먼저 새 터미널로 터널을 열어야 한다:
#   ssh -L 8080:localhost:8080 -p 2222 bk@bkpie00.duckdns.org
# 그 후 Pi에서 이 스크립트 실행 → 출력된 URL을 PC 브라우저에서 열기
creds = flow.run_local_server(port=8080, open_browser=False)

with open(TOKEN_PATH, 'w') as f:
    f.write(creds.to_json())

print("✅ token.json 생성 완료!")
