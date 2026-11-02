# TURBO Leads V8: salvataggio permanente su Cloudflare R2

La V8 archivia database SQLite, progresso, CSV ricostruibili e preferenze nel
bucket privato Cloudflare R2. Il Mac non deve restare acceso e non serve Docker.

## Prima del deploy

**Scarica il CSV attuale prima di riavviare Streamlit.** Un riavvio puo cancellare
i dati temporanei non ancora copiati in R2. Dati gia persi non sono recuperabili.

## 1. Cloudflare R2

Apri https://dash.cloudflare.com/ e vai in Storage & databases > R2 > Overview.
Verifica che R2 sia attivo. Crea un bucket privato dedicato chiamato
**turbo-leads-archive**, classe Standard. Non riutilizzare il bucket dei
documenti AppointRent.

Apri Manage API tokens, crea un token con **Object Read & Write** limitato
**solo al bucket turbo-leads-archive**. Conserva Access Key ID,
Secret Access Key e l'endpoint S3 fornito da Cloudflare.
Per bucket EU usa l'endpoint specifico EU se richiesto dalla dashboard.

## 2. Streamlit Secrets

Apri https://share.streamlit.io/ > pipps-maben > Settings > Secrets.
**Non cancellare la sezione [users]** che contiene le credenziali di accesso.
Aggiungi in fondo:

```toml
[r2]
bucket = "turbo-leads-archive"
endpoint = "https://ACCOUNT_ID.r2.cloudflarestorage.com"
access_key_id = "ACCESS_KEY_ID_CLOUDFLARE"
secret_access_key = "SECRET_ACCESS_KEY_CLOUDFLARE"
prefix = "turbo-leads-v1"
```

Sostituisci solo i valori segnaposto. Non pubblicare mai le chiavi in GitHub
o chat. Salva i Secrets e riavvia l'app se la modifica non e rilevata.

## 3. Verifica e recupero

Accedi al sito e individua **Archivio permanente delle ricerche**.
Premi **Verifica collegamento R2**.
Avvia una ricerca piccola e verifica che compaia **Ultimo backup permanente R2**.
Premi **Aggiorna elenco ricerche salvate**.
Dopo un riavvio di Streamlit seleziona la ricerca archiviata e premi
**Ripristina questa ricerca e il suo CSV**.
Se il piano e incompleto, premi **Continua questa ricerca dalle attivita gia salvate**.
Premi **Salva preferenze e categorie** per archiviarle anche su R2.

## Come funziona

Ogni ricerca salva uno snapshot coerente del DB SQLite via sqlite3.backup.
Il backup ha una chiave unica; il manifesto viene aggiornato soltanto dopo
l'upload riuscito. Una copia precedente resta valida se l'upload fallisce.
I risultati e il CSV vengono rigenerati dal DB dopo il recupero.

**Limite importante:** R2 salva soltanto i dati che hanno raggiunto l'ultimo
checkpoint confermato. Una sospensione durante una ricerca puo far perdere
l'avanzamento non ancora sincronizzato. Il piano di 47.376 ricerche NON e
eseguibile istantaneamente e Google Maps non garantisce tutti i risultati.

Il bucket deve rimanere privato. Non disattivare il login dell'app.
L'opzione recupero email rimane disattivata.

R2 include soglie gratuite ma e un servizio a consumo oltre i limiti:
https://developers.cloudflare.com/r2/pricing/
API S3:
https://developers.cloudflare.com/r2/get-started/s3/
