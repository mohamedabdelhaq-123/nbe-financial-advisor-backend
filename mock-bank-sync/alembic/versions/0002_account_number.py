"""rename mock_accounts.masked_account_number to account_number

The mock bank now hands back the real account number rather than a masked
hint, matching BankConnector.fetch_accounts()'s contract on the Django side
(services/bank_connectors/base.py).

Existing rows keep whatever they held — masked values like "****0000" seeded
before this change can't be recovered into full numbers, so flush and re-run
`seed_bank_demo_data` if realistic data matters.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "mock_accounts",
        "masked_account_number",
        new_column_name="account_number",
        existing_type=sa.String(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "mock_accounts",
        "account_number",
        new_column_name="masked_account_number",
        existing_type=sa.String(),
        existing_nullable=True,
    )
