"""initial schema

Revision ID: 0001
Revises:
"""
from alembic import op

from app.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Начальная схема берётся из моделей (таблицы, FK, CHECK, индексы).
    # Последующие изменения: alembic revision --autogenerate -m "..."
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
