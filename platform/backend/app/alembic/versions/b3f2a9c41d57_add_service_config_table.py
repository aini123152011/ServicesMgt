"""Add service config table

Revision ID: b3f2a9c41d57
Revises: fe56fa70289e
Create Date: 2026-09-20 00:00:00.000000

手写迁移说明：本机无可用 PostgreSQL，alembic revision --autogenerate 无法
连接数据库比对，因此按 SQLModel 元数据对 postgresql 方言编译出的 DDL
（CreateTable 编译结果逐列核对）人工编写，与 autogenerate 产物等价。

"""
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b3f2a9c41d57'
down_revision = 'fe56fa70289e'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'serviceconfig',
        sa.Column('service_name', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('values', sa.JSON(), nullable=False),
        sa.Column('applied', sa.Boolean(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('rendered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_serviceconfig_service_name'), 'serviceconfig', ['service_name'], unique=True
    )


def downgrade():
    op.drop_index(op.f('ix_serviceconfig_service_name'), table_name='serviceconfig')
    op.drop_table('serviceconfig')
