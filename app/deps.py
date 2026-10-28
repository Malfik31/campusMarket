from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User


def current_user(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("uid")
    if not uid:
        return None
    u = db.get(User, uid)
    return u if u and u.is_active else None


def require_user(user=Depends(current_user)):
    if not user:
        raise HTTPException(401, "Войдите в аккаунт, чтобы продолжить.")
    return user


def require_role(*roles):
    def dep(user=Depends(require_user)):
        if user.role not in roles:
            raise HTTPException(403, "У вас нет прав для выполнения этого действия.")
        return user
    return dep
