"""Checkpoint SQLite e backup facoltativi nel GitHub privato già in uso."""
from __future__ import annotations
import base64
import hashlib
import io
import json
import re
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import turbo_scraper as core

MAX_ARCHIVE = 15 * 1024 * 1024
MAX_EXTRACTED = 180 * 1024 * 1024
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
NAME_PATTERN = re.compile(r"^web_[0-9a-f]{12}_[a-z]+_p[0-9]+_[a-z0-9_]+_[0-9a-f]{10}$")

def user_tag(username: str) -> str:
    return hashlib.sha256(username.encode("utf-8")).hexdigest()[:16]

def make_checkpoint(base: Path, dest: Path, username: str, plan_id: str) -> bytes:
    """Backup online sicuro anche con SQLite WAL."""
    if not NAME_PATTERN.fullmatch(plan_id):
        raise ValueError("Identificativo piano non valido")
    if not (base / "progress.sqlite3").exists():
        raise ValueError("Non esiste uno stato di ricerca da salvare")
    manifest = {"version": 1, "user_tag": user_tag(username), "plan_id": plan_id,
                "created": datetime.now(timezone.utc).isoformat()}
    with tempfile.TemporaryDirectory() as tmp:
        tempdb = Path(tmp) / "progress.sqlite3"
        src = sqlite3.connect(str(base / "progress.sqlite3"), timeout=10)
        dst = sqlite3.connect(str(tempdb))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.write(tempdb, "progress.sqlite3")
            for fname in ("piano.json", "categories.txt", "attivita_originali.csv"):
                path = base / fname
                if path.is_file():
                    archive.write(path, fname)
            if (base / "locations").is_dir():
                for path in sorted((base / "locations").glob("*.json")):
                    archive.write(path, "locations/" + path.name)
            if dest.is_file():
                archive.write(dest, "result.csv")
            reject = dest.with_name(dest.stem + "_scartati.csv")
            if reject.is_file():
                archive.write(reject, "excluded.csv")
        payload = buffer.getvalue()
    if len(payload) > MAX_ARCHIVE:
        raise ValueError("Backup oltre 15 MB. Scarica il CSV e suddividi la ricerca.")
    return payload

def restore_checkpoint(payload: bytes, username: str, *, expected_id: str | None = None) -> dict:
    """Ripristina solo un backup valido per l'utente e il piano."""
    if len(payload) > MAX_ARCHIVE:
        raise ValueError("Backup oltre 15 MB")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        if len(infos) > 400 or sum(i.file_size for i in infos) > MAX_EXTRACTED:
            raise ValueError("Backup non valido: dimensioni eccessive")
        if len({x.filename for x in infos}) != len(infos):
            raise ValueError("Backup non valido: file duplicati")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        plan_id = manifest.get("plan_id", "")
        if (manifest.get("version") != 1 or manifest.get("user_tag") != user_tag(username)
                or not NAME_PATTERN.fullmatch(plan_id)
                or (expected_id is not None and expected_id != plan_id)):
            raise ValueError("Backup non valido per utente o piano")
        names = {x.filename for x in infos}
        if "progress.sqlite3" not in names:
            raise ValueError("Database non presente")
        allowed = {"manifest.json", "progress.sqlite3", "categories.txt", "piano.json",
                   "attivita_originali.csv", "result.csv", "excluded.csv"}
        for info in infos:
            if info.is_dir() or (
                info.filename not in allowed and
                not re.fullmatch(r"locations/[A-Za-z0-9_-]+\.json", info.filename)
            ):
                raise ValueError("File non ammesso nel backup")
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            for info in infos:
                if info.filename == "manifest.json":
                    continue
                destfile = stage / info.filename
                destfile.parent.mkdir(parents=True, exist_ok=True)
                destfile.write_bytes(archive.read(info.filename))
            with sqlite3.connect(str(stage / "progress.sqlite3")) as conn:
                if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Stato SQLite danneggiato")
            base = core.STATE / plan_id
            out = core.RESULTS / (plan_id + ".csv")
            base.mkdir(parents=True, exist_ok=True)
            out.parent.mkdir(parents=True, exist_ok=True)
            for info in infos:
                name = info.filename
                if name == "manifest.json":
                    continue
                if name == "result.csv":
                    target = out
                elif name == "excluded.csv":
                    target = out.with_name(out.stem + "_scartati.csv")
                else:
                    target = base / name
                target.parent.mkdir(parents=True, exist_ok=True)
                tmpfile = target.with_name(target.name + ".restore_tmp")
                tmpfile.write_bytes((stage / name).read_bytes())
                tmpfile.replace(target)
    return manifest

