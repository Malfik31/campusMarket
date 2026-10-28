import math
from decimal import Decimal, InvalidOperation
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import MAX_IMAGES, PER_PAGE
from ..db import get_db
from ..deps import current_user, require_user
from ..models import (CONDITIONS, MODES, PUBLIC_STATUSES, Category, DealRequest, Favorite, Listing, ListingImage, Message, Report)
from .. import services as s
from ..web import flash, render

router = APIRouter()
STAFF = ("moderator", "admin")
SORTS = {"new": [Listing.created_at.desc(), Listing.id.desc()],
         "old": [Listing.created_at.asc(), Listing.id.asc()],
         "price_asc": [Listing.price.asc().nulls_last(), Listing.id.desc()],
         "price_desc": [Listing.price.desc().nulls_last(), Listing.id.desc()]}


def get_listing(db: Session, lid: int) -> Listing:
    l = db.get(Listing, lid)
    if not l:
        raise HTTPException(404, "Объявление не найдено.")
    return l


def can_view(l: Listing, user) -> bool:
    return l.status in PUBLIC_STATUSES or bool(user and (user.id == l.seller_id or user.role in STAFF))


def price_changed(raw: str, l: Listing) -> bool:
    raw = raw.strip().replace(",", ".")
    if not raw:
        return False
    try:
        return Decimal(raw) != (l.price if l.price is not None else Decimal("0"))
    except InvalidOperation:
        return True


def categories(db):
    return db.scalars(select(Category).order_by(Category.name)).all()


@router.get("/")
def home():
    return RedirectResponse("/listings", 307)


@router.get("/listings")
def catalog(request: Request, q: str = "", category: str = "", mode: str = "", condition: str = "",
            price_min: str = "", price_max: str = "", sort: str = "new", page: str = "1",
            db: Session = Depends(get_db), user=Depends(current_user)):
    stmt = select(Listing).where(Listing.status.in_(PUBLIC_STATUSES))
    notes = []
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Listing.title.ilike(like), Listing.description.ilike(like)))
    if s.to_int(category):
        stmt = stmt.where(Listing.category_id == s.to_int(category))
    if mode in MODES:
        stmt = stmt.where(Listing.mode == mode)
    if condition in CONDITIONS:
        stmt = stmt.where(Listing.condition == condition)
    for raw, op, label in ((price_min, "ge", "Цена от"), (price_max, "le", "Цена до")):
        if raw.strip():
            try:
                v = Decimal(raw.replace(",", "."))
                if not v.is_finite():
                    raise InvalidOperation
                stmt = stmt.where(Listing.price >= v if op == "ge" else Listing.price <= v)
            except InvalidOperation:
                notes.append(f"{label}: введите число, фильтр не применён.")
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(max(s.to_int(page, 1), 1), pages)
    items = db.scalars(stmt.order_by(*SORTS.get(sort, SORTS["new"])).limit(PER_PAGE).offset((page - 1) * PER_PAGE)
                       .options(selectinload(Listing.images), selectinload(Listing.category))).all()
    favs = set(db.scalars(select(Favorite.listing_id).where(Favorite.user_id == user.id))) if user else set()
    return render(request, "catalog.html", user, items=items, total=total, page=page, pages=pages, notes=notes,
                  categories=categories(db), favs=favs,
                  f={"q": q, "category": category, "mode": mode, "condition": condition,
                     "price_min": price_min, "price_max": price_max, "sort": sort})


# ---------- создание / редактирование ----------
def editor_ctx(db, **kw):
    return dict(categories=categories(db), max_images=MAX_IMAGES, **kw)


