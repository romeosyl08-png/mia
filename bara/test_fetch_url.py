"""Règles de sécurité et de lecture de l'outil fetch_url (aucun accès réseau réel)."""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from bara.tools.definitions import MAX_FETCH_BYTES, MAX_FETCH_CHARS, MAX_REDIRECTS, fetch_url

PUBLIC = [(2, 1, 6, '', ('93.184.215.14', 443))]


def page(status=200, content_type='text/html; charset=utf-8', body=b'', location=None, chunks=None):
    response = MagicMock()
    response.status_code = status
    response.headers = {'Content-Type': content_type, **({'Location': location} if location else {})}
    response.encoding = 'utf-8'
    response.iter_content.return_value = chunks if chunks is not None else [body]
    return response


class FetchUrlTests(SimpleTestCase):
    def fetch(self, url, responses=(), addresses=PUBLIC):
        resolve = addresses if callable(addresses) else (lambda *args, **kwargs: addresses)
        with patch('bara.tools.definitions.socket.getaddrinfo', side_effect=resolve), \
                patch('bara.tools.definitions._public_get', side_effect=list(responses)) as network:
            result = fetch_url(None, url)
        return result, network

    def test_forbidden_destinations_are_refused_before_any_request(self):
        private = {'127.0.0.1': '127.0.0.1', '10.0.0.8': '10.0.0.8', 'métadonnées cloud': '169.254.169.254',
                   'IPv6 locale': '::1', 'IPv4 dans IPv6': '::ffff:127.0.0.1', 'multicast': '224.0.0.1'}
        for label, address in private.items():
            with self.subTest(label):
                result, network = self.fetch('https://piege.example/', addresses=[(2, 1, 6, '', (address, 443))])
                self.assertIn('non autorisée', result['error'])
                network.assert_not_called()
        invalid = {'file:///etc/passwd': 'http(s)', 'ftp://example.com/': 'http(s)', 'https://user:pw@example.com/': 'identifiants',
                   'http://example.com:8080/': 'Port', 'https://': 'invalide', 'http://[::1': 'invalide'}
        for url, message in invalid.items():
            with self.subTest(url):
                result, network = self.fetch(url)
                self.assertIn(message, result['error'])
                network.assert_not_called()

    def test_one_private_address_among_public_ones_is_enough_to_refuse(self):
        mixed = PUBLIC + [(2, 1, 6, '', ('192.168.1.1', 443))]
        result, network = self.fetch('https://mixte.example/', addresses=mixed)
        self.assertIn('non autorisée', result['error'])
        network.assert_not_called()

    def test_redirect_to_an_internal_address_is_not_followed(self):
        def resolve(host, *args, **kwargs):
            return [(2, 1, 6, '', ('169.254.169.254', 80))] if host == 'interne.example' else PUBLIC
        result, network = self.fetch('https://public.example/', [page(302, location='http://interne.example/latest')],
                                     addresses=resolve)
        self.assertIn('non autorisée', result['error'])
        self.assertEqual(network.call_count, 1)

    def test_redirects_are_bounded_and_resolved_relative_to_the_current_page(self):
        loop = [page(302, location='/suivante') for _ in range(MAX_REDIRECTS + 1)]
        result, network = self.fetch('https://boucle.example/depart', loop)
        self.assertEqual(result['error'], 'Trop de redirections.')
        self.assertEqual(network.call_count, MAX_REDIRECTS + 1)
        self.assertEqual(network.call_args.args[0], 'https://boucle.example/suivante')
        self.assertFalse(network.call_args.kwargs['allow_redirects'])

    def test_errors_and_unsupported_content_are_reported(self):
        self.assertIn('404', self.fetch('https://x.example/', [page(404)])[0]['error'])
        self.assertIn('non pris en charge', self.fetch('https://x.example/', [page(content_type='image/png')])[0]['error'])

    def test_download_stops_at_the_size_limit_and_text_is_truncated(self):
        chunk = b'a' * 400_000
        chunks = iter([chunk] * 10)
        result, network = self.fetch('https://gros.example/', [page(content_type='text/plain', chunks=chunks)])
        consumed = 10 - len(list(chunks))
        self.assertLessEqual(consumed * len(chunk), MAX_FETCH_BYTES + len(chunk))
        self.assertLessEqual(len(result['content']), MAX_FETCH_CHARS)
        self.assertTrue(result['truncated'])

    def test_html_is_reduced_to_readable_text(self):
        html = ('<html><head><title> Titre </title><script>alert("x")</script><style>p{}</style></head>'
                '<body><nav>menu</nav><p>Texte utile</p><footer>pied</footer></body></html>').encode()
        result, _ = self.fetch('https://page.example/', [page(body=html)])
        self.assertEqual(result['title'], 'Titre')
        self.assertIn('Texte utile', result['content'])
        for noise in ('alert', 'menu', 'pied', 'p{}'):
            self.assertNotIn(noise, result['content'])
