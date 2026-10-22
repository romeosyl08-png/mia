"""Vérifications transverses : en-têtes de sécurité, CSP, sonde de santé."""

import base64
import hashlib
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings


class SecurityHeadersTests(SimpleTestCase):
    def test_pages_are_served_with_a_content_security_policy(self):
        response = self.client.get("/auth/")
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("base-uri 'self'", policy)
        # Aucune bibliothèque tierce ne doit être exécutée.
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("cdnjs", policy)

    def test_wasm_compilation_is_allowed_without_enabling_javascript_eval(self):
        policy = self.client.get("/auth/").headers["Content-Security-Policy"]
        script_sources = next(
            directive.split()[1:] for directive in policy.split(";")
            if directive.strip().startswith("script-src ")
        )
        self.assertIn("'wasm-unsafe-eval'", script_sources)
        self.assertNotIn("'unsafe-eval'", script_sources)
        self.assertNotIn("'unsafe-inline'", script_sources)

    def test_security_headers_are_present(self):
        response = self.client.get("/auth/")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("geolocation=()", response.headers["Permissions-Policy"])

    def test_forms_allow_redirects_to_only_the_trusted_payment_providers(self):
        response = self.client.get("/auth/")
        policy = response.headers["Content-Security-Policy"]
        form_action = next(
            directive.split()[1:]
            for directive in policy.split(";")
            if directive.strip().startswith("form-action ")
        )
        self.assertEqual(set(form_action), {
            "'self'",
            "https://app.paydunya.com",
            "https://paydunya.com",
            "https://checkout.stripe.com",
        })

    def test_the_local_ai_stays_reachable_from_the_browser(self):
        """Dérogation assumée : Ollama / LM Studio tournent sur le poste."""
        response = self.client.get("/auth/")
        self.assertIn("http://localhost:*", response.headers["Content-Security-Policy"])

    def test_a_view_can_impose_its_own_policy(self):
        from django.http import HttpResponse

        def custom(request):
            return HttpResponse("ok", headers={"Content-Security-Policy": "sandbox"})

        from django.urls import path, get_resolver, clear_url_caches
        from ai_2ai import urls

        urls.urlpatterns.append(path("csp-probe/", custom))
        clear_url_caches()
        try:
            response = self.client.get("/csp-probe/")
            self.assertEqual(response.headers["Content-Security-Policy"], "sandbox")
        finally:
            urls.urlpatterns.pop()
            clear_url_caches()

    def test_the_policy_can_be_extended_from_the_settings(self):
        with override_settings(CSP_EXTRA_DIRECTIVES={"report-uri": ["/csp-report"]}):
            response = self.client.get("/auth/")
        self.assertIn("report-uri /csp-report", response.headers["Content-Security-Policy"])


class HealthCheckTests(TestCase):
    def test_healthz_reports_the_database_state(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["checks"]["database"])

    def test_healthz_degrades_when_the_database_is_down(self):
        from django.db import connection

        original = connection.cursor

        def broken_cursor(*args, **kwargs):
            raise RuntimeError("base indisponible")

        connection.cursor = broken_cursor
        try:
            response = self.client.get("/healthz")
        finally:
            connection.cursor = original
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")


class VendoredAssetTests(SimpleTestCase):
    """Les bibliothèques auto-hébergées doivent rester intègres et complètes."""

    VENDOR = Path(settings.BASE_DIR) / "static" / "app" / "vendor"

    def test_every_integrity_attribute_matches_the_file_on_disk(self):
        import re

        templates = [
            Path(settings.BASE_DIR) / "chat" / "templates" / "includes" / "markdown_scripts.html",
            Path(settings.BASE_DIR) / "bara" / "templates" / "bara" / "workspace.html",
        ]
        found = 0
        for template in templates:
            for source, integrity in re.findall(
                r'(?:src|href)="\{%\s*static \'([^\']+)\'\s*%\}[^"]*"[^>]*?integrity="(sha384-[^"]+)"',
                template.read_text(),
                re.S,
            ):
                found += 1
                data = (self.VENDOR / Path(source).name).read_bytes()
                expected = "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()
                self.assertEqual(integrity, expected, f"{source} : attribut integrity périmé")
        self.assertGreaterEqual(found, 3)

    def test_vendored_scripts_do_not_reference_missing_source_maps(self):
        """Régression : `collectstatic` échouait sur une source map absente."""
        for path in sorted(self.VENDOR.glob("*.js")):
            self.assertNotIn("sourceMappingURL", path.read_text(), path.name)

    def test_markdown_and_sanitizer_are_vendored(self):
        self.assertTrue((self.VENDOR / "marked.min.js").is_file())
        self.assertTrue((self.VENDOR / "purify.min.js").is_file())
        # Un CDN compromis ne doit plus pouvoir retirer DOMPurify.
        rendered = Path(settings.BASE_DIR) / "chat" / "templates" / "includes" / "markdown_scripts.html"
        self.assertNotIn("http", rendered.read_text().replace("http://localhost", ""))


class OutboundGuardTests(SimpleTestCase):
    """Les connexions sortantes vers le réseau interne sont refusées à la connexion."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import http.server
        import threading
        cls.server = http.server.HTTPServer(('127.0.0.1', 0), http.server.SimpleHTTPRequestHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        super().tearDownClass()

    def test_public_ip_classification(self):
        from ai_2ai.outbound import is_public_ip
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1', '224.0.0.1', 'bad'):
            self.assertFalse(is_public_ip(address), address)
        self.assertTrue(is_public_ip('8.8.8.8'))

    def test_sync_session_refuses_a_loopback_connection(self):
        import requests
        from ai_2ai.outbound import public_session
        with self.assertRaises(requests.ConnectionError):
            public_session().get(self.url, timeout=5)

    def test_fetch_url_does_not_trust_a_dns_answer_that_changes_after_validation(self):
        from unittest.mock import patch
        from bara.tools.executor import execute_tool
        answers = iter([
            [(2, 1, 6, '', ('8.8.8.8', 80))],                         # contrôle préalable
            [(2, 1, 6, '', ('127.0.0.1', self.server.server_port))],  # connexion réelle
        ])
        with patch('socket.getaddrinfo', side_effect=lambda *args, **kwargs: next(answers)):
            # Passe par le registre d'outils, comme l'agent.
            result = execute_tool('fetch_url', None, {'url': 'http://rebind.example/'})
        self.assertIn('non autorisée', result.get('error', ''))

    def test_async_transport_refuses_a_loopback_connection(self):
        import asyncio
        import httpx
        from ai_2ai.outbound import public_async_transport

        async def fetch():
            async with httpx.AsyncClient(transport=public_async_transport()) as client:
                await client.get(self.url)

        with self.assertRaises(httpx.ConnectError):
            asyncio.run(fetch())
