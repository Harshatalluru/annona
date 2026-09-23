"""Helpers the examples use to generate their fictional documents.

PDFs are printed by a headless Chrome (the only dependency not in the venv);
emails are real RFC 5322 messages; Office files come from openpyxl and
python-docx, which the runner already installs for its readers.
"""

import subprocess
import tempfile
from email.message import EmailMessage
from pathlib import Path

__all__ = ["pdf", "eml"]

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
