---
status: accepted
---
# 웹 화면은 Python 서버 렌더링으로 만들고 이 PC에서만 띄운다

3단계 화면 3개(Discovery Feed, Knowledge Base, System Monitor, S3-08)는 Starlette와 Jinja2 템플릿으로 서버에서 HTML을 만들어 보낸다(사용자 결정 2026-11-08, Plan v3.0 승인 때 FastAPI에서 Starlette로 좁힘). 상태를 바꾸는 요청은 일반 POST 폼으로 보내고 원래 화면으로 돌려보낸다. htmx와 python-multipart는 쓰지 않는다. 지금 코드의 DB 계층(`radar/store/`)을 그대로 쓰고, DB는 서버만 만진다. 그래서 브라우저에 DB 키를 주지 않으며, 모든 표의 "정책 없는 RLS"를 그대로 둔다.

웹 서버는 `127.0.0.1`에만 묶어 이 PC에서만 열린다. 사용자 1명이고 외부에 공개하지 않으므로 로그인을 만들지 않는다. 사용자별 데이터는 `users` 표의 한 행을 쓴다(ERD 가정 8). 인터넷 배포와 로그인은 4단계 전 상시 운영 결정(PRD 열린 질문)에서 함께 다시 본다.

## Considered Options
- Next.js와 Supabase 클라이언트: 화면을 꾸미기 쉽고 배포가 쉽다. 하지만 TypeScript 스택이 하나 더 생기고, 브라우저가 DB를 읽으려면 RLS 정책과 로그인이 필요하다.
- Streamlit: 가장 빨리 만들 수 있지만, 화면 구성의 자유도가 낮고 6단계 시각화로 넓히기 어렵다.

## Consequences
- 라이브러리는 Plan v3.0에서 승인한 starlette 1.10.0, uvicorn 0.54.0, Jinja2 3.1.6(시험용 httpx 0.28.1)과 하위 의존성이다. FastAPI는 API 검증(pydantic)이 서버 렌더링 폼에 필요 없고 패키지가 7개 늘어서 쓰지 않는다.
- 화면은 단정한 문서형으로 만든다(표·목록 중심, 다크 모드, 키보드 조작, 대비 기준을 지킨다. 사용자 결정 2026-11-08).
- 밖에서 볼 수 없으므로, 휴대폰에서 보려면 이 결정을 개정해야 한다.
