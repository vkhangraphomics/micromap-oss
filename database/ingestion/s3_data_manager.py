"""
S3 Data Manager for MicroMap Knowledge Graph data loading.

Provides download-on-demand access to raw data files stored in S3,
with local caching based on file size matching.

Usage:
    manager = S3DataManager(bucket="graphomics-kg-data", prefix="data/")
    local_path = manager.download("hmdb/hmdb_metabolites.xml")
    # ... use file ...
    manager.cleanup(local_path)
"""

import logging
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    boto3 = None  # type: ignore[assignment]
    HAS_BOTO3 = False

    class ClientError(Exception):  # type: ignore[no-redef]
        """Stub when botocore is not available."""
        pass


class S3DataManager:
    """
    Manages downloading and uploading knowledge graph data files to/from S3.

    Uses IAM role credentials on EC2 or local AWS credentials for development.
    Skips downloads when a local file already exists with matching size.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "data/",
        local_dir: str = "/tmp/kg-data",
        region: str = "us-east-1",
    ):
        """
        Initialize S3DataManager.

        Args:
            bucket: S3 bucket name
            prefix: Key prefix for all data files (e.g. "data/")
            local_dir: Local directory for downloaded files
            region: AWS region
        """
        if not HAS_BOTO3:
            raise ImportError(
                "boto3 is required for S3 data management. "
                "Install it with: pip install boto3"
            )

        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self.local_dir = Path(local_dir)
        self.local_dir.mkdir(parents=True, exist_ok=True)

        self._client = boto3.client("s3", region_name=region)
        logger.info(
            f"S3DataManager initialized: bucket={bucket}, prefix={self.prefix}"
        )

    def _full_key(self, s3_key: str) -> str:
        """Build the full S3 key by prepending the configured prefix."""
        return f"{self.prefix}{s3_key}"

    def _local_path_for(self, s3_key: str) -> Path:
        """Return the local file path corresponding to an S3 key."""
        return self.local_dir / s3_key

    def _remote_size(self, full_key: str) -> Optional[int]:
        """Get the size of an object in S3, or None if it does not exist."""
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=full_key)
            return resp["ContentLength"]
        except ClientError:
            return None

    def download(self, s3_key: str, local_dir: Optional[str] = None) -> str:
        """
        Download a file from S3 to the local filesystem.

        Skips the download if a local file already exists with the same size
        as the remote object.

        Args:
            s3_key: Relative key within the configured prefix
                    (e.g. "hmdb/hmdb_metabolites.xml")
            local_dir: Override the default local directory

        Returns:
            Absolute path to the downloaded local file

        Raises:
            FileNotFoundError: If the key does not exist in S3
        """
        full_key = self._full_key(s3_key)
        dest_dir = Path(local_dir) if local_dir else self.local_dir
        local_path = dest_dir / s3_key

        # Ensure parent directory exists
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Check remote size
        remote_size = self._remote_size(full_key)
        if remote_size is None:
            raise FileNotFoundError(
                f"S3 object not found: s3://{self.bucket}/{full_key}"
            )

        # Skip if local file matches remote size
        if local_path.is_file() and local_path.stat().st_size == remote_size:
            logger.info(
                f"Skipping download, local file matches remote size "
                f"({remote_size} bytes): {local_path}"
            )
            return str(local_path)

        # Download with progress logging
        logger.info(
            f"Downloading s3://{self.bucket}/{full_key} "
            f"({remote_size / (1024 * 1024):.1f} MB) -> {local_path}"
        )

        callback = _ProgressCallback(remote_size, s3_key)
        self._client.download_file(
            Bucket=self.bucket,
            Key=full_key,
            Filename=str(local_path),
            Callback=callback,
        )

        logger.info(f"Download complete: {local_path}")
        return str(local_path)

    def upload(self, local_path: str, s3_key: str) -> str:
        """
        Upload a local file to S3.

        Args:
            local_path: Path to the local file
            s3_key: Relative key within the configured prefix

        Returns:
            Full S3 URI of the uploaded object

        Raises:
            FileNotFoundError: If the local file does not exist
        """
        src = Path(local_path)
        if not src.is_file():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        full_key = self._full_key(s3_key)
        file_size = src.stat().st_size

        logger.info(
            f"Uploading {local_path} ({file_size / (1024 * 1024):.1f} MB) "
            f"-> s3://{self.bucket}/{full_key}"
        )

        callback = _ProgressCallback(file_size, s3_key)
        self._client.upload_file(
            Filename=str(src),
            Bucket=self.bucket,
            Key=full_key,
            Callback=callback,
        )

        s3_uri = f"s3://{self.bucket}/{full_key}"
        logger.info(f"Upload complete: {s3_uri}")
        return s3_uri

    def list_files(self, prefix: str = "") -> List[str]:
        """
        List files in S3 under the given prefix.

        Args:
            prefix: Additional prefix to filter within the configured base
                    prefix (e.g. "hmdb/")

        Returns:
            List of S3 keys (relative to the configured base prefix)
        """
        full_prefix = self._full_key(prefix)
        keys: List[str] = []

        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                # Strip the base prefix to return relative keys
                relative_key = obj["Key"]
                if self.prefix and relative_key.startswith(self.prefix):
                    relative_key = relative_key[len(self.prefix):]
                keys.append(relative_key)

        return keys

    def cleanup(self, local_path: str) -> None:
        """
        Remove a previously downloaded file from local disk.

        Args:
            local_path: Path to the local file to remove
        """
        path = Path(local_path)
        if path.is_file():
            path.unlink()
            logger.info(f"Cleaned up local file: {local_path}")
        else:
            logger.debug(f"Cleanup skipped, file not found: {local_path}")


class _ProgressCallback:
    """Log download/upload progress for large files."""

    def __init__(self, total_size: int, name: str):
        self._total = total_size
        self._name = name
        self._transferred = 0
        self._last_logged_pct = -10  # log every ~10%

    def __call__(self, bytes_transferred: int):
        self._transferred += bytes_transferred
        if self._total > 0:
            pct = int(self._transferred / self._total * 100)
            if pct >= self._last_logged_pct + 10:
                self._last_logged_pct = pct
                logger.info(
                    f"  {self._name}: {pct}% "
                    f"({self._transferred / (1024 * 1024):.1f} / "
                    f"{self._total / (1024 * 1024):.1f} MB)"
                )
