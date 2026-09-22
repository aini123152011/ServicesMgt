"""Add secret fields to service config version

Revision ID: b3e6a1c85f24
Revises: a1c4f7d92e10
Create Date: 2026-09-22 18:05:00.000000

手写迁移说明：与 a1c4f7d92e10 同一套做法（连库 autogenerate 不稳，按元数据编译的 DDL 逐列核对后手写）。

**为什么加这一列**：版本详情原先按「当前 schema」里的 `secret: true` 反推要打码的字段。
schema 一旦演进（字段改名、或去掉了 secret 标记），历史版本里的密文就会明文返回——而历史
正是最不该泄露的地方（前端也明确预期「schema 里已删的键」会出现在历史里）。写入版本时把
当时的 secret 字段名单存下来，详情接口按「写入时的名单 ∪ 当前 schema 的名单」打码。

存量行回填为空数组：它们仍然能靠当前 schema 打码（与本次改动前的行为一致），不会因迁移而
变得更不安全。nullable=False 与模型定义一致（`secret_fields` 默认空列表）。
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b3e6a1c85f24'
down_revision = 'a1c4f7d92e10'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'serviceconfigversion',
        sa.Column('secret_fields', sa.JSON(), nullable=False, server_default='[]'),
    )


def downgrade():
    op.drop_column('serviceconfigversion', 'secret_fields')
