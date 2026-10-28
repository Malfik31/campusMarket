import os
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import BASE_DIR, SECRET_KEY, UPLOAD_DIR
from .db import SessionLocal
from .models import User
from .routers import auth, listings, staff
from .services import AppError
from .web import render

from app.db import engine
from app.models import Base # поправьте импорт под свой проект
from app import models                  # важно: импортировать модели, иначе Base о них не узнает

Base.metadata.create_all(bind=engine)
from .seed import run as run_seed

Base.metadata.create_all(bind=engine)
os.makedirs(UPLOAD_DIR, exist_ok=True)
run_seed()
os.makedirs(UPLOAD_DIR, exist_ok=True)
app = FastAPI(title="CampusMarket", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", max_age=14 * 24 * 3600)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.include_router(auth.router)
app.include_router(listings.router)
app.include_router(staff.router)

TITLES = {400: "Некорректный запрос", 403: "Нет доступа", 404: "Не найдено", 409: "Действие недоступно"}


def error_page(request: Request, code: int, message: str):
    user = None
    uid = request.session.get("uid") if "session" in request.scope else None
    if uid:
        with SessionLocal() as db:
            u = db.get(User, uid)
            user = u if u and u.is_active else None
    return render(request, "error.html", user, code, code=code, title=TITLES.get(code, "Ошибка"), message=message)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401:
        return RedirectResponse(f"/login?next={quote(request.url.path)}", 303)
    msg = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found", "Forbidden") else \
        {404: "Такой страницы или объекта нет.", 403: "У вас нет прав для выполнения этого действия."}.get(exc.status_code, "Что-то пошло не так.")
    return error_page(request, exc.status_code, msg)


@app.exception_handler(AppError)
async def app_error(request: Request, exc: AppError):
    return error_page(request, exc.status, exc.message)


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    return error_page(request, 500, "Не удалось выполнить действие. Попробуйте ещё раз чуть позже.")


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return error_page(request, 400, "Форма заполнена некорректно. Проверьте данные и попробуйте снова.")
