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


if __name__ == "__main__":
    unittest.main()
