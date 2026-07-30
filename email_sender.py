import smtplib
from email.message import EmailMessage
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv(".env")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465

def send_invoice(to_email: str, invoice_num: str, invoice_date: str, pdf_path: str) -> tuple[bool, str]:
    EMAIL = os.getenv("EMAIL")
    APP_PASSWORD = os.getenv("APP_PASSWORD")

    msg = EmailMessage()
    msg["From"] = EMAIL
    msg["To"] = to_email
    msg["Subject"] = f"Invoice {invoice_num}"

    body = ("Dear customer, "
            "\n"
            "\nThank you for your purchase. "
            f"\nPlease find a COPY of your invoice {invoice_num} from {invoice_date} in the attachment. "
            "\nFor further inquiries, please contact us at skbc.bv@gmail.com ."
            "\n"
            "\nKind regards,"
            "\nSKBC bv"
            "\n")

    msg.set_content(body)

    pdf_path = Path(pdf_path)
    with open(pdf_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=pdf_path.name
        )

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=15) as smtp:
            smtp.login(EMAIL, APP_PASSWORD)
            refused = smtp.send_message(msg)
            if refused:
                return False, f"Recipient refused: {refused}"
            return True, f"Invoice sent via email to {to_email}"
    except smtplib.SMTPRecipientsRefused as e:
        return False, f"Recipient refused: {e.recipients}"
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail authentication failed — check app password"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {e}"
    except OSError as e:
        return False, f"Connection/timeout error: {e}"