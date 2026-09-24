"""Add access rules, platform settings, captcha and email verification

Revision ID: d4a7b2c91f30
Revises: b3e6a1c85f24
Create Date: 2026-09-24 16:40:00.000000

手写迁移说明：本机无可用 PostgreSQL，alembic revision --autogenerate 无法连库比对，
因此按 SQLModel 元数据对 postgresql 方言编译出的 DDL 人工编写（与 c7d84e1f2a39 /
b3e6a1c85f24 同一套做法，逐列核对过 CreateTable / CreateIndex 的编译结果）。

**本次新增**
- `accessrule`：准入规则（邮箱后缀 / IP），allow 与 deny 两组，`deny` 优先
- `platformsetting`：平台级开关（当前只有 registration.enabled），KV 形状便于后续扩展
- `captchachallenge`：图片验证码一次性票据，答案只存哈希，`expires_at` 建索引供清理
- `emailverificationcode`：邮箱验证码（6 位数字）落库票据，含尝试次数与过期时间。
  **必须落库**：6 位码只有 10^6 空间，没有服务端记录就无法限制尝试次数
- `user.email_verified_at` / `user.verification_sent_at`：邮箱验证状态与重发节流
- `auditlog.ip`：准入拒绝与登录失败必须能回答「谁在试」

**存量数据回填**：`user.email_verified_at` 回填为 `created_at` —— 既有账号视为已验证，
否则部署瞬间所有人（含运维者）都会被「邮箱未验证」挡在门外。`is_active` 保持原值不动，
「管理员停用」的语义不受影响。
"""
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision = 'd4a7b2c91f30'
down_revision = 'b3e6a1c85f24'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'accessrule',
        sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('list_type', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('value', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('note', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('kind', 'list_type', 'value', name='uq_access_rule'),
    )
    op.create_table(
        'platformsetting',
        sa.Column('key', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('value', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['updated_by'], ['user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_table(
        'captchachallenge',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('scope', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('answer_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_ip', sqlmodel.sql.sqltypes.AutoString(length=45), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_captchachallenge_expires_at'),
        'captchachallenge',
        ['expires_at'],
        unique=False,
    )
    op.create_table(
        'emailverificationcode',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('scope', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('code_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_emailverificationcode_expires_at'),
        'emailverificationcode',
        ['expires_at'],
        unique=False,
    )

    op.add_column(
        'user',
        sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'user',
        sa.Column('verification_sent_at', sa.DateTime(timezone=True), nullable=True),
    )
    # 存量账号视为已验证：回填 created_at（缺 created_at 的老行用 now() 兜底）
    op.execute(
        'UPDATE "user" SET email_verified_at = COALESCE(created_at, now()) '
        'WHERE email_verified_at IS NULL'
    )

    op.add_column(
        'auditlog',
        sa.Column('ip', sqlmodel.sql.sqltypes.AutoString(length=45), nullable=True),
    )


def downgrade():
    op.drop_column('auditlog', 'ip')
    op.drop_column('user', 'verification_sent_at')
    op.drop_column('user', 'email_verified_at')
    op.drop_index(
        op.f('ix_emailverificationcode_expires_at'), table_name='emailverificationcode'
    )
    op.drop_table('emailverificationcode')
    op.drop_index(op.f('ix_captchachallenge_expires_at'), table_name='captchachallenge')
    op.drop_table('captchachallenge')
    op.drop_table('platformsetting')
    op.drop_table('accessrule')
