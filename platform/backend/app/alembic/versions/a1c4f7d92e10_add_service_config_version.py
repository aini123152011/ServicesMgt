"""Add service config version

Revision ID: a1c4f7d92e10
Revises: c7d84e1f2a39
Create Date: 2026-09-22 16:50:00.000000

手写迁移说明：alembic autogenerate 需要连库比对，而本机到测试库的 SSH 隧道不稳定；
本迁移按 SQLModel 元数据对 postgresql 方言编译出的 DDL 逐列核对后人工编写，与 autogenerate 等价。

**只建表、不删表**：autogenerate 会顺带检测到「item 表已无对应模型」并生成 drop_table('item')，
但项目既定决策是保留该表（不做破坏性迁移），故本迁移不含任何 drop。
"""
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a1c4f7d92e10'
down_revision = 'c7d84e1f2a39'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'serviceconfigversion',
        sa.Column('service_name', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('values', sa.JSON(), nullable=False),
        sa.Column('applied', sa.Boolean(), nullable=False),
        sa.Column('rendered_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('user_email', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column('rolled_back_from', sa.Integer(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('service_name', 'version', name='uq_config_version'),
    )
    op.create_index(
        op.f('ix_serviceconfigversion_service_name'),
        'serviceconfigversion',
        ['service_name'],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f('ix_serviceconfigversion_service_name'),
        table_name='serviceconfigversion',
    )
    op.drop_table('serviceconfigversion')
