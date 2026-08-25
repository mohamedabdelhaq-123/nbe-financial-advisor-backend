"""add phone to mock bank customers

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-25

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The temporary default keeps already-seeded demo rows valid during the
    # migration. New writes must provide the phone through the API.
    op.add_column(
        "mock_customers",
        sa.Column(
            "phone",
            sa.String(),
            nullable=False,
            server_default="+201000000000",
        ),
    )
    op.alter_column("mock_customers", "phone", server_default=None)


def downgrade() -> None:
    op.drop_column("mock_customers", "phone")
