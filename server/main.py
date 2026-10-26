"""PatPat 서버: COCO(coco-ai-camera)의 FastAPI 백엔드를 바탕으로 방·스토리·스레드를 얹은 버전.

AR 아바타, AI 스티커, 타임 필터는 뺐다. 로그인은 닉네임 + 비밀번호로 받고,
이후 요청은 Authorization: Bearer <토큰> 으로 누구인지 확인한다.
"""
import hashlib
import hmac
import math
import os
import secrets
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import User, AuthToken, Connection, Room, RoomMember, Story, Reply, Reaction

ROOT = Path(__file__).resolve().parent.parent  # index.html, assets/ 가 있는 저장소 루트
STORY_TTL = timedelta(hours=24)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="PatPat", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AuthRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=6, max_length=128)

class UserOut(BaseModel):
    id: str
    nickname: str
    color: str
    bio: str

class AuthOut(BaseModel):
    token: str
    user: UserOut

class UpdateMe(BaseModel):
    color: str | None = Field(default=None, max_length=9)
    bio: str | None = Field(default=None, max_length=100)

class ConnectionRequest(BaseModel):
    target_id: str

class ConnectionOut(BaseModel):
    id: str
    requester: UserOut
    target: UserOut
    status: str

class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    member_ids: list[str] = []

class DmCreate(BaseModel):
    user_id: str

class RoomOut(BaseModel):
    id: str
    name: str
    is_dm: bool
    members: list[UserOut]

class StoryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    photo_data: str | None = None
    color: str | None = Field(default=None, max_length=9)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    place: str | None = Field(default=None, max_length=30)
    show_distance: bool = False
    archivable: bool = True

class ReplyOut(BaseModel):
    id: str
    user: UserOut
    text: str
    reactions: dict[str, int]
    my_reaction: str | None
    created_at: str

class StoryOut(BaseModel):
    id: str
    room_id: str
    user: UserOut
    title: str
    photo_data: str | None
    color: str | None
    place: str | None
    approx_lat: float | None   # 동네 수준으로 흐린 좌표 (지도 핀용)
    approx_lng: float | None
    distance_m: int | None     # 거리 공개를 켰고 내 위치를 보냈을 때만, 100m 단위
    archivable: bool
    kept_by: UserOut | None
    reply_count: int
    last_reply: ReplyOut | None
    created_at: str
    expires_at: str | None

class ReplyCreate(BaseModel):
    text: str = Field(min_length=1, max_length=1000)

class ReactionCreate(BaseModel):
    emoji: str = Field(min_length=1, max_length=16)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now() -> datetime:
    # SQLite는 시간대 정보를 버리므로 UTC 기준 naive datetime으로 통일
    return datetime.now(timezone.utc).replace(tzinfo=None)


