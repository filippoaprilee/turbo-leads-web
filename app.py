"""TURBO LEADS WEB — pubblicazione su Streamlit Community Cloud.

La ricerca avviene soltanto DOPO login. Non salva credenziali nel repository.
Le email non vengono cercate: il CSV conserva la colonna Email vuota.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time

import streamlit as st

import turbo_scraper as core
from turbo_scraper import FITNESS, get_dataset, search_variants
from web_backend import (JobManager, MAX_PAGES, MAX_SEARCHES, MAX_WORKERS,
                         build_categories, completed_queries, parse_geography, safe_csv_bytes,
                         select_geography, tail_log)

st.set_page_config(page_title='TURBO Leads | Ricerca attività', page_icon='📍',
                   layout='wide', initial_sidebar_state='expanded')

st.markdown('''<style>
.block-container {padding-top: 1.75rem; max-width: 1300px;}
[data-testid="stMetric"] {background: rgba(130,130,130,.055);border:1px solid rgba(130,130,130,.16);padding: .7rem;border-radius: 12px;}
</style>''', unsafe_allow_html=True)


@st.cache_resource
def manager(cache_schema_version: int = 7) -> JobManager:
    # Nuova istanza del gestore quando cambia l'interfaccia JobManager.
    # Evita istanze incompatibili rimaste in cache dopo un deploy Streamlit.
    return JobManager()


@st.cache_resource
def failed_logins():
    return {'lock': threading.Lock(), 'attempts': {}}


@st.cache_data(show_spinner='Caricamento comuni italiani…', ttl=60 * 60 * 24)
def all_municipalities():
    return parse_geography(get_dataset())


def credentials():
    try:
        return dict(st.secrets.get('users', {}))
    except (FileNotFoundError, KeyError):
        return {}


def login():
    st.title('TURBO Leads Web')
    st.caption('Accesso riservato · Ricerca attività per territorio · CSV')
    users = credentials()
    if not users:
        st.error('Nessun utente configurato. Imposta [users] nei Secrets di Streamlit Cloud (leggi README).')
        st.stop()
    if st.session_state.get('username') in users:
        if time.time() - st.session_state.get('authenticated_at', 0) < 8 * 3600:
            return st.session_state['username']
        st.session_state.pop('username', None)
    with st.form('login', clear_on_submit=True):
        u = st.text_input('Username', max_chars=80)
        p = st.text_input('Password', type='password')
        submit = st.form_submit_button('Accedi', type='primary', use_container_width=True)
    if submit:
        u = u.strip()
        guard = failed_logins()
        with guard['lock']:
            last, count = guard['attempts'].get(u, (0, 0))
            if time.time() - last > 900:
                count = 0
            locked = count >= 6
        if locked:
            st.error('Troppi tentativi. Riprovare dopo 15 minuti.')
        elif u in users and hmac.compare_digest(str(users[u]).encode(), p.encode()):
            with guard['lock']:
                guard['attempts'].pop(u, None)
            st.session_state['username'] = u
            st.session_state['authenticated_at'] = time.time()
            st.rerun()
        else:
            with guard['lock']:
                guard['attempts'][u] = (time.time(), count + 1)
            st.error('Credenziali non valide.')
    st.stop()



# Profili senza password o token: possono essere salvati nell'URL e in JSON.
PREF_DEFAULTS = {
    'p_geo': 'Comuni', 'p_zones': [], 'p_regions': [],
    'p_provs': [], 'p_cities': [], 'p_limit': 0,
    'p_allfit': False, 'p_presets': ['palestra'], 'p_manual': '',
    'p_depth': 'Approfondita', 'p_exclude': True,
    'p_custom': '', 'p_uncertain': False,
}


def read_pref_token(raw: str) -> dict:
    if not raw or len(raw) > 12000:
        return {}
    try:
        decoded = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
        obj = json.loads(decoded.decode('utf-8'))
        if not isinstance(obj, dict):
            return {}
        return {k: v for k, v in obj.items()
                if k in PREF_DEFAULTS and isinstance(v, type(PREF_DEFAULTS[k]))}
    except (ValueError, TypeError, UnicodeDecodeError):
        return {}


def pref_token(profile: dict) -> str:
    data = json.dumps(profile, ensure_ascii=False, separators=(',', ':'))
    return base64.urlsafe_b64encode(data.encode('utf-8')).decode('ascii').rstrip('=')


def apply_pref(profile: dict):
    for k, default in PREF_DEFAULTS.items():
        if k in profile and isinstance(profile[k], type(default)):
            st.session_state[k] = profile[k]


user = login()


with st.sidebar:
    st.title('TURBO LEADS')
    st.success(f'Connesso: {user}')
    st.caption('Ricerca su server online, non sul tuo Mac.')
    if st.button('Esci', use_container_width=True):
        st.session_state.pop('username', None)
        st.session_state.pop('authenticated_at', None)
        st.rerun()
    st.divider()
    st.caption('Il servizio gratuito può interrompere ricerche lunghe. Scarica il CSV appena disponibile.')

# Prima dei widget: carica preferenze salvate (URL persistente, file locale best effort).
profile_path = core.STATE / 'profili' / (hashlib.sha256(user.encode()).hexdigest()[:16] + '.json')
url_token = st.query_params.get('preset', '')
source_id = str(url_token)
if st.session_state.get('_prefs_source') != source_id:
    stored = {}
    if profile_path.exists():
        try:
            stored = json.loads(profile_path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
    for k, v in PREF_DEFAULTS.items():
        st.session_state.setdefault(k, v)
    apply_pref(stored)
    apply_pref(read_pref_token(url_token))
    st.session_state['_prefs_source'] = source_id

with st.expander('Preferenze salvate: importa o ripristina'):
    uploaded_profile = st.file_uploader('Carica un profilo JSON salvato', type=['json'], key='profile_upload')
    if uploaded_profile is not None and st.button('Ripristina queste preferenze'):
        try:
            payload = json.loads(uploaded_profile.getvalue().decode('utf-8'))
            if not isinstance(payload, dict):
                raise ValueError('Formato JSON non valido')
            apply_pref(payload)
            st.query_params.clear()
            st.session_state['_prefs_source'] = ''
            st.rerun()
        except (ValueError, UnicodeDecodeError) as err:
            st.error(f'File non valido: {err}')

st.title('Trova attività locali')
st.write('Seleziona categorie e aree geografiche. Il programma esclude per impostazione predefinita le categorie non pertinenti e crea un CSV scaricabile.')

try:
    rows = all_municipalities()
except Exception as err:
    st.error('Impossibile caricare il dataset dei comuni. Riprova oppure controlla la connessione del server.')
    st.code(str(err)[:500])
    st.stop()

zones_list = sorted({x['zone'] for x in rows if x['zone']})
region_list = sorted({x['region'] for x in rows if x['region']})
provinces_by_id = {x['prov']: x['province_name'] for x in rows}
province_options = [f'{sigla} — {provinces_by_id[sigla]}' for sigla in sorted(provinces_by_id)]
city_options = sorted({f"{x['name']} ({x['prov']})" for x in rows})

st.subheader('1. Area geografica')
geo = st.selectbox('Che cosa vuoi cercare?',
                   ['Comuni', 'Province', 'Regioni', 'Zone', 'Selezione mista', 'Tutta Italia'],
                   key='p_geo', help='La selezione mista unisce comuni, province, regioni e zone. Non si sovrappongono e non genera duplicati.')

chosen_zones = []
chosen_regions = []
chosen_provs_ui = []
chosen_cities = []
if geo in ('Zone', 'Selezione mista'):
    chosen_zones = st.multiselect('Una o più zone geografiche', zones_list, key='p_zones',
                                  placeholder='Nord-ovest, Nord-est, Centro…')
if geo in ('Regioni', 'Selezione mista'):
    chosen_regions = st.multiselect('Una o più regioni', region_list, key='p_regions',
                                   placeholder='Lombardia, Veneto, Puglia…')
if geo in ('Province', 'Selezione mista'):
    chosen_provs_ui = st.multiselect('Una o più province', province_options, key='p_provs',
                                    placeholder='MI — Milano, LE — Lecce…')
if geo in ('Comuni', 'Selezione mista'):
    if geo == 'Comuni':
        st.caption('Puoi selezionare più comuni anche di province diverse. Le sigle evitano ambiguità fra comuni con lo stesso nome.')
    chosen_cities = st.multiselect('Uno o più comuni', city_options, key='p_cities',
                                   placeholder='Lecce (LE), Milano (MI), Torino (TO)…')

chosen_provs = [s.split(' — ', 1)[0] for s in chosen_provs_ui]
selected = select_geography(rows, mode=geo, zones=chosen_zones,
                            regions=chosen_regions, provinces=chosen_provs,
                            cities=chosen_cities)
if len(selected) > 500:
    st.caption('I comuni sono ordinati per popolazione per privilegiare inizialmente le città più grandi.')
limit = st.number_input('Analizza prima i comuni più popolosi (0 = tutti)',
                        min_value=0, max_value=8500, step=25, key='p_limit')
if limit:
    selected = selected[:int(limit)]

st.subheader('2. Categorie')
category_presets = sorted(set(FITNESS + [
    'autonoleggio', 'ristorante', 'pizzeria', 'parrucchiere', 'dentista',
    'bar', 'farmacia', 'hotel', 'meccanico', 'carrozzeria', 'idraulico',
    'elettricista', 'agenzia immobiliare', 'lavanderia', 'studio medico',
]))
all_fitness = st.checkbox('Tutte le 24 categorie fitness e benessere', key='p_allfit')
presets = st.multiselect('Categorie predefinite', category_presets,
                         key='p_presets', disabled=all_fitness)
manual = st.text_input('Altre categorie personalizzate (separate da virgola)',
                       key='p_manual', placeholder='es. ferramenta, negozio animali, veterinario')
try:
    categories = build_categories(FITNESS if all_fitness else presets, manual)
    category_error = ''
except ValueError as ex:
    categories, category_error = [], str(ex)

st.subheader('3. Come vuoi cercare?')
st.caption('La modalità approfondita è già selezionata. Non devi impostare parametri tecnici.')
depth = st.radio('Tipo di ricerca',
    ['Approfondita', 'Equilibrata', 'Veloce'], key='p_depth',
    help='Approfondita cerca con sinonimi e più pagine. Su un solo comune e una sola categoria esamina anche zone diverse della città.')
st.caption({
    'Approfondita': 'Consigliata: più ricerche alternative, fino a 10 pagine per ricerca. Con un comune e una categoria controlla anche diverse zone della città.',
    'Equilibrata': 'Un compromesso: due modi di cercare la stessa categoria e cinque pagine.',
    'Veloce': 'Meno ricerche e meno pagine. Adatta per un primo controllo.',
}[depth])
exclude_chains = st.checkbox('Escludi grandi catene e franchising conosciuti', key='p_exclude')
with st.expander('Preferenze facoltative'):
    st.caption('Il riconoscimento delle catene è basato su nomi conosciuti, non su verifiche societarie. Alcune possono sfuggire.')
    custom_brands = st.text_area('Altri marchi da escludere (facoltativo)',
        key='p_custom', placeholder='Un marchio per riga, per esempio: Nome Catena')
    extra_brands = [x.strip() for x in custom_brands.replace('\n', ',').split(',') if x.strip()]
    include_uncertain = st.checkbox('Includi anche attività dalla categoria incerta', key='p_uncertain')
mode = 'ampio' if include_uncertain else 'rigoroso'
batch = {'Veloce': 80, 'Equilibrata': 60, 'Approfondita': 100}[depth]
workers = 2
pages = {'Veloce': 2, 'Equilibrata': 5, 'Approfondita': 10}[depth]
variants = search_variants(categories, depth) if categories else []
m1,m2 = st.columns(2)
m1.metric('Comuni da analizzare', len(selected))
m2.metric('Ricerche previste', len(selected)*len(variants))
if len(selected)==1 and len(categories)==1:
    st.info('Ricerca locale approfondita: dopo i risultati generali saranno esplorate anche diverse zone della città. I risultati saranno unificati.')
elif selected:
    st.caption('Su aree molto estese il sistema procede per gruppi: sul piano gratuito non può completare tutta Italia in un unico avvio.')
if category_error:
    st.warning(category_error)
if len(selected)*len(variants) > 2000:
    st.info('Piano ampio: esegui più lotti piccoli. La modalità gratuita non garantisce che l’app continui a elaborare se il server viene sospeso.')


st.subheader('Salva le tue preferenze')
profile = {k: st.session_state.get(k, v) for k, v in PREF_DEFAULTS.items()}
pref_cols = st.columns([1, 1])
with pref_cols[0]:
    if st.button('Salva preferenze e categorie', use_container_width=True):
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2),
                                encoding='utf-8')
        st.query_params['preset'] = pref_token(profile)
        st.session_state['_prefs_source'] = st.query_params['preset']
        st.success('Preferenze salvate. Aggiungi questa pagina ai preferiti del browser per riaprirle anche dopo il riavvio del server.')
with pref_cols[1]:
    st.download_button('Scarica profilo JSON', data=json.dumps(
        profile, ensure_ascii=False, indent=2).encode('utf-8'),
        file_name='turbo_leads_preferenze.json', mime='application/json',
        use_container_width=True)
st.caption('Il segnalibro e il file JSON conservano categorie, territori e marchi esclusi. Le password non vengono salvate.')

job = manager().user_job(user)
run_disabled = bool(category_error or not selected or not categories or (job and not job.done))
if st.button('Avvia / continua ricerca', type='primary', use_container_width=True, disabled=run_disabled):
    try:
        manager().submit(user=user, cities=selected, categories=categories,
                         batch=int(batch), workers=int(workers), pages=int(pages),
                         filter_mode=mode, depth=depth, exclude_chains=exclude_chains,
                         extra_brands=extra_brands)
        st.rerun()
    except (ValueError, RuntimeError, TypeError) as err:
        st.error('Non è stato possibile avviare la ricerca.')
        st.caption(f'Dettaglio tecnico: {type(err).__name__}: {str(err)[:300]}')
        st.caption('Se hai appena aggiornato il sito, riavvia l’app da Streamlit Cloud.')

st.divider()
st.subheader('4. Stato e risultati')


@st.fragment(run_every='4s')
def live_job():
    current = manager().user_job(user)
    if current is None:
        st.info('Nessuna ricerca avviata. Imposta i filtri e clicca Avvia raccolta.')
        return
    with current.lock:
        status = current.status
        found = current.found
        raw_count = current.raw_count
        rejected = current.rejected
        done = current.done
        err = current.error
    st.write('**Stato:**', status)
    finished_queries = completed_queries(current.base)
    planned_queries = max(1, current.search_count)
    st.progress(min(1.0, finished_queries / planned_queries),
                text=f'Ricerche completate: {finished_queries:,} su {planned_queries:,} — '
                     f'ne mancano {max(0, planned_queries - finished_queries):,}')
    a, b, c = st.columns(3)
    a.metric('Attività pertinenti', found)
    b.metric('Risultati grezzi', raw_count)
    c.metric('Esclusi a fine lotto', rejected)
    if not done:
        st.caption('La raccolta prosegue sul server. Puoi scaricare il CSV parziale.')
        if st.button('Interrompi ricerca', key='stop_running'):
            manager().stop(user)
            st.rerun(scope='fragment')
    if done and finished_queries < planned_queries:
        st.info('Questa sessione non ha completato l’intero territorio. Premi “Avvia / continua ricerca” sopra per ripartire dalle ricerche ancora mancanti, conservando i contatti già trovati.')
    if current.dest.exists():
        payload = safe_csv_bytes(current.dest)
        if payload:
            st.download_button('Scarica CSV attività', data=payload,
                               file_name=f'{current.name}.csv', mime='text/csv',
                               use_container_width=True)
    rejected_file = current.dest.with_name(current.dest.stem + '_scartati.csv')
    if rejected_file.exists():
        st.download_button('Scarica CSV delle attività escluse',
                           data=safe_csv_bytes(rejected_file),
                           file_name=rejected_file.name, mime='text/csv',
                           use_container_width=True)
    if err:
        st.error(err[:1200])
    with st.expander('Ultime righe del log tecnico'):
        st.code(tail_log(current.log_file))
    st.caption('Il CSV contiene: Nome, Comune, Telefono, Email. Il recupero email è disabilitato: il campo resta vuoto se non già presente nei dati originali. I file del server possono sparire con il riavvio dell’hosting.')


live_job()
