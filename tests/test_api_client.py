import ssl
import unittest

from hitmo.api_client import HitMoAPIClient


class APIClientConfigurationTests(unittest.TestCase):
    def test_https_client_uses_certificate_verification(self):
        client = HitMoAPIClient("https://radio.example:8765", "editor", "secret")
        self.assertIsNotNone(client.ssl_context)
        self.assertTrue(client.ssl_context.check_hostname)
        self.assertEqual(client.ssl_context.verify_mode, ssl.CERT_REQUIRED)

    def test_http_local_client_does_not_create_tls_context(self):
        client = HitMoAPIClient("http://127.0.0.1:8765", "editor", "secret")
        self.assertIsNone(client.ssl_context)

    def test_http_credentials_are_not_sent_to_a_non_loopback_host(self):
        client = HitMoAPIClient("http://radio.example:8765", "editor", "secret")
        with self.assertRaisesRegex(ConnectionError, "credenciais não foram enviadas"):
            client.version()

    def test_server_url_rejects_embedded_secrets_and_non_service_paths(self):
        for url in ("https://user:secret@radio.example:8765", "https://radio.example:8765/api", "https://radio.example:8765/?token=secret"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "URL do serviço"):
                HitMoAPIClient(url, "editor", "secret")


if __name__ == "__main__":
    unittest.main()
