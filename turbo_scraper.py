#!/usr/bin/env python3
"""Controller Python per la raccolta di attività locali (Google Maps non ufficiale).
Motore esterno MIT: sonagara-vashram/google-maps-scraper. Non usa API a pagamento.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import csv
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'stato'
RESULTS = ROOT / 'risultati'
ENGINE = ROOT / '.motore' / 'google-maps-scraper-main'
DATASET = STATE / 'comuni.json'
DATA_URLS = [
    'https://cdn.jsdelivr.net/gh/RP92/comuni-italiani@main/data/comuni.json',
    'https://raw.githubusercontent.com/RP92/comuni-italiani/main/data/comuni.json',
]
ENGINE_URL = 'https://codeload.github.com/sonagara-vashram/google-maps-scraper/zip/refs/heads/main'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125.0 Safari/537.36'}
F_CSV = ['Nome', 'Comune', 'Telefono', 'Email']

# Le categorie Maps reali sono nel campo `category` del CSV grezzo.
# Sinonimi IT/EN frequenti, con corrispondenze di parole complete, non substring.
CATEGORY_ALIASES = {
 'palestra': ('palestra', 'palestre', 'gym', 'fitness center', 'fitness centre', 'centro fitness', 'fitness club', 'health club', 'gymnasium', 'sala pesi', 'centro di fitness'),
 'centro fitness': ('palestra', 'gym', 'fitness center', 'fitness centre', 'centro fitness', 'fitness club', 'health club'),
 'personal trainer': ('personal trainer', 'personal training', 'fitness trainer', 'allenatore personale', 'istruttore fitness'),
 'studio personal training': ('personal trainer', 'personal training', 'fitness trainer', 'allenatore personale'),
 'centro crossfit': ('crossfit', 'cross fit'),
 'studio pilates': ('pilates', 'studio di pilates'),
 'studio yoga': ('yoga', 'studio yoga', 'yoga studio'),
 'centro sportivo': ('centro sportivo', 'sports complex', 'sports club', 'sports center', 'sports centre', 'complesso sportivo', 'impianto sportivo', 'recreation center', 'recreation centre'),
 'fisioterapista': ('fisioterapista', 'fisioterapia', 'physiotherapist', 'physical therapist', 'physical therapy', 'physiotherapy', 'terapista fisico'),
 'studio fisioterapia': ('fisioterapista', 'fisioterapia', 'physiotherapist', 'physical therapist', 'physical therapy', 'physiotherapy'),
 'centro riabilitazione': ('centro riabilitazione', 'centro di riabilitazione', 'rehabilitation center', 'rehabilitation centre', 'physical therapy clinic', 'clinica riabilitativa'),
 'osteopata': ('osteopata', 'osteopatia', 'osteopath', 'osteopathy'),
 'chiropratico': ('chiropratico', 'chiropratica', 'chiropractor', 'chiropractic'),
 'medico dello sport': ('medico dello sport', 'medicina dello sport', 'sports medicine', 'sports physician'),
 'nutrizionista': ('nutrizionista', 'nutrizione', 'nutritionist', 'nutrition consultant', 'dietitian', 'dietista', 'dietologo'),
 'dietista': ('dietista', 'dietitian', 'nutrizionista', 'nutritionist', 'dietologo'),
 'centro estetico': ('centro estetico', 'centro di estetica', 'beauty salon', 'beauty center', 'beauty centre', 'beautician', 'estetista', 'istituto di bellezza', 'salone di bellezza', 'skin care clinic'),
 'centro dimagrimento': ('centro dimagrimento', 'centro dimagrante', 'weight loss', 'slimming center', 'slimming centre', 'dimagrimento'),
 'centro massaggi': ('centro massaggi', 'centro massaggio', 'massaggiatore', 'massage therapist', 'massage spa', 'massaggio', 'massaggi', 'massage'),
 'piscina': ('piscina', 'swimming pool', 'aquatic center', 'aquatic centre', 'centro natatorio', 'swim club', 'nuoto'),
 'scuola danza': ('scuola danza', 'scuola di danza', 'dance school', 'dance studio', 'accademia di danza'),
 'centro benessere': ('centro benessere', 'wellness center', 'wellness centre', 'wellness spa', 'day spa', 'spa', 'terme'),
 'negozio integratori sportivi': ('negozio integratori', 'integratori sportivi', 'vitamin and supplement store', 'vitamin supplement store', 'nutrition store', 'supplement store', 'sports nutrition'),
 'negozio articoli sportivi': ('negozio articoli sportivi', 'negozio di articoli sportivi', 'sporting goods store', 'sports equipment store', 'sportswear store', 'negozio sportivo', 'negozio di attrezzature sportive'),
 'ristorante': ('ristorante', 'restaurant', 'trattoria', 'osteria'),
 'pizzeria': ('pizzeria', 'pizza restaurant', 'pizza delivery'),
 'bar': ('bar', 'cafe', 'caffe', 'coffee shop', 'cocktail bar'),
 'parrucchiere': ('parrucchiere', 'parrucchiera', 'hair salon', 'hairdresser', 'barber shop', 'barbiere'),
 'dentista': ('dentista', 'dentist', 'dental clinic', 'studio dentistico', 'odontoiatra'),
 'autonoleggio': ('autonoleggio', 'car rental', 'car rental agency', 'car hire', 'noleggio auto', 'agenzia di noleggio auto'),
 'farmacia': ('farmacia', 'pharmacy'),
 'hotel': ('hotel', 'albergo', 'lodging', 'bed and breakfast'),
 'agenzia immobiliare': ('agenzia immobiliare', 'real estate agency', 'real estate agent'),
 'elettricista': ('elettricista', 'electrician'),
 'idraulico': ('idraulico', 'plumber'),
 'meccanico': ('meccanico', 'auto repair shop', 'car repair and maintenance', 'officina meccanica'),
 'carrozzeria': ('carrozzeria', 'auto body shop', 'car body shop'),
 'lavanderia': ('lavanderia', 'laundry', 'dry cleaner'),
 'negozio abbigliamento': ('negozio di abbigliamento', 'clothing store', 'abbigliamento'),
 'negozio di abbigliamento': ('negozio di abbigliamento', 'clothing store', 'abbigliamento'),
 'medico': ('medico', 'doctor', 'studio medico', 'medical clinic', 'physician'),
 'studio medico': ('studio medico', 'medical clinic', 'medical office', 'medical center', 'medical centre'),
}
# Le persone cercano spesso al plurale: 'palestre' deve riconoscere 'Gym'.
INPUT_CATEGORY_SYNONYMS = {
 'palestre': 'palestra', 'gyms': 'palestra', 'gym': 'palestra',
 'fitness': 'centro fitness', 'centri fitness': 'centro fitness',
 'personal trainers': 'personal trainer', 'personal training': 'personal trainer',
 'crossfit': 'centro crossfit', 'centri crossfit': 'centro crossfit',
 'pilates': 'studio pilates', 'yoga': 'studio yoga',
 'centri sportivi': 'centro sportivo', 'fisioterapisti': 'fisioterapista',
 'fisioterapia': 'fisioterapista', 'studi fisioterapia': 'studio fisioterapia',
 'medici fisioterapisti': 'fisioterapista',
 'centri riabilitazione': 'centro riabilitazione', 'osteopati': 'osteopata',
 'chiropratici': 'chiropratico', 'medici dello sport': 'medico dello sport',
 'nutrizionisti': 'nutrizionista', 'dietisti': 'dietista',
 'centri estetici': 'centro estetico', 'estetiste': 'centro estetico',
 'estetista': 'centro estetico', 'centri dimagrimento': 'centro dimagrimento',
 'centri massaggi': 'centro massaggi', 'piscine': 'piscina',
 'scuole danza': 'scuola danza', 'scuole di danza': 'scuola danza',
 'centri benessere': 'centro benessere',
 'negozi integratori sportivi': 'negozio integratori sportivi',
 'negozi articoli sportivi': 'negozio articoli sportivi',
 'negozio sportivo': 'negozio articoli sportivi',
 'ristoranti': 'ristorante', 'pizzerie': 'pizzeria',
 'parrucchieri': 'parrucchiere', 'parrucchiere donna': 'parrucchiere',
 'dentisti': 'dentista', 'autonoleggi': 'autonoleggio',
 'farmacie': 'farmacia', 'alberghi': 'hotel',
 'agenzie immobiliari': 'agenzia immobiliare',
 'elettricisti': 'elettricista', 'idraulici': 'idraulico',
 'meccanici': 'meccanico', 'carrozzerie': 'carrozzeria',
 'lavanderie': 'lavanderia', 'medici': 'medico',
}


def canonical_category(request: str) -> str:
    label = normalize_label(request)
    return INPUT_CATEGORY_SYNONYMS.get(label, label)


# Categoria Maps generica: concedere il nome SOLO se c'e' un contesto
# Maps coerente. Un negozio di abbigliamento non diventa una palestra.
CONTEXTUAL_ALIASES = {
 'centro crossfit': ('gym', 'palestra', 'fitness center', 'fitness centre', 'sports club'),
 'studio pilates': ('gym', 'palestra', 'fitness center', 'fitness centre', 'physical fitness program'),
 'studio yoga': ('gym', 'palestra', 'fitness center', 'fitness centre', 'physical fitness program'),
 'medico dello sport': ('doctor', 'medico', 'medical clinic', 'studio medico'),
 'centro dimagrimento': ('wellness center', 'centro benessere', 'nutritionist', 'nutrizionista'),
 'negozio integratori sportivi': ('health food store', 'negozio di alimenti naturali'),
 'negozio articoli sportivi': ('store', 'negozio'),
}
# Se le categorie del risultato sono troppo generiche, la modalita' ampia
# puo' tentare la conferma dal NOME, senza far entrare i negozi estranei.
GENERIC_CATEGORIES = ('establishment', 'point of interest', 'service establishment', 'azienda', 'attivita commerciale', 'business', 'store', 'negozio', 'services', 'servizi')
UNRELATED_STORE_CATEGORIES = ('clothing store', 'negozio di abbigliamento', 'fashion accessories store', 'department store', 'shoe store', 'negozio di scarpe', 'shopping mall', 'centro commerciale')


def normalize_label(value: str) -> str:
    value = unicodedata.normalize('NFKD', str(value or '').casefold())
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', value).split())


def has_phrase(text: str, phrase: str) -> bool:
    needle = normalize_label(phrase)
    return bool(needle) and f' {needle} ' in f' {normalize_label(text)} '


def category_aliases(request: str) -> tuple[str, ...]:
    request = canonical_category(request)
    return CATEGORY_ALIASES.get(request, (request,))


def category_verdict(row: dict, requested_categories: list[str], mode: str = 'rigoroso') -> tuple[bool, str]:
    """Accetta solo categorie Maps pertinenti, non solo la query cercata.

    `category` e' il campo effettivamente esportato dal motore upstream.
    La query serve solo da provenienza, NON come prova di pertinenza.
    """
    raw_cat = (row.get('category') or row.get('categories') or '').strip()
    name = (row.get('name') or '').strip()
    if not requested_categories:
        return True, 'Filtro non impostato'
    if not raw_cat:
        return False, 'Categoria Maps non disponibile'
    normalized_cat = normalize_label(raw_cat)
    normalized_name = normalize_label(name)
    for requested in requested_categories:
        label = canonical_category(requested)
        aliases = category_aliases(requested)
        # Si tratta di una prestazione/attivita', non di uno shop con il nome simile.
        if label not in ('negozio integratori sportivi', 'negozio articoli sportivi', 'negozio abbigliamento', 'negozio di abbigliamento'):
            if any(has_phrase(normalized_cat, x) for x in UNRELATED_STORE_CATEGORIES):
                continue
        if any(has_phrase(normalized_cat, alias) for alias in aliases):
            # "massage supply store" non e' un centro massaggi; analogamente
            # "beauty supply store" non e' un centro estetico.
            if label in ('centro massaggi', 'centro estetico', 'centro benessere') and (
                ' store ' in f' {normalized_cat} ' or ' negozio ' in f' {normalized_cat} '
            ):
                continue
            return True, f'Categoria Maps compatibile: {requested}'
        # Centro CrossFit, Pilates o Yoga puo' figurare genericamente come Gym,
        # ma solo se la specializzazione compare nel nome della struttura.
        focus_terms = aliases if label in CONTEXTUAL_ALIASES else ()
        if focus_terms and any(has_phrase(normalized_name, f) for f in focus_terms):
            if any(has_phrase(normalized_cat, g) for g in CONTEXTUAL_ALIASES[label]):
                return True, f'Nome e categoria Maps coerenti: {requested}'
        # Per richieste non in dizionario, confronto conservativo con il testo
        # della categoria (mai sulla query, che potrebbe contenere falsi positivi).
    if mode == 'ampio':
        if any(has_phrase(normalized_cat, x) for x in UNRELATED_STORE_CATEGORIES):
            return False, 'Categoria commerciale estranea alla ricerca'
        # Una categoria puramente generica non prova niente, ma in modalita'
        # ampia il nome puo' confermare chiaramente un'attivita' specializzata.
        if any(normalized_cat == normalize_label(x) for x in GENERIC_CATEGORIES):
            for requested in requested_categories:
                if any(has_phrase(normalized_name, alias) for alias in category_aliases(requested)):
                    return True, f'Modalita ampia: nome coerente ({requested}), categoria generica'
    return False, f'Categoria Maps non pertinente: {raw_cat[:100]}'


def split_by_relevance(rows: list[dict], categories: list[str], mode='rigoroso'):
    kept, rejected = [], []
    for row in rows:
        if not (row.get('name') or '').strip():
            continue
        match, reason = category_verdict(row, categories, mode)
        if match:
            kept.append(row)
        else:
            rejected.append((row, reason))
    return kept, rejected
FITNESS = [
 'palestra', 'centro fitness', 'personal trainer', 'studio personal training',
 'centro crossfit', 'studio pilates', 'studio yoga', 'centro sportivo',
 'fisioterapista', 'studio fisioterapia', 'centro riabilitazione',
 'osteopata', 'chiropratico', 'medico dello sport', 'nutrizionista',
 'dietista', 'centro estetico', 'centro dimagrimento',
 'centro massaggi', 'piscina', 'scuola danza', 'centro benessere',
 'negozio integratori sportivi', 'negozio articoli sportivi',
]
EMAIL_RE = re.compile(r'(?<![\w.+-])([a-zA-Z0-9][\w.+-]{0,63}@[a-zA-Z0-9][a-zA-Z0-9.-]{1,240}\.[a-zA-Z]{2,20})(?![\w.-])')
BAD_EMAIL_PARTS = ('example.com', 'example.org', 'wixpress', 'sentry.io', 'wordpress.org', 'domain.com', 'mailchimp.com', 'yourdomain', 'email.com')


def log(s):
    print(s, flush=True)


def grab(url: str, timeout=25, max_bytes=16_000_000) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes + 1)[:max_bytes]


def get_dataset(offline=False):
    STATE.mkdir(parents=True, exist_ok=True)
    if DATASET.exists():
        return json.loads(DATASET.read_text(encoding='utf-8'))
    if offline:
        raise RuntimeError('Archivio comuni non scaricato. Esegui una volta con connessione Internet.')
    errors = []
    for url in DATA_URLS:
        try:
            log('Scarico l’elenco aggiornabile dei comuni italiani (solo la prima volta)...')
            content = grab(url, timeout=45)
            payload = json.loads(content)
            if not isinstance(payload, list) or len(payload) < 7000:
                raise ValueError('Il dataset ricevuto non contiene tutti i comuni')
            DATASET.write_bytes(content)
            return payload
        except Exception as ex:
            errors.append(str(ex))
    raise RuntimeError('Download comuni fallito: ' + ' | '.join(errors))


def venv_python() -> Path:
    p = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not p.exists():
        import venv
        log('Creo ambiente Python locale (solo al primo avvio)...')
        venv.create(ROOT / '.venv', with_pip=True)
    return p


def ensure_engine():
    main = ENGINE / 'main.py'
    if main.exists():
        return main
    ENGINE.parent.mkdir(parents=True, exist_ok=True)
    log('Scarico il motore gratuito Python (solo la prima volta)...')
    payload = grab(ENGINE_URL, timeout=90, max_bytes=55_000_000)
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        for member in z.infolist():
            if not member.filename.startswith('google-maps-scraper-main/'):
                raise RuntimeError('Archivio del motore non valido')
            destination = (ENGINE.parent / member.filename).resolve()
            if not destination.is_relative_to(ENGINE.parent.resolve()):
                raise RuntimeError('Archivio con percorso non valido')
        z.extractall(ENGINE.parent)
    if not main.exists():
        raise RuntimeError('Scaricato ZIP, ma manca main.py')
    return main


def bootstrap():
    if sys.version_info < (3, 11):
        raise RuntimeError('Serve Python 3.11 o più recente. Scaricalo da python.org.')
    py = venv_python()
    main = ensure_engine()
    stamp = ROOT / '.venv' / '.dipendenze_installate'
    if not stamp.exists():
        log('Installo dipendenze gratuite curl_cffi e requests (solo al primo avvio)...')
        subprocess.run([str(py), '-m', 'pip', 'install', '-r', str(ENGINE/'requirements.txt')], check=True)
        stamp.write_text('ok')
    return py, main


def key_slug(s: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '_', s.casefold())
    return s.strip('_')[:36] or 'ricerca'


def parse_items(s: str) -> list[str]:
    return list(dict.fromkeys(x.strip() for x in re.split('[,;\n]+', s or '') if x.strip()))


def choose_municipalities(dataset, modo: str, comune: str, provincia: str, regioni: list[str], max_comuni: int):
    requested = {x.casefold() for x in parse_items(comune) if x.upper() not in ('TUTTI', 'ALL')}
    regions = {x.casefold() for x in regioni}
    chosen = []
    for row in dataset:
        nome = (row.get('nome') or '').strip()
        prov = row.get('provincia') or {}
        sigla = (prov.get('sigla') or row.get('sigla') or '').upper()
        reg = (row.get('regione') or {}).get('nome') or ''
        zona = (row.get('zona') or {}).get('nome') or ''
        if not nome or not sigla:
            continue
        if modo == 'lecce' and sigla != 'LE':
            continue
        if modo in ('fitness', 'italia') and provincia and sigla != provincia.upper():
            continue
        if modo in ('fitness', 'italia') and not provincia:
            if regions and reg.casefold() not in regions:
                continue
            if modo == 'fitness' and not regions and not zona.casefold().startswith('nord'):
                continue
        if requested and nome.casefold() not in requested:
            continue
        chosen.append({'name': nome, 'prov': sigla, 'region': reg, 'population': row.get('popolazione') or 0})
    chosen.sort(key=lambda r: (-int(r['population'] or 0), r['name']))
    if max_comuni:
        chosen = chosen[:max_comuni]
    if not chosen:
        raise RuntimeError('Nessun comune trovato con i filtri impostati. Controlla i nomi.')
    return chosen


def make_plan(municipalities: list[dict], categories: list[str], modo: str):
    # L'ordinamento per popolazione privilegia prima le città con maggior potenziale.
    plan_payload = {'comuni': [(r['name'], r['prov']) for r in municipalities], 'categorie': categories, 'modo': modo}
    key = hashlib.sha256(json.dumps(plan_payload, ensure_ascii=False).encode('utf-8')).hexdigest()[:10]
    title = f'{modo}_{key_slug(categories[0])}_{key}'
    base = STATE / title
    inp = base / 'locations'
    inp.mkdir(parents=True, exist_ok=True)
    groups = {}
    for r in municipalities:
        groups.setdefault(r['prov'], []).append(r)
    # Alcuni file provincia = schema atteso dal motore terzo: code e cities.
    for p, rows in groups.items():
        content = {'state': {'code': p}, 'cities': {f'{i:04d}': {'name': row['name']} for i, row in enumerate(rows)}}
        (inp/f'{p}.json').write_text(json.dumps(content, ensure_ascii=False), encoding='utf-8')
    (base/'categories.txt').write_text('\n'.join(categories) + '\n', encoding='utf-8')
    (base/'piano.json').write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2), encoding='utf-8')
    RESULTS.mkdir(exist_ok=True)
    return base, RESULTS/f'{title}.csv'


def municipality_from_raw(row: dict) -> str:
    # Preferire il comune effettivo indicato nell'indirizzo (non solo quello cercato).
    address = (row.get('address') or '').strip()
    # Indirizzi come 'Via Roma 1, 73020 Cavallino LE, Italia'.
    match = re.search(r'\b\d{5}\s+([^,]+?)\s+[A-Z]{2}(?:\s*,|\s*$)', address)
    if match:
        return match.group(1).strip()
    q = (row.get('query') or '').strip()
    if ' in ' in q:
        return q.rsplit(' in ', 1)[1].rsplit(',', 1)[0].strip()
    return ''


def read_raw(raw: Path):
    if not raw.exists():
        return []
    try:
        with raw.open('r', newline='', encoding='utf-8-sig') as stream:
            return [r for r in csv.DictReader(stream) if r.get('name')]
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def write_normalized(rows: list[dict], dest: Path, emails: dict | None = None,
                     categories: list[str] | None = None, filter_mode: str = 'rigoroso',
                     rejected_file: Path | None = None):
    emails = emails or {}
    accepted, rejected = split_by_relevance(rows, categories or [], filter_mode)
    existing = set()
    entries = []
    for row in accepted:
        name = (row.get('name') or '').strip()
        city = municipality_from_raw(row)
        phone = (row.get('phone_intl') or row.get('phone') or '').strip()
        website = (row.get('website') or '').strip()
        email = (emails.get(website) or row.get('email') or '').strip()
        place_id = (row.get('place_id') or row.get('google_maps_url') or '').strip()
        duplicate_key = place_id if place_id else (name.casefold(), city.casefold(), phone)
        if duplicate_key in existing:
            continue
        existing.add(duplicate_key)
        entries.append([name, city, phone, email])
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix('.tmp')
    with temp.open('w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(F_CSV)
        w.writerows(entries)
    temp.replace(dest)
    if rejected_file is not None:
        temp_rejected = rejected_file.with_suffix('.tmp')
        with temp_rejected.open('w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh, delimiter=';')
            w.writerow(['Nome', 'Comune', 'Categoria Maps', 'Ricerca originale', 'Motivo esclusione'])
            for row, reason in rejected:
                w.writerow([row.get('name',''), municipality_from_raw(row),
                            row.get('category') or row.get('categories') or '', row.get('query',''), reason])
        temp_rejected.replace(rejected_file)
    return len(entries)


def valid_email(e: str):
    e = e.lower().strip('.,:;\'\"()[]<>{}')
    if any(x in e for x in BAD_EMAIL_PARTS):
        return False
    if len(e) > 254 or e.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.js', '.css')):
        return False
    return re.fullmatch(r'[\w.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+', e) is not None


def extract_emails(body: bytes):
    content = html.unescape(body.decode('utf-8', errors='ignore'))
    content = urllib.parse.unquote(content)
    found = set()
    for e in EMAIL_RE.findall(content):
        e = e.lower().strip('.,:;\'\"()[]<>{}')
        if valid_email(e):
            found.add(e)
    preferred = ('info@', 'contatti@', 'contact@', 'segreteria@', 'reception@', 'commerciale@')
    return sorted(found, key=lambda x: (not x.startswith(preferred), x))


def contact_from_website(site: str):
    # Solo siti pubblici, due pagine al massimo. Esegue tentativi senza bloccare tutta la ricerca.
    if not site:
        return ''
    if not site.startswith(('https://', 'http://')):
        site = 'https://' + site
    parsed = urllib.parse.urlsplit(site)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return ''
    if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
        return ''
    start = f'{parsed.scheme}://{parsed.netloc}/'
    try:
        emails = extract_emails(grab(start, timeout=7, max_bytes=300_000))
        if emails:
            return emails[0]
    except (ValueError, OSError, urllib.error.URLError):
        pass
    for suffix in ('contatti', 'contact'):
        try:
            emails = extract_emails(grab(urllib.parse.urljoin(start, suffix), timeout=6, max_bytes=300_000))
            if emails:
                return emails[0]
        except (ValueError, OSError, urllib.error.URLError):
            pass
    return ''


def enrich_email(rows: list[dict], cache_file: Path, workers: int = 10):
    cache = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            cache = {}
    sites = sorted({(r.get('website') or '').strip() for r in rows if (r.get('website') or '').strip()})
    pending = [site for site in sites if site not in cache]
    if pending:
        log(f'Cerco email su {len(pending)} siti aziendali pubblici, con {workers} connessioni parallele...')
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            tasks = {ex.submit(contact_from_website, site): site for site in pending}
            for i, fut in enumerate(concurrent.futures.as_completed(tasks), 1):
                site = tasks[fut]
                try:
                    cache[site] = fut.result()
                except Exception:
                    cache[site] = ''
                if i % 20 == 0 or i == len(pending):
                    cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
                    log(f'  Email: elaborati {i}/{len(pending)} siti')
    return cache


def run_engine(py: Path, main: Path, base: Path, dest: Path, workers: int, pages: int, lotto: int,
               categories: list[str], filter_mode: str, rejected_file: Path):
    raw = base/'attivita_originali.csv'
    cmd = [str(py), str(main), 'run', '--categories', str(base/'categories.txt'),
           '--locations-dir', str(base/'locations'), '--db', str(base/'progress.sqlite3'),
           '--out', str(raw), '--reviews-out', str(base/'reviews.csv'),
           '--workers', str(workers), '--max-pages', str(pages),
           '--limit-queries', str(lotto), '--lang', 'it', '--gl', 'it',
           '--min-interval', '0.8', '--delay-min', '1', '--delay-max', '2.2',
           '--direct']
    # Il comando 'run' dell'upstream non espone --no-emails/--no-details,
    # quindi il launcher imposta queste due opzioni tramite le API Python pubblicate.
    launch = base/'avvio_motore.py'
    launch.write_text('''import sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\nfrom google_maps_scraper.cli import build_parser\nfrom google_maps_scraper.config import Settings\nfrom google_maps_scraper import engine\nns = build_parser().parse_args(sys.argv[2:])\ns = Settings.from_args(ns)\ns.collect_emails = False\ns.collect_details = False\nraise SystemExit(engine.run(s))\n''', encoding='utf-8')
    args = [str(py), str(launch), str(ENGINE)] + cmd[2:]
    log(f'Avvio {workers} lavoratori paralleli; massimo {lotto} nuove ricerche per lotto.')
    log(f'CSV in aggiornamento: {dest}')
    proc = subprocess.Popen(args, cwd=ENGINE)
    while proc.poll() is None:
        try:
            time.sleep(4)
            n = write_normalized(read_raw(raw), dest, categories=categories,
                                 filter_mode=filter_mode)
            log(f'Attività pertinenti nel CSV: {n}    (puoi aprirlo anche mentre lavora)')
        except KeyboardInterrupt:
            log('Interruzione richiesta: salvo i risultati disponibili...')
            proc.send_signal(__import__('signal').SIGINT)
            break
    status = proc.wait()
    count = write_normalized(read_raw(raw), dest, categories=categories,
                             filter_mode=filter_mode, rejected_file=rejected_file)
    log(f'Motore terminato (codice {status}); {count} attività pertinenti nel CSV.')
    return status, raw


def build_arg_parser():
    p = argparse.ArgumentParser(description='Ricerca attività: Lecce, fitness Nord Italia o categorie personalizzate in tutta Italia')
    p.add_argument('modo', choices=('lecce', 'fitness', 'italia'), help='lecce=provincia LE; fitness=Nord di default; italia=tutti i comuni di default')
    p.add_argument('--categoria', help='Categoria singola o più categorie separate da virgola')
    p.add_argument('--comune', default='TUTTI', help='Comune specifico o TUTTI (per esempio: Lecce)')
    p.add_argument('--provincia', default='', help='Provincia specifica per fitness/italia (esempio MI)')
    p.add_argument('--regioni', default='', help='Regioni separate da virgole (se assente: Nord per fitness, tutte per italia)')
    p.add_argument('--max-comuni', type=int, default=0, help='0=tutti; altrimenti prioritizza i più popolosi')
    p.add_argument('--lotto', type=int, default=150, help='Nuove ricerche per esecuzione, default 150; 0=tutte')
    p.add_argument('--workers', type=int, default=4, help='Connessioni Maps concorrenti: 2-6 consigliate')
    p.add_argument('--pagine', type=int, default=2, help='Pagine per ricerca; 1 più rapido, 3 più ampio')
    p.add_argument('--email', action='store_true', help='Dopo Maps, visita i siti pubblici per le email')
    p.add_argument('--solo-email', action='store_true', help='Non interroga Maps; cerca email solo dei risultati pertinenti')
    p.add_argument('--solo-filtra', action='store_true', help='Rifiltra il CSV grezzo gia esistente, senza fare nuove richieste Maps')
    p.add_argument('--filtro', choices=('rigoroso', 'ampio'), default='rigoroso', help='Rigore nel verificare la categoria Maps (default: rigoroso)')
    p.add_argument('--nessuna-domanda', action='store_true', help='Imposta tutto da riga di comando')
    p.add_argument('--piano', action='store_true', help='Genera piano senza effettuare lo scraping')
    return p


def interactive(args):
    if args.nessuna_domanda or args.categoria:
        return args
    if args.modo == 'lecce':
        args.categoria = input('Categoria da cercare (es. parrucchiere, dentista): ').strip() or 'palestra'
        args.comune = input('Comune (invio = TUTTI i comuni della provincia di Lecce): ').strip() or 'TUTTI'
    elif args.modo == 'fitness':
        log('Categorie disponibili: ' + ', '.join(FITNESS))
        args.categoria = input('Categoria/e (invio = TUTTE quelle fitness): ').strip() or 'TUTTE'
        args.provincia = input('Solo provincia (es. MI; invio = tutto Nord Italia): ').strip().upper()
    else:
        args.categoria = input('Categoria/e da cercare in Italia (es. palestre, fisioterapisti): ').strip() or 'palestre'
        args.provincia = input('Solo provincia (es. MI; invio = tutta Italia): ').strip().upper()
    v = input('Modalità: [1] SUPER VELOCE (solo Maps), [2] con email dai siti? [1]: ').strip()
    args.email = v == '2'
    v = input(f'Numero massimo di ricerche per questa esecuzione [{args.lotto}]: ').strip()
    if v.isdigit():
        args.lotto = int(v)
    return args


def main(argv=None):
    args = interactive(build_arg_parser().parse_args(argv))
    if not (1 <= args.workers <= 12) or not (1 <= args.pagine <= 10) or args.lotto < 0 or args.max_comuni < 0:
        raise RuntimeError('Parametri fuori intervallo: workers 1-12, pagine 1-10, lotto >=0, max-comuni >=0')
    if args.modo in ('fitness', 'italia') and args.categoria and args.categoria.upper() == 'TUTTE':
        cats = FITNESS
    elif args.modo == 'fitness' and not args.categoria:
        cats = FITNESS
    else:
        if args.modo == 'italia' and not args.categoria:
            raise RuntimeError('Per la modalita italia indica --categoria (o usa --categoria TUTTE per le 24 categorie fitness).')
        cats = parse_items(args.categoria or ('palestra' if args.modo == 'lecce' else 'TUTTE'))
    if not cats:
        raise RuntimeError('Specifica almeno una categoria')
    municipalities = choose_municipalities(get_dataset(), args.modo, args.comune, args.provincia,
                                            parse_items(args.regioni), args.max_comuni)
    base, dest = make_plan(municipalities, cats, args.modo)
    log(f'Piano: {len(municipalities)} comuni x {len(cats)} categorie = {len(municipalities)*len(cats)} ricerche possibili.')
    rejected_file = dest.with_name(dest.stem + '_scartati.csv')
    log(f'Output: {dest}')
    log(f'Filtro categorie: {args.filtro}; esclusioni: {rejected_file}')
    if args.piano:
        log('Solo piano: non ho effettuato richieste Google Maps.')
        return 0
    raw = base/'attivita_originali.csv'
    if not args.solo_email and not args.solo_filtra:
        py, engine = bootstrap()
        result, raw = run_engine(py, engine, base, dest, args.workers, args.pagine,
                                 args.lotto, cats, args.filtro, rejected_file)
        if result not in (0, 130):
            log('Attenzione: verifica i messaggi del motore qui sopra.')
    if (args.solo_email or args.solo_filtra) and not raw.exists():
        raise RuntimeError('Non trovo il CSV originale da filtrare. Usa gli stessi filtri della ricerca precedente e conserva la cartella stato/.')
    rows = read_raw(raw)
    selected, discarded = split_by_relevance(rows, cats, args.filtro)
    if rows and all(not (r.get('category') or r.get('categories') or '').strip() for r in rows):
        log('ATTENZIONE: le schede originali non contengono la categoria Maps: il filtro rigoroso non puo confermarle. Controlla attivita_originali.csv.')
    cache = {}
    if args.email or args.solo_email:
        # Nessuna visita inutile ai siti dei risultati gia' esclusi.
        cache = enrich_email(selected, base/'email_cache.json', workers=12)
    n = write_normalized(rows, dest, cache, categories=cats, filter_mode=args.filtro,
                         rejected_file=rejected_file)
    log(f'Filtraggio terminato: {n} attività pertinenti, {len(discarded)} risultati scartati.')
    if not (args.email or args.solo_email):
        log('Per recuperare anche le email senza rifare Maps: riesegui con --solo-email --nessuna-domanda e gli stessi filtri.')
    log(f'FINITO. CSV: {dest}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, urllib.error.URLError) as e:
        log(f'ERRORE: {e}')
        raise SystemExit(1)
