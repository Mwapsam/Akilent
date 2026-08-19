"""SMTP relay listener using aiosmtpd.

Provides a custom SMTP server that:
1. Authenticates clients against SmtpCredential hashed secrets
2. Accepts email submissions
3. Hands off to SES (or other send provider) for delivery

Run as a separate service/process:
    python manage.py smtp_relay_server
"""
import asyncio
import hashlib
import logging
from email.parser import BytesParser

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPProtocol
from aiosmtpd.smtp import AuthResult
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class AuthHandler:
    """Handles SMTP AUTH LOGIN and PLAIN mechanisms."""

    async def handle_AUTH(self, server, session, envelope, args):
        """Validate SMTP credentials against SmtpCredential table."""
        if len(args) < 1:
            return False

        mech = args[0].upper()
        if mech == "LOGIN":
            return await self._auth_login(session)
        elif mech == "PLAIN":
            return await self._auth_plain(session, args)
        return False

    async def _auth_login(self, session):
        """Handle AUTH LOGIN (two-step: username, then password)."""
        await session.challenge_auth(b"Username:")
        username_b64 = await session.read_response()

        await session.challenge_auth(b"Password:")
        password_b64 = await session.read_response()

        return self._verify_credentials(username_b64, password_b64)

    async def _auth_plain(self, session, args):
        """Handle AUTH PLAIN (credentials in single base64-encoded string)."""
        if len(args) > 1:
            # Credentials provided inline
            creds = args[1]
        else:
            # Credentials provided after challenge
            creds = await session.read_response()

        return self._verify_credentials_plain(creds)

    @staticmethod
    def _verify_credentials(username_b64: str, password_b64: str) -> AuthResult | bool:
        """Verify username/password from LOGIN mechanism."""
        import base64

        try:
            username = base64.b64decode(username_b64).decode()
            password = base64.b64decode(password_b64).decode()
        except Exception:
            logger.warning("Failed to decode AUTH LOGIN credentials")
            return False

        return AuthHandler._check_smtp_credential(username, password)

    @staticmethod
    def _verify_credentials_plain(creds: str) -> AuthResult | bool:
        """Verify credentials from PLAIN mechanism.

        Format: [authzid]\0authcid\0passwd (base64-encoded)
        """
        import base64

        try:
            decoded = base64.b64decode(creds).decode()
            parts = decoded.split("\0")
            if len(parts) == 3:
                # authzid, authcid, passwd
                username, password = parts[1], parts[2]
            elif len(parts) == 2:
                # authcid, passwd
                username, password = parts
            else:
                return False
        except Exception:
            logger.warning("Failed to decode AUTH PLAIN credentials")
            return False

        return AuthHandler._check_smtp_credential(username, password)

    @staticmethod
    def _check_smtp_credential(username: str, password: str) -> AuthResult | bool:
        """Look up and verify the credential in the database."""
        from apps.email.models import SmtpCredential

        try:
            cred = SmtpCredential.objects.select_related("account", "domain").get(
                username=username.lower(), is_active=True
            )
        except SmtpCredential.DoesNotExist:
            logger.warning(f"SMTP auth failed: credential not found for {username}")
            return False

        # Hash the provided password and compare
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        if password_hash != cred.secret_hash:
            logger.warning(f"SMTP auth failed: invalid password for {username}")
            return False

        logger.info(f"SMTP auth successful: {username} (account={cred.account_id})")
        # Return AuthResult with user info (username is passed to handle_DATA)
        return AuthResult(success=True, identity=username)


class RelayHandler:
    """Handles incoming SMTP messages and delivers via SES."""

    async def handle_DATA(self, server, session, envelope):
        """Accept email and queue for delivery via SES."""
        if not session.authenticated:
            return "530 Authentication required"

        # Get credential and account
        from apps.email.models import SmtpCredential
        from apps.api.services import create_and_queue_message

        username = session.authenticated
        try:
            cred = SmtpCredential.objects.select_related("account", "domain").get(
                username=username
            )
        except SmtpCredential.DoesNotExist:
            logger.warning(f"SMTP: credential disappeared after auth: {username}")
            return "500 Authentication state lost"

        # Parse the email
        parser = BytesParser()
        msg = parser.parsebytes(envelope.content)

        # Extract headers
        from_email = envelope.mail_from or msg.get("From", "")
        to_email = envelope.rcpt_tos[0] if envelope.rcpt_tos else ""

        if not from_email or not to_email:
            return "550 Missing From or To header"

        # Queue for delivery
        try:
            create_and_queue_message(
                account=cred.account,
                from_email=from_email,
                to_email=to_email,
                subject=msg.get("Subject", ""),
                text_body=msg.get_payload() if msg.is_multipart() is False else "",
                html_body=msg.get_payload(0).get_payload() if msg.is_multipart() else "",
            )
            logger.info(
                f"SMTP: queued message from {username} to {to_email}"
            )
            return "250 Message accepted"
        except Exception as exc:
            logger.error(f"SMTP: failed to queue message: {exc}")
            return f"550 Failed to queue message: {str(exc)[:100]}"


class SMTPRelayServer(SMTPProtocol):
    """Custom SMTP protocol handler combining auth and relay."""

    async def smtp_AUTH(self, *args):
        """Handle AUTH command."""
        if not self.session.authenticated:
            handler = AuthHandler()
            result = await handler.handle_AUTH(self, self.session, None, args)
            if result:
                self.session.authenticated = result.identity if isinstance(result, AuthResult) else args[1]
                return "235 2.7.0 Authentication successful"
        return "503 Already authenticated"

    async def smtp_DATA(self):
        """Handle DATA command after client sends body."""
        if not self.session.authenticated:
            await self.push("530 Authentication required")
            return

        handler = RelayHandler()
        result = await handler.handle_DATA(self, self.session, self.envelope)
        await self.push(result)


def make_smtp_relay_server(host="0.0.0.0", port=2587):
    """Factory for the SMTP relay server."""
    controller = Controller(
        SMTPRelayServer,
        hostname=host,
        port=port,
        auth_required=True,
        auth_required_insecure=False,
    )
    return controller


# Django management command
class Command(BaseCommand):
    """Run the SMTP relay server."""

    help = "Start the SMTP relay server (listens on port 2587)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--host",
            type=str,
            default="127.0.0.1",
            help="Host to bind to (default: 127.0.0.1)",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=2587,
            help="Port to listen on (default: 2587)",
        )

    def handle(self, *args, **options):
        host = options["host"]
        port = options["port"]

        self.stdout.write(f"Starting SMTP relay server on {host}:{port}")
        self.stdout.write("Press Ctrl+C to stop")

        controller = make_smtp_relay_server(host=host, port=port)
        with controller:
            asyncio.run(asyncio.sleep(float("inf")))