@router.get("/listings/new")
def new_form(request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    return render(request, "editor.html", user, **editor_ctx(db, listing=None, values={"mode": "sale", "condition": "good"},
                                                              errors={}, locked=False))


@router.post("/listings")
def create(request: Request, title: str = Form(""), description: str = Form(""), category_id: str = Form(""),
           condition: str = Form(""), mode: str = Form(""), price: str = Form(""),
           images: List[UploadFile] = File(default=[]), user=Depends(require_user), db: Session = Depends(get_db)):
    values = dict(title=title, description=description, category_id=category_id, condition=condition, mode=mode, price=price)
    errors, c = s.validate_listing(db, values)
    img_errors, items = s.check_images(images, MAX_IMAGES)
    if img_errors:
        errors["images"] = " ".join(img_errors)
    if errors:
        return render(request, "editor.html", user, 400, **editor_ctx(db, listing=None, values=values, errors=errors, locked=False))
    l = Listing(seller_id=user.id, **c)
    db.add(l)
    db.flush()
    s.store_images(db, l, items)
    s.commit(db)
    flash(request, "Объявление опубликовано.")
    return RedirectResponse(f"/listings/{l.id}", 303)


@router.get("/listings/{lid}/edit")
def edit_form(lid: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    l = get_listing(db, lid)
    if l.seller_id != user.id:
        raise HTTPException(403, "Редактировать можно только свои объявления.")
    if l.status in ("completed", "hidden"):
        raise s.AppError(409, "Это объявление нельзя редактировать: оно передано или скрыто модератором.")
    values = dict(title=l.title, description=l.description, category_id=l.category_id, condition=l.condition,
                  mode=l.mode, price="" if l.price is None else l.price)
    return render(request, "editor.html", user, **editor_ctx(db, listing=l, values=values, errors={}, locked=l.status == "reserved"))


@router.post("/listings/{lid}/edit")
def edit(lid: int, request: Request, title: str = Form(""), description: str = Form(""), category_id: str = Form(""),
         condition: str = Form(""), mode: str = Form(""), price: str = Form(""),
         delete_images: List[int] = Form(default=[]), images: List[UploadFile] = File(default=[]),
         user=Depends(require_user), db: Session = Depends(get_db)):
    l = s.lock_listing(db, lid)
    if l.seller_id != user.id:
        raise HTTPException(403, "Редактировать можно только свои объявления.")
    if l.status in ("completed", "hidden"):
        raise s.AppError(409, "Это объявление нельзя редактировать: оно передано или скрыто модератором.")
    locked = l.status == "reserved"
    if locked and (mode and mode != l.mode or price_changed(price, l)):
        raise s.AppError(409, "Пока вещь в резерве, цену и режим менять нельзя.")
    values = dict(title=title, description=description, category_id=category_id, condition=condition, mode=mode or l.mode,
                  price=price if not locked else ("" if l.price is None else str(l.price)))
    errors, c = s.validate_listing(db, values, locked=l if locked else None)
    to_delete = [i for i in l.images if i.id in delete_images]
    img_errors, items = s.check_images(images, MAX_IMAGES - (len(l.images) - len(to_delete)))
    if img_errors:
        errors["images"] = " ".join(img_errors)
    if errors:
        db.rollback()
        return render(request, "editor.html", user, 400, **editor_ctx(db, listing=l, values=values, errors=errors, locked=locked))
    for k, v in c.items():
        setattr(l, k, v)
    removed = [i.file for i in to_delete]
    for i in to_delete:
        db.delete(i)
    s.store_images(db, l, items)
    s.commit(db)
    s.remove_files(removed)
    flash(request, "Изменения сохранены.")
    return RedirectResponse(f"/listings/{l.id}", 303)


# ---------- карточка ----------
@router.get("/listings/{lid}")
def detail(lid: int, request: Request, user=Depends(current_user), db: Session = Depends(get_db)):
    l = get_listing(db, lid)
    if not can_view(l, user):
        raise HTTPException(404, "Объявление не найдено или скрыто.")
    mine = bool(user and user.id == l.seller_id)
    my_req = None
    if user and not mine:
        my_req = db.scalars(select(DealRequest).where(DealRequest.listing_id == l.id, DealRequest.buyer_id == user.id)
                            .order_by(DealRequest.id.desc())).first()
    is_fav = bool(user and db.get(Favorite, (user.id, l.id)))
    return render(request, "listing.html", user, l=l, mine=mine, my_req=my_req, is_fav=is_fav,
                  staff=bool(user and user.role in STAFF))


@router.post("/listings/{lid}/favorite")
def toggle_favorite(lid: int, request: Request, back: str = Form("/favorites"), user=Depends(require_user),
                    db: Session = Depends(get_db)):
    l = get_listing(db, lid)
    fav = db.get(Favorite, (user.id, l.id))
    if fav:
        db.delete(fav)
        flash(request, "Убрано из избранного.")
    else:
        if not can_view(l, user):
            raise HTTPException(404, "Объявление не найдено.")
        db.add(Favorite(user_id=user.id, listing_id=l.id))
        flash(request, "Добавлено в избранное.")
    s.commit(db)
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/favorites", 303)


@router.get("/favorites")
def favorites(request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    items = db.scalars(select(Listing).join(Favorite, Favorite.listing_id == Listing.id)
                       .where(Favorite.user_id == user.id, Listing.status.in_(PUBLIC_STATUSES))
                       .order_by(Favorite.created_at.desc()).options(selectinload(Listing.images), selectinload(Listing.category))).all()
    return render(request, "favorites.html", user, items=items, favs={l.id for l in items})


# ---------- кабинет автора ----------
@router.get("/my/listings")
def my_listings(request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    items = db.scalars(select(Listing).where(Listing.seller_id == user.id).order_by(Listing.created_at.desc())
                       .options(selectinload(Listing.requests).selectinload(DealRequest.buyer))).all()
    return render(request, "my_listings.html", user, items=items)


@router.post("/listings/{lid}/archive")
def archive(lid: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    s.archive_listing(db, lid, user)
    flash(request, "Объявление снято с публикации.")
    return RedirectResponse("/my/listings", 303)


@router.post("/listings/{lid}/restore")
def restore(lid: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    s.restore_listing(db, lid, user)
    flash(request, "Объявление снова опубликовано.")
    return RedirectResponse("/my/listings", 303)


@router.post("/listings/{lid}/release")
def release(lid: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    s.release_listing(db, lid, user)
    flash(request, "Резерв снят, объявление снова в каталоге.")
    return RedirectResponse("/my/listings", 303)


@router.post("/listings/{lid}/complete")
def complete(lid: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    s.complete_listing(db, lid, user)
    flash(request, "Передача отмечена. Остальные заявки отклонены.")
    return RedirectResponse("/my/listings", 303)


# ---------- заявки ----------
@router.post("/listings/{lid}/requests")
def create_request(lid: int, request: Request, text: str = Form(""), user=Depends(require_user), db: Session = Depends(get_db)):
    l = s.lock_listing(db, lid)
    if l.seller_id == user.id:
        raise HTTPException(403, "Нельзя подать заявку на своё объявление.")
    if l.status != "published":
        raise s.AppError(409, "Заявки принимаются только по опубликованным объявлениям без резерва.")
    text = text.strip()
    if not 1 <= len(text) <= 1000:
        flash(request, "Напишите сообщение продавцу — от 1 до 1000 символов.", "err")
        return RedirectResponse(f"/listings/{lid}", 303)
    if db.scalar(select(DealRequest.id).where(DealRequest.listing_id == lid, DealRequest.buyer_id == user.id,
                                              DealRequest.status.in_(("pending", "accepted")))):
        raise s.AppError(409, "Вы уже подали заявку на это объявление.")
    r = DealRequest(listing_id=lid, buyer_id=user.id, text=text)
    db.add(r)
    s.commit(db, "Вы уже подали заявку на это объявление.")
    flash(request, "Заявка отправлена продавцу.")
    return RedirectResponse(f"/requests/{r.id}/messages", 303)


@router.get("/my/requests")
def my_requests(request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    items = db.scalars(select(DealRequest).where(DealRequest.buyer_id == user.id).order_by(DealRequest.created_at.desc())
                       .options(selectinload(DealRequest.listing).selectinload(Listing.images))).all()
    return render(request, "my_requests.html", user, items=items)


@router.post("/requests/{rid}/accept")
def accept(rid: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    s.accept_request(db, rid, user)
    flash(request, "Получатель выбран, вещь зарезервирована.")
    return RedirectResponse("/my/listings", 303)


@router.post("/requests/{rid}/reject")
def reject(rid: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    s.reject_request(db, rid, user)
    flash(request, "Заявка отклонена.")
    return RedirectResponse("/my/listings", 303)


@router.post("/requests/{rid}/cancel")
def cancel(rid: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    s.cancel_request(db, rid, user)
    flash(request, "Заявка отменена.")
    return RedirectResponse("/my/requests", 303)


def thread_request(db, rid, user):
    r = db.get(DealRequest, rid)
    if not r:
        raise HTTPException(404, "Заявка не найдена.")
    if user.id not in (r.buyer_id, r.listing.seller_id):
        raise HTTPException(403, "Переписка доступна только продавцу и автору заявки.")
    return r


@router.get("/requests/{rid}/messages")
def messages(rid: int, request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    r = thread_request(db, rid, user)
    return render(request, "thread.html", user, r=r, l=r.listing, messages=r.messages,
                  can_write=r.status in ("pending", "accepted"), error=None)


@router.post("/requests/{rid}/messages")
def send_message(rid: int, request: Request, text: str = Form(""), user=Depends(require_user), db: Session = Depends(get_db)):
    r = thread_request(db, rid, user)
    if r.status not in ("pending", "accepted"):
        raise s.AppError(409, "Заявка закрыта — писать в неё больше нельзя.")
    text = text.strip()
    if not 1 <= len(text) <= 2000:
        return render(request, "thread.html", user, 400, r=r, l=r.listing, messages=r.messages, can_write=True,
                      error="Сообщение — от 1 до 2000 символов.")
    db.add(Message(request_id=r.id, sender_id=user.id, text=text))
    s.commit(db)
    return RedirectResponse(f"/requests/{rid}/messages", 303)


# ---------- жалобы ----------
@router.post("/reports")
def create_report(request: Request, listing_id: int = Form(...), reason: str = Form(""), user=Depends(require_user),
                  db: Session = Depends(get_db)):
    l = get_listing(db, listing_id)
    if not can_view(l, user):
        raise HTTPException(404, "Объявление не найдено.")
    if l.seller_id == user.id:
        raise HTTPException(403, "Нельзя жаловаться на своё объявление.")
    reason = reason.strip()
    if not 5 <= len(reason) <= 500:
        flash(request, "Опишите причину жалобы — от 5 до 500 символов.", "err")
        return RedirectResponse(f"/listings/{listing_id}", 303)
    if db.scalar(select(Report.id).where(Report.listing_id == l.id, Report.reporter_id == user.id, Report.status == "open")):
        raise s.AppError(409, "Ваша жалоба на это объявление уже на рассмотрении.")
    db.add(Report(listing_id=l.id, reporter_id=user.id, reason=reason))
    s.commit(db)
    flash(request, "Жалоба отправлена модератору.")
    return RedirectResponse(f"/listings/{listing_id}", 303)
