"""
Tests for S3DataManager.

All tests use mocked boto3 — no real AWS credentials required.
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_boto3_client():
    """Create a mock boto3 S3 client."""
    client = MagicMock()
    # Default: head_object returns a 1 KB object
    client.head_object.return_value = {"ContentLength": 1024}
    return client


@pytest.fixture
def s3_manager(tmp_path, mock_boto3_client):
    """
    Create an S3DataManager backed by a mock client and a temp directory.

    Works even when boto3 is not installed by patching HAS_BOTO3 and
    injecting the mock client directly.
    """
    import database.ingestion.s3_data_manager as mod

    # Create a mock boto3 module
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_boto3_client

    with patch.object(mod, "HAS_BOTO3", True), \
         patch.object(mod, "boto3", mock_boto3, create=True):
        manager = mod.S3DataManager(
            bucket="test-bucket",
            prefix="data/",
            local_dir=str(tmp_path / "kg-data"),
        )
        # Expose the mock for assertions
        manager._mock_client = mock_boto3_client
        yield manager


# ---------------------------------------------------------------------------
# Download tests
# ---------------------------------------------------------------------------

class TestDownload:
    """Tests for S3DataManager.download()."""

    def test_download_creates_local_file(self, s3_manager, tmp_path):
        """download() should call download_file and return the local path."""
        mock_client = s3_manager._mock_client

        # Simulate download_file writing a file
        def fake_download(Bucket, Key, Filename, Callback=None):
            Path(Filename).parent.mkdir(parents=True, exist_ok=True)
            Path(Filename).write_bytes(b"x" * 1024)

        mock_client.download_file.side_effect = fake_download

        result = s3_manager.download("hmdb/hmdb_metabolites.xml")

        assert result.endswith("hmdb_metabolites.xml")
        assert Path(result).exists()
        mock_client.head_object.assert_called_once_with(
            Bucket="test-bucket", Key="data/hmdb/hmdb_metabolites.xml"
        )
        mock_client.download_file.assert_called_once()

    def test_download_skips_when_file_matches_size(self, s3_manager, tmp_path):
        """download() should skip if local file exists with matching size."""
        mock_client = s3_manager._mock_client
        mock_client.head_object.return_value = {"ContentLength": 512}

        # Pre-create local file with matching size
        local_dir = Path(s3_manager.local_dir) / "hmdb"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_file = local_dir / "hmdb_metabolites.xml"
        local_file.write_bytes(b"x" * 512)

        result = s3_manager.download("hmdb/hmdb_metabolites.xml")

        assert result == str(local_file)
        mock_client.download_file.assert_not_called()

    def test_download_redownloads_when_size_mismatch(self, s3_manager, tmp_path):
        """download() should re-download if local file size does not match remote."""
        mock_client = s3_manager._mock_client
        mock_client.head_object.return_value = {"ContentLength": 2048}

        # Pre-create local file with different size
        local_dir = Path(s3_manager.local_dir) / "card"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_file = local_dir / "card.json"
        local_file.write_bytes(b"x" * 100)

        def fake_download(Bucket, Key, Filename, Callback=None):
            Path(Filename).write_bytes(b"y" * 2048)

        mock_client.download_file.side_effect = fake_download

        result = s3_manager.download("card/card.json")

        mock_client.download_file.assert_called_once()
        assert Path(result).stat().st_size == 2048

    def test_download_raises_on_missing_key(self, s3_manager):
        """download() should raise FileNotFoundError for missing S3 keys."""
        # Import ClientError from botocore if available, otherwise create a mock exception
        try:
            from botocore.exceptions import ClientError as BotoClientError
        except ImportError:
            # botocore not installed; mock the ClientError used by the module
            import database.ingestion.s3_data_manager as mod
            BotoClientError = mod.ClientError

        s3_manager._mock_client.head_object.side_effect = BotoClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )

        with pytest.raises(FileNotFoundError, match="S3 object not found"):
            s3_manager.download("nonexistent/file.xml")


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------

class TestCleanup:
    """Tests for S3DataManager.cleanup()."""

    def test_cleanup_removes_file(self, s3_manager, tmp_path):
        """cleanup() should delete the local file."""
        temp_file = tmp_path / "to_delete.xml"
        temp_file.write_text("data")

        s3_manager.cleanup(str(temp_file))

        assert not temp_file.exists()

    def test_cleanup_handles_missing_file(self, s3_manager, tmp_path):
        """cleanup() should not raise if the file is already gone."""
        missing_path = str(tmp_path / "does_not_exist.xml")
        # Should not raise
        s3_manager.cleanup(missing_path)


# ---------------------------------------------------------------------------
# list_files tests
# ---------------------------------------------------------------------------

class TestListFiles:
    """Tests for S3DataManager.list_files()."""

    def test_list_files_returns_relative_keys(self, s3_manager):
        """list_files() should return keys relative to the configured prefix."""
        mock_client = s3_manager._mock_client
        paginator = MagicMock()
        mock_client.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "data/hmdb/hmdb_metabolites.xml"},
                    {"Key": "data/card/card.json"},
                ]
            }
        ]

        result = s3_manager.list_files()

        assert result == ["hmdb/hmdb_metabolites.xml", "card/card.json"]

    def test_list_files_with_subprefix(self, s3_manager):
        """list_files(prefix) should filter within the given sub-prefix."""
        mock_client = s3_manager._mock_client
        paginator = MagicMock()
        mock_client.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "data/hmdb/hmdb_metabolites.xml"},
                ]
            }
        ]

        result = s3_manager.list_files("hmdb/")

        paginator.paginate.assert_called_with(
            Bucket="test-bucket", Prefix="data/hmdb/"
        )
        assert result == ["hmdb/hmdb_metabolites.xml"]

    def test_list_files_empty_bucket(self, s3_manager):
        """list_files() should return empty list for empty prefix."""
        mock_client = s3_manager._mock_client
        paginator = MagicMock()
        mock_client.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{}]

        result = s3_manager.list_files()

        assert result == []


# ---------------------------------------------------------------------------
# Upload tests
# ---------------------------------------------------------------------------

class TestUpload:
    """Tests for S3DataManager.upload()."""

    def test_upload_sends_file_to_s3(self, s3_manager, tmp_path):
        """upload() should call upload_file with the correct bucket and key."""
        local_file = tmp_path / "test.json"
        local_file.write_text('{"key": "value"}')

        result = s3_manager.upload(str(local_file), "card/card.json")

        assert result == "s3://test-bucket/data/card/card.json"
        s3_manager._mock_client.upload_file.assert_called_once()
        call_kwargs = s3_manager._mock_client.upload_file.call_args
        assert call_kwargs.kwargs["Bucket"] == "test-bucket"
        assert call_kwargs.kwargs["Key"] == "data/card/card.json"

    def test_upload_raises_on_missing_file(self, s3_manager):
        """upload() should raise FileNotFoundError for missing local files."""
        with pytest.raises(FileNotFoundError, match="Local file not found"):
            s3_manager.upload("/nonexistent/file.xml", "hmdb/hmdb.xml")


# ---------------------------------------------------------------------------
# boto3 unavailability test
# ---------------------------------------------------------------------------

class TestBoto3Unavailable:
    """Tests for graceful handling when boto3 is not installed."""

    def test_import_error_when_boto3_missing(self, tmp_path):
        """S3DataManager should raise ImportError if boto3 is not available."""
        import database.ingestion.s3_data_manager as mod

        original_has = mod.HAS_BOTO3
        try:
            mod.HAS_BOTO3 = False
            with pytest.raises(ImportError, match="boto3 is required"):
                mod.S3DataManager(
                    bucket="test-bucket",
                    local_dir=str(tmp_path),
                )
        finally:
            mod.HAS_BOTO3 = original_has
