import os
import tempfile
from datetime import timedelta

os.environ["PATPAT_DB"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")

import pytest
from fastapi.testclient import TestClient

import main
from database import SessionLocal
from models import Story

HONGDAE = (37.5571, 126.9245)
HAPJEONG = (37.5496, 126.9139)


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def signup(client, name):
    r = client.post("/api/auth/register", json={"nickname": name, "password": "secret123"})
    assert r.status_code == 200, r.text
    data = r.json()
    return {"Authorization": f"Bearer {data['token']}"}, data["user"]


def befriend(client, a, b_user, b):
    conn = client.post("/api/connections/request", json={"target_id": b_user["id"]}, headers=a).json()
    assert client.put(f"/api/connections/{conn['id']}/accept", headers=b).status_code == 200


@pytest.fixture(scope="module")
def group(client):
    me, me_u = signup(client, "나")
    jiwoo, jiwoo_u = signup(client, "지우")
    stranger, _ = signup(client, "모르는사람")
    befriend(client, me, jiwoo_u, jiwoo)
    room = client.post("/api/rooms", json={"name": "우리 넷", "member_ids": [jiwoo_u["id"]]}, headers=me).json()
    return dict(me=me, me_u=me_u, jiwoo=jiwoo, jiwoo_u=jiwoo_u, stranger=stranger, room=room)


def test_login_requires_password(client):
    signup(client, "로그인테스트")
    bad = client.post("/api/auth/login", json={"nickname": "로그인테스트", "password": "wrongpass"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"nickname": "로그인테스트", "password": "secret123"})
    assert ok.status_code == 200 and ok.json()["token"]


def test_requests_without_token_are_rejected(client, group):
    assert client.get("/api/rooms").status_code == 401
    assert client.get("/api/rooms", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_only_members_see_room_stories(client, group):
    url = f"/api/rooms/{group['room']['id']}/stories"
    assert client.get(url, headers=group["jiwoo"]).status_code == 200
    assert client.get(url, headers=group["stranger"]).status_code == 404


def test_cannot_invite_non_friends(client, group):
    stranger_id = client.get("/api/me", headers=group["stranger"]).json()["id"]
    r = client.post("/api/rooms", json={"name": "x", "member_ids": [stranger_id]}, headers=group["me"])
    assert r.status_code == 400


def test_story_hides_exact_location_and_distance_is_opt_in(client, group):
    url = f"/api/rooms/{group['room']['id']}/stories"
    hidden = client.post(url, json={"title": "비공개 거리", "lat": HAPJEONG[0], "lng": HAPJEONG[1], "place": "합정"},
                         headers=group["jiwoo"]).json()
    shown = client.post(url, json={"title": "공개 거리", "lat": HAPJEONG[0], "lng": HAPJEONG[1], "place": "합정",
                                   "show_distance": True}, headers=group["jiwoo"]).json()
    stories = {s["id"]: s for s in client.get(url, params={"lat": HONGDAE[0], "lng": HONGDAE[1]}, headers=group["me"]).json()}
    assert "lat" not in stories[shown["id"]]
    assert stories[shown["id"]]["approx_lat"] != HAPJEONG[0]
    assert stories[hidden["id"]]["distance_m"] is None
    assert stories[shown["id"]]["distance_m"] == 1300


def test_replies_and_reactions(client, group):
    url = f"/api/rooms/{group['room']['id']}/stories"
    story = client.post(url, json={"title": "퇴근길 하늘"}, headers=group["jiwoo"]).json()
    reply = client.post(f"/api/stories/{story['id']}/replies", json={"text": "어디야?"}, headers=group["me"]).json()
    r = client.post(f"/api/replies/{reply['id']}/react", json={"emoji": "😮"}, headers=group["jiwoo"]).json()
    assert r["reactions"] == {"😮": 1}
    r = client.post(f"/api/replies/{reply['id']}/react", json={"emoji": "😮"}, headers=group["jiwoo"]).json()
    assert r["reactions"] == {}
    assert client.post(f"/api/stories/{story['id']}/replies", json={"text": "x"}, headers=group["stranger"]).status_code == 404


def expire(story_id):
    db = SessionLocal()
    s = db.get(Story, story_id)
    s.expires_at = main.now() - timedelta(minutes=1)
    db.commit()
    db.close()


def test_stories_expire_unless_kept(client, group):
    url = f"/api/rooms/{group['room']['id']}/stories"
    gone = client.post(url, json={"title": "사라질 것"}, headers=group["jiwoo"]).json()
    kept = client.post(url, json={"title": "남길 것"}, headers=group["jiwoo"]).json()
    assert client.post(f"/api/stories/{kept['id']}/keep", headers=group["me"]).json()["kept_by"]["nickname"] == "나"
    # 보관한 사람만 해제할 수 있음
    assert client.post(f"/api/stories/{kept['id']}/keep", headers=group["jiwoo"]).status_code == 403
    expire(gone["id"])
    expire(kept["id"])
    ids = {s["id"] for s in client.get(url, headers=group["me"]).json()}
    assert kept["id"] in ids and gone["id"] not in ids


def test_poster_can_disable_keeping(client, group):
    url = f"/api/rooms/{group['room']['id']}/stories"
    s = client.post(url, json={"title": "보관 금지", "archivable": False}, headers=group["jiwoo"]).json()
    assert client.post(f"/api/stories/{s['id']}/keep", headers=group["me"]).status_code == 400


def test_dm_stories_do_not_expire(client, group):
    dm = client.post("/api/rooms/dm", json={"user_id": group["jiwoo_u"]["id"]}, headers=group["me"]).json()
    assert dm["is_dm"]
    again = client.post("/api/rooms/dm", json={"user_id": group["me_u"]["id"]}, headers=group["jiwoo"]).json()
    assert again["id"] == dm["id"]
    s = client.post(f"/api/rooms/{dm['id']}/stories", json={"title": "저녁 뭐 먹지"}, headers=group["me"]).json()
    assert s["expires_at"] is None
