"""SQLAlchemy ORM models for the mock bank's ledger.

Mirrored by hand in alembic/versions/0001_initial.py — if you change a model,
update that migration (or add a new one) to match.
"""

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db import Base


class MockCustomer(Base):
    __tablename__ = "mock_customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Opaque bank-login identifier (customer number, username, etc) — do NOT
    # assume it's an email. This is what mock-bank-oauth resolves via
    # GET /internal/customers/lookup.
    customer_bank_id = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, nullable=False)
    name = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    accounts = relationship("MockAccount", back_populates="customer", cascade="all, delete-orphan")


class MockAccount(Base):
    __tablename__ = "mock_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("mock_customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bank_name = Column(String, nullable=False, default="Mock National Bank")
    account_type = Column(String, nullable=True)  # e.g. checking/savings/credit_card
    masked_account_number = Column(String, nullable=True)
    currency = Column(String, nullable=False, default="EGP")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    customer = relationship("MockCustomer", back_populates="accounts")
    transactions = relationship(
        "MockTransaction", back_populates="account", cascade="all, delete-orphan"
    )


class MockTransaction(Base):
    __tablename__ = "mock_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("mock_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transaction_date = Column(DateTime(timezone=True), nullable=False)
    merchant = Column(String, nullable=True)
    amount = Column(Numeric(14, 2), nullable=False)
    transaction_type = Column(String, nullable=True)  # e.g. debit/credit
    balance = Column(Numeric(14, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    account = relationship("MockAccount", back_populates="transactions")
