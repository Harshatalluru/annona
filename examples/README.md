# Esempi

Ogni esempio è una cartella con le sue pratiche finte, la sua policy e il suo README; gli script (`setup.sh`, `run.sh`, `stop.sh`) sono comuni e stanno in [`_shared`](_shared). Uno alla volta: usano tutti la porta 7070.

| Esempio | Cosa fa vedere |
|---|---|
| [`rfq-orione`](rfq-orione) | Una RFQ di probe card sotto NDA: resta sulla GPU locale, si ferma se la GPU cade, un'email con codice fiscale esce solo redatta, una domanda pubblica va al modello migliore |
| [`memoria-storica`](memoria-storica) | Una bozza di contratto che non va scritta: solo la memoria dell'azienda sa del conflitto con un cliente storico, e la memoria resta in casa |

Ogni esempio ha il suo test in `tests/test_examples.py`, che gira in CI senza modelli: se un esempio smette di comportarsi come dice il suo README, la build diventa rossa.

## Prossimi esempi

Ognuno nasce con le sue pratiche finte, la sua policy e il suo test in `tests/test_examples.py`: un esempio senza test non entra.

| Esempio | Pratiche finte | Cosa dimostra | Esiti che diventano test | Dipende da |
|---|---|---|---|---|
| `studio-legale` | Tre fascicoli di clienti diversi (atti, PEC, visure), un parere di diritto pubblico senza dati | Classe per cartella di cliente; una skill `scadenze` che estrae termini dagli atti, in locale; il numero di ruolo (`RG \d+/\d{4}`) come sigillo | un fascicolo resta in locale; la domanda di diritto pubblico va al modello migliore; un atto con codice fiscale esce solo redatto; il nome di un cliente non compare nel lavoro su un altro | niente: si fa oggi |
| `sanita-hr` | Uno studio DICOM (intestazione finta), cartelle del personale, una circolare pubblica | Il lettore DICOM e la classe `restricted` per `*.dcm`; un codice fiscale **scritto nel prompt** blocca il primo turno | il DICOM non esce mai; il CF digitato ferma la frontiera prima di partire; la circolare va fuori | niente: si fa oggi |
| `fatture-p7m` | Venti FatturaPA `.xml.p7m` firmate, due con importi incoerenti | Il lettore delle buste firmate; una skill `riconcilia` locale; un *brief* (totali per mese, senza P.IVA né nomi) che può uscire | le fatture restano in locale; l'anomalia è trovata; il brief esce e non contiene una P.IVA; il registro lo dimostra | niente: si fa oggi |
| `dgx-condivisa` | La memoria storica di `memoria-storica` più una cartella legale; due persone dietro un proxy | Identità da proxy, regole per gruppo, memoria per gruppo, soggetto nel registro — [`docs/design/multi-user.md`](../docs/design/multi-user.md) | vendite recupera il verbale Veloce, legale no; una richiesta senza il segreto del proxy è rifiutata; `annona audit --subject` separa le due persone | U1–U3 |
| `monitoraggio` | Un generatore di carico: dieci domande in parallelo su tre esempi | tokens/s, coda, GPU e VRAM, holds ed egress in Grafana e nella vista Monitor — [`docs/design/observability.md`](../docs/design/observability.md) | `/metrics` espone le serie previste e **nessun percorso né nome**; con `max_concurrency: 1` la seconda richiesta aspetta invece di uscire; la dashboard ha dati entro 30 s | O1–O6 |
