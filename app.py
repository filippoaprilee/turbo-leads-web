"""TURBO LEADS WEB — pubblicazione su Streamlit Community Cloud.

La ricerca avviene soltanto DOPO login. Non salva credenziali nel repository.
Le email non vengono cercate: il CSV conserva la colonna Email vuota.
"""
from __future__ import annotations

import hmac
import threading
import time

import streamlit as st

from turbo_scraper import FITNESS, get_dataset
from web_backend import (JobManager, MAX_PAGES, MAX_SEARCHES, MAX_WORKERS,
                         build_categories, parse_geography, safe_csv_bytes,
                         select_geography, tail_log)

st.set_page_config(page_title='TURBO Leads | Ricerca attività', page_icon='📍',
                   layout='wide', initial_sidebar_state='expanded')

st.markdown('''<style>
.block-container {padding-top: 1.75rem; max-width: 1300px;}
[data-testid="stMetric"] {background: rgba(130,130,130,.055);border:1px solid rgba(130,130,130,.16);padding: .7rem;border-radius: 12px;}
</style>''', unsafe_allow_html=True)


@st.cache_resource
def manager() -> JobManager:
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
    st.caption('Limiti cautelativi del piano gratuito: una ricerca alla volta, massimo 200 query per lotto, massimo 4 lavoratori. I dati non sono archivio permanente.')

st.title('Ricerca attività in Italia')
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
                   help='La selezione mista unisce comuni, province, regioni e zone. Non si sovrappongono e non genera duplicati.')

chosen_zones = []
chosen_regions = []
chosen_provs_ui = []
chosen_cities = []
if geo in ('Zone', 'Selezione mista'):
    chosen_zones = st.multiselect('Una o più zone geografiche', zones_list,
                                  placeholder='Nord-ovest, Nord-est, Centro…')
if geo in ('Regioni', 'Selezione mista'):
    chosen_regions = st.multiselect('Una o più regioni', region_list,
                                   placeholder='Lombardia, Veneto, Puglia…')
if geo in ('Province', 'Selezione mista'):
    chosen_provs_ui = st.multiselect('Una o più province', province_options,
                                    placeholder='MI — Milano, LE — Lecce…')
if geo in ('Comuni', 'Selezione mista'):
    if geo == 'Comuni':
        st.caption('Puoi selezionare più comuni anche di province diverse. Le sigle evitano ambiguità fra comuni con lo stesso nome.')
    chosen_cities = st.multiselect('Uno o più comuni', city_options,
                                   placeholder='Lecce (LE), Milano (MI), Torino (TO)…')

chosen_provs = [s.split(' — ', 1)[0] for s in chosen_provs_ui]
selected = select_geography(rows, mode=geo, zones=chosen_zones,
                            regions=chosen_regions, provinces=chosen_provs,
                            cities=chosen_cities)
if len(selected) > 500:
    st.caption('I comuni sono ordinati per popolazione per privilegiare inizialmente le città più grandi.')
limit = st.number_input('Limita il piano ai primi N comuni (0 = tutti i selezionati)',
                        min_value=0, max_value=8500, value=0, step=25)
if limit:
    selected = selected[:int(limit)]

st.subheader('2. Categorie')
category_presets = sorted(set(FITNESS + [
    'autonoleggio', 'ristorante', 'pizzeria', 'parrucchiere', 'dentista',
    'bar', 'farmacia', 'hotel', 'meccanico', 'carrozzeria', 'idraulico',
    'elettricista', 'agenzia immobiliare', 'lavanderia', 'studio medico',
]))
all_fitness = st.checkbox('Tutte le 24 categorie fitness e benessere', value=False)
presets = st.multiselect('Categorie predefinite', category_presets,
                         default=['palestra'] if not all_fitness else FITNESS,
                         disabled=all_fitness)
manual = st.text_input('Altre categorie personalizzate (separate da virgola)',
                       placeholder='es. ferramenta, negozio animali, veterinario')
try:
    categories = build_categories(FITNESS if all_fitness else presets, manual)
    category_error = ''
except ValueError as ex:
    categories, category_error = [], str(ex)

st.subheader('3. Precisione e velocità')
c1, c2, c3, c4 = st.columns(4)
with c1:
    batch = st.number_input('Ricerche per lotto', min_value=1, max_value=MAX_SEARCHES,
                            value=60, step=10)
with c2:
    workers = st.slider('Ricerche parallele', 1, MAX_WORKERS, 2)
with c3:
    pages = st.slider('Pagine per ricerca', 1, MAX_PAGES, 2)
with c4:
    mode = st.selectbox('Filtro categoria', ['rigoroso', 'ampio'],
                        help='Rigoroso: scarta le schede senza categoria verificabile.')

c_a, c_b, c_c = st.columns(3)
c_a.metric('Comuni selezionati', f'{len(selected):,}'.replace(',', '.'))
c_b.metric('Categorie', len(categories))
c_c.metric('Ricerche nel piano', f'{len(selected)*len(categories):,}'.replace(',', '.'))
st.caption('Lotto = nuove ricerche per avvio, non numero di aziende. Ripetendo la ricerca identica il motore può riprendere dal database temporaneo finché il server conserva i file.')
if category_error:
    st.warning(category_error)
if len(selected)*len(categories) > 2000:
    st.info('Piano ampio: esegui più lotti piccoli. La modalità gratuita non garantisce che l’app continui a elaborare se il server viene sospeso.')

job = manager().user_job(user)
run_disabled = bool(category_error or not selected or not categories or (job and not job.done))
if st.button('Avvia raccolta', type='primary', use_container_width=True, disabled=run_disabled):
    try:
        manager().submit(user=user, cities=selected, categories=categories,
                         batch=int(batch), workers=int(workers), pages=int(pages),
                         filter_mode=mode)
        st.rerun()
    except (ValueError, RuntimeError) as err:
        st.error(str(err))

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
    a, b, c = st.columns(3)
    a.metric('Attività pertinenti', found)
    b.metric('Risultati grezzi', raw_count)
    c.metric('Esclusi a fine lotto', rejected)
    if not done:
        st.progress(0, text='Ricerca sul server in corso…')
        if st.button('Interrompi ricerca', key='stop_running'):
            manager().stop(user)
            st.rerun(scope='fragment')
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
