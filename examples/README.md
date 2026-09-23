# Esempi

Ogni esempio è una cartella con le sue pratiche finte, la sua policy e il suo README; gli script (`setup.sh`, `run.sh`, `stop.sh`) sono comuni e stanno in [`_shared`](_shared). Uno alla volta: usano tutti la porta 7070.

| Esempio | Cosa fa vedere |
|---|---|
| [`rfq-orione`](rfq-orione) | Una RFQ di probe card sotto NDA: resta sulla GPU locale, si ferma se la GPU cade, un'email con codice fiscale esce solo redatta, una domanda pubblica va al modello migliore |
| [`memoria-storica`](memoria-storica) | Una bozza di contratto che non va scritta: solo la memoria dell'azienda sa del conflitto con un cliente storico, e la memoria resta in casa |

Ogni esempio ha il suo test in `tests/test_examples.py`, che gira in CI senza modelli: se un esempio smette di comportarsi come dice il suo README, la build diventa rossa.
