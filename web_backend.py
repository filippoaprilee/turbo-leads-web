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
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import turbo_scraper as core


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
    ended: float = 0
    process: subprocess.Popen | None = field(default=None, repr=False)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    log_file: Path | None = None


class JobManager:
    def __init__(self):
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
            base, dest = core.make_plan(cities, search_categories, f'web_{user_slug}_{depth.lower()}_p{pages}')
            job = Job(user=user, id=dest.stem, name=dest.stem,
                      base=base, dest=dest, categories=categories,
                      search_categories=search_categories, depth=depth,
                      exclude_chains=exclude_chains, extra_brands=extra_brands,
                      search_count=len(cities)*len(search_categories), n_cities=len(cities),
                      filter_mode=filter_mode, batch=batch, workers=workers, pages=pages,
                      log_file=base/'motore_web.log')
            self.jobs[user] = job
            self.active_id = job.id
            thread = threading.Thread(target=self._run, args=(job,), daemon=True,
                                      name=f"turbo-{user_slug}")
            thread.start()
            return job

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
        with job.lock:
            job.status = 'Preparo il motore'
        # Le dipendenze curl_cffi / requests sono installate in fase di deploy da requirements.txt.
        main = core.ensure_engine()
        if job.cancel.is_set():
            job.status = 'Interrotto'
            return
        raw = job.base/'attivita_originali.csv'
        rejected = job.dest.with_name(job.dest.stem + '_scartati.csv')
        launcher = job.base/'avvio_motore.py'
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
        cmd = [sys.executable, str(launcher), str(core.ENGINE),
               'run', '--categories', str(job.base/'categories.txt'),
               '--locations-dir', str(job.base/'locations'),
               '--db', str(job.base/'progress.sqlite3'),
               '--out', str(raw), '--reviews-out', str(job.base/'reviews.csv'),
               '--workers', str(job.workers), '--max-pages', str(job.pages),
               '--limit-queries', str(job.batch), '--lang', 'it', '--gl', 'it',
               '--min-interval', '0.8', '--delay-min', '1', '--delay-max', '2.2',
               '--direct']
        with job.lock:
            job.status = 'Ricerca in corso'
        environment = dict(os.environ, TURBO_DETAILS='0' if job.depth == 'Veloce' else '1')
        # Redirige stdout, evitando output voluminosi nei log del server Streamlit.
        with job.log_file.open('ab') as logfile:
            logfile.write(f'\n--- Avvio lotto {time.strftime("%F %T")} ---\n'.encode())
            logfile.flush()
            job.process = subprocess.Popen(cmd, cwd=core.ENGINE, stdout=logfile,
                                           stderr=subprocess.STDOUT, env=environment)
            while job.process.poll() is None:
                if job.cancel.wait(4):
                    job.process.terminate()
                    try:
                        job.process.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        job.process.kill()
                    break
                self._update(job, raw, rejected=None)
            exit_code = job.process.wait()
        self._update(job, raw, rejected=rejected)
        with job.lock:
            if job.cancel.is_set():
                job.status = 'Interrotto (CSV disponibile)'
            elif exit_code == 0:
                job.status = 'Completato'
            else:
                job.status = f'Motore terminato con errore ({exit_code})'
                job.error = (job.log_file.read_text(encoding='utf-8', errors='replace')[-1200:]
                             if job.log_file.exists() else 'Controlla i log')

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


def tail_log(path: Path | None, n: int = 14) -> str:
    if path is None or not path.exists():
        return ''
    try:
        return '\n'.join(path.read_text(encoding='utf-8', errors='replace').splitlines()[-n:])
    except OSError:
        return ''
