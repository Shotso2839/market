"""add oracle_params and new oracle types

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем JSON-колонку oracle_params в таблицу markets
    op.add_column('markets',
        sa.Column('oracle_params', sa.JSON(), nullable=True)
    )

    # Добавляем новые значения в enum oracletype
    # В PostgreSQL нельзя просто добавить значение в ENUM через ALTER TABLE
    # Нужно ALTER TYPE
    op.execute("ALTER TYPE oracletype ADD VALUE IF NOT EXISTS 'sports'")
    op.execute("ALTER TYPE oracletype ADD VALUE IF NOT EXISTS 'weather'")


def downgrade() -> None:
    op.drop_column('markets', 'oracle_params')
    # Откат enum значений в PostgreSQL не поддерживается напрямую
    # Для полного отката нужно пересоздать тип без новых значений
