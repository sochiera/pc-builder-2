import json
from html.parser import HTMLParser
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from src.server import create_app


class OptionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.options = []
        self.current_value = None
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'option':
            self.current_value = dict(attrs).get('value')
            self.current_text = []

    def handle_data(self, data):
        if self.current_value is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        if tag == 'option' and self.current_value is not None:
            self.options.append((self.current_value, ''.join(self.current_text)))
            self.current_value = None


class AppTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(port=0)
        self.thread = Thread(target=self.app.serve_forever)
        self.thread.start()
        self.base_url = f'http://127.0.0.1:{self.app.server_port}'

    def tearDown(self):
        self.app.shutdown()
        self.thread.join()
        self.app.server_close()

    def get_json(self, path):
        try:
            with urlopen(f'{self.base_url}{path}') as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, {}

    def test_catalog_exposes_named_products_with_stable_identifiers(self):
        with urlopen(self.base_url) as response:
            page = response.read().decode()

        parser = OptionParser()
        parser.feed(page)
        self.assertIn(
            ('ryzen-7-7800x3d', 'AMD Ryzen 7 7800X3D'), parser.options
        )
        self.assertIn(
            ('core-i5-14600k', 'Intel Core i5-14600K'), parser.options
        )
        self.assertIn(('msi-b650', 'MSI B650'), parser.options)
        self.assertIn(('asus-z790', 'ASUS Z790'), parser.options)

    def test_analysis_accepts_identifiers_for_compatible_pair(self):
        status, analysis = self.get_json(
            '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=msi-b650'
        )

        self.assertEqual(status, 200)
        self.assertEqual(analysis['level'], 'ok')
        self.assertIn(
            'Socket AM5 procesora i plyty glownej jest zgodny.',
            analysis['message'],
        )

    def test_analysis_reports_required_sockets_for_incompatible_pair(self):
        status, analysis = self.get_json(
            '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=asus-z790'
        )

        self.assertEqual(status, 200)
        self.assertEqual(analysis['level'], 'blocking')
        self.assertIn('AM5', analysis['message'])
        self.assertIn('LGA1700', analysis['message'])

    def test_analysis_requests_both_parts_when_one_identifier_is_missing(self):
        status, analysis = self.get_json(
            '/api/analyze?cpuId=ryzen-7-7800x3d'
        )

        self.assertEqual(status, 200)
        self.assertEqual(analysis['level'], 'info')
        self.assertIn('Wybierz procesor i plyte glowna', analysis['message'])

    def test_analysis_does_not_accept_unknown_product_identifiers(self):
        status, analysis = self.get_json(
            '/api/analyze?cpuId=unknown-cpu&motherboardId=msi-b650'
        )

        self.assertEqual(status, 200)
        self.assertEqual(analysis['level'], 'info')

    def test_running_app_detects_incompatible_cpu_and_motherboard_socket(self):
        with urlopen(f'{self.base_url}/') as response:
            self.assertIn('Konfigurator PC', response.read().decode())

        with urlopen(f'{self.base_url}/api/analyze?cpuSocket=AM5&motherboardSocket=LGA1700') as response:
            analysis = json.loads(response.read())

        self.assertEqual(analysis, {
            'level': 'blocking',
            'message': 'Procesor wymaga socketu AM5, a plyta ma LGA1700.',
        })
