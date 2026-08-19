"""Management command to start the SMTP relay server."""
import asyncio
import hashlib
import logging

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPProtocol, AuthResult
from django.core.management.base import BaseCommand
from email.parser import BytesParser

logger = logging.getLogger(__name__)


class SMTPRelayHandler:
    """Combined AUTH and DATA handler for SMTP relay."""

    async def handle_AUTH(self, args):
        """Validate SMTP credentials."""
        if len(args) < 1:
            return False

        mech = args[0].upper()
        if mech == "LOGIN":
            return await self._auth_login()
        elif mech == "PLAIN":
            return await self._auth_plain(args)
        return False

    async def _auth_login(self):
        """Handle AUTH LOGIN."""
        raise NotImplementedError("Use aiosmtpd's built-in AUTH")

    async def _auth_plain(self, args):
        """Handle AUTH PLAIN."""
        raise NotImplementedError("Use aiosmtpd's built-in AUTH")

    @staticmethod
    def _verify_credential(username: str, password: str) -> bool:
        """Verify SMTP credential against database."""
        from apps.email.models import SmtpCredential

        try:
            cred = SmtpCredential.objects.get(
                username=username.lower(), is_active=True
            )
        except SmtpCredential.DoesNotExist:
            return False

        password_hash = hashlib.sha256(password.encode()).hexdigest()
        return password_hash == cred.secret_hash


class SMTPRelayServer(SMTPProtocol):
    """SMTP server with credential validation and SES delivery."""

    async def smtp_EHLO(self, hostname):
        """Announce AUTH capabilities."""
        self.peer = self._peer
        await self.push(f"250-{self.hostname} Hello {hostname}")
        await self.push(b"250-AUTH LOGIN PLAIN")
        await self.push(b"250-STARTTLS")
        await self.push(b"250 HELP")
        return True

    async def smtp_AUTH(self, *args):
        """Handle AUTH command using aiosmtpd built-in mechanism."""
        # aiosmtpd handles AUTH automatically if auth_required=True
        # and we provide an auth_required_insecure callback
        return NotImplemented

    async def handle_DATA(self, server, session, envelope):
        """Accept and queue email for delivery."""
        if not session.authenticated:
            return "530 Authentication required"

        from apps.email.models import SmtpCredential
        from apps.api.services import create_and_queue_message

        username = session.authenticated
        try:
            cred = SmtpCredential.objects.select_related("account", "domain").get(
                username=username
            )
        except SmtpCredential.DoesNotExist:
            logger.warning(f"SMTP: credential not found: {username}")
            return "500 Authentication state lost"

        # Parse email
        parser = BytesParser()
        msg = parser.parsebytes(envelope.content)

        from_email = envelope.mail_from or msg.get("From", "")
        to_email = envelope.rcpt_tos[0] if envelope.rcpt_tos else ""

        if not from_email or not to_email:
            return "550 Missing From or To"

        try:
            create_and_queue_message(
                account=cred.account,
                from_email=from_email,
                to_email=to_email,
                subject=msg.get("Subject", ""),
                text_body=msg.get_payload() if not msg.is_multipart() else "",
                html_body="",
            )
            logger.info(f"SMTP: queued from {username} to {to_email}")
            return "250 Message accepted"
        except Exception as exc:
            logger.error(f"SMTP: {exc}")
            return "550 Failed to queue message"


class Command(BaseCommand):
    """Start SMTP relay server."""

    help = "Start SMTP relay server on port 2587"

    def add_arguments(self, parser):
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=2587)

    def handle(self, *args, **options):
        host = options["host"]
        port = options["port"]

        self.stdout.write(f"Starting SMTP relay on {host}:{port}")

        def auth_callback(server, session, envelope, mechanism, auth_data):
            """Verify SMTP credentials."""
            if mechanism == "LOGIN":
                # auth_data is list: [username, password]
                username, password = auth_data
            elif mechanism == "PLAIN":
                # auth_data is bytes: \0username\0password (base64 encoded)
                import base64
                try:
                    decoded = base64.b64decode(auth_data).decode()
                    parts = decoded.split("\0")
                    username = parts[1] if len(parts) >= 2 else parts[0]
                    password = parts[2] if len(parts) >= 3 else parts[1]
                except:
                    return AuthResult(success=False)
            else:
                return AuthResult(success=False)

            from apps.email.models import SmtpCredential
            try:
                cred = SmtpCredential.objects.get(
                    username=username.lower(), is_active=True
                )
                password_hash = hashlib.sha256(password.encode()).hexdigest()
                if password_hash == cred.secret_hash:
                    return AuthResult(success=True, identity=username)
            except:
                pass
            return AuthResult(success=False)

        controller = Controller(
            SMTPRelayServer,
            hostname=host,
            port=port,
            auth_required=True,
            auth_required_insecure=False,
            auth_callback=auth_callback,
        )

        with controller:
            self.stdout.write("Press Ctrl+C to stop")
            try:
                asyncio.run(asyncio.sleep(float("inf")))
            except KeyboardInterrupt:
                self.stdout.write("Stopping SMTP relay server")
