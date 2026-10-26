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
