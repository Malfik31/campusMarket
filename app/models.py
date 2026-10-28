from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Numeric, String, Text, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MODES = {"sale": "Продажа", "exchange": "Обмен", "gift": "Даром"}
CONDITIONS = {"new": "Новое", "like_new": "Как новое", "good": "Хорошее",
              "fair": "Удовлетворительное", "poor": "Требует ремонта"}
LISTING_STATUSES = {"published": "Опубликовано", "reserved": "Зарезервировано", "completed": "Передано",
                    "archived": "В архиве", "hidden": "Скрыто модератором"}
REQUEST_STATUSES = {"pending": "На рассмотрении", "accepted": "Принята", "rejected": "Отклонена",
                    "cancelled": "Отменена", "completed": "Передача завершена"}
ROLES = {"member": "Участник", "moderator": "Модератор", "admin": "Администратор"}
PUBLIC_STATUSES = ("published", "reserved")


def utcnow():
    return datetime.now(timezone.utc)


def _in(col, d):
    return f"{col} IN ({', '.join(repr(k) for k in d)})"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="ck_user_role"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(60))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="member")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint(_in("mode", MODES), name="ck_listing_mode"),
        CheckConstraint(_in("condition", CONDITIONS), name="ck_listing_condition"),
        CheckConstraint(_in("status", LISTING_STATUSES), name="ck_listing_status"),
        CheckConstraint(
            "(mode = 'sale' AND price > 0) OR (mode = 'gift' AND price = 0) OR (mode = 'exchange' AND price IS NULL)",
            name="ck_listing_price_by_mode"),
        Index("ix_listings_status_created", "status", "created_at"),
        Index("ix_listings_status_price", "status", "price"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    condition: Mapped[str] = mapped_column(String(20), index=True)
    mode: Mapped[str] = mapped_column(String(20), index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="published")
    hide_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    seller: Mapped[User] = relationship()
    category: Mapped[Category] = relationship()
    images: Mapped[list["ListingImage"]] = relationship(order_by="ListingImage.position", back_populates="listing")
    requests: Mapped[list["DealRequest"]] = relationship(order_by="DealRequest.id", back_populates="listing")


class ListingImage(Base):
    __tablename__ = "listing_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    file: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(default=0)
    listing: Mapped[Listing] = relationship(back_populates="images")


class DealRequest(Base):
    __tablename__ = "deal_requests"
    __table_args__ = (
        CheckConstraint(_in("status", REQUEST_STATUSES), name="ck_request_status"),
        # не более одной принятой заявки на объявление (защита на уровне БД)
        Index("uq_one_accepted_per_listing", "listing_id", unique=True,
              postgresql_where=text("status = 'accepted'"), sqlite_where=text("status = 'accepted'")),
        # у покупателя не больше одной активной заявки на объявление
        Index("uq_active_request_per_buyer", "listing_id", "buyer_id", unique=True,
              postgresql_where=text("status IN ('pending', 'accepted')"),
              sqlite_where=text("status IN ('pending', 'accepted')")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listing: Mapped[Listing] = relationship(back_populates="requests")
    buyer: Mapped[User] = relationship()
    messages: Mapped[list["Message"]] = relationship(order_by="Message.id", back_populates="request")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("deal_requests.id"), index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    request: Mapped[DealRequest] = relationship(back_populates="messages")
    sender: Mapped[User] = relationship()


class Favorite(Base):
    __tablename__ = "favorites"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (CheckConstraint("status IN ('open', 'resolved')", name="ck_report_status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    listing: Mapped[Listing] = relationship()
    reporter: Mapped[User] = relationship(foreign_keys=[reporter_id])
