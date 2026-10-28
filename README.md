# TURBO LEADS WEB — Streamlit Community Cloud

Versione online dello scraper TURBO V3 con accesso username/password, selezione multipla geografica e CSV. **Nessun Mac acceso, nessun Docker e nessuna API key di pagamento richiesta** per la configurazione. L'app gira sul server gratuito di Streamlit Community Cloud, che può imporre limiti, sospensioni e restrizioni sulle chiamate automatizzate.

## Funzioni

- Login con **username e password** decisi dal proprietario (più utenti configurabili).
- Scelta di **uno o più comuni, province, regioni, zone geografiche** (Nord-ovest, Nord-est, Centro, Sud, Isole) o **selezione mista** che unisce tutte le aree scelte.
- Selezione di qualsiasi categoria o più categorie personalizzate; 24 categorie fitness preimpostate.
- Filtraggio **rigoroso** o **ampio** della categoria effettiva; esportazione dei risultati scartati.
- Download CSV compatibile Excel: `Nome;Comune;Telefono;Email`.
- Possibilità di interrompere l'elaborazione, rivedere il risultato parziale e scaricarlo.
- **Nessuna opzione per il recupero email:** il crawler per le email è disabilitato. La colonna Email è conservata solo per compatibilità; normalmente è vuota.
- Lotti limitati (massimo 200 ricerche), un solo lotto in esecuzione alla volta; lo stesso utente può ripetere una ricerca identica per tentare di proseguire finché il server conserva i file.

## Pubblicazione online, solo da browser (senza Terminale)

**1. GitHub**

- Accedi a https://github.com e crea un nuovo repository, ad esempio `turbo-leads-web` (meglio **privato**, per mantenere il codice non pubblico).
- Carica nella **radice** del repository `app.py`, `web_backend.py`, `turbo_scraper.py`, `requirements.txt`, `README.md`, `secrets.example.toml`, `.gitignore`, la cartella `.streamlit` con `config.toml`, eventualmente `tests/`.
- **NON caricare password nel repository.** Il file `secrets.example.toml` contiene soltanto valori fittizi.

**2. Streamlit Community Cloud**

- Vai su https://share.streamlit.io, entra con il tuo account e collega GitHub.
- Premi **Create app** → **Yup, I have an app**.
- Seleziona il repository, branch `main`, file principale `app.py`.
- Apri **Advanced settings** → **Secrets** e incolla **modificando entrambe le password**:

```toml
[users]
amministratore = "PASSWORD-FORTE-PER-TE-DA-CAMBIARE"
amico = "PASSWORD-FORTE-PER-L-AMICO-DA-CAMBIARE"
```

- Imposta Python **3.12** (se ti viene chiesto) e pubblica l'app. Avrai un indirizzo simile a `https://turbo-leads-web.streamlit.app` (è un esempio, l'URL effettivo dipende dal nome disponibile e dall'assegnazione del servizio).
- Imposta la **visibilità pubblica dell'app**, altrimenti Streamlit potrebbe richiedere un proprio login anche al tuo amico. **L'applicazione presenta comunque il tuo login username/password.** Il repository GitHub può restare privato.
- Condividi l'URL con il tuo amico assieme alle sue credenziali, usando un canale separato e sicuro.

**3. Gestione utenti**

Per aggiungere o cambiare un accesso, apri Streamlit Cloud → menu dell'app → **Settings** → **Secrets**. Aggiorna la sezione `[users]`. Non modificare il codice. Scegli password uniche e lunghe, preferibilmente 16+ caratteri.

## Istruzioni per l'utente finale

1. Apri l'URL e accedi.
2. Scegli `Comuni`, `Province`, `Regioni`, `Zone`, `Selezione mista` o `Tutta Italia`.
3. Seleziona più località nell'elenco, oppure unisci località diverse tramite la selezione mista.
4. Seleziona una o più categorie (es. `palestra`, `fisioterapista`, `centro estetico`) o digita categorie libere.
5. Lascia il filtro `rigoroso`, scegli un lotto da 40 a 100 e 2 lavoratori per il primo test. Clicca **Avvia raccolta**.
6. Quando appaiono le aziende, scarica il **CSV attività**; puoi scaricare anche risultati parziali.
7. Se la pagina si blocca per limite del servizio, ripeti lo stesso piano dopo aver riaperto l'app. Non c'è garanzia di conservazione dei dati tra i riavvii.

## Limiti reali: leggere prima di usarlo

- Il piano **Streamlit Community Cloud è gratuito** ma non è un server sempre acceso né offre lavoro continuativo garantito. Durante le sospensioni le ricerche si interrompono e il filesystem locale può essere perso. Se ti servono migliaia di lotti continuativi o conservazione sicura, serve un hosting adatto o un database esterno.
- Il motore di scraping usa l'endpoint non ufficiale di Google Maps del progetto MIT `sonagara-vashram/google-maps-scraper`, scaricato da GitHub al primo avvio dell'istanza. **Le richieste possono fallire**, essere limitate da Google o non funzionare dal server Streamlit. Non ci sono garanzie di numero di risultati né di velocità.
- **Termini Google Maps:** Google limita download massivi, copiatura e costruzione di elenchi di attività. Valuta i termini applicabili e usa fonti/licenze autorizzate per dataset da sfruttare commercialmente. I dati di contatto pubblici non autorizzano automaticamente invii promozionali: rispetta GDPR e normativa italiana su comunicazioni commerciali.
- Il filtro rigoroso scarta le schede senza categoria o con categoria non compatibile, quindi può produrre elenchi incompleti.
- `Zone` indica le **macrozone italiane**, non i quartieri: per quartieri o località specifiche scegli i relativi comuni.
- I CSV sono visibili soltanto attraverso la sessione loggata del rispettivo username, ma i file sono memorizzati **temporaneamente** sul filesystem del server. Per esigenze di riservatezza elevate serve un'infrastruttura privata.

## Struttura

- `app.py`: interfaccia web, login, selezioni, download.
- `web_backend.py`: selezione territoriale, controlli di sicurezza e gestione di lotti in thread.
- `turbo_scraper.py`: motore della V3, categorie, filtro e preparazione CSV.
- `requirements.txt`: dipendenze installate automaticamente da Streamlit.
- `.streamlit/config.toml`: tema.
- `secrets.example.toml`: **soltanto modello** per impostare gli account.
- `tests/test_web_backend.py`: test senza richieste a Google Maps.

## Test locali (facoltativi)

```bash
python3 -m unittest discover -s tests -v
```

Per avviare sul proprio computer **solo per un test facoltativo**, non richiesto in produzione:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Il collaudo in rete contro Google Maps va eseguito **dopo il deploy**: qui sono state verificate soltanto la logica, la correttezza locale e l'esportazione dei file.
