import json
import base64
from html.parser import HTMLParser
import os
import socket
import subprocess
from threading import Thread
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from src import catalog
from src.catalog import CPUS, MEMORY, MOTHERBOARDS, POWER_SUPPLIES
from src.server import create_app


class OptionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.options = []
        self.options_by_select = {}
        self.current_select = None
        self.current_value = None
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'select':
            self.current_select = dict(attrs).get('id')
        if tag == 'option':
            self.current_value = dict(attrs).get('value')
            self.current_text = []

    def handle_data(self, data):
        if self.current_value is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        if tag == 'option' and self.current_value is not None:
            option = (self.current_value, ''.join(self.current_text))
            self.options.append(option)
            self.options_by_select.setdefault(self.current_select, []).append(option)
            self.current_value = None
        if tag == 'select':
            self.current_select = None


class Browser:
    def __init__(self, url):
        self.url = url

    def __enter__(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        self.process = subprocess.Popen(
            ['/snap/bin/chromium', '--headless', '--no-sandbox', '--disable-gpu',
             f'--remote-debugging-port={port}', '--user-data-dir=/tmp/pc-builder-test',
             'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                with urlopen(f'http://127.0.0.1:{port}/json') as response:
                    target = json.loads(response.read())[0]['webSocketDebuggerUrl']
                break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError('Chromium DevTools endpoint did not start')
        host, path = target.removeprefix('ws://').split('/', 1)
        self.socket = socket.create_connection(tuple(host.split(':')))
        key = base64.b64encode(os.urandom(16)).decode()
        self.socket.sendall(
            f'GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n'
            f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
            'Sec-WebSocket-Version: 13\r\n\r\n'.encode())
        self.socket.recv(4096)
        self.message_id = 0
        self.command('Page.navigate', {'url': self.url})
        time.sleep(0.2)
        return self

    def __exit__(self, *_):
        self.socket.close()
        self.process.terminate()
        self.process.wait(timeout=5)

    def command(self, method, params=None):
        self.message_id += 1
        payload = json.dumps({'id': self.message_id, 'method': method, 'params': params or {}}).encode()
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if len(masked) < 126:
            length = bytes([0x80 | len(masked)])
        elif len(masked) < 65536:
            length = b'\xfe' + len(masked).to_bytes(2, 'big')
        else:
            length = b'\xff' + len(masked).to_bytes(8, 'big')
        self.socket.sendall(b'\x81' + length + mask + masked)
        while True:
            header = self.socket.recv(2)
            length = header[1] & 0x7f
            if length == 126:
                length = int.from_bytes(self.socket.recv(2), 'big')
            elif length == 127:
                length = int.from_bytes(self.socket.recv(8), 'big')
            if header[1] & 0x80:
                self.socket.recv(4)
            message = json.loads(self.socket.recv(length))
            if message.get('id') == self.message_id:
                return message

    def evaluate(self, expression):
        result = self.command('Runtime.evaluate', {
            'expression': expression, 'awaitPromise': True, 'returnByValue': True})
        return result['result']['result']['value']


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

    def test_page_exposes_named_ram_products_in_different_memory_standards(self):
        with urlopen(self.base_url) as response:
            page = response.read().decode()

        parser = OptionParser()
        parser.feed(page)
        ram_options = parser.options_by_select.get('memory', [])

        self.assertGreaterEqual(len(ram_options), 2)
        self.assertIn(('corsair-vengeance-ddr5', 'Corsair Vengeance DDR5'), ram_options)
        self.assertIn(('kingston-fury-ddr4', 'Kingston Fury DDR4'), ram_options)
        self.assertTrue(all(value and value != name for value, name in ram_options))

    def test_page_exposes_named_power_supply_products(self):
        with urlopen(self.base_url) as response:
            page = response.read().decode()

        parser = OptionParser()
        parser.feed(page)
        power_supply_options = parser.options_by_select.get('power-supply', [])

        self.assertGreaterEqual(len(power_supply_options), 2)
        names = [name for _, name in power_supply_options]
        self.assertIn('Corsair RM750x', names)
        self.assertIn('be quiet! Pure Power 12 M 850W', names)
        self.assertTrue(all(value and value != name for value, name in power_supply_options))

        options_by_name = {name: value for value, name in power_supply_options}
        for product in POWER_SUPPLIES:
            self.assertEqual(options_by_name[product['name']], product['id'])

    def test_case_catalog_exposes_formats_for_matching_and_mismatching_boards(self):
        cases = getattr(catalog, 'CASES', ())

        self.assertGreaterEqual(len(cases), 2)
        if len(cases) < 2:
            return

        self.assertEqual(len({case.get('id') for case in cases}), len(cases))
        self.assertTrue(all(case.get('id') and case.get('name') for case in cases))
        self.assertTrue(all(case.get('supported_form_factors') for case in cases))
        self.assertGreaterEqual(
            len({tuple(case['supported_form_factors']) for case in cases}),
            2,
        )

        self.assertTrue(MOTHERBOARDS)
        self.assertTrue(
            all(
                board.get('id') and board.get('name') and board.get('form_factor')
                for board in MOTHERBOARDS
            )
        )
        self.assertTrue(
            any(
                board['form_factor'] in case['supported_form_factors']
                for board in MOTHERBOARDS
                for case in cases
            )
        )
        self.assertTrue(
            any(
                board['form_factor'] not in case['supported_form_factors']
                for board in MOTHERBOARDS
                for case in cases
            )
        )

    def test_page_exposes_named_case_products_with_stable_catalog_identifiers(self):
        with urlopen(self.base_url) as response:
            page = response.read().decode()

        parser = OptionParser()
        parser.feed(page)
        case_options = parser.options_by_select.get('case', [])
        cases = getattr(catalog, 'CASES', ())

        self.assertGreaterEqual(len(case_options), 2)
        if len(case_options) < 2:
            return
        self.assertTrue(all(value and value != name for value, name in case_options))
        self.assertEqual(
            {value for value, _ in case_options},
            {case.get('id') for case in cases},
        )
        self.assertEqual(
            {name for _, name in case_options},
            {case.get('name') for case in cases},
        )

    def test_page_sends_selected_case_identifier_when_case_changes(self):
        with Browser(self.base_url) as browser:
            requests = browser.evaluate("""
                (async () => {
                    const requests = [];
                    window.fetch = url => {
                        requests.push(url);
                        return Promise.resolve({
                            json: () => Promise.resolve({level: 'info', message: 'ok'})
                        });
                    };
                    const caseSelect = document.querySelector('#case');
                    if (!caseSelect) return requests;
                    caseSelect.value = 'atx-mid-tower';
                    caseSelect.dispatchEvent(new Event('change'));
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return requests;
                })()
            """)

        self.assertEqual(len(requests), 1)
        self.assertIn('caseId=atx-mid-tower', requests[0])

    def test_full_build_analysis_combines_case_fit_with_other_results(self):
        base_query = (
            'cpuId=ryzen-7-7800x3d&motherboardId=msi-b650'
            '&ramId=corsair-vengeance-ddr5&psuId=corsair-rm750x'
        )

        with self.subTest('compatible case'):
            status, analysis = self.get_json(
                f'/api/analyze?{base_query}&caseId=atx-mid-tower'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'ok')
            self.assertIn('ATX', analysis['message'])
            self.assertIn('obud', analysis['message'].lower())

        with self.subTest('incompatible case'):
            status, analysis = self.get_json(
                f'/api/analyze?{base_query}&caseId=mini-itx-compact'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'blocking')
            self.assertIn('ATX', analysis['message'])
            self.assertIn('Mini-ITX', analysis['message'])

        with self.subTest('unknown case'):
            status, analysis = self.get_json(
                f'/api/analyze?{base_query}&caseId=unknown-case'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'info')
            self.assertIn('znana plyte glowna i obudowe', analysis['message'])

        with self.subTest('unknown motherboard'):
            status, analysis = self.get_json(
                '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=unknown-motherboard'
                '&ramId=corsair-vengeance-ddr5&psuId=corsair-rm750x'
                '&caseId=atx-mid-tower'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'info')
            self.assertIn('znana plyte glowna i obudowe', analysis['message'])

    def test_partial_case_analysis_uses_motherboard_and_case_without_other_parts(self):
        with self.subTest('compatible case'):
            status, analysis = self.get_json(
                '/api/analyze?motherboardId=msi-b650&caseId=atx-mid-tower'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'ok')
            self.assertIn('ATX', analysis['message'])
            self.assertIn('obudowy', analysis['message'])

        with self.subTest('incompatible case'):
            status, analysis = self.get_json(
                '/api/analyze?motherboardId=msi-b650&caseId=mini-itx-compact'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'blocking')
            self.assertIn('ATX', analysis['message'])
            self.assertIn('Mini-ITX', analysis['message'])

    def test_case_only_analysis_reports_missing_motherboard_and_case_data(self):
        status, analysis = self.get_json('/api/analyze?caseId=atx-mid-tower')

        self.assertEqual(status, 200)
        self.assertEqual(analysis['level'], 'info')
        self.assertIn('Wybierz plyte glowna i obudowe', analysis['message'])

    def test_partial_case_analysis_combines_case_with_selected_ram_or_cpu(self):
        with self.subTest('case analysis preserves RAM mismatch'):
            status, analysis = self.get_json(
                '/api/analyze?motherboardId=msi-b650'
                '&ramId=kingston-fury-ddr4&caseId=atx-mid-tower'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'blocking')
            self.assertIn('Pamiec RAM DDR4 jest niezgodna', analysis['message'])
            self.assertIn('Plyta w formacie ATX pasuje do obudowy', analysis['message'])

        with self.subTest('case analysis preserves socket mismatch'):
            status, analysis = self.get_json(
                '/api/analyze?cpuId=ryzen-7-7800x3d'
                '&motherboardId=asus-z790&caseId=atx-mid-tower'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'blocking')
            self.assertIn('socketu AM5', analysis['message'])
            self.assertIn('LGA1700', analysis['message'])
            self.assertIn('Plyta w formacie ATX pasuje do obudowy', analysis['message'])

    def test_catalog_exposes_power_demand_for_parts_and_power_rating(self):
        for products in (CPUS, MOTHERBOARDS, MEMORY):
            for product in products:
                self.assertIn('power_watts', product)
                self.assertGreater(product['power_watts'], 0)

        ratings = [product['power_watts'] for product in POWER_SUPPLIES]
        self.assertGreaterEqual(len(ratings), 2)
        self.assertEqual(len(set(ratings)), len(ratings))
        self.assertTrue(all(rating > 0 for rating in ratings))

    def test_catalog_binds_positive_pln_prices_to_every_selectable_product_id(self):
        products = CPUS + MOTHERBOARDS + MEMORY + POWER_SUPPLIES + catalog.CASES
        product_ids = [product.get('id') for product in products]

        self.assertEqual(len(product_ids), len(set(product_ids)))

        prices_by_id = {}
        for product in products:
            with self.subTest(product_id=product.get('id')):
                self.assertTrue(product.get('id'))
                self.assertIn('price_pln', product)
                self.assertIsInstance(product['price_pln'], (int, float))
                self.assertGreater(product['price_pln'], 0)
                prices_by_id[product['id']] = product['price_pln']

        self.assertEqual(
            set(prices_by_id),
            {product['id'] for product in products},
        )

    def test_memory_catalog_binds_products_to_public_standards(self):
        standards_by_id = {product['id']: product['standard'] for product in MEMORY}

        self.assertEqual(standards_by_id['corsair-vengeance-ddr5'], 'DDR5')
        self.assertEqual(standards_by_id['kingston-fury-ddr4'], 'DDR4')

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

    def test_power_supply_analysis_reports_sufficient_missing_and_unknown_choices(self):
        base_query = (
            'cpuId=ryzen-7-7800x3d&motherboardId=msi-b650'
            '&ramId=corsair-vengeance-ddr5'
        )

        with self.subTest('sufficient power supply'):
            status, sufficient = self.get_json(
                f'/api/analyze?{base_query}&psuId=corsair-rm750x'
            )
            self.assertEqual(status, 200)
            self.assertEqual(sufficient['level'], 'ok')
            self.assertIn('wymaga 210 W', sufficient['message'])
            self.assertIn('750 W', sufficient['message'])
            self.assertIn('wystarczajaca', sufficient['message'])
            self.assertIn('Socket AM5 procesora i plyty glownej jest zgodny.', sufficient['message'])
            self.assertIn('Pamiec RAM DDR5 jest zgodna z plyta glowna.', sufficient['message'])

        with self.subTest('exactly sufficient power supply'):
            exact_supply = ({'id': 'exact-psu', 'name': 'Graniczny zasilacz', 'power_watts': 210},)
            with patch('src.server.POWER_SUPPLIES', exact_supply):
                status, exact = self.get_json(f'/api/analyze?{base_query}&psuId=exact-psu')
            self.assertEqual(status, 200)
            self.assertEqual(exact['level'], 'ok')

        with self.subTest('insufficient power supply'):
            weak_supply = ({'id': 'weak-psu', 'name': 'Slaby zasilacz', 'power_watts': 100},)
            with patch('src.server.POWER_SUPPLIES', weak_supply):
                status, insufficient = self.get_json(f'/api/analyze?{base_query}&psuId=weak-psu')
            self.assertEqual(status, 200)
            self.assertEqual(insufficient['level'], 'blocking')
            self.assertIn('wymaga 210 W', insufficient['message'])
            self.assertIn('dostarcza 100 W', insufficient['message'])

        with self.subTest('missing power supply'):
            status, missing = self.get_json(f'/api/analyze?{base_query}')
            self.assertEqual(status, 200)
            self.assertEqual(missing['level'], 'info')
            self.assertIn('zasilacz', missing['message'].lower())

        with self.subTest('empty power supply form choice'):
            status, missing = self.get_json(f'/api/analyze?{base_query}&psuId=')
            self.assertEqual(status, 200)
            self.assertEqual(missing['level'], 'info')
            self.assertIn('zasilacz', missing['message'].lower())

        with self.subTest('missing part'):
            status, missing = self.get_json(
                '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=msi-b650'
                '&ramId=&psuId=corsair-rm750x'
            )
            self.assertEqual(status, 200)
            self.assertEqual(missing['level'], 'info')
            self.assertIn('pamiec RAM', missing['message'])

        with self.subTest('missing RAM key with power supply'):
            status, missing = self.get_json(
                '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=msi-b650'
                '&psuId=corsair-rm750x'
            )
            self.assertEqual(status, 200)
            self.assertEqual(missing['level'], 'info')
            self.assertIn('pamiec RAM', missing['message'])

        with self.subTest('missing CPU key with power supply'):
            status, missing = self.get_json(
                '/api/analyze?motherboardId=msi-b650'
                '&ramId=corsair-vengeance-ddr5&psuId=corsair-rm750x'
            )
            self.assertEqual(status, 200)
            self.assertEqual(missing['level'], 'info')
            self.assertIn('procesor', missing['message'].lower())

        with self.subTest('unknown power supply'):
            status, unknown = self.get_json(
                f'/api/analyze?{base_query}&psuId=unknown-psu'
            )
            self.assertEqual(status, 200)
            self.assertNotIn(unknown['level'], ('ok', 'blocking'))

        with self.subTest('unknown part'):
            status, unknown = self.get_json(
                '/api/analyze?cpuId=unknown-cpu&motherboardId=msi-b650'
                '&ramId=corsair-vengeance-ddr5&psuId=corsair-rm750x'
            )
            self.assertEqual(status, 200)
            self.assertNotIn(unknown['level'], ('ok', 'blocking'))

    def test_analysis_accepts_compatible_ram_for_motherboard(self):
        status, analysis = self.get_json(
            '/api/analyze?motherboardId=msi-b650&ramId=corsair-vengeance-ddr5'
        )

        self.assertEqual(status, 200)
        self.assertEqual(analysis['level'], 'ok')
        self.assertIn('DDR5', analysis['message'])
        self.assertIn('zgodna', analysis['message'])

    def test_analysis_reports_ram_standard_mismatch(self):
        status, analysis = self.get_json(
            '/api/analyze?motherboardId=msi-b650&ramId=kingston-fury-ddr4'
        )

        self.assertEqual(status, 200)
        self.assertEqual(analysis['level'], 'blocking')
        self.assertIn('DDR4', analysis['message'])
        self.assertIn('DDR5', analysis['message'])

    def test_full_build_analysis_combines_socket_and_ram_results(self):
        for ram_id, expected_level, expected_message in (
            ('corsair-vengeance-ddr5', 'ok', 'Pamiec RAM DDR5 jest zgodna'),
            ('kingston-fury-ddr4', 'blocking', 'Pamiec RAM DDR4 jest niezgodna'),
        ):
            with self.subTest(ram_id=ram_id):
                status, analysis = self.get_json(
                    '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=msi-b650'
                    f'&ramId={ram_id}&psuId=corsair-rm750x'
                )

                self.assertEqual(status, 200)
                self.assertEqual(analysis['level'], expected_level)
                self.assertIn(
                    'Socket AM5 procesora i plyty glownej jest zgodny.',
                    analysis['message'],
                )
                self.assertIn(expected_message, analysis['message'])
                if expected_level == 'blocking':
                    self.assertIn('plyta obsluguje DDR5', analysis['message'])

    def test_incomplete_power_analysis_does_not_hide_socket_or_ram_findings(self):
        with self.subTest('missing power supply preserves incomplete power status'):
            status, analysis = self.get_json(
                '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=asus-z790'
                '&ramId=corsair-vengeance-ddr5'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'info')
            self.assertIn('zasilacz', analysis['message'].lower())
            self.assertIn('socketu AM5', analysis['message'])

        with self.subTest('unknown power supply preserves incomplete power status'):
            status, analysis = self.get_json(
                '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=asus-z790'
                '&ramId=corsair-vengeance-ddr5&psuId=unknown-psu'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'info')
            self.assertIn('znane czesci i zasilacz', analysis['message'])
            self.assertIn('socketu AM5', analysis['message'])

        with self.subTest('complete power analysis preserves blocking findings'):
            status, analysis = self.get_json(
                '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=asus-z790'
                '&ramId=kingston-fury-ddr4&psuId=corsair-rm750x'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'blocking')
            self.assertIn('socketu AM5', analysis['message'])
            self.assertIn('Pamiec RAM DDR4 jest niezgodna', analysis['message'])

    def test_power_analysis_requires_cpu_when_all_form_fields_are_present(self):
        status, analysis = self.get_json(
            '/api/analyze?cpuId=&motherboardId=msi-b650'
            '&ramId=corsair-vengeance-ddr5&psuId=corsair-rm750x'
        )

        self.assertEqual(status, 200)
        self.assertEqual(analysis['level'], 'info')
        self.assertIn('procesor', analysis['message'].lower())
        self.assertIn('zasilacz', analysis['message'].lower())

    def test_analysis_keeps_socket_blocking_when_ram_is_selected(self):
        for ram_id in (
            'corsair-vengeance-ddr5',
            'kingston-fury-ddr4',
        ):
            with self.subTest(ram_id=ram_id):
                status, analysis = self.get_json(
                    '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=asus-z790'
                    f'&ramId={ram_id}&psuId=corsair-rm750x'
                )

                self.assertEqual(status, 200)
                self.assertEqual(analysis['level'], 'blocking')
                self.assertIn('AM5', analysis['message'])
                self.assertIn('LGA1700', analysis['message'])

    def test_ram_analysis_requests_motherboard_and_memory_when_missing(self):
        for query in (
            'motherboardId=msi-b650',
            'ramId=corsair-vengeance-ddr5',
            'cpuId=ryzen-7-7800x3d&motherboardId=msi-b650&ramId=',
        ):
            with self.subTest(query=query):
                status, analysis = self.get_json(f'/api/analyze?{query}')

                self.assertEqual(status, 200)
                self.assertEqual(analysis['level'], 'info')
                self.assertIn('plyte glowna', analysis['message'])
                self.assertIn('pamiec RAM', analysis['message'])

    def test_ram_analysis_does_not_accept_unknown_identifiers(self):
        for query in (
            'motherboardId=msi-b650&ramId=unknown-ram',
            'motherboardId=unknown-motherboard&ramId=corsair-vengeance-ddr5',
        ):
            with self.subTest(query=query):
                status, analysis = self.get_json(f'/api/analyze?{query}')

                self.assertEqual(status, 200)
                self.assertNotIn(analysis['level'], ('ok', 'blocking'))

    def test_page_refreshes_ram_analysis_with_selected_memory(self):
        with Browser(self.base_url) as browser:
            requests = browser.evaluate("""
                (async () => {
                    const requests = [];
                    window.fetch = url => {
                        requests.push(url);
                        return Promise.resolve({
                            json: () => Promise.resolve({level: 'ok', message: 'ok'})
                        });
                    };
                    const motherboard = document.querySelector('#motherboard');
                    const memory = document.querySelector('#memory');
                    motherboard.value = 'msi-b650';
                    memory.value = 'kingston-fury-ddr4';
                    memory.dispatchEvent(new Event('change'));
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return requests;
                })()
            """)

        self.assertEqual(len(requests), 1)
        self.assertIn('motherboardId=msi-b650', requests[0])
        self.assertIn('ramId=kingston-fury-ddr4', requests[0])

    def test_page_refreshes_power_analysis_with_selected_power_supply(self):
        with Browser(self.base_url) as browser:
            requests = browser.evaluate("""
                (async () => {
                    const requests = [];
                    window.fetch = url => {
                        requests.push(url);
                        return Promise.resolve({
                            json: () => Promise.resolve({level: 'ok', message: 'ok'})
                        });
                    };
                    document.querySelector('#cpu').value = 'ryzen-7-7800x3d';
                    document.querySelector('#motherboard').value = 'msi-b650';
                    document.querySelector('#memory').value = 'corsair-vengeance-ddr5';
                    const powerSupply = document.querySelector('#power-supply');
                    powerSupply.value = 'corsair-rm750x';
                    powerSupply.dispatchEvent(new Event('change'));
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return requests;
                })()
            """)

        self.assertEqual(len(requests), 1)
        self.assertIn('cpuId=ryzen-7-7800x3d', requests[0])
        self.assertIn('motherboardId=msi-b650', requests[0])
        self.assertIn('ramId=corsair-vengeance-ddr5', requests[0])
        self.assertIn('psuId=corsair-rm750x', requests[0])

    def test_page_reports_missing_cpu_when_power_supply_changes_before_complete_build(self):
        with Browser(self.base_url) as browser:
            analysis = browser.evaluate("""
                (async () => {
                    document.querySelector('#motherboard').value = 'msi-b650';
                    document.querySelector('#memory').value = 'corsair-vengeance-ddr5';
                    const powerSupply = document.querySelector('#power-supply');
                    powerSupply.value = 'corsair-rm750x';
                    powerSupply.dispatchEvent(new Event('change'));
                    await new Promise(resolve => setTimeout(resolve, 100));
                    return {
                        level: document.querySelector('#result').dataset.level,
                        text: document.querySelector('#result').textContent,
                    };
                })()
            """)

        self.assertEqual(analysis['level'], 'info')
        self.assertIn('procesor', analysis['text'].lower())
        self.assertIn('zasilacz', analysis['text'].lower())
        self.assertIn('Pamiec RAM DDR5 jest zgodna', analysis['text'])

    def test_page_shows_power_status_before_and_after_power_supply_change(self):
        weak_supply = {'id': 'test-weak-psu', 'name': 'Testowy zasilacz 100 W', 'power_watts': 100}
        with patch('src.server.POWER_SUPPLIES', POWER_SUPPLIES + (weak_supply,)):
            with Browser(self.base_url) as browser:
                statuses = browser.evaluate("""
                (async () => {
                    const waitForResult = async (level, text) => {
                        for (let attempt = 0; attempt < 200; attempt++) {
                            const result = document.querySelector('#result');
                            if (result.dataset.level === level && result.textContent.includes(text)) {
                                return;
                            }
                            await new Promise(resolve => setTimeout(resolve, 10));
                        }
                        throw new Error(`Timed out waiting for ${level}: ${text}`);
                    };
                    const initial = {
                        level: document.querySelector('#result').dataset.level,
                        text: document.querySelector('#result').textContent,
                    };
                    document.querySelector('#cpu').value = 'ryzen-7-7800x3d';
                    document.querySelector('#motherboard').value = 'msi-b650';
                    document.querySelector('#memory').value = 'corsair-vengeance-ddr5';
                    const powerSupply = document.querySelector('#power-supply');
                    powerSupply.value = 'corsair-rm750x';
                    powerSupply.dispatchEvent(new Event('change'));
                    await waitForResult('ok', 'wystarczajaca');
                    const sufficient = {
                        level: document.querySelector('#result').dataset.level,
                        text: document.querySelector('#result').textContent,
                    };
                    powerSupply.value = 'test-weak-psu';
                    powerSupply.dispatchEvent(new Event('change'));
                    await waitForResult('blocking', 'dostarcza 100 W');
                    return {initial, sufficient, afterChange: {
                        level: document.querySelector('#result').dataset.level,
                        text: document.querySelector('#result').textContent,
                    }};
                })()
                """)

        self.assertIn('zasilacz', statuses['initial']['text'].lower())
        self.assertEqual(statuses['sufficient']['level'], 'ok')
        self.assertIn('wystarczajaca', statuses['sufficient']['text'])
        self.assertEqual(statuses['afterChange']['level'], 'blocking')
        self.assertIn('wymaga 210 W', statuses['afterChange']['text'])
        self.assertIn('dostarcza 100 W', statuses['afterChange']['text'])

    def test_page_shows_ram_status_before_and_after_memory_change(self):
        with Browser(self.base_url) as browser:
            initial = browser.evaluate("document.querySelector('#result').textContent")
            statuses = browser.evaluate("""
                (async () => {
                    const motherboard = document.querySelector('#motherboard');
                    const memory = document.querySelector('#memory');
                    motherboard.value = 'msi-b650';
                    memory.value = 'corsair-vengeance-ddr5';
                    memory.dispatchEvent(new Event('change'));
                    await new Promise(resolve => setTimeout(resolve, 100));
                    const compatible = {
                        text: document.querySelector('#result').textContent,
                        level: document.querySelector('#result').dataset.level,
                    };
                    const compatiblePublic = await fetch(
                        '/api/analyze?motherboardId=msi-b650&ramId=corsair-vengeance-ddr5'
                    ).then(response => response.json());
                    memory.value = 'kingston-fury-ddr4';
                    memory.dispatchEvent(new Event('change'));
                    await new Promise(resolve => setTimeout(resolve, 100));
                    const incompatible = {
                        text: document.querySelector('#result').textContent,
                        level: document.querySelector('#result').dataset.level,
                    };
                    const incompatiblePublic = await fetch(
                        '/api/analyze?motherboardId=msi-b650&ramId=kingston-fury-ddr4'
                    ).then(response => response.json());
                    return {
                        compatible,
                        compatiblePublic,
                        incompatible,
                        incompatiblePublic,
                        location: window.location.href,
                    };
                })()
            """)

        self.assertIn('zasilacz', initial.lower())
        self.assertEqual(statuses['compatible']['level'], 'ok')
        self.assertIn('DDR5', statuses['compatible']['text'])
        self.assertIn('zgodna', statuses['compatible']['text'])
        self.assertEqual(statuses['compatible']['level'], statuses['compatiblePublic']['level'])
        self.assertEqual(statuses['compatible']['text'], statuses['compatiblePublic']['message'])
        self.assertEqual(statuses['incompatible']['level'], 'blocking')
        self.assertIn('DDR4', statuses['incompatible']['text'])
        self.assertIn('DDR5', statuses['incompatible']['text'])
        self.assertEqual(statuses['incompatible']['level'], statuses['incompatiblePublic']['level'])
        self.assertEqual(statuses['incompatible']['text'], statuses['incompatiblePublic']['message'])
        self.assertEqual(statuses['location'], self.base_url + '/')

    def test_page_ignores_stale_ram_analysis_response(self):
        with Browser(self.base_url) as browser:
            status = browser.evaluate("""
                (async () => {
                    const responses = new Map();
                    const requested = [];
                    window.fetch = url => {
                        requested.push(url);
                        return new Promise(resolve => responses.set(url, resolve))
                            .then(response => response);
                    };
                    const motherboard = document.querySelector('#motherboard');
                    const memory = document.querySelector('#memory');
                    motherboard.value = 'msi-b650';
                    memory.value = 'corsair-vengeance-ddr5';
                    memory.dispatchEvent(new Event('change'));
                    memory.value = 'kingston-fury-ddr4';
                    memory.dispatchEvent(new Event('change'));
                    while (requested.length < 2) {
                        await new Promise(resolve => setTimeout(resolve, 0));
                    }
                    const oldResponse = {
                        json: () => Promise.resolve({
                            level: 'ok',
                            message: 'Plyta obsluguje pamiec DDR5.'
                        })
                    };
                    const currentResponse = {
                        json: () => Promise.resolve({
                            level: 'blocking',
                            message: 'Plyta obsluguje DDR5, a wybrana pamiec to DDR4.'
                        })
                    };
                    responses.get(requested[1])(currentResponse);
                    await new Promise(resolve => setTimeout(resolve, 0));
                    responses.get(requested[0])(oldResponse);
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return {
                        text: document.querySelector('#result').textContent,
                        level: document.querySelector('#result').dataset.level,
                        requested,
                    };
                })()
            """)

        self.assertEqual(len(status['requested']), 2)
        self.assertEqual(status['level'], 'blocking')
        self.assertEqual(
            status['text'],
            'Plyta obsluguje DDR5, a wybrana pamiec to DDR4.',
        )

    def test_running_app_detects_incompatible_cpu_and_motherboard_socket(self):
        with urlopen(f'{self.base_url}/') as response:
            self.assertIn('Konfigurator PC', response.read().decode())

        with urlopen(f'{self.base_url}/api/analyze?cpuSocket=AM5&motherboardSocket=LGA1700') as response:
            analysis = json.loads(response.read())

        self.assertEqual(analysis, {
            'level': 'blocking',
            'message': 'Procesor wymaga socketu AM5, a plyta ma LGA1700.',
        })
