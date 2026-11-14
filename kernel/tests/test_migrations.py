from __future__ import annotations

import pytest
from heycarl.migrations import Migration, MigrationError, pending

A = Migration("core", "0001_a.sql", "select 1;")
B = Migration("core", "0002_b.sql", "select 2;")


def test_pending_runs_only_new() -> None:
    assert pending([A, B], {A.name: A.checksum}, "core") == [B]


def test_edited_migration_fails() -> None:
    with pytest.raises(MigrationError, match="edited"):
        pending([A, B], {A.name: "0" * 64}, "core")


def test_unknown_applied_migration_fails() -> None:
    with pytest.raises(MigrationError, match="lacks"):
        pending([A], {A.name: A.checksum, B.name: B.checksum}, "core")
