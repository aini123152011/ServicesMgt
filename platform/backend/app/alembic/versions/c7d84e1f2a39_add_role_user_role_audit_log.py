"""Add role user_role audit_log

Revision ID: c7d84e1f2a39
Revises: b3f2a9c41d57
Create Date: 2026-09-20 00:00:00.000000

手写迁移说明：本机无可用 PostgreSQL，alembic revision --autogenerate 无法
连接数据库比对，因此按 SQLModel 元数据对 postgresql 方言编译出的 DDL
（CreateTable/CreateIndex 编译结果逐列核对）人工编写，与 autogenerate
产物等价。

"""
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c7d84e1f2a39'
down_revision = 'b3f2a9c41d57'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'role',
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_role_name'), 'role', ['name'], unique=True)
    op.create_table(
        'userrole',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('role_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['role.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'role_id'),
    )
    op.create_table(
        'auditlog',
        sa.Column('action', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('service_name', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column('detail', sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=True),
        sa.Column('user_email', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('auditlog')
    op.drop_table('userrole')
    op.drop_index(op.f('ix_role_name'), table_name='role')
    op.drop_table('role')
