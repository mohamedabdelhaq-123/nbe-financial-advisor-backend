"""initial schema: mock_customers, mock_accounts, mock_transactions

Revision ID: 0001
Revises:
Create Date: 2026-07-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mock_customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_bank_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_mock_customers_customer_bank_id",
        "mock_customers",
        ["customer_bank_id"],
        unique=True,
    )

    op.create_table(
        "mock_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mock_customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "bank_name",
            sa.String(),
            nullable=False,
            server_default="Mock National Bank",
        ),
        sa.Column("account_type", sa.String(), nullable=True),
        sa.Column("masked_account_number", sa.String(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False, server_default="EGP"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index("ix_mock_accounts_customer_id", "mock_accounts", ["customer_id"], unique=False)

    op.create_table(
        "mock_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mock_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("merchant", sa.String(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("transaction_type", sa.String(), nullable=True),
        sa.Column("balance", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_mock_transactions_account_id",
        "mock_transactions",
        ["account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mock_transactions_account_id", table_name="mock_transactions")
    op.drop_table("mock_transactions")

    op.drop_index("ix_mock_accounts_customer_id", table_name="mock_accounts")
    op.drop_table("mock_accounts")

    op.drop_index("ix_mock_customers_customer_bank_id", table_name="mock_customers")
    op.drop_table("mock_customers")
