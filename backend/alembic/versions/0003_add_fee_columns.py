"""add fee columns to bets and markets

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-18
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Новые колонки в bets
    op.add_column('bets', sa.Column('fee_nano',    sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('bets', sa.Column('net_amount',  sa.BigInteger(), nullable=True))
    op.add_column('bets', sa.Column('is_free_bet', sa.Boolean(),    nullable=False, server_default='false'))

    # Бэкфилл: net_amount = amount для старых ставок (без комиссии)
    op.execute("UPDATE bets SET net_amount = amount WHERE net_amount IS NULL")

    # Новые колонки в markets
    op.add_column('markets', sa.Column('taker_fee_collected',     sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('markets', sa.Column('resolution_fee_collected',sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('markets', sa.Column('creator_fee_earned',      sa.BigInteger(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('bets', 'fee_nano')
    op.drop_column('bets', 'net_amount')
    op.drop_column('bets', 'is_free_bet')
    op.drop_column('markets', 'taker_fee_collected')
    op.drop_column('markets', 'resolution_fee_collected')
    op.drop_column('markets', 'creator_fee_earned')
