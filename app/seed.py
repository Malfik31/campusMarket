"""Демоданные: python -m app.seed  (5 пользователей, 5 категорий, 20 объявлений, 10 заявок)."""
import os
import struct
import zlib
from decimal import Decimal

from sqlalchemy import select

from .config import UPLOAD_DIR
from .db import SessionLocal
from .models import (Category, DealRequest, Favorite, Listing, ListingImage, Message, Report, User)
from .security import hash_password

COLORS = [(31, 58, 104), (255, 216, 77), (95, 160, 130), (200, 90, 80), (120, 110, 190)]


def make_png(color, w=320, h=240):
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# (заголовок, категория, состояние, режим, цена, продавец 2..4, статус)
LISTINGS = [
    ("Физика. Сивухин, том 1", 0, "good", "sale", 450, 2, "published"),
    ("Высшая математика, конспекты", 0, "like_new", "gift", 0, 3, "published"),
    ("Учебник по программированию на Python", 0, "new", "sale", 700, 4, "published"),
    ("Органическая химия, Артеменко", 0, "fair", "exchange", None, 2, "published"),
    ("Ноутбук Lenovo ThinkPad T480", 1, "good", "sale", 28000, 3, "published"),
    ("Механическая клавиатура", 1, "like_new", "sale", 2500, 4, "published"),
    ("Наушники проводные", 1, "fair", "gift", 0, 2, "published"),
    ("Калькулятор Casio fx-991", 1, "good", "exchange", None, 3, "published"),
    ("Зимняя куртка M", 2, "good", "sale", 1800, 4, "published"),
    ("Кроссовки 42 размер", 2, "like_new", "exchange", None, 2, "published"),
    ("Рюкзак для ноутбука", 2, "good", "gift", 0, 3, "published"),
    ("Письменный стол", 3, "fair", "sale", 3000, 4, "published"),
    ("Настольная лампа", 3, "good", "gift", 0, 2, "published"),
    ("Офисное кресло", 3, "good", "sale", 2200, 3, "reserved"),
    ("Гантели 2 × 5 кг", 4, "good", "sale", 900, 4, "reserved"),
    ("Футбольный мяч", 4, "fair", "exchange", None, 2, "published"),
    ("Беговые лыжи с палками", 4, "good", "sale", 4000, 3, "completed"),
    ("Старый монитор 19 дюймов", 1, "poor", "gift", 0, 4, "archived"),
    ("Сомнительная реклама курсов", 0, "new", "sale", 100, 2, "hidden"),
    ("Скакалка и коврик для йоги", 4, "like_new", "sale", 600, 3, "published"),
]


def run():
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)):
            print("В базе уже есть данные — наполнение пропущено.")
            return
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        files = []
        for i, c in enumerate(COLORS):
            name = f"seed_{i}.png"
            with open(os.path.join(UPLOAD_DIR, name), "wb") as fh:
                fh.write(make_png(c))
            files.append(name)

        admin_email = os.getenv("ADMIN_EMAIL", "admin@campus.test")
        admin_pw = os.getenv("ADMIN_PASSWORD", "admin12345")
        users = [User(email=admin_email, display_name="Администратор", role="admin", password_hash=hash_password(admin_pw)),
                 User(email="mod@campus.test", display_name="Модератор Марат", role="moderator", password_hash=hash_password("password123")),
                 User(email="anna@campus.test", display_name="Анна", password_hash=hash_password("password123")),
                 User(email="boris@campus.test", display_name="Борис", password_hash=hash_password("password123")),
                 User(email="chingiz@campus.test", display_name="Чингиз", password_hash=hash_password("password123"))]
        cats = [Category(name=n) for n in ("Учебники", "Техника", "Одежда и обувь", "Мебель", "Спорт")]
        db.add_all(users + cats)
        db.flush()

        listings = []
        for i, (title, ci, cond, mode, price, seller, status) in enumerate(LISTINGS):
            l = Listing(seller_id=users[seller].id, category_id=cats[ci].id, title=title, condition=cond, mode=mode,
                        price=None if price is None else Decimal(price), status=status,
                        description=f"{title}. Состояние: см. фото. Забрать можно в кампусе после занятий, цену обсудим в переписке.",
                        hide_reason="Реклама сторонних услуг" if status == "hidden" else None)
            db.add(l)
            db.flush()
            db.add(ListingImage(listing_id=l.id, file=files[i % len(files)], position=0))
            listings.append(l)

        def buyer_of(l):
            idx = [u.id for u in users].index(l.seller_id)
            return users[(idx - 2 + 1) % 3 + 2]

        reqs = []
        # 3 принятых (2 резерва + 1 завершённая передача) и 7 ожидающих = 10 заявок
        for i, st in ((13, "accepted"), (14, "accepted"), (16, "completed")):
            reqs.append(DealRequest(listing_id=listings[i].id, buyer_id=buyer_of(listings[i]).id,
                                    text="Здравствуйте! Хочу забрать, могу сегодня после пар.", status=st))
        for i in (0, 2, 4, 5, 8, 11, 19):
            reqs.append(DealRequest(listing_id=listings[i].id, buyer_id=buyer_of(listings[i]).id,
                                    text="Привет! Ещё актуально? Могу встретиться в главном корпусе.", status="pending"))
        db.add_all(reqs)
        db.flush()
        for r in reqs[:3]:
            db.add(Message(request_id=r.id, sender_id=db.get(Listing, r.listing_id).seller_id,
                           text="Договорились, жду вас у входа в корпус в 15:00."))
        db.add_all([Favorite(user_id=users[2].id, listing_id=listings[4].id), Favorite(user_id=users[3].id, listing_id=listings[0].id),
                    Favorite(user_id=users[4].id, listing_id=listings[11].id)])
        db.add(Report(listing_id=listings[18].id, reporter_id=users[3].id, reason="Похоже на рекламу, а не на вещь.",
                      status="resolved", resolution="Объявление скрыто: Реклама сторонних услуг", resolved_by_id=users[1].id))
        db.add(Report(listing_id=listings[5].id, reporter_id=users[2].id, reason="Цена выглядит завышенной, проверьте, пожалуйста."))
        db.commit()
        print("Готово. Аккаунты (пароль password123, у админа — из ADMIN_PASSWORD):")
        for u in users:
            print(f"  {u.role:<10} {u.email}")


if __name__ == "__main__":
    run()
