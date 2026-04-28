# Pi 운영 명령어 모음

## Pi 접속

```bash
# 외부 접속
ssh -p 2222 bk@bkpie00.duckdns.org
```

---

## 코드 배포

```bash
# PC에서 푸시
git add .
git commit -m "변경 내용"
git push

# Pi에서 반영 + 서비스 재시작 (한 줄)
cd ~/ai-bot && git pull && sudo systemctl restart ai-bot.service
```

---

## 서비스 관리

```bash
# 시작 / 중지 / 재시작
sudo systemctl start ai-bot.service
sudo systemctl stop ai-bot.service
sudo systemctl restart ai-bot.service

# 현재 상태 확인 (실행 중인지, 오류 있는지)
sudo systemctl status ai-bot.service

# 부팅 시 자동 시작 등록 / 해제
sudo systemctl enable ai-bot.service
sudo systemctl disable ai-bot.service
```

---

## 로그 확인

```bash
# 최근 로그 50줄
sudo journalctl -u ai-bot.service -n 50

# 실시간 로그 스트리밍 (Ctrl+C로 종료)
sudo journalctl -u ai-bot.service -f

# 오늘 로그 전체
sudo journalctl -u ai-bot.service --since today

# 특정 시간 이후 로그
sudo journalctl -u ai-bot.service --since "2026-04-28 09:00"

# 에러 로그만 필터링
sudo journalctl -u ai-bot.service -p err
```

---

## 의존성 관리

```bash
# 패키지 설치
pip install -r requirements.txt

# 새 패키지 추가 후 requirements.txt 갱신
pip freeze > requirements.txt

# 패키지 제거 (예: Notion 걷어낼 때)
pip uninstall notion-client
```

---

## Google 인증 재발급

> token.json 만료 또는 스코프 변경 시 필요

```bash
cd ~/ai-bot

# 기존 토큰 삭제
rm token.json

# 인증 스크립트 실행 (브라우저 없이 Pi에서 하려면 포트 포워딩 필요)
python auth.py
```

---

## 환경변수 (.env)

```bash
# .env 편집
nano ~/ai-bot/.env

# 적용은 서비스 재시작으로 반영됨
sudo systemctl restart ai-bot.service

# 현재 .env 내용 확인
cat ~/ai-bot/.env
```

### 필요한 키 목록

| 키 | 설명 |
|----|------|
| `TELEGRAM_BOT_TOKEN` | 봇파더에서 발급받은 토큰 |
| `TELEGRAM_CH_SCHEDULE` | 📅 일정 채널 ID (음수) |
| `TELEGRAM_CH_TODO` | ✅ Todo 채널 ID (음수) |
| `TELEGRAM_CH_DAILY` | 📥 일상 메모 채널 ID (음수) |
| `GEMINI_API_KEY` | Google AI Studio에서 발급 |

---

## 자주 쓰는 조합

```bash
# 배포 + 재시작 + 로그 확인 (가장 많이 쓰는 흐름)
cd ~/ai-bot && git pull && sudo systemctl restart ai-bot.service && sudo journalctl -u ai-bot.service -f

# 봇이 죽었을 때 빠른 상태 파악
sudo systemctl status ai-bot.service && sudo journalctl -u ai-bot.service -n 30
```
