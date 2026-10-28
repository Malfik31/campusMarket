from datetime import timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import APP_TIMEZONE, BASE_DIR
from .models import (CONDITIONS, LISTING_STATUSES, MODES, REQUEST_STATUSES, ROLES)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
_tz = ZoneInfo(APP_TIMEZONE)


def localtime(dt):
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz).strftime("%d.%m.%Y %H:%M")


def price_label(l):
    if l.mode == "gift":
        return "Даром"
    if l.mode == "exchange":
        return "Обмен"
    return f"{l.price:,.2f}".replace(",", " ").replace(".00", "") + " сом"


templates.env.filters["localtime"] = localtime
templates.env.filters["price_label"] = price_label
templates.env.globals.update(MODES=MODES, CONDITIONS=CONDITIONS, LISTING_STATUSES=LISTING_STATUSES,
                             REQUEST_STATUSES=REQUEST_STATUSES, ROLES=ROLES, TZ_NAME=APP_TIMEZONE)


def flash(request: Request, message: str, kind: str = "ok"):
    items = list(request.session.get("flash", []))
    items.append([kind, message])
    request.session["flash"] = items


def render(request: Request, name: str, user, status: int = 200, **ctx):
    flashes = request.session.pop("flash", [])
    return templates.TemplateResponse(request, name, {"user": user, "flashes": flashes, **ctx}, status_code=status)
