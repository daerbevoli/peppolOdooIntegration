import smtplib
from email.message import EmailMessage
from pathlib import Path
import os

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465

EMAIL = os.getenv("EMAIL")
APP_PASSWORD = os.getenv("APP_PASSWORD")


def send_invoice(to_email: str, subject: str, body: str, pdf_path: str) -> tuple[bool, str]:
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

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=15) as smtp:
            smtp.login(EMAIL, APP_PASSWORD)
            refused = smtp.send_message(msg)
            if refused:
                return False, f"Recipient refused: {refused}"
            return True, f"Invoice sent → {to_email}"
    except smtplib.SMTPRecipientsRefused as e:
        return False, f"Recipient refused: {e.recipients}"
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail authentication failed — check app password"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {e}"
    except OSError as e:
        return False, f"Connection/timeout error: {e}"


if __name__ == '__main__':
    email = "eurostarmomomaki@gmail.com"
    subject = "Invoice"
    body = "Thank you for your invoice"
    path = "AsmitaBv_20260123_7361.pdf"
    send_invoice(to_email=email, subject=subject, body=body, pdf_path=path)