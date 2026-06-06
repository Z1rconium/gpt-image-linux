"""SQLite connection and migration helpers.

storage.py remains the compatibility facade; these aliases provide a narrower
import surface for new repository code.
"""

from .storage import (  # noqa: F401
    _connect as connect,
    _table_columns as table_columns,
    _table_exists as table_exists,
    _transaction as transaction,
    close_database_connections,
    verify_storage_writable,
)
