"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(64), nullable=True),
        sa.Column('ton_address', sa.String(68), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('telegram_id'),
        sa.UniqueConstraint('ton_address'),
    )
    op.create_index('ix_users_telegram_id', 'users', ['telegram_id'])
    op.create_index('ix_users_ton_address', 'users', ['ton_address'])

    # ── markets ────────────────────────────────────────────────────────────────
    market_status = postgresql.ENUM(
        'open', 'closed', 'resolved', 'cancelled', name='marketstatus'
    )
    market_status.create(op.get_bind())

    market_category = postgresql.ENUM(
        'Спорт', 'Крипто', 'Политика', 'Погода', 'Другое', name='marketcategory'
    )
    market_category.create(op.get_bind())

    oracle_type = postgresql.ENUM('self', 'pyth', 'dao', name='oracletype')
    oracle_type.create(op.get_bind())

    bet_outcome = postgresql.ENUM('yes', 'no', name='betoutcome')
    bet_outcome.create(op.get_bind())

    op.create_table(
        'markets',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('creator_id', sa.String(36), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', postgresql.ENUM('Спорт', 'Крипто', 'Политика', 'Погода', 'Другое',
                                               name='marketcategory', create_type=False), nullable=False),
        sa.Column('status', postgresql.ENUM('open', 'closed', 'resolved', 'cancelled',
                                            name='marketstatus', create_type=False), nullable=False),
        sa.Column('oracle_type', postgresql.ENUM('self', 'pyth', 'dao',
                                                  name='oracletype', create_type=False), nullable=False),
        sa.Column('contract_address', sa.String(68), nullable=True),
        sa.Column('deploy_tx_hash', sa.String(64), nullable=True),
        sa.Column('yes_pool', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('no_pool', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('bet_closes_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('winning_outcome', postgresql.ENUM('yes', 'no',
                                                      name='betoutcome', create_type=False), nullable=True),
        sa.Column('resolution_tx_hash', sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(['creator_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('contract_address'),
    )
    op.create_index('ix_markets_status', 'markets', ['status'])
    op.create_index('ix_markets_status_closes', 'markets', ['status', 'bet_closes_at'])
    op.create_index('ix_markets_category', 'markets', ['category'])

    # ── bets ───────────────────────────────────────────────────────────────────
    tx_status = postgresql.ENUM('pending', 'confirmed', 'failed', name='txstatus')
    tx_status.create(op.get_bind())

    op.create_table(
        'bets',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('market_id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('outcome', postgresql.ENUM('yes', 'no', name='betoutcome', create_type=False), nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('potential_payout', sa.BigInteger(), nullable=True),
        sa.Column('tx_hash', sa.String(64), nullable=True),
        sa.Column('tx_status', postgresql.ENUM('pending', 'confirmed', 'failed',
                                                name='txstatus', create_type=False), nullable=False),
        sa.Column('is_winner', sa.Boolean(), nullable=True),
        sa.Column('payout_amount', sa.BigInteger(), nullable=True),
        sa.Column('payout_tx_hash', sa.String(64), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['market_id'], ['markets.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tx_hash'),
    )
    op.create_index('ix_bets_market_user', 'bets', ['market_id', 'user_id'])
    op.create_index('ix_bets_tx_hash', 'bets', ['tx_hash'])


def downgrade() -> None:
    op.drop_table('bets')
    op.drop_table('markets')
    op.drop_table('users')

    for enum in ('txstatus', 'betoutcome', 'oracletype', 'marketcategory', 'marketstatus'):
        postgresql.ENUM(name=enum).drop(op.get_bind())
