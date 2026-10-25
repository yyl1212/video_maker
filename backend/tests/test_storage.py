from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.services.storage import StorageService


def test_path_escape_and_symlink_rejected(tmp_path):
    storage = StorageService(tmp_path / "data")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"secret")
    link = storage.staging / "link.png"
    link.symlink_to(outside)
    for path in (outside, link, storage.root / ".." / "outside.png"):
        with pytest.raises(AppError):
            storage.checked_file(path)


def test_atomic_publish_does_not_register_partial_file(tmp_path):
    storage = StorageService(tmp_path / "data")
    source = storage.staging / "ready.png"
    source.write_bytes(b"complete")
    published = storage.publish_file(source, 1)
    assert published.path.is_absolute()
    assert published.path.read_bytes() == b"complete"
    assert not source.exists()
    assert len(published.sha256) == 64


def test_orphan_is_quarantined_without_becoming_done(tmp_path):
    storage = StorageService(tmp_path / "data")
    source = storage.staging / "orphan.png"
    source.write_bytes(b"complete")
    published = storage.publish_file(source, 1)
    assert storage.quarantine_orphans(set()) == 1
    assert not published.path.exists()
    assert len(list(storage.quarantine.iterdir())) == 1


def test_freeze_verifies_content_and_never_follows_links(tmp_path):
    storage = StorageService(tmp_path / "data")
    source = storage.staging / "input.png"
    source.write_bytes(b"original")
    published = storage.publish_file(source, 1)
    asset = SimpleNamespace(
        id=3, file_path=str(published.path), sha256=published.sha256
    )
    frozen = storage.freeze_inputs(7, [asset])
    assert frozen[0].path.read_bytes() == b"original"
    assert frozen[0].path != published.path
    published.path.chmod(0o600)
    published.path.write_bytes(b"tampered")
    with pytest.raises(AppError):
        storage.freeze_inputs(8, [asset])
