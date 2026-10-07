import json
import struct
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


class PwaTests(TestCase):
    def test_manifest_describes_installable_2ai_app_and_existing_icons(self):
        response = self.client.get(reverse("pwa-manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        manifest = json.loads(b"".join(response.streaming_content))
        self.assertEqual(manifest["name"], "2AI — atelier de travail")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")

        for icon in manifest["icons"]:
            path = Path(settings.BASE_DIR) / icon["src"].lstrip("/")
            self.assertTrue(path.is_file(), icon["src"])
            with path.open("rb") as icon_file:
                icon_file.seek(16)
                width, height = struct.unpack(">II", icon_file.read(8))
            expected_size = int(icon["sizes"].split("x")[0])
            self.assertEqual((width, height), (expected_size, expected_size))

    def test_service_worker_has_root_scope_and_no_store_for_private_pages(self):
        response = self.client.get(reverse("pwa-service-worker"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/javascript"))
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertEqual(response["Cache-Control"], "no-cache")
        source = b"".join(response.streaming_content).decode()
        self.assertIn('url.pathname.startsWith(STATIC_PREFIX)', source)
        self.assertIn('request.mode === "navigate"', source)
        self.assertIn('fetch(request).catch(() => unavailableResponse())', source)
        self.assertNotIn('caches.match(request)', source)

    def test_base_template_exposes_pwa_metadata_and_install_controls(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'rel="manifest" href="/manifest.webmanifest"')
        self.assertContains(response, 'name="apple-mobile-web-app-capable" content="yes"')
        self.assertContains(response, 'id="pwa-install-button"')
        self.assertContains(response, 'id="pwa-ios-install-help"')
