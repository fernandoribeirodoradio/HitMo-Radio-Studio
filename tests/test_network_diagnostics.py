from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hitmo.api_service import diagnose_media_path


class MediaPathDiagnosticTests(unittest.TestCase):
    def test_classifies_existing_file_directory_missing_and_logical_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            file_path = root / "audio.wav"
            file_path.write_bytes(b"test")
            self.assertEqual(diagnose_media_path(str(file_path))["status"], "accessible")
            self.assertEqual(diagnose_media_path(str(root), "directory")["status"], "accessible")
            self.assertEqual(diagnose_media_path(str(file_path), "directory")["status"], "wrong_type")
            self.assertEqual(diagnose_media_path(str(root / "missing.wav"))["status"], "not_found")
            self.assertEqual(diagnose_media_path("hitmo://text/example")["status"], "logical_reference")
            self.assertEqual(diagnose_media_path("")["status"], "missing_path")

    def test_classifies_permission_denied_without_returning_path(self):
        with patch("hitmo.api_service.os.stat", side_effect=PermissionError("denied")):
            result = diagnose_media_path(r"\\radio\audio\spot.wav")
        self.assertEqual(result["status"], "permission_denied")
        self.assertNotIn("path", result)

    def test_classifies_file_read_permission_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audio.wav"
            path.write_bytes(b"audio")
            with patch("builtins.open", side_effect=PermissionError("read denied")):
                result = diagnose_media_path(str(path))
        self.assertEqual(result["status"], "permission_denied")

    def test_classifies_windows_share_unavailable(self):
        unavailable = OSError("network path unavailable")
        unavailable.winerror = 53
        with patch("hitmo.api_service.os.stat", side_effect=unavailable):
            result = diagnose_media_path(r"\\radio\audio\spot.wav")
        self.assertEqual(result["status"], "share_unavailable")

    def test_classifies_temporary_windows_network_failure(self):
        temporary = OSError("network timeout")
        temporary.winerror = 121
        with patch("hitmo.api_service.os.stat", side_effect=temporary):
            result = diagnose_media_path(r"\\radio\audio\spot.wav")
        self.assertEqual(result["status"], "temporarily_unavailable")


try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:  # cryptography is optional for application users
    x509 = hashes = serialization = rsa = NameOID = None


@unittest.skipIf(x509 is None, "cryptography opcional não está instalado")
class TLSNetworkDiagnosticTests(unittest.TestCase):
    def test_valid_self_signed_ca_and_untrusted_certificate(self):
        from datetime import datetime, timedelta, timezone
        import threading

        from hitmo.api_client import HitMoAPIClient
        from hitmo.api_service import create_api_user, create_server
        from hitmo.database import Database

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
            now = datetime.now(timezone.utc)
            cert = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]), critical=False)
                .sign(key, hashes.SHA256())
            )
            cert_file, key_file = root / "test-cert.pem", root / "test-key.pem"
            cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
            db = Database(root / "hitmo.sqlite3")
            create_api_user(db, "tls-admin", "StudioTLS#Test2026", "admin")
            server = create_server(db, "127.0.0.1", 0, str(cert_file), str(key_file))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                trusted = HitMoAPIClient(f"https://localhost:{port}", "tls-admin", "StudioTLS#Test2026", ca_file=str(cert_file))
                self.assertEqual(trusted.health()["status"], "ok")
                self.assertEqual(trusted.network_diagnostics()["role"], "admin")
                untrusted = HitMoAPIClient(f"https://localhost:{port}", "tls-admin", "StudioTLS#Test2026")
                with self.assertRaises(ConnectionError):
                    untrusted.health()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
