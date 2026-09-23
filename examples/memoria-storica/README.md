# Esempio «Memoria storica»: il contratto che non va scritto

Il CEO di Meccatronica Lariana chiede all'ufficio vendite una bozza di contratto per **Nordika Mobility**: 4 banchi di test BMS, condizioni standard.

Nella richiesta non c'è nessun problema. Il problema è nella **memoria dell'azienda**, e solo lì:

- Nordika è partner commerciale diretto di **Veloce Automotive** sulla piattaforma Kappa (verbale del 12 marzo);
- Veloce è cliente dal 2014 e vale il 38% del fatturato (anagrafica, note della direzione);
- l'NDA con Veloce, clausola 7.3, impone di avvisarli prima di lavorare con i loro partner.

Un modello di frontiera non può saperlo, e la policy non deve permettergli di scoprirlo. Annona indicizza la memoria **in locale** (`bge-m3` su Ollama), estrae la mappa di chi è partner di chi con il modello locale, e la interroga prima del primo turno. Quello che recupera porta con sé il percorso del documento: il run diventa restricted e sigillato, resta sulla GPU in ufficio, e la skill `conflict-check` si ferma per una decisione del CEO.

Aziende, persone, quote e clausole in `Pratiche/` sono inventate.

## Installare (una volta)

```bash
git clone https://github.com/akaion-ai/annona.git
cd annona/examples/memoria-storica
./setup.sh
```

Scarica ciò che manca (gli strumenti comuni stanno in `../_shared/.work`), il modello locale e `bge-m3`, scrive la policy, installa `conflict-check` e indicizza la memoria con le relazioni (un paio di minuti). Per una frontiera facoltativa: `GCP_PROJECT=...` (Vertex, UE) o `ANTHROPIC_API_KEY=...` in `.env`.

## Usare

```bash
./run.sh      # un esempio alla volta: se c'è l'altro acceso, prima ./stop.sh
```

Nella schermata **Ask**:

1. `Prepara la bozza di contratto per Nordika Mobility, come chiesto dal CEO.`
   → sotto la risposta, **Retrieved from memory**: la catena Nordika → partner di Veloce → cliente → NDA 7.3, con i file da cui viene. Classe restricted, sigillata (`Veloce Automotive`), `local-gpu`. Il modello non scrive la bozza e chiede la decisione del CEO.
2. Spegni Ollama (`pkill ollama`) e ripeti → **held**. La memoria funziona anche senza embedder: cerca per nomi.
3. `Spiegami cos'è un banco di test BMS` → la memoria non si attiva (nessun nome che conosce) e la domanda resta pubblica.

Dal terminale:

```bash
export ANNONA_HOME=.work/home
../../env/bin/annona memory status
../../env/bin/annona memory search "Nordika"      # relazioni e passaggi, come li vede il modello
../../env/bin/annona audit                          # la voce «retrieval» elenca i file usati, mai i passaggi
```

Per rigenerare i documenti: `../../env/bin/python build.py` (serve Chrome per il PDF).
