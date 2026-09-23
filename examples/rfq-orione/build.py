"""Genera la pratica finta «Progetto Orione» per la demo di Annona.

Tutto è inventato: cliente, persone, codici fiscali, IBAN e numeri.
Uso: ../akaion-app-runner/env/bin/python build.py
"""
import subprocess
import tempfile
from email.message import EmailMessage
from pathlib import Path

import openpyxl
from docx import Document

HERE = Path(__file__).parent
OUT = HERE / "Pratiche" / "Progetto-Orione"
PUB = HERE / "Pratiche" / "Pubblico"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
body{font-family:Helvetica,Arial,sans-serif;font-size:11pt;color:#111;margin:36px}
h1{font-size:18pt;margin:0 0 4px} h2{font-size:13pt;margin:22px 0 6px}
.conf{border:2px solid #b00;color:#b00;padding:6px 10px;font-weight:bold;margin-bottom:18px}
table{border-collapse:collapse;width:100%} td,th{border:1px solid #999;padding:5px 8px;text-align:left}
th{background:#eee} .small{font-size:9pt;color:#555}
"""


def pdf(html: str, dest: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{html}</body></html>")
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={dest}", f"file://{f.name}"],
        check=True, capture_output=True,
    )


def eml(dest: Path, frm: str, to: str, subject: str, date: str, body: str) -> None:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = frm, to, subject, date
    m.set_content(body)
    dest.write_bytes(bytes(m))


def spec() -> str:
    rows = [
        ("Dispositivo", "HBM4 base die, wafer 300 mm"),
        ("Pad count", "12.480"),
        ("Pad pitch", "45 µm (minimo), layout full array"),
        ("Temperatura di test", "da −40 °C a +125 °C, hot e cold entrambi richiesti"),
        ("Touchdown", "1.000.000 lifetime; pulizia ogni 5.000"),
        ("Corrente per pin", "1,2 A (pin di alimentazione)"),
        ("Planarità", "±10 µm"),
        ("Allineamento", "±3 µm (valore tipico, da confermare)"),
        ("Quantità", "3 probe card"),
        ("Consegna richiesta", "settimana 48 / 2026"),
    ]
    tr = "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in rows)
    return f"""
<div class="conf">CONFIDENZIALE — Progetto Orione — coperto da NDA HX-2026-114</div>
<h1>Specifica tecnica probe card</h1>
<p class="small">Helix Microdevices B.V. · Documento ORI-SPEC-002 rev. 2 · 12 settembre 2026</p>
<h2>1. Oggetto</h2>
<p>Probe card per il test wafer-level del die base HBM4 del Progetto Orione. Il presente
documento prevale su qualunque sintesi inviata per email.</p>
<h2>2. Requisiti</h2>
<table><tr><th style="width:35%">Parametro</th><th>Valore</th></tr>{tr}</table>
<h2>3. Note</h2>
<p>Il layout dei pad è descritto nel disegno ORI-PH-02. Le informazioni su volumi e prezzi
obiettivo sono nel file separato e non vanno inoltrate a terzi.</p>
<p>Il pad pitch di 45 µm è vincolante. Per l'allineamento il valore di ±3 µm è indicativo.</p>
"""


def drawing() -> str:
    svg = """
<svg width="680" height="420" viewBox="0 0 680 420" xmlns="http://www.w3.org/2000/svg" font-family="Helvetica" font-size="12">
 <rect x="1" y="1" width="678" height="418" fill="none" stroke="#000"/>
 <circle cx="230" cy="200" r="150" fill="none" stroke="#000" stroke-width="2"/>
 <rect x="170" y="140" width="120" height="120" fill="none" stroke="#000" stroke-dasharray="4 3"/>
 <g fill="#000">""" + "".join(
        f'<circle cx="{180 + i * 12}" cy="{150 + j * 12}" r="2"/>' for i in range(9) for j in range(9)
    ) + """</g>
 <line x1="170" y1="280" x2="290" y2="280" stroke="#000"/><text x="200" y="296">pitch 45 µm</text>
 <text x="150" y="375">Ø 300 mm wafer</text>
 <rect x="440" y="300" width="230" height="110" fill="none" stroke="#000"/>
 <text x="450" y="322" font-weight="bold">ORI-PH-02  probe head</text>
 <text x="450" y="342">Progetto Orione</text>
 <text x="450" y="362">Helix Microdevices B.V.</text>
 <text x="450" y="382">Scala 1:2 · rev. B</text>
 <text x="450" y="402">CONFIDENZIALE — NDA HX-2026-114</text>
</svg>"""
    return f"<h1>Disegno ORI-PH-02</h1>{svg}"


def nda() -> str:
    return """
<h1>Accordo di riservatezza HX-2026-114</h1>
<p class="small">tra Helix Microdevices B.V. e il Fornitore · firmato il 2 settembre 2026</p>
<p>1. Le Informazioni Riservate relative al Progetto Orione comprendono specifiche, disegni,
volumi, prezzi obiettivo e l'esistenza stessa del progetto.</p>
<p>2. Il Fornitore conserva e tratta le Informazioni Riservate esclusivamente su sistemi
sotto il proprio controllo nell'Unione Europea, e non le comunica a terzi, inclusi fornitori
di servizi di intelligenza artificiale, senza consenso scritto.</p>
<p>3. La durata degli obblighi è di cinque anni dalla firma.</p>
"""


def volumes(dest: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Orione volumi"
    ws.append(["Progetto Orione — RISERVATO"])
    ws.append(["Anno", "Wafer/mese", "Probe card attese", "Prezzo obiettivo €/card"])
    for row in [(2027, 4000, 6, 185000), (2028, 11000, 14, 172000), (2029, 18000, 22, 160000)]:
        ws.append(row)
    wb.save(dest)


def letter(dest: Path) -> None:
    d = Document()
    d.add_heading("Nomina del referente tecnico — Progetto Orione", 1)
    d.add_paragraph("Milano, 15 settembre 2026")
    d.add_paragraph(
        "Con la presente comunichiamo che il referente tecnico per il Progetto Orione sarà "
        "l'ing. Laura Bianchi, codice fiscale BNCLRA85M41F205Z, reperibile al +39 333 123 4567 "
        "e all'indirizzo laura.bianchi@helix-micro.example. Per la fase di qualifica la "
        "sostituirà l'ing. Marco Rossi, codice fiscale RSSMRC79C12L219K."
    )
    d.add_paragraph("Cordiali saluti,\nHelix Microdevices B.V. — Ufficio Acquisti")
    d.save(dest)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    PUB.mkdir(parents=True, exist_ok=True)
    eml(
        OUT / "01_RFQ-2026-0917.eml",
        "Giulia Ferri <g.ferri@helix-micro.example>",
        "Sales Engineering <sales@fornitore.example>",
        "RFQ-2026-0917 — Progetto Orione — probe card HBM4 300 mm",
        "Wed, 17 Sep 2026 09:12:00 +0200",
        "Buongiorno,\n\nvi inviamo la richiesta di offerta RFQ-2026-0917 per il Progetto Orione "
        "(NDA HX-2026-114).\nIn sintesi: probe card per die base HBM4 su wafer 300 mm, pad pitch "
        "40 µm, circa 12.500 pad, test da -40 a +125 °C, 3 pezzi, consegna settimana 48.\n\n"
        "In allegato specifica ORI-SPEC-002 rev. 2, disegno ORI-PH-02 e il file volumi.\n"
        "Il referente tecnico vi sarà comunicato a parte.\n\nCordiali saluti\nGiulia Ferri\n"
        "Strategic Sourcing, Helix Microdevices B.V.\n",
    )
    pdf(spec(), OUT / "02_Specifica_ORI-SPEC-002_rev2.pdf")
    pdf(drawing(), OUT / "03_Disegno_ORI-PH-02.pdf")
    volumes(OUT / "04_Volumi_e_prezzi_obiettivo.xlsx")
    letter(OUT / "05_Referente_tecnico.docx")
    pdf(nda(), OUT / "06_NDA_HX-2026-114.pdf")

    # Una mail di accredito fornitore: dati personali, nessun progetto. È il caso che la
    # redazione può far uscire; la lettera 05 no, perché nomina il progetto sigillato.
    (HERE / "Pratiche" / "Accrediti").mkdir(exist_ok=True)
    eml(
        HERE / "Pratiche" / "Accrediti" / "Accredito_fornitore_Brianza_Tecnica.eml",
        "Paolo Verdi <p.verdi@brianzatecnica.example>",
        "Ufficio Acquisti <acquisti@fornitore.example>",
        "Documenti per accredito fornitore",
        "Thu, 18 Sep 2026 15:40:00 +0200",
        "Buongiorno,\n\nper completare l'accredito come fornitore di lavorazioni meccaniche vi "
        "invio i dati richiesti: legale rappresentante Paolo Verdi, codice fiscale "
        "VRDPLA72D15F704Y, IBAN IT60X0542811101000000123456.\nCapacità: fresatura 5 assi, "
        "tolleranze ±5 µm, certificazione ISO 9001.\n\nVi chiedo quali altri documenti servono "
        "e in che tempi viene di solito completato l'accredito.\n\nPaolo Verdi\n",
    )
    pdf(
        "<h1>Probe card MEMS: nota tecnica pubblica</h1><p>Una probe card collega il tester "
        "ai pad del wafer. Le tecnologie MEMS permettono pitch sotto i 50 µm e array completi "
        "con migliaia di contatti.</p>",
        PUB / "Nota_tecnica_probe_card.pdf",
    )
    print("ok:", *sorted(p.relative_to(HERE) for p in (HERE / "Pratiche").rglob("*.*")), sep="\n  ")


if __name__ == "__main__":
    main()
