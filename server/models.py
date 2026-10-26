import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Float, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship

from database import Base


def generate_uuid():
    return str(uuid.uuid4())


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_uuid)
    nickname = Column(String(50), nullable=False, unique=True)
    password_hash = Column(String, nullable=False)  # pbkdf2$<iterations>$<salt>$<hash>
    color = Column(String(9), default="#87CBB9")    # 아바타 색
    bio = Column(String(100), default="")
    created_at = Column(DateTime, default=utcnow)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    token = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User")


class Connection(Base):
    __tablename__ = "connections"

    id = Column(String, primary_key=True, default=generate_uuid)
    requester_id = Column(String, ForeignKey("users.id"), nullable=False)
    target_id = Column(String, ForeignKey("users.id"), nullable=False)
    status = Column(String(20), default="pending")  # pending/accepted/rejected
    created_at = Column(DateTime, default=utcnow)

    requester = relationship("User", foreign_keys=[requester_id])
    target = relationship("User", foreign_keys=[target_id])


class Room(Base):
    """그룹 방 또는 1:1 방. 1:1 방의 스토리는 사라지지 않고 쌓임"""
    __tablename__ = "rooms"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String(50), nullable=False)
    is_dm = Column(Boolean, default=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    members = relationship("RoomMember", back_populates="room", cascade="all, delete-orphan")


class RoomMember(Base):
    __tablename__ = "room_members"

    room_id = Column(String, ForeignKey("rooms.id"), primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), primary_key=True)
    joined_at = Column(DateTime, default=utcnow)

    room = relationship("Room", back_populates="members")
    user = relationship("User")


class Story(Base):
    """사진(또는 글) 하나와 그에 달린 스레드. COCO의 Trace에 해당"""
    __tablename__ = "stories"

    id = Column(String, primary_key=True, default=generate_uuid)
    room_id = Column(String, ForeignKey("rooms.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    title = Column(Text, nullable=False)
    photo_data = Column(Text, nullable=True)
    color = Column(String(9), nullable=True)    # 사진 없는 글 스토리의 배경색
    # 정확한 좌표는 서버에만 두고 응답에는 동네 이름과 흐린 좌표만 내보냄
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    place = Column(String(30), nullable=True)
    show_distance = Column(Boolean, default=False)  # 올린 사람이 거리 공개를 켰을 때만
    archivable = Column(Boolean, default=True)      # 올린 사람이 보관 허용을 껐으면 아무도 보관 못 함
    kept_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=True)    # 1:1 방이면 비어 있음

    user = relationship("User", foreign_keys=[user_id])
    kept_by = relationship("User", foreign_keys=[kept_by_id])
    replies = relationship("Reply", back_populates="story", cascade="all, delete-orphan", order_by="Reply.created_at")


class Reply(Base):
    __tablename__ = "replies"

    id = Column(String, primary_key=True, default=generate_uuid)
    story_id = Column(String, ForeignKey("stories.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    story = relationship("Story", back_populates="replies")
    user = relationship("User")
    reactions = relationship("Reaction", cascade="all, delete-orphan")


class Reaction(Base):
    """답장 하나에 사람마다 반응 하나"""
    __tablename__ = "reactions"

    reply_id = Column(String, ForeignKey("replies.id"), primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), primary_key=True)
    emoji = Column(String(16), nullable=False)
