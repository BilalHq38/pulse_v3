from services import email_service


def test_tenant_sender_name_uses_company_name():
    assert email_service._tenant_sender_name("Acme Co") == "Message from Acme Co"
    assert email_service._tenant_sender_name("", "Fallback Co") == "Message from Fallback Co"


def test_tenant_smtp_from_header_uses_sender_label(monkeypatch):
    captured = {}

    class DummySMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            return None

        def starttls(self):
            return None

        def login(self, *_args):
            return None

        def send_message(self, message):
            captured["from"] = message["From"]

    monkeypatch.setattr(email_service.smtplib, "SMTP", DummySMTP)

    email_service._send_smtp_tenant(
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_user="sender@example.test",
        smtp_pass="secret",
        smtp_from="sender@example.test",
        to_email="to@example.test",
        subject="Hello",
        body="Body",
        html_body="",
        use_tls=True,
        sender_name="Message from Acme Co",
    )

    assert captured["from"] == "Message from Acme Co <sender@example.test>"
