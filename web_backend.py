"""Backend del pannello web. Niente Streamlit nelle funzioni di lavoro.

Riusa il motore e il filtro della versione TURBO V3; tutte le ricerche
vengono eseguite nel processo del server, con lotto limitato e stop manuale.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import turbo_scraper as core
from r2_archive import R2Archive, StorageError
from durable_storage import GitHubStorage, make_checkpoint, restore_checkpoint


MAX_CATEGORIES = 30
MAX_SEARCHES = 250
MAX_WORKERS = 4
MAX_PAGES = 10


def parse_geography(dataset: list[dict]) -> list[dict]:
    rows = []
    for r in dataset:
        prov = r.get('provincia') or {}
        regione = r.get('regione') or {}
        zona = r.get('zona') or {}
        name = str(r.get('nome') or '').strip()
        sigla = str(prov.get('sigla') or r.get('sigla') or '').upper()
        if not name or not sigla:
            continue
        rows.append({
            'name': name,
            'prov': sigla,
            'province_name': str(prov.get('nome') or sigla).strip(),
            'region': str(regione.get('nome') or '').strip(),
            'zone': str(zona.get('nome') or '').strip(),
            'population': int(r.get('popolazione') or 0),
        })
    rows.sort(key=lambda x: (-x['population'], x['name'], x['prov']))
    return rows


def select_geography(rows: list[dict], *, mode: str, zones: list[str] | None = None,
                     regions: list[str] | None = None, provinces: list[str] | None = None,
                     cities: list[str] | None = None) -> list[dict]:
    """In selezione mista, unisce senza duplicati tutte le scelte."""
    zones = set(zones or [])
    regions = set(regions or [])
    provinces = set(provinces or [])
    cities = set(cities or [])  # Nome (SIGLA): evita ambiguità per omonimi
    accepted = []
    for r in rows:
        label = f"{r['name']} ({r['prov']})"
        criteria = {
            'Tutta Italia': True,
            'Zone': r['zone'] in zones,
            'Regioni': r['region'] in regions,
            'Province': r['prov'] in provinces,
            'Comuni': label in cities,
            'Selezione mista': (r['zone'] in zones or r['region'] in regions
                                or r['prov'] in provinces or label in cities),
        }
        if criteria.get(mode, False):
            accepted.append({k: r[k] for k in ('name', 'prov', 'region', 'population')})
    return accepted


def build_categories(presets: list[str], manual: str) -> list[str]:
    categories = list(dict.fromkeys(
        [x.strip() for x in presets if x.strip()]
        + core.parse_items(manual)
    ))
    if not categories or len(categories) > MAX_CATEGORIES:
        raise ValueError(f'Seleziona da 1 a {MAX_CATEGORIES} categorie.')
    if any(len(c) > 70 for c in categories):
        raise ValueError('Ogni categoria deve avere al massimo 70 caratteri.')
    return categories


def safe_csv_bytes(path: Path) -> bytes:
    """CSV Excel-friendly, evita che nomi/recapiti siano formule attive."""
    if not path.exists():
        return b''
    try:
        with path.open('r', encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f, delimiter=';'))
    except (UnicodeError, csv.Error, OSError):
        return b''
    out = io.StringIO(newline='')
    writer = csv.writer(out, delimiter=';')
    for row in rows:
        writer.writerow(["'" + s if s.lstrip().startswith(('=', '+', '-', '@', '\t')) else s
                         for s in row])
    return ('\ufeff' + out.getvalue()).encode('utf-8')


@dataclass
class Job:
    user: str
    id: str
    name: str
    base: Path
    dest: Path
    categories: list[str]
    search_categories: list[str]
    depth: str
    exclude_chains: bool
    extra_brands: list[str]
    search_count: int
    n_cities: int
    deep_local: bool
    selected_cities: list[dict]
    filter_mode: str
    workers: int
    pages: int
    batch: int
    status: str = 'In coda'
    error: str = ''
    done: bool = False
    found: int = 0
    rejected: int = 0
    raw_count: int = 0
    started: float = field(default_factory=time.time)
    cloud_saved_at: int = 0
    cloud_error: str = ''
    github_saved_at: int = 0
    github_error: str = ''
    ended: float = 0
    process: subprocess.Popen | None = field(default=None, repr=False)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    log_file: Path | None = None


class JobManager:
    def __init__(self, archive: R2Archive | None = None,
                 storage: GitHubStorage | None = None):
        self.archive = archive
        self.storage = storage
        self.lock = threading.RLock()
        self.jobs: dict[str, Job] = {}
        self.active_id: str | None = None  # Un job alla volta: hosting gratuito e file condivisi

    def user_job(self, user: str) -> Job | None:
        with self.lock:
            return self.jobs.get(user)

    def submit(self, *, user: str, cities: list[dict], categories: list[str],
               batch: int, workers: int, pages: int, filter_mode: str,
               depth: str = 'Equilibrata', exclude_chains: bool = True,
               extra_brands: list[str] | None = None) -> Job:
        if not (1 <= batch <= MAX_SEARCHES and 1 <= workers <= MAX_WORKERS
                and 1 <= pages <= MAX_PAGES and filter_mode in ('rigoroso', 'ampio')):
            raise ValueError('Impostazioni fuori dai limiti del piano gratuito.')
        if not cities or len(categories) < 1:
            raise ValueError('Seleziona almeno un comune e una categoria.')
        if depth not in ('Veloce', 'Equilibrata', 'Approfondita'):
            raise ValueError('Modalita non valida')
        extra_brands = core.parse_items(','.join(extra_brands or []))
        if len(extra_brands) > 80 or any(len(b) > 70 for b in extra_brands):
            raise ValueError('Troppi marchi personalizzati')
        with self.lock:
            if self.active_id:
                raise RuntimeError('Una ricerca è già in esecuzione. Attendi o arrestala prima di avviarne un’altra.')
            # Cartelle per utente: ricerche uguali di utenti diversi non si sovrascrivono.
            user_slug = hashlib.sha256(user.encode()).hexdigest()[:12]
            search_categories = core.search_variants(categories, depth)
            deep_local = len(cities) == 1 and len(categories) == 1
            base, dest = core.make_plan(cities, search_categories, f'web_{user_slug}_{depth.lower()}_p{pages}')
            job = Job(user=user, id=dest.stem, name=dest.stem,
                      base=base, dest=dest, categories=categories,
                      search_categories=search_categories, depth=depth,
                      exclude_chains=exclude_chains, extra_brands=extra_brands,
                      search_count=len(cities)*len(search_categories), n_cities=len(cities),
                      deep_local=deep_local, selected_cities=list(cities),
                      filter_mode=filter_mode, batch=batch, workers=workers, pages=pages,
                      log_file=base/'motore_web.log')
            if self.archive and not (base / 'progress.sqlite3').exists():
                # Il nuovo processo riprende da R2, mai da un database vuoto
                # se per questo piano esiste già un checkpoint remoto.
                previous = self.archive.get_manifest(user, job.id)
                if previous:
                    manifest = self.archive.restore_checkpoint(
                        user, job.id, base / 'progress.sqlite3')
                    restore_raw_from_database(base / 'progress.sqlite3',
                                              base / 'attivita_originali.csv')
                    job.cloud_saved_at = int(manifest.get('updated_at', 0))
            if self.storage is not None and not (base / 'progress.sqlite3').exists():
                # Ripristina un checkpoint già presente sul repository privato.
                # Se GitHub non risponde, interrompe l'avvio senza ricominciare
                # per errore da zero.
                saved_blob = self.storage.load(user, job.id)
                if saved_blob is not None:
                    restore_checkpoint(saved_blob, user, expected_id=job.id)
                    job.github_saved_at = int(time.time())
            self.jobs[user] = job
            self.active_id = job.id
            thread = threading.Thread(target=self._run, args=(job,), daemon=True,
                                      name=f"turbo-{user_slug}")
            thread.start()
            return job

    def archived_jobs(self, user: str) -> list[dict]:
        return self.archive.list_jobs(user) if self.archive else []

    def restore_saved(self, user: str, job_id: str) -> Job:
        if not self.archive:
            raise StorageError('Cloudflare R2 non configurato.')
        with self.lock:
            if self.active_id:
                raise RuntimeError('Ferma la ricerca in corso prima di ripristinarne un’altra.')
            metadata = self.archive.get_manifest(user, job_id)
            if not metadata:
                raise StorageError('Ricerca salvata non disponibile.')
            cities = metadata.get('cities', [])
            categories = metadata.get('categories', [])
            depth = metadata.get('depth', 'Approfondita')
            if not (isinstance(cities, list) and isinstance(categories, list)
                    and cities and categories):
                raise StorageError('Il manifesto salvato è incompleto.')
            search_categories = core.search_variants(categories, depth)
            user_slug = hashlib.sha256(user.encode()).hexdigest()[:12]
            pages = int(metadata.get('pages', 10))
            base, dest = core.make_plan(
                cities, search_categories, f'web_{user_slug}_{depth.lower()}_p{pages}')
            if dest.stem != job_id:
                raise StorageError('Identificativo ricerca e metadati non corrispondono.')
            info = self.archive.restore_checkpoint(
                user, job_id, base / 'progress.sqlite3')
            raw = base / 'attivita_originali.csv'
            restore_raw_from_database(base / 'progress.sqlite3', raw)
            job = Job(
                user=user, id=job_id, name=job_id, base=base, dest=dest,
                categories=categories, search_categories=search_categories,
                depth=depth, exclude_chains=bool(metadata.get('exclude_chains', True)),
                extra_brands=metadata.get('extra_brands', []),
                search_count=len(cities)*len(search_categories),
                n_cities=len(cities),
                deep_local=len(cities) == 1 and len(categories) == 1,
                selected_cities=cities,
                filter_mode=metadata.get('filter_mode', 'rigoroso'),
                workers=int(metadata.get('workers', 2)),
                pages=pages, batch=int(metadata.get('batch', 100)),
                log_file=base / 'motore_web.log',
                cloud_saved_at=int(info.get('updated_at', 0)),
                status='Archivio R2 ripristinato; pronto a continuare', done=True
            )
            self._update(job, raw, rejected=dest.with_name(dest.stem+'_scartati.csv'))
            self.jobs[user] = job
            return job

    def _checkpoint(self, job: Job):
        if not (job.base / 'progress.sqlite3').exists():
            return
        if self.storage:
            try:
                payload = make_checkpoint(job.base, job.dest, job.user, job.id)
                self.storage.save(job.user, job.id, payload)
                with job.lock:
                    job.github_saved_at = int(time.time())
                    job.github_error = ''
            except (ValueError, OSError, sqlite3.Error, RuntimeError) as exc:
                with job.lock:
                    job.github_error = str(exc)[:220]
        if not self.archive:
            return
        metadata = {
            'cities': job.selected_cities,
            'categories': job.categories,
            'depth': job.depth,
            'exclude_chains': job.exclude_chains,
            'extra_brands': job.extra_brands,
            'filter_mode': job.filter_mode,
            'pages': job.pages,
            'batch': job.batch,
            'workers': job.workers,
            'search_count': job.search_count,
            'completed': completed_queries(job.base),
            'found': job.found,
            'raw_count': job.raw_count,
        }
        try:
            saved = self.archive.save_checkpoint(
                job.user, job.id, job.base / 'progress.sqlite3', metadata)
            with job.lock:
                job.cloud_saved_at = int(saved.get('updated_at', 0))
                job.cloud_error = ''
        except (StorageError, ValueError, OSError) as exc:
            with job.lock:
                job.cloud_error = str(exc)[:220]

    def stop(self, user: str):
        job = self.user_job(user)
        if job and not job.done:
            job.cancel.set()
            with job.lock:
                job.status = 'Arresto richiesto'
            return True
        return False

    def _run(self, job: Job):
        try:
            self._execute(job)
        except Exception as ex:
            with job.lock:
                job.error = f'{type(ex).__name__}: {str(ex)[:400]}'
                job.status = 'Errore'
        finally:
            with job.lock:
                job.done = True
                job.ended = time.time()
            with self.lock:
                if self.active_id == job.id:
                    self.active_id = None

    def _execute(self, job: Job):
        """Esegue più gruppi, mantenendo sempre lo stato SQLite esistente.

        Il sito gratuito può sospendersi: questa funzione non promette di
        completare ricerche nazionali in un'unica sessione.
        """
        with job.lock:
            job.status = 'Preparo il motore'
        core.ensure_engine()
        if job.cancel.is_set():
            job.status = 'Interrotto'
            return
        raw = job.base / 'attivita_originali.csv'
        if not raw.exists() and (job.base / 'progress.sqlite3').exists():
            restore_raw_from_database(job.base / 'progress.sqlite3', raw)
        rejected = job.dest.with_name(job.dest.stem + '_scartati.csv')
        launcher = job.base / 'avvio_motore.py'
        launcher.write_text('''import os
import sys
sys.path.insert(0, sys.argv[1])
from google_maps_scraper.cli import build_parser
from google_maps_scraper.config import Settings
from google_maps_scraper import engine
args = build_parser().parse_args(sys.argv[2:])
settings = Settings.from_args(args)
settings.collect_emails = False
settings.collect_details = os.environ.get('TURBO_DETAILS') == '1'
raise SystemExit(engine.run(settings))
''', encoding='utf-8')
        environment = dict(os.environ, TURBO_DETAILS='0' if job.depth == 'Veloce' else '1')
        start_time = time.monotonic()
        # Gruppi consecutivi e un limite di durata: lo stato rimane riprendibile.
        max_groups = 6
        time_budget_seconds = 10 * 60
        previous_done = completed_queries(job.base)
        last_checkpoint = time.monotonic()
        last_checkpoint_done = previous_done
        exit_code = 0
        no_progress = False

        for round_no in range(max_groups):
            if job.cancel.is_set() or previous_done >= job.search_count:
                break
            with job.lock:
                job.status = ('Ricerca in corso — '
                              f'{previous_done:,}/{job.search_count:,} ricerche completate')
            cmd = [
                sys.executable, str(launcher), str(core.ENGINE),
                'run', '--categories', str(job.base / 'categories.txt'),
                '--locations-dir', str(job.base / 'locations'),
                '--db', str(job.base / 'progress.sqlite3'),
                '--out', str(raw), '--reviews-out', str(job.base / 'reviews.csv'),
                '--workers', str(job.workers), '--max-pages', str(job.pages),
                '--limit-queries', str(job.batch), '--lang', 'it', '--gl', 'it',
                '--min-interval', '0.8', '--delay-min', '1', '--delay-max', '2.2',
                '--direct'
            ]
            with job.log_file.open('ab') as logfile:
                logfile.write(
                    f'\n--- Gruppo {round_no + 1} | '
                    f'{time.strftime("%F %T")} | gia concluse {previous_done} ---\n'.encode()
                )
                logfile.flush()
                job.process = subprocess.Popen(
                    cmd, cwd=core.ENGINE, stdout=logfile,
                    stderr=subprocess.STDOUT, env=environment
                )
                while job.process.poll() is None:
                    if job.cancel.wait(4):
                        job.process.terminate()
                        try:
                            job.process.wait(timeout=12)
                        except subprocess.TimeoutExpired:
                            job.process.kill()
                        break
                    self._update(job, raw, rejected=None)
                    if ((self.archive or self.storage) and time.monotonic() - last_checkpoint >= 90
                            and completed_queries(job.base) > last_checkpoint_done):
                        self._checkpoint(job)
                        last_checkpoint = time.monotonic()
                        last_checkpoint_done = completed_queries(job.base)
                exit_code = job.process.wait()
            self._update(job, raw, rejected=None)
            self._checkpoint(job)
            last_checkpoint = time.monotonic()
            current_done = completed_queries(job.base)
            last_checkpoint_done = current_done
            if exit_code != 0 or job.cancel.is_set():
                break
            if current_done <= previous_done:
                no_progress = True
                break
            previous_done = current_done
            if time.monotonic() - start_time >= time_budget_seconds:
                break

        # La ricerca a griglia si esegue solo quando è stata completata la
        # ricerca generale per una singola città e una singola categoria.
        if (job.deep_local and not job.cancel.is_set() and exit_code == 0
                and completed_queries(job.base) >= job.search_count):
            city = job.selected_cities[0]
            category = job.categories[0]
            with job.lock:
                job.status = f'Esploro anche le zone di {city["name"]}'
            grid_cmd = [
                sys.executable, str(launcher), str(core.ENGINE),
                'scrape', '--category', category,
                '--location', f'{city["name"]}, {city["prov"]}, Italia',
                '--all', '--cell-km', '4', '--radius-km', '5',
                '--no-emails', '--no-details',
                '--db', str(job.base / 'progress.sqlite3'),
                '--out', str(raw), '--reviews-out', str(job.base / 'reviews.csv'),
                '--workers', '2', '--max-pages', '6',
                '--lang', 'it', '--gl', 'it',
                '--min-interval', '1.0', '--delay-min', '1.2',
                '--delay-max', '3.0', '--direct'
            ]
            with job.log_file.open('ab') as logfile:
                logfile.write(b'\n--- Ricerca territoriale per zone ---\n')
                logfile.flush()
                job.process = subprocess.Popen(
                    grid_cmd, cwd=core.ENGINE, stdout=logfile,
                    stderr=subprocess.STDOUT,
                    env=dict(os.environ, TURBO_DETAILS='0')
                )
                while job.process.poll() is None:
                    if job.cancel.wait(4):
                        job.process.terminate()
                        try:
                            job.process.wait(timeout=12)
                        except subprocess.TimeoutExpired:
                            job.process.kill()
                        break
                    self._update(job, raw, rejected=None)
                grid_exit = job.process.wait()
                if grid_exit != 0 and not job.cancel.is_set():
                    job.error = 'Ricerca territoriale non terminata; risultati generali disponibili.'

        self._update(job, raw, rejected=rejected)
        self._checkpoint(job)
        completed = completed_queries(job.base)
        with job.lock:
            if job.cancel.is_set():
                job.status = 'Interrotto: puoi continuare'
            elif exit_code != 0:
                job.status = 'Ricerca interrotta per errore'
                job.error = (job.log_file.read_text(encoding='utf-8', errors='replace')[-1200:]
                             if job.log_file.exists() else 'Consulta il log')
            elif no_progress and completed < job.search_count:
                job.status = 'Nessun avanzamento: possibile blocco o errore delle richieste'
                job.error = 'Controlla il log tecnico. Non sono state completate nuove ricerche.'
            elif completed >= job.search_count:
                job.status = 'Ricerca generale completata'
            else:
                job.status = 'Sessione terminata: ricerca ancora da continuare'


    def _update(self, job: Job, raw: Path, rejected: Path | None):
        rows = core.read_raw(raw)
        n = core.write_normalized(rows, job.dest, categories=job.categories,
                                  filter_mode=job.filter_mode, rejected_file=rejected,
                                  exclude_chains=job.exclude_chains, extra_brands=job.extra_brands)
        with job.lock:
            job.found = n
            job.raw_count = len(rows)
            if rejected is not None:
                job.rejected = len(core.split_by_relevance(rows, job.categories, job.filter_mode,
                                                          job.exclude_chains, job.extra_brands)[1])




def restore_raw_from_database(db_path: Path, raw_path: Path) -> int:
    """Ricrea il CSV grezzo dal database archiviato, senza chiamate web."""
    if not db_path.exists():
        return 0
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    temp = raw_path.with_suffix('.recover.tmp')
    columns = ['name', 'category', 'address', 'phone', 'phone_intl',
               'website', 'email', 'place_id', 'query']
    count = 0
    try:
        with sqlite3.connect(str(db_path)) as db:
            with temp.open('w', newline='', encoding='utf-8') as out:
                writer = csv.DictWriter(out, fieldnames=columns)
                writer.writeheader()
                cur = db.execute(
                    'SELECT name, categories, address, phone, phone_intl, '
                    'website, email, place_id, query FROM places'
                )
                for record in cur:
                    writer.writerow(dict(zip(columns, record)))
                    count += 1
        os.replace(temp, raw_path)
    except (sqlite3.Error, OSError) as exc:
        raise StorageError('Impossibile ricostruire il CSV dal backup') from exc
    finally:
        temp.unlink(missing_ok=True)
    return count


def completed_queries(base: Path) -> int:
    """Legge quante ricerche il motore ha effettivamente marcato come concluse."""
    db_path = base / 'progress.sqlite3'
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(str(db_path), timeout=2) as conn:
            found = conn.execute(
                "SELECT COUNT(*) FROM searches WHERE status = 'done'"
            ).fetchone()
        return int(found[0]) if found else 0
    except (sqlite3.Error, OSError):
        return 0


def tail_log(path: Path | None, n: int = 14) -> str:
    if path is None or not path.exists():
        return ''
    try:
        return '\n'.join(path.read_text(encoding='utf-8', errors='replace').splitlines()[-n:])
    except OSError:
        return ''
