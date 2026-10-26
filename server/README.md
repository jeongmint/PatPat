# PatPat 서버

[COCO(coco-ai-camera)](https://github.com/jeongmint/coco-ai-camera)의 FastAPI 백엔드를 바탕으로 만든 PatPat 서버예요.
AR 아바타, AI 스티커, 타임 필터는 빼고 방·스토리·스레드·보관을 얹었어요.

## 실행

```bash
cd server
pip install -r requirements.txt
python main.py          # http://localhost:8000
python -m pytest -q     # 테스트
```

DB는 기본으로 `server/patpat.db`(SQLite)에 저장돼요. 다른 곳을 쓰려면 `PATPAT_DB`를 지정하세요.

## 주요 API

모든 API는 로그인해서 받은 토큰을 `Authorization: Bearer <토큰>`으로 보내야 해요.

| 영역 | 엔드포인트 |
|------|-----------|
| 로그인 | `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET/PUT /api/me` |
| 친구 | `GET /api/users/search?q=`, `POST /api/connections/request`, `PUT /api/connections/{id}/accept\|reject`, `GET /api/connections` |
| 방 | `GET /api/rooms`, `POST /api/rooms` (친구만 초대), `POST /api/rooms/dm` |
| 스토리 | `GET/POST /api/rooms/{id}/stories`, `POST /api/stories/{id}/keep`, `DELETE /api/stories/{id}` |
| 답장 | `GET/POST /api/stories/{id}/replies`, `POST /api/replies/{id}/react` |

## 위치와 보관 규칙

- 정확한 좌표는 서버에만 저장하고, 응답에는 동네 이름과 약 500m 단위로 흐린 좌표만 내보내요.
- 거리(`distance_m`)는 올린 사람이 거리 공개를 켰고 보는 사람이 자기 위치를 보냈을 때만, 100m 단위로 내보내요.
- 그룹 방 스토리는 24시간 뒤 사라져요. 누군가 보관하면 남고, 해제는 보관한 사람만 할 수 있어요. 올린 사람이 보관을 끄면 아무도 보관할 수 없어요.
- 1:1 방 스토리는 사라지지 않아요.

## 화면 연결

서버로 열면(`http://localhost:8000`) 화면이 `/api/me`로 서버를 확인하고 로그인 화면을 띄워요.
로그인하면 방·스토리·답장·반응·보관·글쓰기가 모두 이 API로 저장돼요.
서버 없이 `index.html`만 열면 예전처럼 예시 데이터로 동작해요.

- 친구 찾기·요청·수락, 방 만들기, 1:1 시작은 오른쪽 위 사람 아이콘에서 해요.
- 새 답장은 열려 있는 스레드에서 4초, 피드에서 15초마다 확인해요. 실시간 연결(WebSocket)은 다음 단계예요.
- 지도 상태, 같이 듣기, 내 홈(미니룸·방명록), 뮤직캠은 아직 서버에 저장하지 않아요.
- 거리는 지도에서 내 동네 상태를 남긴 뒤부터 계산돼요.
