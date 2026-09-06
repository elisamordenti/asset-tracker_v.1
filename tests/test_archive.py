"""No `supabase` import anywhere in this file -- SupabaseStorageArchive is
tested purely against a fake raw client, same pattern as test_database.py."""

from compliance_tracker.archive import LocalDiskArchive, SupabaseStorageArchive


def test_local_disk_archive_list_filenames_empty_for_missing_directory(tmp_path):
    archive = LocalDiskArchive()
    assert archive.list_filenames(str(tmp_path / "no_such_dir")) == set()


def test_local_disk_archive_upload_then_list_and_read(tmp_path):
    archive = LocalDiskArchive()
    directory = str(tmp_path / "docs")

    archive.upload(directory, "AST-1_permit.pdf", b"%PDF-1.4")

    assert archive.list_filenames(directory) == {"AST-1_permit.pdf"}
    assert archive.read(directory, "AST-1_permit.pdf") == b"%PDF-1.4"


def test_local_disk_archive_delete_removes_file(tmp_path):
    archive = LocalDiskArchive()
    directory = str(tmp_path / "docs")
    archive.upload(directory, "scan.pdf", b"data")

    archive.delete(directory, "scan.pdf")

    assert archive.list_filenames(directory) == set()


def test_local_disk_archive_delete_missing_file_is_noop(tmp_path):
    archive = LocalDiskArchive()
    directory = str(tmp_path / "docs")
    archive.delete(directory, "nope.pdf")  # must not raise


class FakeBucket:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def list(self, directory):
        prefix = f"{directory}/"
        return [{"name": key[len(prefix):]} for key in self.files if key.startswith(prefix)]

    def upload(self, path, content, options=None):
        self.files[path] = content

    def download(self, path):
        return self.files[path]

    def remove(self, paths):
        for path in paths:
            self.files.pop(path, None)


class FakeStorage:
    def __init__(self):
        self._bucket = FakeBucket()

    def from_(self, bucket_name):
        return self._bucket


class FakeSupabaseClient:
    def __init__(self):
        self.storage = FakeStorage()


def test_supabase_storage_archive_upload_then_list_and_read():
    raw = FakeSupabaseClient()
    archive = SupabaseStorageArchive(raw, bucket="documents")

    archive.upload("energy_assets_documents", "AST-1_permit.pdf", b"%PDF-1.4")

    assert archive.list_filenames("energy_assets_documents") == {"AST-1_permit.pdf"}
    assert archive.read("energy_assets_documents", "AST-1_permit.pdf") == b"%PDF-1.4"


def test_supabase_storage_archive_list_filenames_empty_for_unused_directory():
    raw = FakeSupabaseClient()
    archive = SupabaseStorageArchive(raw, bucket="documents")

    assert archive.list_filenames("nothing_here") == set()


def test_supabase_storage_archive_delete_removes_file():
    raw = FakeSupabaseClient()
    archive = SupabaseStorageArchive(raw, bucket="documents")
    archive.upload("docs", "scan.pdf", b"data")

    archive.delete("docs", "scan.pdf")

    assert archive.list_filenames("docs") == set()
