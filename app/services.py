import os
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import MAX_IMAGE_BYTES, MAX_IMAGES, UPLOAD_DIR
from .models import (CONDITIONS, MODES, Category, DealRequest, Listing, ListingImage)


class AppError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def to_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------- валидация объявления ----------
def validate_listing(db: Session, data: dict, locked: Listing | None = None):
    """locked — объявление в резерве: цена и режим не меняются."""
    errors, c = {}, {}
    c["title"] = (data.get("title") or "").strip()
    if not 3 <= len(c["title"]) <= 120:
        errors["title"] = "Заголовок — от 3 до 120 символов."
    c["description"] = (data.get("description") or "").strip()
    if not 10 <= len(c["description"]) <= 5000:
        errors["description"] = "Описание — от 10 до 5000 символов."
    c["category_id"] = to_int(data.get("category_id"))
    if not c["category_id"] or not db.get(Category, c["category_id"]):
        errors["category_id"] = "Выберите категорию."
    c["condition"] = data.get("condition")
    if c["condition"] not in CONDITIONS:
        errors["condition"] = "Выберите состояние."

    if locked:
        c["mode"], c["price"] = locked.mode, locked.price
        return errors, c

    c["mode"] = data.get("mode")
    raw = (data.get("price") or "").strip().replace(",", ".").replace(" ", "")
    c["price"] = None
    if c["mode"] not in MODES:
        errors["mode"] = "Выберите режим: продажа, обмен или даром."
    elif c["mode"] == "sale":
        try:
            p = Decimal(raw)
            if not p.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            errors["price"] = "Укажите цену числом, например 450 или 450.50."
        else:
            if p <= 0:
                errors["price"] = "Для продажи цена должна быть больше нуля."
            elif p != p.quantize(Decimal("0.01")):
                errors["price"] = "Не больше двух знаков после запятой."
            elif p >= Decimal("100000000"):
                errors["price"] = "Слишком большая цена."
            else:
                c["price"] = p
    elif c["mode"] == "gift":
        if raw not in ("", "0", "0.0", "0.00"):
            errors["price"] = "Для режима «даром» цена равна нулю."
        c["price"] = Decimal("0")
    else:
        if raw:
            errors["price"] = "Для обмена цена не указывается."
    return errors, c


# ---------- изображения ----------
def sniff_image(b: bytes):
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b[:4] == b"GIF8":
        return "gif"
    return None


def check_images(files, allowed: int):
    files = [f for f in files if f is not None and getattr(f, "filename", "")]
    errors, ok = [], []
    if len(files) > max(allowed, 0):
        errors.append(f"К объявлению можно прикрепить не более {MAX_IMAGES} фото. "
                      f"Сейчас можно добавить ещё {max(allowed, 0)}.")
        return errors, ok
    for f in files:
        data = f.file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            errors.append(f"«{f.filename}»: файл больше 5 МБ.")
            continue
        ext = sniff_image(data)
        if not ext or not (f.content_type or "").startswith("image/"):
            errors.append(f"«{f.filename}»: допустимы только изображения JPEG, PNG, WebP и GIF.")
            continue
        ok.append((data, ext))
    return errors, ok


def store_images(db: Session, listing: Listing, items):
    pos = max([i.position for i in listing.images], default=-1) + 1
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    for data, ext in items:
        name = f"{uuid.uuid4().hex}.{ext}"
        with open(os.path.join(UPLOAD_DIR, name), "wb") as fh:
            fh.write(data)
        db.add(ListingImage(listing_id=listing.id, file=name, position=pos))
        pos += 1


def remove_files(names):
    for n in names:
        try:
            os.remove(os.path.join(UPLOAD_DIR, n))
        except OSError:
            pass


# ---------- переходы статусов ----------
def lock_listing(db: Session, listing_id: int) -> Listing:
    l = db.execute(select(Listing).where(Listing.id == listing_id).with_for_update()
                   .execution_options(populate_existing=True)).scalar_one_or_none()
    if not l:
        raise AppError(404, "Объявление не найдено.")
    return l


