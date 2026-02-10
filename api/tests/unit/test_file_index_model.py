"""Tests for FileIndex ORM model."""


def test_file_index_model_exists():
    """FileIndex model can be imported."""
    from src.models.orm.file_index import FileIndex
    assert FileIndex.__tablename__ == "file_index"


def test_file_index_columns():
    """FileIndex has expected columns."""
    from src.models.orm.file_index import FileIndex
    columns = {c.name for c in FileIndex.__table__.columns}
    assert "path" in columns
    assert "content" in columns
    assert "content_hash" in columns
    assert "updated_at" in columns


def test_file_index_primary_key():
    """Path is the primary key."""
    from src.models.orm.file_index import FileIndex
    pk_cols = [c.name for c in FileIndex.__table__.primary_key.columns]
    assert pk_cols == ["path"]