class GitHubStorage:
    """GitHub REST: rifiuta esplicitamente repository pubblici."""
    def __init__(self, repo: str, token: str, branch: str = "main"):
        if not REPO_PATTERN.fullmatch(repo or "") or not token:
            raise ValueError("Configura repository e token del backup")
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", branch):
            raise ValueError("Branch non valido")
        self.repo, self.token, self.branch = repo, token, branch
        self.verified = False

    def _request(self, method: str, endpoint: str, payload=None):
        req = urllib.request.Request(
            "https://api.github.com" + endpoint,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Accept": "application/vnd.github+json",
                     "Authorization": "Bearer " + self.token,
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "TurboLeads-Private-Backup",
                     "Content-Type": "application/json"},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read(24 * 1024 * 1024))
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                raise FileNotFoundError("Backup inesistente o token senza accesso") from None
            if ex.code == 403:
                raise RuntimeError("GitHub: controlla autorizzazioni del token o limiti API") from None
            raise RuntimeError("GitHub HTTP " + str(ex.code)) from None

    def verify_private(self):
        repo_data = self._request("GET", "/repos/" + self.repo)
        if repo_data.get("private") is not True:
            raise RuntimeError("Backup bloccato: il repository deve essere PRIVATO")
        self.verified = True

    def _path(self, username, plan_id):
        if not NAME_PATTERN.fullmatch(plan_id):
            raise ValueError("Piano non valido")
        return "backups/" + user_tag(username) + "/" + plan_id + ".zip"

    def _url(self, path):
        return "/repos/" + self.repo + "/contents/" + path

    def _sha(self, path):
        try:
            data = self._request("GET", self._url(path) + "?ref=" +
                                 urllib.parse.quote(self.branch, safe=""))
            return data.get("sha")
        except FileNotFoundError:
            return None

    def save(self, username, plan_id, checkpoint):
        if not self.verified:
            self.verify_private()
        path = self._path(username, plan_id)
        payload = {
            "message": "backup stato TURBO " + plan_id,
            "branch": self.branch,
            "content": base64.b64encode(checkpoint).decode("ascii"),
        }
        old_sha = self._sha(path)
        if old_sha:
            payload["sha"] = old_sha
        self._request("PUT", self._url(path), payload)

    def load(self, username, plan_id):
        if not self.verified:
            self.verify_private()
        path = self._path(username, plan_id)
        try:
            data = self._request("GET", self._url(path) + "?ref=" +
                                 urllib.parse.quote(self.branch, safe=""))
        except FileNotFoundError:
            return None
        if data.get("encoding") == "base64" and data.get("content"):
            result = base64.b64decode(data["content"])
        else:
            blob = self._request("GET", "/repos/" + self.repo + "/git/blobs/" + data["sha"])
            result = base64.b64decode(blob["content"])
        if len(result) > MAX_ARCHIVE:
            raise ValueError("Backup remoto troppo grande")
        return result

    def list_plans(self, username):
        if not self.verified:
            self.verify_private()
        path = "backups/" + user_tag(username)
        try:
            entries = self._request("GET", self._url(path) + "?ref=" +
                                    urllib.parse.quote(self.branch, safe=""))
        except FileNotFoundError:
            return []
        return [e["name"][:-4] for e in entries
                if e.get("type") == "file" and e.get("name", "").endswith(".zip")
                and NAME_PATTERN.fullmatch(e["name"][:-4])]
