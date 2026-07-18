"""Vocabularies shared across the app, defined once so they cannot drift apart."""

# Where a transaction's row came from: an OCR'd statement, direct manual entry,
# or an automated push from a linked (synced) bank account. Transaction.source
# is a plain CharField with no DB-level constraint, so this is purely
# self-documenting/DRF-schema metadata, not an enforced constraint.
TRANSACTION_SOURCES = ("statement", "manual", "synced")

# BankAccount.link_type: "manual" accounts are user-managed (statement upload /
# manual entry, fully editable); "synced" accounts are bank-integrated via a
# BankConnection and read-only to the end user (see BankAccount.is_synced).
BANK_ACCOUNT_LINK_TYPES = ("manual", "synced")