def naive(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리(m)"""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def hash_password(password: str, salt: bytes | None = None, iterations: int = 200_000) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    _, iterations, salt, digest = stored.split("$")
    check = hash_password(password, bytes.fromhex(salt), int(iterations)).split("$")[3]
    return hmac.compare_digest(check, digest)


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "로그인이 필요해요")
    row = db.query(AuthToken).filter(AuthToken.token == token).first()
    if not row:
        raise HTTPException(401, "로그인이 만료됐어요. 다시 로그인해 주세요")
    return row.user


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, nickname=u.nickname, color=u.color or "#87CBB9", bio=u.bio or "")


def friend_ids(user_id: str, db: Session) -> set[str]:
    conns = db.query(Connection).filter(
        or_(Connection.requester_id == user_id, Connection.target_id == user_id),
        Connection.status == "accepted",
    ).all()
    return {c.target_id if c.requester_id == user_id else c.requester_id for c in conns}


def room_for_member(room_id: str, user: User, db: Session) -> Room:
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room or not any(m.user_id == user.id for m in room.members):
        raise HTTPException(404, "방을 찾을 수 없어요")
    return room


def story_for_member(story_id: str, user: User, db: Session) -> Story:
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise HTTPException(404, "스토리를 찾을 수 없어요")
    room_for_member(story.room_id, user, db)
    if not is_visible(story):
        raise HTTPException(404, "사라진 스토리예요")
    return story


def is_visible(story: Story) -> bool:
    """24시간이 안 지났거나, 누군가 보관했거나, 1:1 방 스토리면 보임"""
    return story.expires_at is None or naive(story.expires_at) > now() or story.kept_by_id is not None


def room_out(room: Room) -> RoomOut:
    return RoomOut(id=room.id, name=room.name, is_dm=room.is_dm, members=[user_out(m.user) for m in room.members])


def reply_out(r: Reply, me: User) -> ReplyOut:
    counts: dict[str, int] = {}
    mine = None
    for rx in r.reactions:
        counts[rx.emoji] = counts.get(rx.emoji, 0) + 1
        if rx.user_id == me.id:
            mine = rx.emoji
    return ReplyOut(id=r.id, user=user_out(r.user), text=r.text, reactions=counts, my_reaction=mine,
                    created_at=r.created_at.isoformat())


def blur(v: float | None) -> float | None:
    # 약 500m 격자로 반올림
    return None if v is None else round(v / 0.005) * 0.005


def story_out(s: Story, me: User, lat: float | None, lng: float | None) -> StoryOut:
    dist = None
    if s.show_distance and None not in (s.lat, s.lng, lat, lng):
        dist = max(100, round(haversine(lat, lng, s.lat, s.lng) / 100) * 100)
    last = s.replies[-1] if s.replies else None
    return StoryOut(
        id=s.id, room_id=s.room_id, user=user_out(s.user), title=s.title,
        photo_data=s.photo_data, color=s.color, place=s.place,
        approx_lat=blur(s.lat), approx_lng=blur(s.lng), distance_m=dist,
        archivable=s.archivable, kept_by=user_out(s.kept_by) if s.kept_by else None,
        reply_count=len(s.replies), last_reply=reply_out(last, me) if last else None,
        created_at=s.created_at.isoformat(),
        expires_at=s.expires_at.isoformat() if s.expires_at else None,
    )

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def issue_token(user: User, db: Session) -> AuthOut:
    token = secrets.token_urlsafe(32)
    db.add(AuthToken(token=token, user_id=user.id))
    db.commit()
    return AuthOut(token=token, user=user_out(user))


@app.post("/api/auth/register", response_model=AuthOut)
def register(req: AuthRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.nickname == req.nickname).first():
        raise HTTPException(400, "이미 사용 중인 닉네임이에요")
    user = User(nickname=req.nickname, password_hash=hash_password(req.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return issue_token(user, db)


@app.post("/api/auth/login", response_model=AuthOut)
def login(req: AuthRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.nickname == req.nickname).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "닉네임이나 비밀번호가 맞지 않아요")
    return issue_token(user, db)


@app.post("/api/auth/logout")
def logout(authorization: str = Header(default=""), db: Session = Depends(get_db)):
    token = authorization.partition(" ")[2]
    db.query(AuthToken).filter(AuthToken.token == token).delete()
    db.commit()
    return {"ok": True}


@app.get("/api/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user_out(user)


@app.put("/api/me", response_model=UserOut)
def update_me(req: UpdateMe, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if req.color is not None:
        user.color = req.color
    if req.bio is not None:
        user.bio = req.bio
    db.commit()
    return user_out(user)


@app.get("/api/users/search", response_model=list[UserOut])
def search_users(q: str = Query(min_length=1, max_length=50), user: User = Depends(current_user), db: Session = Depends(get_db)):
    found = db.query(User).filter(User.nickname.contains(q), User.id != user.id).limit(20).all()
    return [user_out(u) for u in found]

# ---------------------------------------------------------------------------
# 친구 (COCO의 connections, 요청한 사람은 토큰으로 확인)
# ---------------------------------------------------------------------------

def connection_out(c: Connection) -> ConnectionOut:
    return ConnectionOut(id=c.id, requester=user_out(c.requester), target=user_out(c.target), status=c.status)


@app.post("/api/connections/request", response_model=ConnectionOut)
def request_connection(req: ConnectionRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if req.target_id == user.id or not db.query(User).filter(User.id == req.target_id).first():
        raise HTTPException(400, "친구 요청을 보낼 수 없는 사람이에요")
    existing = db.query(Connection).filter(
        or_(
            (Connection.requester_id == user.id) & (Connection.target_id == req.target_id),
            (Connection.requester_id == req.target_id) & (Connection.target_id == user.id),
        )
    ).first()
    if existing:
        return connection_out(existing)
    conn = Connection(requester_id=user.id, target_id=req.target_id)
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return connection_out(conn)


def _answer(conn_id: str, status: str, user: User, db: Session) -> ConnectionOut:
    conn = db.query(Connection).filter(Connection.id == conn_id, Connection.target_id == user.id).first()
    if not conn:
        raise HTTPException(404, "받은 친구 요청이 아니에요")
    conn.status = status
    db.commit()
    return connection_out(conn)


@app.put("/api/connections/{conn_id}/accept", response_model=ConnectionOut)
def accept_connection(conn_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _answer(conn_id, "accepted", user, db)


@app.put("/api/connections/{conn_id}/reject", response_model=ConnectionOut)
def reject_connection(conn_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _answer(conn_id, "rejected", user, db)


@app.get("/api/connections", response_model=list[ConnectionOut])
def list_connections(user: User = Depends(current_user), db: Session = Depends(get_db)):
    conns = db.query(Connection).filter(
        or_(Connection.requester_id == user.id, Connection.target_id == user.id)
    ).order_by(Connection.created_at.desc()).all()
    return [connection_out(c) for c in conns]

# ---------------------------------------------------------------------------
# 방
# ---------------------------------------------------------------------------

@app.get("/api/rooms", response_model=list[RoomOut])
def list_rooms(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rooms = db.query(Room).join(RoomMember).filter(RoomMember.user_id == user.id).order_by(Room.created_at).all()
    return [room_out(r) for r in rooms]


@app.post("/api/rooms", response_model=RoomOut)
def create_room(req: RoomCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # 친구만 방에 넣을 수 있음
    friends = friend_ids(user.id, db)
    if any(mid not in friends for mid in req.member_ids if mid != user.id):
        raise HTTPException(400, "친구만 방에 초대할 수 있어요")
    room = Room(name=req.name, created_by=user.id)
    room.members = [RoomMember(user_id=uid) for uid in {user.id, *req.member_ids}]
    db.add(room)
    db.commit()
    db.refresh(room)
    return room_out(room)


@app.post("/api/rooms/dm", response_model=RoomOut)
def open_dm(req: DmCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if req.user_id not in friend_ids(user.id, db):
        raise HTTPException(400, "친구와만 1:1 대화를 할 수 있어요")
    for room in db.query(Room).join(RoomMember).filter(Room.is_dm == True, RoomMember.user_id == user.id).all():
        if {m.user_id for m in room.members} == {user.id, req.user_id}:
            return room_out(room)
    other = db.query(User).filter(User.id == req.user_id).first()
    room = Room(name=other.nickname, is_dm=True, created_by=user.id)
    room.members = [RoomMember(user_id=user.id), RoomMember(user_id=req.user_id)]
    db.add(room)
    db.commit()
    db.refresh(room)
    return room_out(room)

# ---------------------------------------------------------------------------
# 스토리 · 보관
# ---------------------------------------------------------------------------

@app.get("/api/rooms/{room_id}/stories", response_model=list[StoryOut])
def list_stories(
    room_id: str,
    lat: float | None = Query(default=None),
    lng: float | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    room_for_member(room_id, user, db)
    stories = db.query(Story).filter(Story.room_id == room_id).order_by(Story.created_at.desc()).all()
    return [story_out(s, user, lat, lng) for s in stories if is_visible(s)]


@app.post("/api/rooms/{room_id}/stories", response_model=StoryOut)
def create_story(room_id: str, req: StoryCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    room = room_for_member(room_id, user, db)
    story = Story(
        room_id=room.id, user_id=user.id, title=req.title, photo_data=req.photo_data, color=req.color,
        lat=req.lat, lng=req.lng, place=req.place,
        show_distance=req.show_distance and req.lat is not None,
        archivable=req.archivable,
        created_at=now(),
        expires_at=None if room.is_dm else now() + STORY_TTL,
    )
    db.add(story)
    db.commit()
    db.refresh(story)
    return story_out(story, user, None, None)


@app.post("/api/stories/{story_id}/keep", response_model=StoryOut)
def toggle_keep(story_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """공유 보관: 누가 보관하든 방 전체에 남음. 해제는 보관한 사람만"""
    story = story_for_member(story_id, user, db)
    if not story.archivable:
        raise HTTPException(400, "올린 사람이 보관을 꺼 뒀어요")
    if story.kept_by_id is None:
        story.kept_by_id = user.id
    elif story.kept_by_id == user.id:
        story.kept_by_id = None
    else:
        raise HTTPException(403, "보관한 사람만 해제할 수 있어요")
    db.commit()
    db.refresh(story)
    return story_out(story, user, None, None)


@app.delete("/api/stories/{story_id}")
def delete_story(story_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    story = story_for_member(story_id, user, db)
    if story.user_id != user.id:
        raise HTTPException(403, "내가 올린 스토리만 지울 수 있어요")
    db.delete(story)
    db.commit()
    return {"ok": True}

# ---------------------------------------------------------------------------
# 스레드 답장 · 반응
# ---------------------------------------------------------------------------

@app.get("/api/stories/{story_id}/replies", response_model=list[ReplyOut])
def list_replies(story_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    story = story_for_member(story_id, user, db)
    return [reply_out(r, user) for r in story.replies]


@app.post("/api/stories/{story_id}/replies", response_model=ReplyOut)
def create_reply(story_id: str, req: ReplyCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    story = story_for_member(story_id, user, db)
    reply = Reply(story_id=story.id, user_id=user.id, text=req.text.strip(), created_at=now())
    db.add(reply)
    db.commit()
    db.refresh(reply)
    return reply_out(reply, user)


@app.post("/api/replies/{reply_id}/react", response_model=ReplyOut)
def react(reply_id: str, req: ReactionCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """같은 반응을 다시 누르면 취소, 다른 반응을 누르면 바꿈"""
    reply = db.query(Reply).filter(Reply.id == reply_id).first()
    if not reply:
        raise HTTPException(404, "답장을 찾을 수 없어요")
    story_for_member(reply.story_id, user, db)
    mine = db.query(Reaction).filter(Reaction.reply_id == reply_id, Reaction.user_id == user.id).first()
    if mine and mine.emoji == req.emoji:
        db.delete(mine)
    elif mine:
        mine.emoji = req.emoji
    else:
        db.add(Reaction(reply_id=reply_id, user_id=user.id, emoji=req.emoji))
    db.commit()
    db.refresh(reply)
    return reply_out(reply, user)

# ---------------------------------------------------------------------------
# 화면 (저장소 루트의 index.html 과 assets/)
# ---------------------------------------------------------------------------

app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
