import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_user
from ..models import DealRequest, Listing, User
from ..security import hash_password, verify_password
from ..services import AppError, commit
from ..web import flash, render

router = APIRouter()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def safe_next(n: str) -> str:
    return n if n.startswith("/") and not n.startswith("//") else "/listings"


@router.get("/register")
def register_form(request: Request, user=Depends(current_user)):
    if user:
        return RedirectResponse("/listings", 303)
    return render(request, "register.html", user, values={}, errors={})


@router.post("/register")
def register(request: Request, email: str = Form(""), display_name: str = Form(""),
             password: str = Form(""), password2: str = Form(""), db: Session = Depends(get_db)):
    email, display_name = email.strip().lower(), display_name.strip()
    errors = {}
    if not EMAIL_RE.match(email) or len(email) > 254:
        errors["email"] = "Введите корректный email."
    elif db.scalar(select(User).where(User.email == email)):
        errors["email"] = "Этот email уже зарегистрирован."
    if not 2 <= len(display_name) <= 60:
        errors["display_name"] = "Имя — от 2 до 60 символов."
    if len(password) < 8:
        errors["password"] = "Пароль — не короче 8 символов."
    if password != password2:
        errors["password2"] = "Пароли не совпадают."
    if errors:
        return render(request, "register.html", None, 400, values={"email": email, "display_name": display_name}, errors=errors)
    u = User(email=email, display_name=display_name, password_hash=hash_password(password), role="member")
    db.add(u)
    commit(db, "Этот email уже зарегистрирован.")
    request.session["uid"] = u.id
    flash(request, "Аккаунт создан. Добро пожаловать!")
    return RedirectResponse("/listings", 303)


@router.get("/login")
def login_form(request: Request, next: str = "/listings", user=Depends(current_user)):
    if user:
        return RedirectResponse("/listings", 303)
    return render(request, "login.html", None, values={}, errors={}, next=next)


@router.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("/listings"),
          db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not u or not u.is_active or not verify_password(password, u.password_hash):
        return render(request, "login.html", None, 400, values={"email": email}, next=next,
                      errors={"form": "Неверный email или пароль."})
    request.session.clear()
    request.session["uid"] = u.id
    return RedirectResponse(safe_next(next), 303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/listings", 303)


@router.get("/profile")
def profile_form(request: Request, user=Depends(require_user)):
    return render(request, "profile.html", user, errors={})


@router.post("/profile")
def profile_save(request: Request, display_name: str = Form(""), current_password: str = Form(""),
                 new_password: str = Form(""), user=Depends(require_user), db: Session = Depends(get_db)):
    errors = {}
    display_name = display_name.strip()
    if not 2 <= len(display_name) <= 60:
        errors["display_name"] = "Имя — от 2 до 60 символов."
    if new_password:
        if not verify_password(current_password, user.password_hash):
            errors["current_password"] = "Текущий пароль указан неверно."
        if len(new_password) < 8:
            errors["new_password"] = "Новый пароль — не короче 8 символов."
    if errors:
        return render(request, "profile.html", user, 400, errors=errors)
    user.display_name = display_name
    if new_password:
        user.password_hash = hash_password(new_password)
    commit(db)
    flash(request, "Профиль сохранён.")
    return RedirectResponse("/profile", 303)


@router.post("/profile/delete")
def profile_delete(request: Request, password: str = Form(""), user=Depends(require_user), db: Session = Depends(get_db)):
    """Мягкое удаление: история заявок и сообщений сохраняется, вход закрывается."""
    if not verify_password(password, user.password_hash):
        return render(request, "profile.html", user, 400, errors={"delete_password": "Пароль указан неверно."})
    listings = db.scalars(select(Listing).where(Listing.seller_id == user.id)).all()
    if any(l.status == "reserved" for l in listings):
        raise AppError(409, "У вас есть зарезервированные вещи. Завершите передачу или снимите резерв, затем удаляйте аккаунт.")
    for l in listings:
        if l.status == "published":
            l.status = "archived"
        for r in l.requests:
            if r.status == "pending":
                r.status = "rejected"
    for r in db.scalars(select(DealRequest).where(DealRequest.buyer_id == user.id, DealRequest.status.in_(("pending", "accepted")))):
        r.listing.status = "published" if r.status == "accepted" and r.listing.status == "reserved" else r.listing.status
        r.status = "cancelled"
    user.is_active = False
    user.display_name = "Удалённый пользователь"
    user.email = f"deleted-{user.id}@deleted.invalid"
    commit(db)
    request.session.clear()
    flash(request, "Аккаунт удалён. История сделок сохранена без ваших личных данных.")
    return RedirectResponse("/listings", 303)