def fresh_request(db: Session, req_id: int) -> DealRequest:
    r = db.execute(select(DealRequest).where(DealRequest.id == req_id)
                   .execution_options(populate_existing=True)).scalar_one_or_none()
    if not r:
        raise AppError(404, "Заявка не найдена.")
    return r


def commit(db: Session, conflict_msg="Состояние уже изменилось. Обновите страницу и попробуйте снова."):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, conflict_msg)


def accept_request(db: Session, req_id: int, user):
    listing_id = fresh_request(db, req_id).listing_id
    listing = lock_listing(db, listing_id)           # блокировка строки: выбор получателя атомарен
    req = fresh_request(db, req_id)
    if listing.seller_id != user.id:
        raise AppError(403, "Выбирать получателя может только автор объявления.")
    if listing.status != "published":
        raise AppError(409, "Объявление уже зарезервировано или недоступно — выбрать получателя нельзя.")
    if req.status != "pending":
        raise AppError(409, "Эта заявка уже обработана.")
    req.status, listing.status = "accepted", "reserved"
    commit(db, "У этого объявления уже есть принятая заявка.")


def release_listing(db: Session, listing_id: int, user):
    l = lock_listing(db, listing_id)
    if l.seller_id != user.id:
        raise AppError(403, "Снять резерв может только автор объявления.")
    if l.status != "reserved":
        raise AppError(409, "Резерв можно снять только у зарезервированного объявления.")
    for r in l.requests:
        if r.status == "accepted":
            r.status = "rejected"
    l.status = "published"
    commit(db)


def complete_listing(db: Session, listing_id: int, user):
    l = lock_listing(db, listing_id)
    if l.seller_id != user.id:
        raise AppError(403, "Отметить передачу может только автор объявления.")
    if l.status != "reserved":
        raise AppError(409, "Передать можно только зарезервированную вещь: сначала выберите получателя.")
    for r in l.requests:
        r.status = "completed" if r.status == "accepted" else ("rejected" if r.status == "pending" else r.status)
    l.status = "completed"
    commit(db)


def archive_listing(db: Session, listing_id: int, user):
    l = lock_listing(db, listing_id)
    if l.seller_id != user.id:
        raise AppError(403, "Это не ваше объявление.")
    if l.status != "published":
        raise AppError(409, "В архив можно отправить только опубликованное объявление. "
                            "Если вещь в резерве — сначала снимите резерв.")
    for r in l.requests:
        if r.status == "pending":
            r.status = "rejected"
    l.status = "archived"
    commit(db)


def restore_listing(db: Session, listing_id: int, user):
    l = lock_listing(db, listing_id)
    if l.seller_id != user.id:
        raise AppError(403, "Это не ваше объявление.")
    if l.status != "archived":
        raise AppError(409, "Вернуть в публикацию можно только архивное объявление.")
    l.status = "published"
    commit(db)


def cancel_request(db: Session, req_id: int, user):
    listing_id = fresh_request(db, req_id).listing_id
    l = lock_listing(db, listing_id)
    r = fresh_request(db, req_id)
    if r.buyer_id != user.id:
        raise AppError(403, "Это не ваша заявка.")
    if r.status not in ("pending", "accepted"):
        raise AppError(409, "Эту заявку уже нельзя отменить.")
    if r.status == "accepted" and l.status == "reserved":
        l.status = "published"
    r.status = "cancelled"
    commit(db)


def reject_request(db: Session, req_id: int, user):
    listing_id = fresh_request(db, req_id).listing_id
    l = lock_listing(db, listing_id)
    r = fresh_request(db, req_id)
    if l.seller_id != user.id:
        raise AppError(403, "Это заявка не на ваше объявление.")
    if r.status != "pending":
        raise AppError(409, "Отклонить можно только заявку на рассмотрении.")
    r.status = "rejected"
    commit(db)
