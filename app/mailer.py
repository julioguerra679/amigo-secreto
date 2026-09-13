"""
Envío de correo (opcional).

Diseño: **degradación elegante**. Si `MAIL_ENABLED=false` o faltan
credenciales SMTP, la app no falla: simplemente informa de que no se envió
nada y el admin reparte los links a mano desde el panel. El juego nunca
depende del correo.

El correo enviado contiene el link y el PIN, pero **nunca** revela a quién le
toca regalar: ese dato solo se ve tras autenticarse en la web. Así, si el
buzón de alguien se ve comprometido más tarde, la sorpresa sigue a salvo
(el PIN se puede regenerar desde el panel).
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from app.config import Settings, get_settings
from app.schemas import ParticipantCredentialsOut

logger = logging.getLogger("amigo_secreto.mailer")


@dataclass
class MailReport:
    """Resultado de un envío masivo."""

    enabled: bool
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    @property
    def summary(self) -> str:
        if not self.enabled:
            return "Envío de correos desactivado (MAIL_ENABLED=false)."
        return (
            f"Correos enviados: {self.sent}. "
            f"Sin email: {self.skipped}. Fallidos: {self.failed}."
        )


def is_configured(settings: Settings | None = None) -> bool:
    """¿Hay configuración suficiente para intentar enviar?"""
    settings = settings or get_settings()
    return bool(settings.mail_enabled and settings.smtp_host and settings.smtp_from)


def _build_message(
    cred: ParticipantCredentialsOut, settings: Settings
) -> EmailMessage:
    """Construye el correo (texto plano + HTML) para un participante."""
    message = EmailMessage()
    display_name, address = parseaddr(settings.smtp_from)
    message["From"] = formataddr((display_name or settings.app_name, address))
    message["To"] = cred.email or ""
    message["Subject"] = f"🎁 {settings.app_name} — tu acceso personal"

    text = f"""\
¡Hola, {cred.name}!

Ya estás dentro del sorteo de {settings.app_name}.

Para descubrir a quién le regalas:

  1. Abre tu link personal:
     {cred.link}

  2. Escribe tu código de 4 dígitos:
     {cred.pin}

Este link es único y personal: no lo compartas con nadie.
Nadie más puede ver tu resultado sin tu código.

¡Feliz sorteo!
"""
    message.set_content(text)

    html = f"""\
<html>
  <body style="font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
               background:#f6f7fb;padding:32px;color:#1f2430">
    <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:16px;
                padding:32px;box-shadow:0 4px 16px rgba(0,0,0,.07)">
      <h1 style="margin:0 0 8px;font-size:22px">🎁 ¡Hola, {cred.name}!</h1>
      <p style="color:#555;line-height:1.6">
        Ya estás dentro del sorteo de <strong>{settings.app_name}</strong>.
        Sigue estos dos pasos para descubrir a quién le regalas.
      </p>
      <p style="margin:24px 0">
        <a href="{cred.link}"
           style="background:#c0392b;color:#fff;text-decoration:none;
                  padding:14px 24px;border-radius:10px;display:inline-block;
                  font-weight:600">Abrir mi link personal</a>
      </p>
      <p style="line-height:1.6">Tu código de 4 dígitos es:</p>
      <p style="font-size:32px;letter-spacing:10px;font-weight:700;
                background:#f2f3f7;border-radius:10px;padding:14px 0;
                text-align:center;margin:0 0 24px">{cred.pin}</p>
      <p style="color:#888;font-size:13px;line-height:1.6">
        Este link es único y personal: no lo compartas. Nadie puede ver tu
        resultado sin tu código.
      </p>
    </div>
  </body>
</html>
"""
    message.add_alternative(html, subtype="html")
    return message


def send_credentials(
    credentials: list[ParticipantCredentialsOut],
    settings: Settings | None = None,
) -> MailReport:
    """
    Envía a cada participante con email su link y su PIN.

    Nunca lanza excepción hacia arriba: los fallos se recogen en el reporte
    para mostrarlos en el panel. Un SMTP caído no debe tumbar el sorteo.
    """
    settings = settings or get_settings()

    if not is_configured(settings):
        return MailReport(enabled=False, skipped=len(credentials))

    report = MailReport(enabled=True)
    targets = [c for c in credentials if c.email]
    report.skipped = len(credentials) - len(targets)

    if not targets:
        return report

    try:
        with _connect(settings) as server:
            for cred in targets:
                try:
                    server.send_message(_build_message(cred, settings))
                    report.sent += 1
                except Exception as exc:  # noqa: BLE001
                    report.failed += 1
                    report.errors.append(f"{cred.name}: {exc}")
                    logger.warning("Fallo enviando a %s: %s", cred.name, exc)
    except Exception as exc:  # noqa: BLE001
        report.failed += len(targets) - report.sent
        report.errors.append(f"Conexión SMTP: {exc}")
        logger.error("No se pudo conectar al servidor SMTP: %s", exc)

    return report


def _connect(settings: Settings) -> smtplib.SMTP:
    """Abre la conexión SMTP con SSL o STARTTLS según la configuración."""
    if settings.smtp_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=20
        )
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        if settings.smtp_starttls:
            server.starttls()

    if settings.smtp_user:
        server.login(settings.smtp_user, settings.smtp_password)

    return server
