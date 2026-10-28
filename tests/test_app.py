import os

os.environ["DATABASE_URL"] = "sqlite:///./test_campus.db"
os.environ["UPLOAD_DIR"] = "test_uploads"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Category, DealRequest, Listing, Report, User  # noqa: E402
from app.security import hash_password  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
PW = hash_password("password123")


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(Category(name="Учебники"))
        for email, role in [("seller@t.t", "member"), ("buyer@t.t", "member"), ("other@t.t", "member"), ("mod@t.t", "moderator")]:
            db.add(User(email=email, display_name=email.split("@")[0], password_hash=PW, role=role))
        db.commit()


def login(email):
    c = TestClient(app, follow_redirects=False)
    assert c.post("/login", data={"email": email, "password": "password123"}).status_code == 303
    return c


def make_listing(c, **over):
    data = {"title": "Учебник по физике", "description": "Хорошее состояние, без пометок",
            "category_id": "1", "condition": "good", "mode": "sale", "price": "500"}
    data.update(over)
    return c.post("/listings", data=data)


def lid(resp):
    return int(resp.headers["location"].rstrip("/").split("/")[-1])


def request_for(c, listing_id, text="Беру!"):
    r = c.post(f"/listings/{listing_id}/requests", data={"text": text})
    assert r.status_code == 303
    return int(r.headers["location"].split("/")[2])


def test_main_scenario_publish_find_request_accept_complete():
    seller, buyer = login("seller@t.t"), login("buyer@t.t")
    r = make_listing(seller, title="Квантовая механика")
    assert r.status_code == 303
    i = lid(r)
    assert "Квантовая механика" in buyer.get("/listings?q=Квантовая&mode=sale&price_max=600").text
    rid = request_for(buyer, i)
    assert seller.post(f"/requests/{rid}/accept").status_code == 303
    with SessionLocal() as db:
        assert db.get(Listing, i).status == "reserved"
    assert seller.post(f"/listings/{i}/complete").status_code == 303
    with SessionLocal() as db:
        assert db.get(Listing, i).status == "completed"
        assert db.get(DealRequest, rid).status == "completed"


def test_validation_negative_price_and_sixth_photo():
    seller = login("seller@t.t")
    r = make_listing(seller, price="-5")
    assert r.status_code == 400 and "больше нуля" in r.text
    assert make_listing(seller, mode="gift", price="10").status_code == 400
    assert make_listing(seller, mode="exchange", price="10").status_code == 400
    files = [("images", (f"{n}.png", PNG, "image/png")) for n in range(6)]
    data = {"title": "Книга", "description": "Описание книги тут", "category_id": "1", "condition": "good", "mode": "sale", "price": "10"}
    r = seller.post("/listings", data=data, files=files)
    assert r.status_code == 400 and "не более 5" in r.text
    r = seller.post("/listings", data=data, files=[("images", ("a.exe", b"MZ\x90\x00", "application/octet-stream"))])
    assert r.status_code == 400 and "допустимы только изображения" in r.text
    ok = seller.post("/listings", data=data, files=files[:5])
    assert ok.status_code == 303


def test_permissions_foreign_thread_and_edit():
    seller, buyer, other = login("seller@t.t"), login("buyer@t.t"), login("other@t.t")
    i = lid(make_listing(seller))
    rid = request_for(buyer, i)
    assert other.get(f"/requests/{rid}/messages").status_code == 403
    assert other.post(f"/requests/{rid}/messages", data={"text": "hi"}).status_code == 403
    assert other.post(f"/listings/{i}/edit", data={"title": "x"}).status_code == 403
    assert buyer.post(f"/requests/{rid}/accept").status_code == 403
    assert buyer.post(f"/requests/{rid}/messages", data={"text": "Здравствуйте"}).status_code == 303
    guest = TestClient(app, follow_redirects=False)
    assert guest.get("/my/listings").status_code == 303  # редирект на вход
    assert buyer.get("/moderation").status_code == 403


def test_forbidden_transitions_and_own_listing():
    seller, buyer = login("seller@t.t"), login("buyer@t.t")
    i = lid(make_listing(seller))
    assert seller.post(f"/listings/{i}/complete").status_code == 409      # нельзя передать без резерва
    assert seller.post(f"/listings/{i}/release").status_code == 409
    assert seller.post(f"/listings/{i}/requests", data={"text": "сам себе"}).status_code == 403
    rid = request_for(buyer, i)
    assert buyer.post(f"/listings/{i}/requests", data={"text": "ещё раз"}).status_code == 409
    seller.post(f"/requests/{rid}/accept")
    assert seller.post(f"/listings/{i}/archive").status_code == 409        # в резерве нельзя в архив
    r = seller.post(f"/listings/{i}/edit", data={"title": "Новое имя", "description": "Описание хорошее",
                                                  "category_id": "1", "condition": "good", "mode": "sale", "price": "999"})
    assert r.status_code == 409                                            # цену в резерве менять нельзя


def test_second_accept_conflicts_and_db_guard():
    seller, b1, b2 = login("seller@t.t"), login("buyer@t.t"), login("other@t.t")
    i = lid(make_listing(seller))
    r1, r2 = request_for(b1, i), request_for(b2, i)
    assert seller.post(f"/requests/{r1}/accept").status_code == 303
    assert seller.post(f"/requests/{r2}/accept").status_code == 409
    with SessionLocal() as db:
        accepted = db.scalars(select(DealRequest).where(DealRequest.listing_id == i, DealRequest.status == "accepted")).all()
        assert len(accepted) == 1
        db.get(DealRequest, r2).status = "accepted"         # даже в обход сервиса БД не даст второй резерв
        with pytest.raises(IntegrityError):
            db.commit()


def test_hidden_listing_disappears_from_public_catalog():
    seller, buyer, mod = login("seller@t.t"), login("buyer@t.t"), login("mod@t.t")
    guest = TestClient(app, follow_redirects=False)
    i = lid(make_listing(seller, title="Скрываемая вещь"))
    assert "Скрываемая вещь" in guest.get("/listings").text
    assert buyer.post("/reports", data={"listing_id": i, "reason": "Подозрительное объявление"}).status_code == 303
    with SessionLocal() as db:
        rep_id = db.scalar(select(Report.id))
    assert mod.post(f"/moderation/reports/{rep_id}/hide", data={"reason": "Нарушает правила"}).status_code == 303
    assert "Скрываемая вещь" not in guest.get("/listings").text
    assert guest.get(f"/listings/{i}").status_code == 404
    assert seller.get(f"/listings/{i}").status_code == 200
