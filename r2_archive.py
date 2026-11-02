"""Cloudflare R2 durable snapshots. No credentials are committed to GitHub.

The SQLite database is authoritative. Each backup is an immutable object.
Only after a successful upload do we move the small JSON manifest pointer.
A failed upload never replaces the last known-good snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class StorageError(RuntimeError):
    pass


def user_key(username: str) -> str:
    return hashlib.sha256(username.encode("utf-8")).hexdigest()[:24]


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", value):
        raise StorageError("Identificativo archivio non valido")
    return value


class R2Archive:
    def __init__(self, config: dict):
        self.bucket = str(config.get("bucket", "")).strip()
        endpoint = str(config.get("endpoint", "")).rstrip("/")
        access = str(config.get("access_key_id", ""))
        secret = str(config.get("secret_access_key", ""))
        prefix = str(config.get("prefix", "turbo-leads-v1")).strip("/")
        if not (self.bucket and endpoint.startswith("https://")
                and endpoint.endswith(".r2.cloudflarestorage.com")
                and access and secret):
            raise StorageError("Imposta endpoint, bucket e chiavi R2 nei Secrets")
        if not re.fullmatch(r"[A-Za-z0-9/_.-]{1,100}", prefix) or ".." in prefix:
            raise StorageError("Prefisso R2 non valido")
        self.prefix = prefix
        self.client = boto3.client(
            "s3", endpoint_url=endpoint,
            aws_access_key_id=access, aws_secret_access_key=secret,
            region_name="auto",
            config=Config(signature_version="s3v4",
                          retries={"max_attempts": 3, "mode": "standard"},
                          connect_timeout=10, read_timeout=45),
        )
        self._lock = threading.RLock()

    def probe(self) -> None:
        try:
            self.client.list_objects_v2(Bucket=self.bucket,
                                        Prefix=self.prefix + '/', MaxKeys=1)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError("Connessione R2 non riuscita. Verifica bucket e permessi.") from exc

    def _user_prefix(self, username: str) -> str:
        return f"{self.prefix}/users/{user_key(username)}"

    def _job_prefix(self, username: str, job_id: str) -> str:
        return f"{self._user_prefix(username)}/jobs/{_safe_id(job_id)}"

    def _get_json(self, key: str) -> dict | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                return None
            raise StorageError("Lettura archivio R2 non riuscita") from exc
        except (BotoCoreError, ValueError, UnicodeDecodeError) as exc:
            raise StorageError("Archivio R2 non leggibile") from exc

    def _put_json(self, key: str, value: dict) -> None:
        try:
            self.client.put_object(Bucket=self.bucket, Key=key,
                                   Body=json.dumps(value, ensure_ascii=False).encode("utf-8"),
                                   ContentType="application/json")
        except (ClientError, BotoCoreError) as exc:
            raise StorageError("Salvataggio su R2 non riuscito") from exc

    def save_profile(self, username: str, prefs: dict) -> None:
        key = f"{self._user_prefix(username)}/preferences.json"
        self._put_json(key, prefs)

    def load_profile(self, username: str) -> dict:
        obj = self._get_json(f"{self._user_prefix(username)}/preferences.json")
        return obj if isinstance(obj, dict) else {}

    def list_jobs(self, username: str, maximum: int = 100) -> list[dict]:
        prefix = self._user_prefix(username) + "/jobs/"
        result = []
        token = None
        try:
            while True:
                opts = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
                if token:
                    opts["ContinuationToken"] = token
                page = self.client.list_objects_v2(**opts)
                for entry in page.get("Contents", []):
                    key = entry["Key"]
                    if key.endswith("/manifest.json"):
                        metadata = self._get_json(key)
                        if isinstance(metadata, dict) and metadata.get("id"):
                            result.append(metadata)
                            if len(result) >= maximum:
                                return sorted(result, key=lambda x: x.get("updated_at", ""), reverse=True)
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
                if not token:
                    break
        except (ClientError, BotoCoreError) as exc:
            raise StorageError("Impossibile elencare le ricerche salvate") from exc
        return sorted(result, key=lambda x: x.get("updated_at", ""), reverse=True)

    def get_manifest(self, username: str, job_id: str) -> dict | None:
        return self._get_json(self._job_prefix(username, job_id) + "/manifest.json")

    def save_checkpoint(self, username: str, job_id: str, db_path: Path,
                        metadata: dict) -> dict:
        if not db_path.exists():
            raise StorageError("Database locale non presente: nessun dato da salvare")
        prefix = self._job_prefix(username, job_id)
        # Versioned keys ensure an interrupted upload doesn't destroy the prior backup.
        new_key = f"{prefix}/snapshots/{int(time.time())}-{uuid4().hex}.sqlite3"
        manifest_key = prefix + "/manifest.json"
        with self._lock:
            old = self._get_json(manifest_key)
            with tempfile.TemporaryDirectory() as directory:
                backup = Path(directory) / "checkpoint.sqlite3"
                try:
                    with sqlite3.connect(str(db_path), timeout=30) as src:
                        with sqlite3.connect(str(backup)) as dest:
                            src.backup(dest, pages=100, sleep=0.05)
                    with sqlite3.connect(str(backup)) as verify:
                        check = verify.execute("PRAGMA quick_check").fetchone()
                    if not check or check[0] != "ok":
                        raise StorageError("Copia SQLite non valida: checkpoint non salvato")
                    self.client.upload_file(str(backup), self.bucket, new_key)
                except (sqlite3.Error, OSError, ClientError, BotoCoreError) as exc:
                    raise StorageError("Impossibile eseguire il backup SQLite su R2") from exc
            payload = dict(metadata)
            payload.update({"id": job_id, "db_key": new_key,
                            "updated_at": int(time.time()), "schema": 1})
            try:
                self._put_json(manifest_key, payload)
            except StorageError:
                # Un oggetto orfano può restare, ma il manifesto resta sul backup precedente.
                raise
            old_key = old.get("db_key") if isinstance(old, dict) else None
            if old_key and old_key != new_key and old_key.startswith(prefix + "/snapshots/"):
                try:
                    self.client.delete_object(Bucket=self.bucket, Key=old_key)
                except (ClientError, BotoCoreError):
                    pass
            return payload

    def restore_checkpoint(self, username: str, job_id: str, db_path: Path) -> dict:
        manifest = self.get_manifest(username, job_id)
        if not manifest or manifest.get("id") != job_id:
            raise StorageError("Nessuna ricerca salvata con questo identificativo")
        key = str(manifest.get("db_key", ""))
        if not key.startswith(self._job_prefix(username, job_id) + "/snapshots/"):
            raise StorageError("Il riferimento al backup non è valido")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = db_path.with_suffix(".download.tmp")
        try:
            self.client.download_file(self.bucket, key, str(tmp))
            with sqlite3.connect(str(tmp)) as test:
                valid = test.execute("PRAGMA quick_check").fetchone()
            if not valid or valid[0] != "ok":
                raise StorageError("Backup danneggiato, non ripristinato")
            os.replace(tmp, db_path)
        except (ClientError, BotoCoreError, sqlite3.Error, OSError) as exc:
            raise StorageError("Ripristino del database R2 non riuscito") from exc
        finally:
            tmp.unlink(missing_ok=True)
        return manifest
