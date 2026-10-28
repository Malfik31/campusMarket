from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import services as s
from ..db import get_db
from ..deps import require_role
from ..models import ROLES, DealRequest, Listing, Report, User
from ..web import flash, render

router = APIRouter()
mod_only = require_role("moderator", "admin")
admin_only = require_role("admin")


@router.get("/moderation")
def moderation(request: Request, user=Depends(mod_only), db: Session = Depends(get_db)):
    reports = db.scalars(select(Report).where(Report.status == "open").order_by(Report.created_at)
                         .options(selectinload(Report.listing), selectinload(Report.reporter))).all()
    hidden = db.scalars(select(Listing).where(Listing.status == "hidden").order_by(Listing.updated_at.desc())).all()
    done = db.scalars(select(Report).where(Report.status == "resolved").order_by(Report.id.desc()).limit(10)
                      .options(selectinload(Report.listing))).all()
    return render(request, "moderation.html", user, reports=reports, hidden=hidden, done=done)


@router.post("/moderation/reports/{rid}/hide")
def hide(rid: int, request: Request, reason: str = Form(""), user=Depends(mod_only), db: Session = Depends(get_db)):
    rep = db.get(Report, rid)
    if not rep:
        raise HTTPException(404, "Жалоба не найдена.")
    reason = reason.strip()
    if not 5 <= len(reason) <= 500:
        flash(request, "Укажите причину скрытия — от 5 до 500 символов.", "err")
        return RedirectResponse("/moderation", 303)
    l = s.lock_listing(db, rep.listing_id)
    if l.status == "hidden":
        raise s.AppError(409, "Объявление уже скрыто.")
    l.status, l.hide_reason = "hidden", reason
    for r in db.scalars(select(Report).where(Report.listing_id == l.id, Report.status == "open")):
        r.status, r.resolution, r.resolved_by_id = "resolved", f"Объявление скрыто: {reason}", user.id
    s.commit(db)
    flash(request, "Объявление скрыто, жалобы закрыты.")
    return RedirectResponse("/moderation", 303)


@router.post("/moderation/reports/{rid}/dismiss")
def dismiss(rid: int, request: Request, comment: str = Form(""), user=Depends(mod_only), db: Session = Depends(get_db)):
    rep = db.get(Report, rid)
    if not rep:
        raise HTTPException(404, "Жалоба не найдена.")
    if rep.status != "open":
        raise s.AppError(409, "Жалоба уже рассмотрена.")
    rep.status, rep.resolved_by_id = "resolved", user.id
    rep.resolution = f"Отклонена. {comment.strip()}".strip()
    s.commit(db)
    flash(request, "Жалоба отклонена.")
    return RedirectResponse("/moderation", 303)


@router.post("/moderation/listings/{lid}/restore")
def unhide(lid: int, request: Request, user=Depends(mod_only), db: Session = Depends(get_db)):
    l = s.lock_listing(db, lid)
    if l.status != "hidden":
        raise s.AppError(409, "Объявление не скрыто.")
    has_accepted = any(r.status == "accepted" for r in l.requests)
    l.status, l.hide_reason = ("reserved" if has_accepted else "published"), None
    s.commit(db)
    flash(request, "Объявление возвращено в каталог.")
    return RedirectResponse("/moderation", 303)


@router.get("/admin/users")
def users(request: Request, user=Depends(admin_only), db: Session = Depends(get_db)):
    return render(request, "admin_users.html", user, users=db.scalars(select(User).order_by(User.id)).all())


@router.post("/admin/users/{uid}/role")
def set_role(uid: int, request: Request, role: str = Form(""), user=Depends(admin_only), db: Session = Depends(get_db)):
    target = db.get(User, uid)
    if not target:
        raise HTTPException(404, "Пользователь не найден.")
    if role not in ROLES:
        raise s.AppError(400, "Неизвестная роль.")
    if target.id == user.id:
        raise s.AppError(409, "Свою роль изменить нельзя — это защита от потери доступа администратора.")
    target.role = role
    s.commit(db)
    flash(request, f"Роль пользователя «{target.display_name}» изменена.")
    return RedirectResponse("/admin/users", 303)
