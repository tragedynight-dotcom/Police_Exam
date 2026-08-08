# DaMoa Streamlit

Next.js 앱과 같은 SQLite DB(`dev.db`)를 쓰는 Streamlit 버전입니다.

## 실행

```bash
python -m pip install -r streamlit_app/requirements.txt
python -m streamlit run streamlit_app/app.py
```

- Local: http://localhost:8501

## 포함 기능

- 로그인 / 회원가입 / 이메일 OTP 인증 / 비밀번호 재설정
- 대시보드 (이어하기, 학습/시험 모드, 실전 모의고사 40문항, 최근 결과)
- 주제별·전체 풀이, 즉시 해설 / 제출 후 해설
- 시험 타이머(문항당 1분), 채점, 틀린 문제 다시 풀기

## 참고

- `@police.go.kr` 이메일만 가입·로그인 가능
- 인증번호는 화면에 표시하지 않으며, EmailJS/SMTP 설정으로 메일 발송합니다
- Next.js와 동시에 같은 DB를 쓰면 잠금이 날 수 있으니, 가능하면 하나만 실행하세요
