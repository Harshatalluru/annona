# Demo «Progetto Orione»: una RFQ riservata dentro Annona

Una richiesta di offerta per probe card coperta da NDA, e tre esiti:

1. **Resta qui**: il triage della RFQ gira sulla GPU locale. Se la GPU si spegne, Annona si ferma invece di usare il cloud.
2. **Esce, senza nomi**: un'email di accredito con codice fiscale e IBAN esce solo dopo la redazione locale (rizzo-pii).
3. **Va al migliore**: la nota pubblica va al modello di frontiera.

Cliente, persone, codici fiscali e IBAN in `Pratiche/` sono inventati.

## Installare (una volta)

Mac Apple Silicon o Linux, DGX Spark compreso:

```bash
git clone https://github.com/akaion-ai/annona.git
cd annona/examples/rfq-orione
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env     # facoltativo: senza, la demo è solo locale
./setup.sh
```

`setup.sh` scarica ciò che manca (uv con Python 3.12, Node 22, Ollama, rizzo-pii, il modello) e scrive la policy. Non tocca `~/.annona`: tutto resta in `.work/`.

Il modello locale si sceglie dalla RAM: 48 GB o più → `qwen2.5:32b`, altrimenti `qwen2.5:14b`. Per forzarlo: `MODEL=qwen2.5:72b ./setup.sh`.

## Usare

```bash
./run.sh      # avvia tutto e apre http://127.0.0.1:7070
./stop.sh     # spegne Annona e rizzo-pii (--all anche Ollama)
```

Nella schermata **Ask**, nell'ordine:

1. `Usa la skill rfq-triage sulla cartella <percorso>/Pratiche/Progetto-Orione. Leggi tutti i file, compresa la mail, e confronta mail e specifica.` → classe restricted, sigillata, `local-gpu`. Deve trovare il conflitto sul pitch: 40 µm nella mail, 45 µm nella specifica.
2. Spegni Ollama (`pkill ollama`) e ripeti la domanda 1 → **held**: la pratica sigillata non esce.
3. Con Ollama ancora spento: `Che documenti servono per completare l'accredito di Brianza Tecnica?` con allegata `Pratiche/Accrediti/…eml` → redatta, va alla frontiera; il pannello egress mostra cosa è uscito.
4. `Spiegami in tre righe cos'è una probe card MEMS` con allegata `Pratiche/Pubblico/Nota_tecnica_probe_card.pdf` → va alla frontiera.

Dal terminale: `ANNONA_HOME=.work/home ../../env/bin/annona why <step>` e `… annona verify`.

Per rigenerare i documenti: `../../env/bin/python build.py` (serve Chrome per i PDF).
