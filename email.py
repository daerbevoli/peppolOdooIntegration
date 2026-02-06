import smtplib
from email.message import EmailMessage
from pathlib import Path

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465

EMAIL = "you@gmail.com"
APP_PASSWORD = ""

def send_invoice(
    to_email: str,
    subject: str,
    body: str,
    pdf_path: str
):
    msg = EmailMessage()
    msg["From"] = EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    pdf_path = Path(pdf_path)
    with open(pdf_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=pdf_path.name
        )

    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.login(EMAIL, APP_PASSWORD)
        smtp.send_message(msg)

    print(f"Invoice sent → {to_email}")