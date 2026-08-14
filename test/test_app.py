import json
import base64
from html.parser import HTMLParser
from http.client import RemoteDisconnected
import os
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src import catalog
from src import server
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
            try:
                body = json.loads(error.read())
            except json.JSONDecodeError:
                body = {}
            finally:
                error.close()
            return error.code, body

    def post_json(self, path, payload):
        request = Request(
            f'{self.base_url}{path}',
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            try:
                body = json.loads(error.read())
            except json.JSONDecodeError:
                body = {}
            finally:
                error.close()
            return error.code, body

    def test_configuration_save_returns_id_and_preserves_selected_parts_and_budget(self):
        payload = {
            'cpuId': 'ryzen-7-7800x3d',
            'motherboardId': 'msi-b650',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'caseId': 'atx-mid-tower',
            'budgetPln': 5000,
        }

        status, saved = self.post_json('/api/configurations', payload)

        self.assertEqual(status, 201)
        self.assertTrue(saved.get('configuration_id'))
        self.assertEqual(saved.get('parts'), {
            'cpuId': 'ryzen-7-7800x3d',
            'motherboardId': 'msi-b650',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'caseId': 'atx-mid-tower',
        })
        self.assertEqual(saved.get('budgetPln'), 5000)
        first_configuration_id = saved['configuration_id']
        first_share_url = saved.get('share_url')
        self.assertEqual(first_share_url, f'/api/configurations/{first_configuration_id}')
        status, shared = self.get_json(first_share_url)
        self.assertEqual(status, 200)
        self.assertEqual(shared['configuration_id'], first_configuration_id)

        status, saved = self.post_json('/api/configurations', {
            'cpuId': 'core-i5-14600k',
        })

        self.assertEqual(status, 201)
        self.assertTrue(saved.get('configuration_id'))
        self.assertEqual(saved.get('parts'), {'cpuId': 'core-i5-14600k'})
        self.assertNotIn('name', saved)
        self.assertNotIn('budgetPln', saved)
        self.assertEqual(
            saved.get('share_url'),
            f"/api/configurations/{saved['configuration_id']}",
        )
        self.assertNotEqual(saved['share_url'], first_share_url)

    def test_named_configuration_is_persisted_after_app_restart(self):
        payload = {
            'name': 'Wydajny zestaw do pracy',
            'cpuId': 'ryzen-7-7800x3d',
            'motherboardId': 'msi-b650',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'budgetPln': 5000,
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, saved = self.post_json('/api/configurations', payload)

                self.assertEqual(status, 201)
                self.assertTrue(saved.get('configuration_id'))
                self.assertEqual(saved.get('name'), payload['name'])

                restarted_app = create_app(port=0)
                restarted_thread = Thread(target=restarted_app.serve_forever)
                restarted_thread.start()
                try:
                    restarted_url = f'http://127.0.0.1:{restarted_app.server_port}'
                    with urlopen(
                        f"{restarted_url}/api/configurations/{saved['configuration_id']}"
                    ) as response:
                        self.assertEqual(response.status, 200)
                        reopened = json.loads(response.read())
                finally:
                    restarted_app.shutdown()
                    restarted_thread.join()
                    restarted_app.server_close()

                self.assertEqual(reopened['name'], payload['name'])
                self.assertEqual(reopened['parts'], {
                    key: value for key, value in payload.items()
                    if key.endswith('Id')
                })
                self.assertEqual(reopened['budgetPln'], payload['budgetPln'])

    def test_configuration_list_returns_named_variants_only(self):
        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                named_status, named = self.post_json('/api/configurations', {
                    'name': 'Zestaw do pracy',
                    'cpuId': 'core-i5-14600k',
                })
                unnamed_status, unnamed = self.post_json('/api/configurations', {
                    'cpuId': 'ryzen-7-7800x3d',
                })

                self.assertEqual(named_status, 201)
                self.assertEqual(unnamed_status, 201)
                list_status, configurations = self.get_json('/api/configurations')

                self.assertEqual(list_status, 200)
                self.assertIsInstance(configurations, list)
                self.assertEqual(
                    {
                        (configuration['configuration_id'], configuration['name'])
                        for configuration in configurations
                    },
                    {(named['configuration_id'], 'Zestaw do pracy')},
                )
                self.assertNotIn(
                    unnamed['configuration_id'],
                    {configuration['configuration_id'] for configuration in configurations},
                )

                restarted_app = create_app(port=0)
                restarted_thread = Thread(target=restarted_app.serve_forever)
                restarted_thread.start()
                try:
                    restarted_url = f'http://127.0.0.1:{restarted_app.server_port}'
                    with urlopen(f'{restarted_url}/api/configurations') as response:
                        self.assertEqual(response.status, 200)
                        restarted_configurations = json.loads(response.read())
                finally:
                    restarted_app.shutdown()
                    restarted_thread.join()
                    restarted_app.server_close()

                self.assertEqual(
                    {
                        (configuration['configuration_id'], configuration['name'])
                        for configuration in restarted_configurations
                    },
                    {(named['configuration_id'], 'Zestaw do pracy')},
                )
                self.assertNotIn(
                    unnamed['configuration_id'],
                    {configuration['configuration_id'] for configuration in restarted_configurations},
                )


    def test_configuration_save_rejects_invalid_names_without_creating_one(self):
        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                for invalid_name in ('', None, 42):
                    with self.subTest(invalid_name=invalid_name):
                        before = store.read_bytes()
                        status, response = self.post_json(
                            '/api/configurations',
                            {'name': invalid_name, 'cpuId': 'core-i5-14600k'},
                        )
                        self.assertEqual(status, 400)
                        self.assertIn('name', response.get('error', '').lower())
                        self.assertEqual(store.read_bytes(), before)

    def test_configuration_can_be_opened_after_save_and_app_restart(self):
        payload = {
            'cpuId': 'ryzen-7-7800x3d',
            'motherboardId': 'msi-b650',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'caseId': 'atx-mid-tower',
            'budgetPln': 5000,
        }

        status, saved = self.post_json('/api/configurations', payload)
        self.assertEqual(status, 201)
        self.assertEqual(
            saved['share_url'],
            f"/api/configurations/{saved['configuration_id']}",
        )

        status, opened = self.get_json(saved['share_url'])
        self.assertEqual(status, 200)
        self.assertEqual(opened['configuration_id'], saved['configuration_id'])
        self.assertEqual(
            opened['parts'],
            {key: value for key, value in payload.items() if key != 'budgetPln'},
        )
        self.assertEqual(opened['budgetPln'], payload['budgetPln'])
        self.assertTrue(opened.get('share_url'))
        self.assertEqual(opened, saved)
        self.assertEqual(opened['share_url'], saved['share_url'])

        restarted_app = create_app(port=0)
        restarted_thread = Thread(target=restarted_app.serve_forever)
        restarted_thread.start()
        try:
            restarted_url = f'http://127.0.0.1:{restarted_app.server_port}'
            with urlopen(
                f"{restarted_url}/api/configurations/{saved['configuration_id']}"
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), saved)
        finally:
            restarted_app.shutdown()
            restarted_thread.join()
            restarted_app.server_close()

        status, missing = self.get_json('/api/configurations/does-not-exist')
        self.assertEqual(status, 404)
        self.assertEqual(missing, {'error': 'Nie znaleziono konfiguracji.'})

    def test_configuration_open_adds_share_url_to_legacy_record(self):
        configuration_id = 'legacy-config-123'
        legacy = {
            configuration_id: {
                'configuration_id': configuration_id,
                'parts': {'cpuId': 'core-i5-14600k'},
            },
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps(legacy), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, opened = self.get_json(
                    f'/api/configurations/{configuration_id}'
                )

        self.assertEqual(status, 200)
        self.assertEqual(opened, {
            'configuration_id': configuration_id,
            'parts': {'cpuId': 'core-i5-14600k'},
            'share_url': f'/api/configurations/{configuration_id}',
        })

    def test_configuration_save_rejects_invalid_parts_and_budget_without_creating_one(self):
        invalid_payloads = (
            ({'cpuId': 'msi-b650'}, 'cpuId'),
            ({'budgetPln': -1}, 'Budzet'),
            ({'budgetPln': '9' * 5000}, 'Budzet'),
        )

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                for payload, expected_error in invalid_payloads:
                    with self.subTest(payload=payload):
                        before = store.read_bytes()
                        try:
                            status, response = self.post_json('/api/configurations', payload)
                        except RemoteDisconnected:
                            status, response = None, {}

                        self.assertEqual(status, 400)
                        self.assertIn(expected_error, response.get('error', ''))
                        self.assertEqual(store.read_bytes(), before)

    def test_compare_configurations_returns_costs_and_cheapest_or_tie(self):
        configurations = {
            'first-config': {
                'configuration_id': 'first-config',
                'parts': {'cpuId': 'ryzen-7-7800x3d'},
            },
            'second-config': {
                'configuration_id': 'second-config',
                'parts': {'cpuId': 'core-i5-14600k'},
            },
            'tie-config': {
                'configuration_id': 'tie-config',
                'parts': {'cpuId': 'ryzen-7-7800x3d'},
            },
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps(configurations), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, comparison = self.get_json(
                    '/api/compare?firstId=first-config&secondId=second-config'
                )
                self.assertEqual(status, 200)
                self.assertEqual(comparison['first_configuration_id'], 'first-config')
                self.assertEqual(comparison['second_configuration_id'], 'second-config')
                self.assertEqual(comparison['first_cost_pln'], 1599)
                self.assertEqual(comparison['second_cost_pln'], 1249)
                self.assertEqual(comparison['cheaper'], 'second')

                status, comparison = self.get_json(
                    '/api/compare?firstId=first-config&secondId=tie-config'
                )
                self.assertEqual(status, 200)
                self.assertEqual(comparison['first_cost_pln'], 1599)
                self.assertEqual(comparison['second_cost_pln'], 1599)
                self.assertEqual(comparison['cheaper'], 'tie')

            self.assertEqual(json.loads(store.read_text(encoding='utf-8')), configurations)

    def test_compare_configurations_returns_named_variants_and_maps_recommendations(self):
        configurations = {
            'workstation': {
                'configuration_id': 'workstation',
                'name': 'Stacja robocza',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'gaming': {
                'configuration_id': 'gaming',
                'name': 'Zestaw gamingowy',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'motherboardId': 'asus-z790',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'over-budget': {
                'configuration_id': 'over-budget',
                'name': 'Wariant ponad budzetem',
                'budgetPln': 1,
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'motherboardId': 'asus-z790',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'incompatible': {
                'configuration_id': 'incompatible',
                'name': 'Wariant niezgodny',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'asus-z790',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'legacy': {
                'configuration_id': 'legacy',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps(configurations), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                for first_id, second_id, expected_recommendations in (
                    (
                        'workstation',
                        'gaming',
                        {
                            'recommended_configuration_id': None,
                            'budget_recommended_configuration_id': None,
                            'cost_recommended_configuration_id': 'gaming',
                        },
                    ),
                    (
                        'gaming',
                        'workstation',
                        {
                            'recommended_configuration_id': None,
                            'budget_recommended_configuration_id': None,
                            'cost_recommended_configuration_id': 'gaming',
                        },
                    ),
                ):
                    with self.subTest(first_id=first_id, second_id=second_id):
                        status, comparison = self.get_json(
                            f'/api/compare?firstId={first_id}&secondId={second_id}'
                        )
                        self.assertEqual(status, 200)
                        self.assertIn('first_configuration_name', comparison)
                        self.assertIn('second_configuration_name', comparison)
                        self.assertEqual(
                            comparison['first_configuration_name'],
                            configurations[first_id]['name'],
                        )
                        self.assertEqual(
                            comparison['second_configuration_name'],
                            configurations[second_id]['name'],
                        )
                        names_by_id = {
                            comparison['first_configuration_id']:
                                comparison['first_configuration_name'],
                            comparison['second_configuration_id']:
                                comparison['second_configuration_name'],
                        }
                        for recommendation_type in (
                            'recommended_configuration_id',
                            'budget_recommended_configuration_id',
                            'cost_recommended_configuration_id',
                        ):
                            recommendation_id = comparison[recommendation_type]
                            self.assertEqual(
                                recommendation_id,
                                expected_recommendations[recommendation_type],
                            )
                            if recommendation_id is not None:
                                self.assertEqual(
                                    names_by_id[recommendation_id],
                                    configurations[recommendation_id]['name'],
                                )

                for first_id, second_id in (
                    ('workstation', 'legacy'),
                    ('legacy', 'workstation'),
                ):
                    with self.subTest(first_id=first_id, second_id=second_id):
                        status, comparison = self.get_json(
                            f'/api/compare?firstId={first_id}&secondId={second_id}'
                        )
                        self.assertEqual(status, 200)
                        self.assertEqual(
                            comparison['first_configuration_name'],
                            configurations[first_id].get('name'),
                        )
                        self.assertEqual(
                            comparison['second_configuration_name'],
                            configurations[second_id].get('name'),
                        )
                        self.assertEqual(
                            comparison['first_configuration_id'], first_id
                        )
                        self.assertEqual(
                            comparison['second_configuration_id'], second_id
                        )
                        self.assertEqual(comparison['differences'], {})
                        self.assertEqual(comparison['cheaper'], 'tie')
                        self.assertEqual(
                            comparison['first_compatibility']['level'], 'ok'
                        )
                        self.assertEqual(
                            comparison['second_compatibility']['level'], 'ok'
                        )
                        self.assertEqual(comparison['first_budget']['level'], 'ok')
                        self.assertEqual(comparison['second_budget']['level'], 'ok')
                        self.assertIsNone(comparison['recommended_configuration_id'])
                        self.assertIsNone(
                            comparison['budget_recommended_configuration_id']
                        )
                        self.assertIsNone(comparison['cost_recommended_configuration_id'])

                for first_id, second_id, expected_recommendations in (
                    (
                        'workstation',
                        'incompatible',
                        {
                            'recommended_configuration_id': 'workstation',
                            'budget_recommended_configuration_id': None,
                            'cost_recommended_configuration_id': None,
                        },
                    ),
                    (
                        'workstation',
                        'over-budget',
                        {
                            'recommended_configuration_id': None,
                            'budget_recommended_configuration_id': 'workstation',
                            'cost_recommended_configuration_id': None,
                        },
                    ),
                ):
                    with self.subTest(first_id=first_id, second_id=second_id):
                        status, comparison = self.get_json(
                            f'/api/compare?firstId={first_id}&secondId={second_id}'
                        )
                        self.assertEqual(status, 200)
                        self.assertIn('first_configuration_name', comparison)
                        self.assertIn('second_configuration_name', comparison)
                        names_by_id = {
                            comparison['first_configuration_id']:
                                comparison['first_configuration_name'],
                            comparison['second_configuration_id']:
                                comparison['second_configuration_name'],
                        }
                        for recommendation_type in (
                            'recommended_configuration_id',
                            'budget_recommended_configuration_id',
                            'cost_recommended_configuration_id',
                        ):
                            recommendation_id = comparison[recommendation_type]
                            self.assertEqual(
                                recommendation_id,
                                expected_recommendations[recommendation_type],
                            )
                            if recommendation_id is not None:
                                self.assertEqual(
                                    names_by_id[recommendation_id],
                                    configurations[recommendation_id]['name'],
                                )

    def test_compare_configurations_recommends_cheaper_equally_safe_variant(self):
        configurations = {
            'expensive-safe': {
                'configuration_id': 'expensive-safe',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'cheap-safe': {
                'configuration_id': 'cheap-safe',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'motherboardId': 'asus-z790',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'cheap-safe-copy': {
                'configuration_id': 'cheap-safe-copy',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'motherboardId': 'asus-z790',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'blocking-expensive': {
                'configuration_id': 'blocking-expensive',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'asus-z790',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'cheap-different-budget': {
                'configuration_id': 'cheap-different-budget',
                'budgetPln': 1,
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'motherboardId': 'asus-z790',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps(configurations), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, comparison = self.get_json(
                    '/api/compare?firstId=expensive-safe&secondId=cheap-safe'
                )
                self.assertEqual(status, 200)
                with self.subTest('recommends the cheaper equally safe variant'):
                    self.assertEqual(
                        comparison.get('cost_recommended_configuration_id'),
                        'cheap-safe',
                    )

                status, comparison = self.get_json(
                    '/api/compare?firstId=cheap-safe&secondId=expensive-safe'
                )
                self.assertEqual(status, 200)
                with self.subTest('keeps recommendation after swapping variants'):
                    self.assertEqual(
                        comparison.get('cost_recommended_configuration_id'),
                        'cheap-safe',
                    )

                status, comparison = self.get_json(
                    '/api/compare?firstId=cheap-safe&secondId=cheap-safe-copy'
                )
                self.assertEqual(status, 200)
                with self.subTest('does not recommend a tied cost'):
                    self.assertIn('cost_recommended_configuration_id', comparison)
                    self.assertIsNone(comparison['cost_recommended_configuration_id'])

                status, comparison = self.get_json(
                    '/api/compare?firstId=blocking-expensive&secondId=cheap-safe'
                )
                self.assertEqual(status, 200)
                with self.subTest('does not recommend when a variant has a blocking conflict'):
                    self.assertNotEqual(comparison['first_cost_pln'], comparison['second_cost_pln'])
                    self.assertEqual(comparison['first_compatibility']['level'], 'blocking')
                    self.assertIn('cost_recommended_configuration_id', comparison)
                    self.assertIsNone(comparison['cost_recommended_configuration_id'])

                status, comparison = self.get_json(
                    '/api/compare?firstId=expensive-safe&secondId=cheap-different-budget'
                )
                self.assertEqual(status, 200)
                with self.subTest('does not recommend when budget levels differ'):
                    self.assertNotEqual(comparison['first_cost_pln'], comparison['second_cost_pln'])
                    self.assertNotEqual(
                        comparison['first_budget']['level'], comparison['second_budget']['level']
                    )
                    self.assertIn('cost_recommended_configuration_id', comparison)
                    self.assertIsNone(comparison['cost_recommended_configuration_id'])

    def test_compare_configurations_reports_compatibility_for_each_variant(self):
        configurations = {
            'compatible-config': {
                'configuration_id': 'compatible-config',
                'budgetPln': 1,
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'compatible-config-copy': {
                'configuration_id': 'compatible-config-copy',
                'budgetPln': 1,
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'conflicting-config': {
                'configuration_id': 'conflicting-config',
                'budgetPln': 99999,
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'conflicting-config-copy': {
                'configuration_id': 'conflicting-config-copy',
                'budgetPln': 99999,
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps(configurations), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, comparison = self.get_json(
                    '/api/compare?firstId=compatible-config&secondId=conflicting-config'
                )

                self.assertEqual(status, 200)
                self.assertIn('first_compatibility', comparison)
                self.assertIn('second_compatibility', comparison)
                self.assertEqual(comparison['first_compatibility']['level'], 'ok')
                self.assertIn('zgodny', comparison['first_compatibility']['message'])
                self.assertEqual(comparison['second_compatibility']['level'], 'blocking')
                self.assertIn('socket', comparison['second_compatibility']['message'])

                self.assertEqual(
                    comparison.get('recommended_configuration_id'), 'compatible-config'
                )

                status, comparison = self.get_json(
                    '/api/compare?firstId=conflicting-config&secondId=compatible-config'
                )
                self.assertEqual(status, 200)
                self.assertEqual(
                    comparison.get('recommended_configuration_id'), 'compatible-config'
                )

                status, comparison = self.get_json(
                    '/api/compare?firstId=compatible-config&secondId=compatible-config-copy'
                )
                self.assertEqual(status, 200)
                self.assertIsNone(comparison['recommended_configuration_id'])

                status, comparison = self.get_json(
                    '/api/compare?firstId=conflicting-config&secondId=conflicting-config-copy'
                )
                self.assertEqual(status, 200)
                self.assertIsNone(comparison['recommended_configuration_id'])

    def test_compare_configurations_keeps_partial_compatibility_independent(self):
        configurations = {
            'partial-conflict': {
                'configuration_id': 'partial-conflict',
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'motherboardId': 'msi-b650',
                },
            },
            'partial-compatible': {
                'configuration_id': 'partial-compatible',
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                },
            },
            'partial-compatible-ram': {
                'configuration_id': 'partial-compatible-ram',
                'parts': {
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                },
            },
            'partial-compatible-case': {
                'configuration_id': 'partial-compatible-case',
                'parts': {
                    'motherboardId': 'msi-b650',
                    'caseId': 'atx-mid-tower',
                },
            },
            'partial-missing-psu': {
                'configuration_id': 'partial-missing-psu',
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                },
            },
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps(configurations), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, first_order = self.get_json(
                    '/api/compare?firstId=partial-conflict&secondId=partial-compatible'
                )

                self.assertEqual(status, 200)
                self.assertEqual(first_order['first_compatibility']['level'], 'blocking')
                conflict_message = first_order['first_compatibility']['message']
                self.assertIn('socketu LGA1700', conflict_message)
                self.assertIn('pamiec RAM', conflict_message)
                self.assertIn('zasilacz', conflict_message)
                self.assertEqual(
                    conflict_message.count(
                        'Wybierz plyte glowna i pamiec RAM, aby sprawdzic zgodnosc.'
                    ),
                    1,
                )
                self.assertEqual(conflict_message.count('zasilacz'), 1)
                self.assertEqual(first_order['second_compatibility']['level'], 'info')
                self.assertIn('Wybierz', first_order['second_compatibility']['message'])

                for configuration_id, missing_part in (
                    ('partial-compatible-ram', 'procesor'),
                    ('partial-compatible-case', 'pamiec RAM'),
                ):
                    with self.subTest(configuration_id=configuration_id):
                        status, partial = self.get_json(
                            '/api/compare?firstId='
                            f'{configuration_id}&secondId=partial-compatible'
                        )
                        self.assertEqual(status, 200)
                        self.assertEqual(partial['first_compatibility']['level'], 'info')
                        self.assertIn(missing_part, partial['first_compatibility']['message'])
                        self.assertNotIn(
                            'socketu LGA1700', partial['first_compatibility']['message']
                        )

                status, missing_psu = self.get_json(
                    '/api/compare?firstId=partial-missing-psu&secondId=partial-compatible'
                )
                self.assertEqual(status, 200)
                self.assertEqual(missing_psu['first_compatibility']['level'], 'info')
                self.assertEqual(
                    missing_psu['first_compatibility']['message'].count('zasilacz'), 1
                )

                status, second_order = self.get_json(
                    '/api/compare?firstId=partial-compatible&secondId=partial-conflict'
                )

                self.assertEqual(status, 200)
                self.assertEqual(
                    second_order['first_compatibility'],
                    first_order['second_compatibility'],
                )
                self.assertEqual(
                    second_order['second_compatibility'],
                    first_order['first_compatibility'],
                )

    def test_compare_configurations_reports_each_saved_budget_independently(self):
        configurations = {
            'within-budget': {
                'configuration_id': 'within-budget',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'over-budget': {
                'configuration_id': 'over-budget',
                'budgetPln': 3000,
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'motherboardId': 'asus-z790',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'also-within-budget': {
                'configuration_id': 'also-within-budget',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'motherboardId': 'asus-z790',
                    'ramId': 'corsair-vengeance-ddr5',
                    'psuId': 'corsair-rm750x',
                    'caseId': 'atx-mid-tower',
                },
            },
            'without-budget': {
                'configuration_id': 'without-budget',
                'parts': {'cpuId': 'ryzen-7-7800x3d'},
            },
            'blocking-within-budget': {
                'configuration_id': 'blocking-within-budget',
                'budgetPln': 5000,
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'asus-z790',
                },
            },
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps(configurations), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, comparison = self.get_json(
                    '/api/compare?firstId=within-budget&secondId=over-budget'
                )
                self.assertEqual(status, 200)
                self.assertIn('first_budget', comparison)
                self.assertIn('second_budget', comparison)
                self.assertEqual(comparison['first_budget']['level'], 'ok')
                self.assertEqual(comparison['first_budget']['remaining_pln'], 1025)
                self.assertIn('1025', comparison['first_budget']['message'])
                self.assertEqual(comparison['second_budget']['level'], 'blocking')
                self.assertEqual(comparison['second_budget']['overage_pln'], 825)
                self.assertIn('825', comparison['second_budget']['message'])
                with self.subTest('recommends the only within-budget variant'):
                    self.assertEqual(
                        comparison.get('budget_recommended_configuration_id'),
                        'within-budget',
                    )

                status, comparison = self.get_json(
                    '/api/compare?firstId=without-budget&secondId=within-budget'
                )
                self.assertEqual(status, 200)
                self.assertEqual(comparison['first_cost_pln'], 1599)
                self.assertEqual(comparison['second_cost_pln'], 3975)
                self.assertIn('first_budget', comparison)
                self.assertIn('second_budget', comparison)
                self.assertEqual(comparison['first_budget']['level'], 'info')
                self.assertIn('nie ustaw', comparison['first_budget']['message'])
                self.assertEqual(comparison['second_budget']['level'], 'ok')
                self.assertEqual(comparison['second_budget']['remaining_pln'], 1025)
                self.assertEqual(comparison['second_compatibility']['level'], 'ok')
                self.assertIn('zgodny', comparison['second_compatibility']['message'])
                with self.subTest('does not recommend without two unambiguous budgets'):
                    self.assertIsNone(comparison.get('budget_recommended_configuration_id'))

                status, comparison = self.get_json(
                    '/api/compare?firstId=over-budget&secondId=within-budget'
                )
                self.assertEqual(status, 200)
                with self.subTest('keeps recommendation after swapping variants'):
                    self.assertEqual(
                        comparison.get('budget_recommended_configuration_id'),
                        'within-budget',
                    )

                status, comparison = self.get_json(
                    '/api/compare?firstId=within-budget&secondId=also-within-budget'
                )
                self.assertEqual(status, 200)
                with self.subTest('does not recommend when both fit their budgets'):
                    self.assertIsNone(comparison.get('budget_recommended_configuration_id'))

                status, comparison = self.get_json(
                    '/api/compare?firstId=blocking-within-budget&secondId=within-budget'
                )
                self.assertEqual(status, 200)
                self.assertEqual(comparison['first_compatibility']['level'], 'blocking')
                with self.subTest('does not recommend when a variant is blocking'):
                    self.assertIsNone(comparison.get('budget_recommended_configuration_id'))

    def test_compare_configurations_reports_only_different_selected_parts(self):
        configurations = {
            'first-config': {
                'configuration_id': 'first-config',
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                },
            },
            'second-config': {
                'configuration_id': 'second-config',
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'ramId': 'corsair-vengeance-ddr5',
                },
            },
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps(configurations), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, comparison = self.get_json(
                    '/api/compare?firstId=first-config&secondId=second-config'
                )

        self.assertEqual(status, 200)
        self.assertEqual(comparison.get('differences', {}).get('cpuId'), {
            'first_id': 'ryzen-7-7800x3d',
            'second_id': 'core-i5-14600k',
            'first_price_pln': 1599,
            'second_price_pln': 1249,
            'price_difference_pln': 350,
        })
        self.assertEqual(comparison.get('differences', {}).get('motherboardId'), {
            'first_id': 'msi-b650',
            'second_id': None,
            'first_price_pln': 899,
            'second_price_pln': 0,
            'price_difference_pln': 899,
        })
        self.assertNotIn('ramId', comparison.get('differences', {}))

    def test_compare_configurations_reports_prices_for_different_parts(self):
        configurations = {
            'first-config': {
                'configuration_id': 'first-config',
                'parts': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                    'ramId': 'corsair-vengeance-ddr5',
                },
            },
            'second-config': {
                'configuration_id': 'second-config',
                'parts': {
                    'cpuId': 'core-i5-14600k',
                    'ramId': 'corsair-vengeance-ddr5',
                },
            },
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps(configurations), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, comparison = self.get_json(
                    '/api/compare?firstId=first-config&secondId=second-config'
                )

        self.assertEqual(status, 200)
        self.assertEqual(comparison['first_configuration_id'], 'first-config')
        self.assertEqual(comparison['second_configuration_id'], 'second-config')
        self.assertEqual(comparison['first_cost_pln'], 3027)
        self.assertEqual(comparison['second_cost_pln'], 1778)
        self.assertEqual(comparison['cheaper'], 'second')
        differences = comparison['differences']
        with self.subTest(part='different selection'):
            self.assertEqual(differences['cpuId'], {
                'first_id': 'ryzen-7-7800x3d',
                'second_id': 'core-i5-14600k',
                'first_price_pln': 1599,
                'second_price_pln': 1249,
                'price_difference_pln': 350,
            })
        with self.subTest(part='missing selection'):
            self.assertEqual(differences['motherboardId'], {
                'first_id': 'msi-b650',
                'second_id': None,
                'first_price_pln': 899,
                'second_price_pln': 0,
                'price_difference_pln': 899,
            })
        with self.subTest(part='shared selection'):
            self.assertNotIn('ramId', differences)

    def test_compare_configurations_rejects_missing_or_duplicate_ids_without_partial_result(self):
        configurations = {
            'saved-config': {
                'configuration_id': 'saved-config',
                'parts': {'cpuId': 'ryzen-7-7800x3d'},
            },
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps(configurations), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                for query in (
                    'firstId=saved-config&secondId=does-not-exist',
                    'firstId=does-not-exist&secondId=saved-config',
                    'firstId=saved-config&secondId=saved-config',
                ):
                    with self.subTest(query=query):
                        status, response = self.get_json('/api/compare?' + query)
                        self.assertEqual(status, 400)
                        self.assertIn('error', response)
                        self.assertNotIn('first_cost_pln', response)
                        self.assertNotIn('second_cost_pln', response)

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

    def test_page_saves_current_configuration_and_shows_identifier(self):
        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                with Browser(self.base_url) as browser:
                    status = browser.evaluate("""
                     (async () => {
                         const save = document.querySelector('#save-configuration');
                         const identifier = document.querySelector('#configuration-id');
                         const name = document.querySelector('#configuration-name');
                         if (!save || !identifier || !name) return {controls: false};
                         name.value = 'Wydajny zestaw do pracy';
                            document.querySelector('#cpu').value = 'ryzen-7-7800x3d';
                            document.querySelector('#motherboard').value = 'msi-b650';
                            document.querySelector('#memory').value = 'corsair-vengeance-ddr5';
                            document.querySelector('#power-supply').value = 'corsair-rm750x';
                            document.querySelector('#case').value = 'atx-mid-tower';
                            document.querySelector('#budget').value = '5000';
                            save.click();
                            for (let attempt = 0; attempt < 200 && !identifier.textContent; attempt++) {
                                await new Promise(resolve => setTimeout(resolve, 10));
                            }
                            const response = await fetch('/api/configurations/' + identifier.textContent);
                             return {
                                 controls: true,
                                 id: identifier.textContent,
                                 status: response.status,
                                 saved: await response.json(),
                            };
                        })()
                    """)

        self.assertIsInstance(status, dict)
        self.assertTrue(status['controls'], 'zapis udostepnia opisane pole nazwy')
        self.assertTrue(status['id'])
        self.assertEqual(status['status'], 200)
        self.assertEqual(status['saved']['name'], 'Wydajny zestaw do pracy')
        self.assertEqual(status['saved']['parts'], {
            'cpuId': 'ryzen-7-7800x3d',
            'motherboardId': 'msi-b650',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'caseId': 'atx-mid-tower',
        })
        self.assertEqual(status['saved']['budgetPln'], 5000)

    def test_page_compares_named_configurations_after_app_restart(self):
        first_payload = {
            'name': 'Zestaw do pracy',
            'cpuId': 'ryzen-7-7800x3d',
            'motherboardId': 'msi-b650',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'caseId': 'atx-mid-tower',
            'budgetPln': 5000,
        }
        second_payload = {
            'name': 'Zestaw z konfliktem',
            'cpuId': 'ryzen-7-7800x3d',
            'motherboardId': 'asus-z790',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'caseId': 'atx-mid-tower',
            'budgetPln': 5000,
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                first_status, first_saved = self.post_json(
                    '/api/configurations', first_payload
                )
                second_status, second_saved = self.post_json(
                    '/api/configurations', second_payload
                )
                self.assertEqual(first_status, 201)
                self.assertEqual(second_status, 201)

                restarted_app = create_app(port=0)
                restarted_thread = Thread(target=restarted_app.serve_forever)
                restarted_thread.start()
                try:
                    restarted_url = f'http://127.0.0.1:{restarted_app.server_port}'
                    with Browser(restarted_url) as browser:
                        state = browser.evaluate(f"""
                            (async () => {{
                                const first = document.querySelector('#compare-first-id');
                                const second = document.querySelector('#compare-second-id');
                                const button = document.querySelector('#compare-configurations');
                                const output = document.querySelector('#comparison-result');
                                if (!first || !second || !button || !output) return false;
                                first.value = '{first_saved['configuration_id']}';
                                second.value = '{second_saved['configuration_id']}';
                                button.click();
                                for (let attempt = 0; attempt < 200; attempt++) {{
                                    if (output.textContent.includes('Zestaw do pracy') &&
                                        output.textContent.includes('Zestaw z konfliktem') &&
                                        output.textContent.includes('Rekomendowany wariant: Zestaw do pracy') &&
                                        !output.textContent.includes('Rekomendowany wariant: {first_saved['configuration_id']}')) return true;
                                    await new Promise(resolve => setTimeout(resolve, 10));
                                }}
                                return false;
                            }})()
                        """)
                finally:
                    restarted_app.shutdown()
                    restarted_thread.join()
                    restarted_app.server_close()

        self.assertTrue(state, 'porownanie po restarcie pokazuje nazwy i rekomendacje')

    def test_page_lists_and_opens_named_configurations_after_app_restart(self):
        first_payload = {
            'name': 'Zestaw do pracy',
            'cpuId': 'ryzen-7-7800x3d',
            'motherboardId': 'msi-b650',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'caseId': 'atx-mid-tower',
            'budgetPln': 5000,
        }
        second_payload = {
            'name': 'Zestaw gamingowy',
            'cpuId': 'core-i5-14600k',
            'motherboardId': 'asus-z790',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'caseId': 'atx-mid-tower',
            'budgetPln': 3000,
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                first_status, first_saved = self.post_json(
                    '/api/configurations', first_payload
                )
                second_status, second_saved = self.post_json(
                    '/api/configurations', second_payload
                )
                self.assertEqual(first_status, 201)
                self.assertEqual(second_status, 201)

                restarted_app = create_app(port=0)
                restarted_thread = Thread(target=restarted_app.serve_forever)
                restarted_thread.start()
                try:
                    restarted_url = f'http://127.0.0.1:{restarted_app.server_port}'
                    with Browser(restarted_url) as browser:
                        state = browser.evaluate(f"""
                            (async () => {{
                                const list = document.querySelector('#saved-configurations');
                                if (!list) return {{list: false}};
                                for (let attempt = 0; attempt < 200 &&
                                    list.querySelectorAll('[data-configuration-id]').length < 2; attempt++) {{
                                    await new Promise(resolve => setTimeout(resolve, 10));
                                }}
                                const entries = [...list.querySelectorAll('[data-configuration-id]')];
                                const names = list.textContent;
                                const states = [];
                                for (const id of ['{first_saved['configuration_id']}', '{second_saved['configuration_id']}']) {{
                                    const entry = list.querySelector(`[data-configuration-id="${{id}}"]`);
                                    if (!entry) return {{list: true, names, entries: entries.length, states}};
                                    entry.click();
                                    const expectedCpu = id === '{first_saved['configuration_id']}'
                                        ? '{first_payload['cpuId']}' : '{second_payload['cpuId']}';
                                    const expectedBudget = id === '{first_saved['configuration_id']}'
                                        ? '{first_payload['budgetPln']}' : '{second_payload['budgetPln']}';
                                    const expectedCost = id === '{first_saved['configuration_id']}'
                                        ? '3975 PLN' : '3825 PLN';
                                    for (let attempt = 0; attempt < 200 &&
                                        (document.querySelector('#cpu').value !== expectedCpu ||
                                         document.querySelector('#budget').value !== expectedBudget ||
                                         document.querySelector('#total-cost').textContent !== expectedCost); attempt++) {{
                                        await new Promise(resolve => setTimeout(resolve, 10));
                                    }}
                                    states.push({{
                                        id,
                                        cpu: document.querySelector('#cpu').value,
                                        motherboard: document.querySelector('#motherboard').value,
                                        memory: document.querySelector('#memory').value,
                                        powerSupply: document.querySelector('#power-supply').value,
                                        caseId: document.querySelector('#case').value,
                                        budget: document.querySelector('#budget').value,
                                        cost: document.querySelector('#total-cost').textContent,
                                        result: document.querySelector('#result').textContent,
                                        budgetResult: document.querySelector('#budget-result').textContent,
                                    }});
                                }}
                                return {{list: true, names, entries: entries.length, states}};
                            }})()
                        """)
                finally:
                    restarted_app.shutdown()
                    restarted_thread.join()
                    restarted_app.server_close()

        self.assertTrue(state['list'], 'ekran udostepnia liste zapisow')
        self.assertEqual(state['entries'], 2)
        self.assertIn('Zestaw do pracy', state['names'])
        self.assertIn('Zestaw gamingowy', state['names'])
        expected_states = [
            {
                'id': first_saved['configuration_id'],
                'cpu': first_payload['cpuId'],
                'motherboard': first_payload['motherboardId'],
                'memory': first_payload['ramId'],
                'powerSupply': first_payload['psuId'],
                'caseId': first_payload['caseId'],
                'budget': str(first_payload['budgetPln']),
                'cost': '3975 PLN',
            },
            {
                'id': second_saved['configuration_id'],
                'cpu': second_payload['cpuId'],
                'motherboard': second_payload['motherboardId'],
                'memory': second_payload['ramId'],
                'powerSupply': second_payload['psuId'],
                'caseId': second_payload['caseId'],
                'budget': str(second_payload['budgetPln']),
                'cost': '3825 PLN',
            },
        ]
        for actual, expected in zip(state['states'], expected_states):
            for field, value in expected.items():
                self.assertEqual(actual[field], value)
        self.assertIn('Socket AM5 procesora i plyty glownej jest zgodny.', state['states'][0]['result'])
        self.assertIn('Pamiec RAM DDR5 jest zgodna z plyta glowna.', state['states'][0]['result'])
        self.assertIn('Moc zasilacza 750 W jest wystarczajaca; zestaw wymaga 210 W.', state['states'][0]['result'])
        self.assertIn('Plyta w formacie ATX pasuje do obudowy.', state['states'][0]['result'])
        self.assertIn('1025 PLN', state['states'][0]['budgetResult'])
        self.assertIn('Socket LGA1700 procesora i plyty glownej jest zgodny.', state['states'][1]['result'])
        self.assertIn('Pamiec RAM DDR5 jest zgodna z plyta glowna.', state['states'][1]['result'])
        self.assertIn('Moc zasilacza 750 W jest wystarczajaca; zestaw wymaga 205 W.', state['states'][1]['result'])
        self.assertIn('Plyta w formacie ATX pasuje do obudowy.', state['states'][1]['result'])
        self.assertIn('825 PLN', state['states'][1]['budgetResult'])

    def test_page_provides_two_named_configuration_choices_after_restart(self):
        payloads = [
            {
                'name': 'Zestaw do pracy',
                'cpuId': 'ryzen-7-7800x3d',
                'motherboardId': 'msi-b650',
            },
            {
                'name': 'Zestaw gamingowy',
                'cpuId': 'core-i5-14600k',
                'motherboardId': 'asus-z790',
            },
        ]

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                saved = []
                for payload in payloads:
                    status, configuration = self.post_json('/api/configurations', payload)
                    self.assertEqual(status, 201)
                    saved.append(configuration)

                restarted_app = create_app(port=0)
                restarted_thread = Thread(target=restarted_app.serve_forever)
                restarted_thread.start()
                try:
                    restarted_url = f'http://127.0.0.1:{restarted_app.server_port}'
                    with Browser(restarted_url) as browser:
                        state = browser.evaluate(f"""
                            (async () => {{
                                const first = document.querySelector('#compare-first-id');
                                const second = document.querySelector('#compare-second-id');
                                const firstLabel = document.querySelector('label[for="compare-first-id"]');
                                const secondLabel = document.querySelector('label[for="compare-second-id"]');
                                for (let attempt = 0; attempt < 200 &&
                                    (!first?.querySelector('option[value="{saved[0]['configuration_id']}"]') ||
                                     !second?.querySelector('option[value="{saved[1]['configuration_id']}"]')); attempt++) {{
                                    await new Promise(resolve => setTimeout(resolve, 10));
                                }}
                                return {{
                                    firstTag: first?.tagName,
                                    secondTag: second?.tagName,
                                    firstLabel: firstLabel?.textContent,
                                    secondLabel: secondLabel?.textContent,
                                    firstNames: [...(first?.options || [])].map(option => option.textContent),
                                    secondNames: [...(second?.options || [])].map(option => option.textContent),
                                }};
                            }})()
                        """)
                finally:
                    restarted_app.shutdown()
                    restarted_thread.join()
                    restarted_app.server_close()

        self.assertEqual(state['firstTag'], 'SELECT')
        self.assertEqual(state['secondTag'], 'SELECT')
        self.assertIn('Pierwszy wariant', state['firstLabel'])
        self.assertIn('Drugi wariant', state['secondLabel'])
        self.assertIn('Zestaw do pracy', state['firstNames'])
        self.assertIn('Zestaw gamingowy', state['firstNames'])
        self.assertIn('Zestaw do pracy', state['secondNames'])
        self.assertIn('Zestaw gamingowy', state['secondNames'])

    def test_page_communicates_empty_named_configuration_list(self):
        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                with Browser(self.base_url) as browser:
                    state = browser.evaluate("""
                        (async () => {
                            const list = document.querySelector('#saved-configurations');
                            const first = document.querySelector('#compare-first-id');
                            const second = document.querySelector('#compare-second-id');
                            if (!list) return {list: false};
                            for (let attempt = 0; attempt < 200 &&
                                list.textContent.includes('Wczytywanie'); attempt++) {
                                await new Promise(resolve => setTimeout(resolve, 10));
                            }
                            return {
                                list: true,
                                text: list.textContent,
                                firstComparisonText: first?.textContent,
                                secondComparisonText: second?.textContent,
                            };
                        })()
                    """)

        self.assertTrue(state['list'])
        self.assertIn('Brak nazwanych zapisow do wyboru.', state['text'])
        self.assertIn('Brak nazwanych zapisow do porownania', state['firstComparisonText'])
        self.assertIn('Brak nazwanych zapisow do porownania', state['secondComparisonText'])

    def test_page_opens_named_configuration_from_keyboard_control(self):
        payload = {
            'name': 'Zestaw klawiaturowy',
            'cpuId': 'ryzen-7-7800x3d',
            'motherboardId': 'msi-b650',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'caseId': 'atx-mid-tower',
            'budgetPln': 5000,
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, saved = self.post_json('/api/configurations', payload)
                self.assertEqual(status, 201)
                with Browser(self.base_url) as browser:
                    state = browser.evaluate(f"""
                        (async () => {{
                            const list = document.querySelector('#saved-configurations');
                            for (let attempt = 0; attempt < 200 &&
                                !list.querySelector('[data-configuration-id]'); attempt++) {{
                                await new Promise(resolve => setTimeout(resolve, 10));
                            }}
                            const entry = list.querySelector('[data-configuration-id]');
                            if (!entry) return {{entry: false}};
                            entry.focus();
                            entry.dispatchEvent(new KeyboardEvent('keydown', {{
                                key: 'Enter', bubbles: true
                            }}));
                            for (let attempt = 0; attempt < 200 &&
                                document.querySelector('#cpu').value !== '{payload['cpuId']}'; attempt++) {{
                                await new Promise(resolve => setTimeout(resolve, 10));
                            }}
                            return {{
                                entry: true,
                                tag: entry.tagName,
                                tabIndex: entry.tabIndex,
                                cpu: document.querySelector('#cpu').value,
                            }};
                        }})()
                    """)

        self.assertTrue(state['entry'])
        self.assertIn(state['tag'], ('BUTTON', 'A'))
        self.assertGreaterEqual(state['tabIndex'], 0)
        self.assertEqual(state['cpu'], payload['cpuId'])

    def test_page_saves_configuration_without_empty_name(self):
        with Browser(self.base_url) as browser:
            state = browser.evaluate("""
                (async () => {
                    const save = document.querySelector('#save-configuration');
                    const name = document.querySelector('#configuration-name');
                    let request;
                    window.fetch = (url, options) => {
                        request = JSON.parse(options.body);
                        return Promise.resolve({
                            ok: true,
                            json: () => Promise.resolve({
                                configuration_id: 'unnamed-config',
                                share_url: '/api/configurations/unnamed-config',
                            }),
                        });
                    };
                    if (!save || !name) return {controls: false};
                    save.click();
                    for (let attempt = 0; attempt < 200 && !request; attempt++) {
                        await new Promise(resolve => setTimeout(resolve, 10));
                    }
                    return {controls: true, request};
                })()
            """)

        self.assertTrue(state['controls'])
        self.assertIsNotNone(state['request'])
        self.assertNotIn('name', state['request'])

    def test_page_escapes_script_closing_sequence_in_saved_name(self):
        name = 'Zestaw </script><script>window.injected=true</script>'
        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps({
                'unsafe-config': {
                    'name': name,
                    'parts': {'cpuId': 'ryzen-7-7800x3d'},
                },
            }), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                request = Request(
                    f'{self.base_url}/api/configurations/unsafe-config',
                    headers={'Accept': 'text/html'},
                )
                with urlopen(request) as response:
                    status = response.status
                    html = response.read().decode()

        self.assertEqual(status, 200)
        self.assertIn(
            'Zestaw <\\/script><script>window.injected=true<\\/script>',
            html,
        )
        self.assertNotIn(
            'Zestaw </script><script>window.injected=true</script>',
            html,
        )

    def test_share_url_opens_saved_configuration_in_a_new_page_session(self):
        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            payload = {
                'cpuId': 'ryzen-7-7800x3d',
                'motherboardId': 'msi-b650',
                'ramId': 'corsair-vengeance-ddr5',
                'psuId': 'corsair-rm750x',
                'caseId': 'atx-mid-tower',
                'budgetPln': 5000,
            }
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, saved = self.post_json('/api/configurations', payload)
                self.assertEqual(status, 201)
                with Browser(self.base_url + saved['share_url']) as browser:
                    state = browser.evaluate("""
                        (async () => {
                            const waitForSavedBuild = async () => {
                                for (let attempt = 0; attempt < 200; attempt++) {
                                    const total = document.querySelector('#total-cost')?.textContent;
                                    const result = document.querySelector('#result')?.textContent;
                                    if (document.querySelector('#cpu')?.value === 'ryzen-7-7800x3d'
                                        && document.querySelector('#motherboard')?.value === 'msi-b650'
                                        && document.querySelector('#memory')?.value === 'corsair-vengeance-ddr5'
                                        && document.querySelector('#power-supply')?.value === 'corsair-rm750x'
                                        && document.querySelector('#case')?.value === 'atx-mid-tower'
                                        && document.querySelector('#budget')?.value === '5000'
                                        && total === '3975 PLN'
                                        && result.includes('Socket AM5')) return;
                                    await new Promise(resolve => setTimeout(resolve, 10));
                                }
                            };
                            await waitForSavedBuild();
                            return {
                                cpu: document.querySelector('#cpu')?.value,
                                motherboard: document.querySelector('#motherboard')?.value,
                                memory: document.querySelector('#memory')?.value,
                                powerSupply: document.querySelector('#power-supply')?.value,
                                caseId: document.querySelector('#case')?.value,
                                budget: document.querySelector('#budget')?.value,
                                result: document.querySelector('#result')?.textContent,
                                level: document.querySelector('#result')?.dataset.level,
                                budgetResult: document.querySelector('#budget-result')?.textContent,
                                total: document.querySelector('#total-cost')?.textContent,
                            };
                        })()
                    """)

        self.assertIsInstance(state, dict)
        self.assertEqual(state.get('cpu'), 'ryzen-7-7800x3d')
        self.assertEqual(state.get('motherboard'), 'msi-b650')
        self.assertEqual(state.get('memory'), 'corsair-vengeance-ddr5')
        self.assertEqual(state.get('powerSupply'), 'corsair-rm750x')
        self.assertEqual(state.get('caseId'), 'atx-mid-tower')
        self.assertEqual(state.get('budget'), '5000')
        self.assertEqual(state.get('total'), '3975 PLN')
        self.assertEqual(state.get('level'), 'ok')
        self.assertIn('Socket AM5 procesora i plyty glownej jest zgodny.', state.get('result', ''))
        self.assertIn('Pamiec RAM DDR5 jest zgodna z plyta glowna.', state.get('result', ''))
        self.assertIn('Moc zasilacza 750 W jest wystarczajaca', state.get('result', ''))
        self.assertIn('Plyta w formacie ATX pasuje do obudowy.', state.get('result', ''))
        self.assertIn('Zestaw miesci sie w budzecie', state.get('budgetResult', ''))
        self.assertIn('pozostaje 1025 PLN', state.get('budgetResult', ''))

    def test_invalid_share_url_reports_error_without_loading_saved_parts(self):
        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text(json.dumps({
                'known-config': {
                    'cpuId': 'ryzen-7-7800x3d',
                    'motherboardId': 'msi-b650',
                    'budgetPln': 5000,
                },
            }), encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                with Browser(self.base_url + '/api/configurations/does-not-exist') as browser:
                    state = browser.evaluate("""
                        (async () => {
                            for (let attempt = 0; attempt < 200; attempt++) {
                                if (document.querySelector('#result')?.dataset.level === 'blocking') break;
                                await new Promise(resolve => setTimeout(resolve, 10));
                            }
                            return {
                                cpu: document.querySelector('#cpu')?.value,
                                motherboard: document.querySelector('#motherboard')?.value,
                                memory: document.querySelector('#memory')?.value,
                                powerSupply: document.querySelector('#power-supply')?.value,
                                caseId: document.querySelector('#case')?.value,
                                budget: document.querySelector('#budget')?.value,
                                result: document.querySelector('#result')?.textContent,
                                level: document.querySelector('#result')?.dataset.level,
                            };
                        })()
                    """)

        self.assertIsInstance(state, dict)
        self.assertEqual(state.get('cpu'), '')
        self.assertEqual(state.get('motherboard'), '')
        self.assertEqual(state.get('memory'), '')
        self.assertEqual(state.get('powerSupply'), '')
        self.assertEqual(state.get('caseId'), '')
        self.assertEqual(state.get('budget'), '')
        self.assertEqual(state.get('level'), 'blocking')
        self.assertIn('Nie znaleziono konfiguracji', state.get('result', ''))

    def test_page_without_share_identifier_opens_empty_configuration(self):
        with Browser(self.base_url) as browser:
            state = browser.evaluate("""
                (() => ({
                    cpu: document.querySelector('#cpu')?.value,
                    motherboard: document.querySelector('#motherboard')?.value,
                    memory: document.querySelector('#memory')?.value,
                    powerSupply: document.querySelector('#power-supply')?.value,
                    caseId: document.querySelector('#case')?.value,
                    budget: document.querySelector('#budget')?.value,
                    total: document.querySelector('#total-cost')?.textContent,
                }))()
            """)

        self.assertIsInstance(state, dict)
        self.assertEqual(
            {state.get('cpu'), state.get('motherboard'), state.get('memory'), state.get('powerSupply'), state.get('caseId')},
            {'', '', '', '', ''},
        )
        self.assertEqual(state.get('budget'), '')
        self.assertEqual(state.get('total'), '0 PLN')

    def test_page_shows_replaces_and_clears_saved_configuration_share_link(self):
        with Browser(self.base_url) as browser:
            state = browser.evaluate("""
                (async () => {
                    const save = document.querySelector('#save-configuration');
                    const result = document.querySelector('#result');
                    const responses = [
                        {configuration_id: 'first-config', share_url: '/api/configurations/first-config'},
                        {configuration_id: 'second-config', share_url: '/api/configurations/second-config'},
                    ];
                    let saveCount = 0;
                    window.fetch = (url, options) => {
                        if (url !== '/api/configurations' || options.method !== 'POST') {
                            return Promise.reject(new Error('unexpected request'));
                        }
                        if (saveCount < responses.length) {
                            return Promise.resolve({ok: true, json: () => Promise.resolve(responses[saveCount++])});
                        }
                        return Promise.resolve({
                            ok: false,
                            json: () => Promise.resolve({error: 'Nie udalo sie zapisac konfiguracji.'}),
                        });
                    };
                    const shareLinks = () => Array.from(document.querySelectorAll('a'))
                        .map(link => ({href: link.getAttribute('href'), text: link.textContent, hidden: link.hidden}));
                    const waitFor = async predicate => {
                        for (let attempt = 0; attempt < 200; attempt++) {
                            if (predicate()) return true;
                            await new Promise(resolve => setTimeout(resolve, 10));
                        }
                        return false;
                    };
                    if (!save || !result) return {error: 'missing save controls'};
                    save.click();
                    await waitFor(() => shareLinks().some(link => link.href === '/api/configurations/first-config'));
                    const first = shareLinks();
                    save.click();
                    await waitFor(() => shareLinks().some(link => link.href === '/api/configurations/second-config'));
                    const second = shareLinks();
                    save.click();
                    await waitFor(() => result.textContent.includes('Nie udalo sie zapisac konfiguracji.'));
                    return {first, second, failed: shareLinks(), error: result.textContent};
                })()
            """)

        self.assertIsInstance(state, dict)
        self.assertTrue(any(
            link['href'] == '/api/configurations/first-config'
            and link['text'] == '/api/configurations/first-config'
            and link['hidden'] is False
            for link in state['first']
        ))
        self.assertTrue(any(
            link['href'] == '/api/configurations/second-config'
            and link['text'] == '/api/configurations/second-config'
            and link['hidden'] is False
            for link in state['second']
        ))
        self.assertFalse(any(
            link['href'] == '/api/configurations/first-config'
            for link in state['second']
        ))
        self.assertFalse(any(
            (link['href'] == '/api/configurations/first-config'
             or link['href'] == '/api/configurations/second-config')
            and link['hidden'] is False
            for link in state['failed']
        ))
        self.assertTrue(all(link['hidden'] is True for link in state['failed']))
        self.assertIn('Nie udalo sie zapisac konfiguracji.', state['error'])

    def test_page_compares_two_saved_configurations_and_refreshes_to_a_tie(self):
        with Browser(self.base_url) as browser:
            state = browser.evaluate("""
                (async () => {
                    const first = document.querySelector('#compare-first-id');
                    const second = document.querySelector('#compare-second-id');
                    const button = document.querySelector('#compare-configurations');
                    const output = document.querySelector('#comparison-result');
                    const responses = new Map([
                         ['first-config|second-config', {
                              first_configuration_id: 'first-config',
                              second_configuration_id: 'second-config',
                              first_configuration_name: 'Stacja robocza',
                              second_configuration_name: 'Zestaw gamingowy',
                              recommended_configuration_id: 'first-config',
                             first_cost_pln: 3975,
                            second_cost_pln: 3250,
                            cheaper: 'second',
                             first_compatibility: {
                                 level: 'ok',
                                 message: 'Pierwszy zestaw jest zgodny.',
                             },
                             first_budget: {
                                 level: 'ok',
                                 message: 'Budzet wystarcza; pozostaje 1025 PLN.',
                             },
                             second_compatibility: {
                                 level: 'blocking',
                                 message: 'Drugi zestaw ma konflikt socketu.',
                             },
                             second_budget: {
                                 level: 'blocking',
                                 message: 'Budzet przekroczony o 825 PLN.',
                             },
                            differences: {
                                cpuId: {
                                    first_id: 'ryzen-7-7800x3d',
                                    second_id: 'core-i5-14600k',
                                    first_price_pln: 1599,
                                    second_price_pln: 1249,
                                    price_difference_pln: 350,
                                },
                            },
                        }],
                           ['first-config|tie-config', {
                             first_configuration_id: 'first-config',
                             second_configuration_id: 'tie-config',
                             recommended_configuration_id: null,
                             first_cost_pln: 3975,
                            second_cost_pln: 3975,
                            cheaper: 'tie',
                             first_compatibility: {
                                 level: 'blocking',
                                 message: 'Pierwszy remisowy zestaw ma konflikt.',
                             },
                             first_budget: {
                                 level: 'info',
                                 message: 'Dla pierwszego wariantu nie ustawiono budzetu.',
                             },
                             second_compatibility: {
                                 level: 'ok',
                                 message: 'Drugi remisowy zestaw jest zgodny.',
                             },
                             second_budget: {
                                 level: 'ok',
                                 message: 'Budzet wystarcza; pozostaje 0 PLN.',
                             },
                            differences: {
                                motherboardId: {
                                    first_id: 'msi-b650',
                                    second_id: 'asus-z790',
                                    first_price_pln: 899,
                                    second_price_pln: 1099,
                                    price_difference_pln: -200,
                                },
                             },
                         }],
                          ['second-config|first-config', {
                             first_configuration_id: 'second-config',
                             second_configuration_id: 'first-config',
                             recommended_configuration_id: 'first-config',
                             first_cost_pln: 3250,
                             second_cost_pln: 3975,
                             cheaper: 'first',
                             first_compatibility: {
                                 level: 'blocking',
                                 message: 'Pierwszy zestaw ma konflikt socketu.',
                             },
                             first_budget: {
                                 level: 'blocking',
                                 message: 'Budzet przekroczony o 825 PLN.',
                             },
                             second_compatibility: {
                                 level: 'ok',
                                 message: 'Drugi zestaw jest zgodny.',
                             },
                             second_budget: {
                                 level: 'ok',
                                 message: 'Budzet wystarcza; pozostaje 1025 PLN.',
                             },
                              differences: {},
                          }],
                          ['within-budget|over-budget', {
                              first_configuration_id: 'within-budget',
                              second_configuration_id: 'over-budget',
                              recommended_configuration_id: null,
                              cost_recommended_configuration_id: null,
                              budget_recommended_configuration_id: 'within-budget',
                              first_cost_pln: 3250,
                              second_cost_pln: 3975,
                              cheaper: 'first',
                              first_compatibility: {
                                  level: 'ok',
                                  message: 'Pierwszy zestaw jest zgodny.',
                              },
                              first_budget: {
                                  level: 'ok',
                                  message: 'Budzet wystarcza; pozostaje 1750 PLN.',
                              },
                              second_compatibility: {
                                  level: 'ok',
                                  message: 'Drugi zestaw jest zgodny.',
                              },
                              second_budget: {
                                  level: 'blocking',
                                  message: 'Budzet przekroczony o 825 PLN.',
                              },
                              differences: {},
                          }],
                           ['over-budget|within-budget', {
                              first_configuration_id: 'over-budget',
                              second_configuration_id: 'within-budget',
                              recommended_configuration_id: null,
                              budget_recommended_configuration_id: 'within-budget',
                              first_cost_pln: 3975,
                              second_cost_pln: 3250,
                              cheaper: 'second',
                              first_compatibility: {
                                  level: 'ok',
                                  message: 'Pierwszy zestaw jest zgodny.',
                              },
                              first_budget: {
                                  level: 'blocking',
                                  message: 'Budzet przekroczony o 825 PLN.',
                              },
                              second_compatibility: {
                                  level: 'ok',
                                  message: 'Drugi zestaw jest zgodny.',
                              },
                              second_budget: {
                                  level: 'ok',
                                  message: 'Budzet wystarcza; pozostaje 1750 PLN.',
                              },
                               differences: {},
                           }],
                           ['both-within|also-within', {
                               first_configuration_id: 'both-within',
                               second_configuration_id: 'also-within',
                               recommended_configuration_id: null,
                               budget_recommended_configuration_id: null,
                               cost_recommended_configuration_id: 'also-within',
                               first_cost_pln: 3250,
                               second_cost_pln: 3000,
                               cheaper: 'second',
                               first_compatibility: {
                                   level: 'ok',
                                   message: 'Pierwszy zestaw jest zgodny.',
                               },
                               first_budget: {
                                   level: 'ok',
                                   message: 'Budzet wystarcza; pozostaje 1750 PLN.',
                               },
                               second_compatibility: {
                                   level: 'ok',
                                   message: 'Drugi zestaw jest zgodny.',
                               },
                               second_budget: {
                                   level: 'ok',
                                   message: 'Budzet wystarcza; pozostaje 2000 PLN.',
                               },
                               differences: {},
                           }],
                           ['also-within|both-within', {
                               first_configuration_id: 'also-within',
                               second_configuration_id: 'both-within',
                               recommended_configuration_id: null,
                               budget_recommended_configuration_id: null,
                               cost_recommended_configuration_id: 'also-within',
                               first_cost_pln: 3000,
                               second_cost_pln: 3250,
                               cheaper: 'first',
                               first_compatibility: {
                                   level: 'ok',
                                   message: 'Pierwszy zestaw jest zgodny.',
                               },
                               first_budget: {
                                   level: 'ok',
                                   message: 'Budzet wystarcza; pozostaje 2000 PLN.',
                               },
                               second_compatibility: {
                                   level: 'ok',
                                   message: 'Drugi zestaw jest zgodny.',
                               },
                               second_budget: {
                                   level: 'ok',
                                   message: 'Budzet wystarcza; pozostaje 1750 PLN.',
                               },
                               differences: {},
                           }],
                           ['blocking|within-budget', {
                               first_configuration_id: 'blocking',
                               second_configuration_id: 'within-budget',
                               recommended_configuration_id: 'within-budget',
                               budget_recommended_configuration_id: null,
                               first_cost_pln: 3975,
                               second_cost_pln: 3250,
                               cheaper: 'second',
                               first_compatibility: {
                                   level: 'blocking',
                                   message: 'Pierwszy zestaw ma konflikt socketu.',
                               },
                               first_budget: {
                                   level: 'ok',
                                   message: 'Budzet wystarcza; pozostaje 1025 PLN.',
                               },
                               second_compatibility: {
                                   level: 'ok',
                                   message: 'Drugi zestaw jest zgodny.',
                               },
                               second_budget: {
                                   level: 'ok',
                                   message: 'Budzet wystarcza; pozostaje 1750 PLN.',
                               },
                               differences: {},
                           }],
                       ]);
                    window.fetch = url => {
                        const query = new URL(url, window.location).searchParams;
                        const key = query.get('firstId') + '|' + query.get('secondId');
                        const comparison = responses.get(key);
                        return Promise.resolve({
                            ok: Boolean(comparison),
                            json: () => Promise.resolve(comparison || {
                                error: 'Nie znaleziono porownania.',
                            }),
                        });
                    };
                    const waitFor = async predicate => {
                        for (let attempt = 0; attempt < 200; attempt++) {
                            if (predicate()) return true;
                            await new Promise(resolve => setTimeout(resolve, 10));
                        }
                        return false;
                    };
                    const controls = Boolean(first && second && button && output);
                    if (!controls) return {
                        controls,
                        firstPair: false,
                        firstCompatibility: false,
                        refreshedPair: false,
                        refreshedCompatibility: false,
                        tie: false,
                    };

                    ['first-config', 'second-config', 'within-budget', 'over-budget',
                     'both-within', 'also-within', 'blocking', 'tie-config']
                        .forEach(id => [first, second].forEach(select =>
                            select.add(new Option(id, id))));
                    first.value = 'first-config';
                    second.value = 'second-config';
                    button.click();
                      const firstPair = await waitFor(() =>
                          output.textContent.includes('Stacja robocza: 3975 PLN') &&
                          output.textContent.includes('Zestaw gamingowy: 3250 PLN') &&
                          output.textContent.includes('3250 PLN') &&
                        output.textContent.toLowerCase().includes('second') &&
                        output.textContent.includes('AMD Ryzen 7 7800X3D') &&
                        output.textContent.includes('Intel Core i5-14600K') &&
                        output.textContent.includes('1599 PLN') &&
                        output.textContent.includes('1249 PLN') &&
                          output.textContent.includes('+350 PLN')
                      );
                      const missingCostRecommendation = await waitFor(() =>
                          !output.textContent.includes('Rekomendowany wariant kosztowy:')
                      );
                       const firstRecommendation = output.textContent.includes('Rekomendowany wariant: Stacja robocza') &&
                           !output.textContent.includes('Rekomendowany wariant: first-config');
                     const firstCompatibility = await waitFor(() =>
                         output.textContent.includes('Pierwszy wariant: ok') &&
                         output.textContent.includes('Drugi wariant: blocking') &&
                         output.textContent.includes('Pierwszy zestaw jest zgodny.') &&
                         output.textContent.includes('Drugi zestaw ma konflikt socketu.')
                     );
                     const firstBudget = await waitFor(() =>
                         output.textContent.includes('Budzet pierwszego wariantu: ok; Budzet wystarcza; pozostaje 1025 PLN.')
                     );
                     const secondBudget = await waitFor(() =>
                         output.textContent.includes('Budzet drugiego wariantu: blocking; Budzet przekroczony o 825 PLN.') &&
                         output.textContent.includes('825 PLN')
                     );
                     first.value = 'second-config';
                     second.value = 'first-config';
                     button.click();
                       const reversedRecommendation = await waitFor(() =>
                           !output.textContent.includes('Rekomendowany wariant:')
                       );
                      first.value = 'within-budget';
                      second.value = 'over-budget';
                      button.click();
                        const budgetRecommendation = await waitFor(() =>
                            !output.textContent.includes('Rekomendowany wariant budzetowy:')
                        );
                       const nullCostRecommendation = await waitFor(() =>
                           !output.textContent.includes('Rekomendowany wariant kosztowy:')
                       );
                       first.value = 'over-budget';
                       second.value = 'within-budget';
                       button.click();
                        const reversedBudgetRecommendation = await waitFor(() =>
                           output.textContent.includes('over-budget: 3975 PLN') &&
                           output.textContent.includes('within-budget: 3250 PLN') &&
                            !output.textContent.includes('Rekomendowany wariant budzetowy:')
                        );
                       first.value = 'both-within';
                       second.value = 'also-within';
                       button.click();
                         const bothWithinNoBudgetRecommendation = await waitFor(() =>
                             output.textContent.includes('both-within: 3250 PLN') &&
                             output.textContent.includes('also-within: 3000 PLN') &&
                             output.textContent.includes('Tanszy: second') &&
                             !output.textContent.includes('Rekomendowany wariant kosztowy:') &&
                             !output.textContent.includes('Rekomendowany wariant:') &&
                             !output.textContent.includes('Rekomendowany wariant budzetowy:')
                         );
                        first.value = 'also-within';
                        second.value = 'both-within';
                        button.click();
                        const reversedCostRecommendation = await waitFor(() =>
                             output.textContent.includes('also-within: 3000 PLN') &&
                             output.textContent.includes('both-within: 3250 PLN') &&
                             output.textContent.includes('Tanszy: first') &&
                             !output.textContent.includes('Rekomendowany wariant kosztowy:')
                         );
                       first.value = 'blocking';
                       second.value = 'within-budget';
                       button.click();
                        const blockingNoBudgetRecommendation = await waitFor(() =>
                            output.textContent.includes('blocking: 3975 PLN') &&
                            !output.textContent.includes('Rekomendowany wariant:') &&
                            !output.textContent.includes('Rekomendowany wariant budzetowy:')
                        );
                      first.value = 'first-config';
                     second.value = 'tie-config';
                     button.click();
                    const refreshedPair = await waitFor(() =>
                        output.textContent.includes('3975 PLN') &&
                        !output.textContent.includes('3250 PLN') &&
                        output.textContent.includes('MSI B650') &&
                        output.textContent.includes('ASUS Z790') &&
                        output.textContent.includes('899 PLN') &&
                        output.textContent.includes('1099 PLN') &&
                        output.textContent.includes('-200 PLN') &&
                        !output.textContent.includes('1599 PLN') &&
                        !output.textContent.includes('1249 PLN') &&
                        !output.textContent.includes('+350 PLN') &&
                        !output.textContent.includes('AMD Ryzen 7 7800X3D') &&
                        !output.textContent.includes('Intel Core i5-14600K')
                    );
                      const refreshedCompatibility = await waitFor(() =>
                         output.textContent.includes('Pierwszy wariant: blocking') &&
                        output.textContent.includes('Drugi wariant: ok') &&
                        output.textContent.includes('Pierwszy remisowy zestaw ma konflikt.') &&
                        output.textContent.includes('Drugi remisowy zestaw jest zgodny.') &&
                        !output.textContent.includes('Pierwszy zestaw jest zgodny.') &&
                         !output.textContent.includes('Drugi zestaw ma konflikt socketu.')
                     );
                      const refreshedBudget = await waitFor(() =>
                          output.textContent.includes('Budzet pierwszego wariantu: info; Dla pierwszego wariantu nie ustawiono budzetu.') &&
                          !output.textContent.includes('Budzet pierwszego wariantu: info; Dla pierwszego wariantu nie ustawiono budzetu. pozostaje') &&
                          !output.textContent.includes('Budzet pierwszego wariantu: info; Dla pierwszego wariantu nie ustawiono budzetu. przekrocz') &&
                          !output.textContent.includes('pozostaje 1025 PLN') &&
                          output.textContent.includes('Budzet drugiego wariantu: ok; Budzet wystarcza; pozostaje 0 PLN.')
                      );
                      const costRecommendationCleared = await waitFor(() =>
                          !output.textContent.includes('Rekomendowany wariant kosztowy:')
                      );
                     const tie = await waitFor(() =>
                         output.textContent.toLowerCase().includes('tie') ||
                         output.textContent.toLowerCase().includes('remis')
                     );
                     const noRecommendation = !output.textContent.includes('Rekomendowany wariant:');
                    first.value = 'tie-config';
                    second.value = 'tie-config';
                    button.click();
                    const unavailablePair = await waitFor(() =>
                        output.textContent.toLowerCase().includes('dwa rozne dostepne zapisy') &&
                        !output.textContent.includes('PLN')
                    );
                    return {
                        controls,
                          firstPair,
                          missingCostRecommendation,
                          firstRecommendation,
                          reversedRecommendation,
                          budgetRecommendation,
                          nullCostRecommendation,
                          reversedBudgetRecommendation,
                         bothWithinNoBudgetRecommendation,
                         reversedCostRecommendation,
                         costRecommendationCleared,
                         blockingNoBudgetRecommendation,
                         noRecommendation,
                        firstCompatibility,
                        firstBudget,
                        secondBudget,
                        refreshedPair,
                        refreshedCompatibility,
                        refreshedBudget,
                        tie,
                        unavailablePair,
                    };
                })()
            """)

        self.assertTrue(state['controls'], 'ekran udostepnia porownywarke')
        self.assertTrue(state['firstPair'], 'porownanie pokazuje oba koszty i tanszy wariant')
        self.assertTrue(
            state['missingCostRecommendation'],
            'porownanie nie pokazuje rekomendacji kosztowej bez pola w odpowiedzi',
        )
        self.assertTrue(
            state['firstRecommendation'],
            'porownanie pokazuje rekomendowany identyfikator pierwszego wariantu',
        )
        self.assertTrue(
            state['reversedRecommendation'],
            'porownanie pokazuje rekomendowany identyfikator po odwroceniu wariantow',
        )
        self.assertTrue(
            state['budgetRecommendation'],
            'porownanie pokazuje rekomendacje wariantu mieszczacego sie w budzecie',
        )
        self.assertTrue(
            state['nullCostRecommendation'],
            'porownanie ukrywa rekomendacje kosztowa dla wartosci null',
        )
        self.assertTrue(
            state['reversedBudgetRecommendation'],
            'porownanie pokazuje rekomendacje budzetowa po odwroceniu wariantow',
        )
        self.assertTrue(
            state['bothWithinNoBudgetRecommendation'],
            'porownanie pokazuje niezalezna rekomendacje kosztowa tanszego wariantu',
        )
        self.assertTrue(
            state['reversedCostRecommendation'],
            'porownanie zachowuje rekomendacje kosztowa po odwroceniu wariantow',
        )
        self.assertTrue(
            state['costRecommendationCleared'],
            'porownanie usuwa rekomendacje kosztowa po remisie',
        )
        self.assertTrue(
            state['blockingNoBudgetRecommendation'],
            'porownanie nie oglasza rekomendacji budzetowej przy konflikcie blokujacym',
        )
        self.assertTrue(
            state['noRecommendation'],
            'porownanie nie pokazuje rekomendacji przy jednakowych statusach',
        )
        self.assertTrue(state['firstCompatibility'], 'porownanie pokazuje osobne oceny wariantow')
        self.assertTrue(state['firstBudget'], 'porownanie pokazuje pozostala kwote pierwszego wariantu')
        self.assertTrue(state['secondBudget'], 'porownanie pokazuje przekroczenie drugiego wariantu')
        self.assertTrue(state['refreshedPair'], 'zmiana zapisu usuwa koszt poprzedniej pary')
        self.assertTrue(state['refreshedCompatibility'], 'zmiana zapisu zastepuje oceny wariantow')
        self.assertTrue(state['refreshedBudget'], 'porownanie bez budzetu pokazuje brak limitu wariantu')
        self.assertTrue(state['tie'], 'porownanie rownych kosztow pokazuje remis')
        self.assertTrue(state['unavailablePair'], 'nieudane porownanie czysci stary wynik i wyjasnia wymagane zapisy')

    def test_page_shows_named_differences_and_omits_shared_parts(self):
        with Browser(self.base_url) as browser:
            state = browser.evaluate("""
                (async () => {
                    const first = document.querySelector('#compare-first-id');
                    const second = document.querySelector('#compare-second-id');
                    const button = document.querySelector('#compare-configurations');
                    const output = document.querySelector('#comparison-result');
                    window.fetch = () => Promise.resolve({
                        ok: true,
                        json: () => Promise.resolve({
                            first_configuration_id: 'first-config',
                            second_configuration_id: 'second-config',
                            first_cost_pln: 3975,
                            second_cost_pln: 3250,
                            cheaper: 'second',
                            first_compatibility: {
                                level: 'ok',
                                message: 'Pierwszy zestaw jest zgodny.',
                            },
                            second_compatibility: {
                                level: 'blocking',
                                message: 'Drugi zestaw ma konflikt socketu.',
                            },
                            differences: {
                                cpuId: {
                                    category: 'CPU',
                                    first_id: 'ryzen-7-7800x3d',
                                    second_id: 'core-i5-14600k',
                                    first_price_pln: 1599,
                                    second_price_pln: 1249,
                                    price_difference_pln: 350,
                                },
                                motherboardId: {
                                    first_id: 'msi-b650',
                                    second_id: null,
                                    first_price_pln: 899,
                                    second_price_pln: 0,
                                    price_difference_pln: 899,
                                },
                            },
                        }),
                    });
                    const waitFor = async predicate => {
                        for (let attempt = 0; attempt < 200; attempt++) {
                            if (predicate()) return true;
                            await new Promise(resolve => setTimeout(resolve, 10));
                        }
                        return false;
                    };
                    if (!first || !second || !button || !output) {
                        return {controls: false, named: false, missing: false, shared: false};
                    }
                    ['first-config', 'second-config'].forEach(id =>
                        [first, second].forEach(select => select.add(new Option(id, id))));
                    first.value = 'first-config';
                    second.value = 'second-config';
                    button.click();
                    const named = await waitFor(() =>
                        output.textContent.includes('CPU') &&
                        output.textContent.includes('AMD Ryzen 7 7800X3D') &&
                        output.textContent.includes('Intel Core i5-14600K') &&
                        output.textContent.includes('1599 PLN') &&
                        output.textContent.includes('1249 PLN') &&
                        output.textContent.includes('350 PLN')
                    );
                    const missing = await waitFor(() =>
                        output.textContent.includes('plyta glowna') &&
                        output.textContent.includes('MSI B650') &&
                        output.textContent.toLowerCase().includes('brak') &&
                        output.textContent.includes('899 PLN') &&
                        output.textContent.includes('0 PLN')
                    );
                    const shared = !output.textContent.includes('Corsair Vengeance DDR5');
                    return {controls: true, named, missing, shared};
                })()
            """)

        self.assertTrue(state['controls'], 'ekran udostepnia porownywarke')
        self.assertTrue(state['named'], 'roznica pokazuje kategorie i nazwy obu wariantow')
        self.assertTrue(state['missing'], 'jednostronny wybor pokazuje nazwe i brak drugiego wyboru')
        self.assertTrue(state['shared'], 'wspolny wybor nie jest wyswietlany jako roznica')

    def test_page_keeps_latest_started_save_after_responses_arrive_out_of_order(self):
        with Browser(self.base_url) as browser:
            state = browser.evaluate("""
                (async () => {
                    const save = document.querySelector('#save-configuration');
                    const identifier = document.querySelector('#configuration-id');
                    const share = document.querySelector('#saved-configuration-share');
                    const pending = [];
                    window.fetch = (url, options) => {
                        if (url !== '/api/configurations' || options.method !== 'POST') {
                            return Promise.reject(new Error('unexpected request'));
                        }
                        return new Promise((resolve, reject) => pending.push({resolve, reject}));
                    };
                    if (!save || !identifier || !share) return {error: 'missing save controls'};
                    save.click();
                    save.click();
                    if (pending.length !== 2) return {error: 'requests were not started'};
                    pending[1].resolve({ok: true, json: () => Promise.resolve({
                        configuration_id: 'latest-config',
                        share_url: '/api/configurations/latest-config',
                    })});
                    await new Promise(resolve => setTimeout(resolve, 0));
                    pending[0].resolve({ok: true, json: () => Promise.resolve({
                        configuration_id: 'older-config',
                        share_url: '/api/configurations/older-config',
                    })});
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return {
                        id: identifier.textContent,
                        href: share.getAttribute('href'),
                        text: share.textContent,
                        hidden: share.hidden,
                    };
                })()
            """)

        self.assertIsInstance(state, dict)
        self.assertEqual(state['id'], 'latest-config')
        self.assertEqual(state['href'], '/api/configurations/latest-config')
        self.assertEqual(state['text'], '/api/configurations/latest-config')
        self.assertFalse(state['hidden'])

    def test_page_reports_rejected_save_and_keeps_share_link_hidden(self):
        with Browser(self.base_url) as browser:
            state = browser.evaluate("""
                (async () => {
                    const save = document.querySelector('#save-configuration');
                    const result = document.querySelector('#result');
                    const share = document.querySelector('#saved-configuration-share');
                    const cpu = document.querySelector('#cpu');
                    let resolveAnalysis;
                    window.fetch = (url, options) => {
                        if (url.startsWith('/api/analyze?')) {
                            return new Promise(resolve => { resolveAnalysis = resolve; });
                        }
                        if (url !== '/api/configurations' || options.method !== 'POST') {
                            return Promise.reject(new Error('unexpected request'));
                        }
                        return Promise.reject(new Error('network unavailable'));
                    };
                    if (!save || !result || !share || !cpu) return {error: 'missing save controls'};
                    cpu.value = 'core-i5-14600k';
                    cpu.dispatchEvent(new Event('change'));
                    save.click();
                    await new Promise(resolve => setTimeout(resolve, 20));
                    resolveAnalysis({
                        json: () => Promise.resolve({
                            level: 'ok',
                            message: 'Analiza zakonczona pomyslnie.',
                            budget: {level: 'ok', message: 'Budzet nie ustawiony.'},
                            total_cost_pln: 1599,
                        }),
                    });
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return {
                        message: result.textContent,
                        level: result.dataset.level,
                        href: share.getAttribute('href'),
                        text: share.textContent,
                        hidden: share.hidden,
                    };
                })()
            """)

        self.assertIsInstance(state, dict)
        self.assertEqual(state['message'], 'Nie udalo sie zapisac konfiguracji.')
        self.assertEqual(state['level'], 'blocking')
        self.assertIsNone(state['href'])
        self.assertEqual(state['text'], '')
        self.assertTrue(state['hidden'])

    def test_page_clears_save_error_after_later_success(self):
        with Browser(self.base_url) as browser:
            state = browser.evaluate("""
                (async () => {
                    const save = document.querySelector('#save-configuration');
                    const result = document.querySelector('#result');
                    let saveCount = 0;
                    window.fetch = (url, options) => {
                        if (url !== '/api/configurations' || options.method !== 'POST') {
                            return Promise.reject(new Error('unexpected request'));
                        }
                        saveCount++;
                        if (saveCount === 1) {
                            return Promise.resolve({
                                ok: false,
                                json: () => Promise.resolve({error: 'Nie udalo sie zapisac konfiguracji.'}),
                            });
                        }
                        return Promise.resolve({
                            ok: true,
                            json: () => Promise.resolve({
                                configuration_id: 'recovered-config',
                                share_url: '/api/configurations/recovered-config',
                            }),
                        });
                    };
                    if (!save || !result) return {error: 'missing save controls'};
                    save.click();
                    for (let attempt = 0; attempt < 200; attempt++) {
                        if (result.dataset.level === 'blocking') break;
                        await new Promise(resolve => setTimeout(resolve, 10));
                    }
                    save.click();
                    for (let attempt = 0; attempt < 200; attempt++) {
                        if (document.querySelector('#saved-configuration-share').textContent) break;
                        await new Promise(resolve => setTimeout(resolve, 10));
                    }
                    return {message: result.textContent, level: result.dataset.level};
                })()
            """)

        self.assertIsInstance(state, dict)
        self.assertNotEqual(state['message'], 'Nie udalo sie zapisac konfiguracji.')
        self.assertNotEqual(state['level'], 'blocking')

    def test_page_opens_saved_configuration_and_refreshes_visible_analysis(self):
        with Browser(self.base_url) as browser:
            state = browser.evaluate("""
                (async () => {
                    const open = document.querySelector('#open-configuration');
                    const identifier = document.querySelector('#configuration-id-input');
                    if (!open || !identifier) return false;
                    window.fetch = url => {
                        if (url === '/api/configurations/saved-config-123') {
                            return Promise.resolve({json: () => Promise.resolve({
                                parts: {
                                    cpuId: 'ryzen-7-7800x3d',
                                    motherboardId: 'msi-b650',
                                    ramId: 'corsair-vengeance-ddr5',
                                    psuId: 'corsair-rm750x',
                                    caseId: 'atx-mid-tower',
                                },
                                budgetPln: 5000,
                            })});
                        }
                        return Promise.resolve({json: () => Promise.resolve({
                            level: 'ok', message: 'Zestaw zgodny.',
                            total_cost_pln: 4500,
                            budget: {level: 'ok', message: 'Pozostalo 500 PLN.'},
                        })});
                    };
                    identifier.value = 'saved-config-123';
                    open.click();
                    await new Promise(resolve => setTimeout(resolve, 50));
                    return {
                        cpu: document.querySelector('#cpu').value,
                        motherboard: document.querySelector('#motherboard').value,
                        memory: document.querySelector('#memory').value,
                        powerSupply: document.querySelector('#power-supply').value,
                        caseId: document.querySelector('#case').value,
                        budget: document.querySelector('#budget').value,
                        cost: document.querySelector('#total-cost').textContent,
                        result: document.querySelector('#result').textContent,
                        budgetResult: document.querySelector('#budget-result').textContent,
                    };
                })()
            """)

        self.assertIsInstance(state, dict)
        self.assertEqual(state['cpu'], 'ryzen-7-7800x3d')
        self.assertEqual(state['motherboard'], 'msi-b650')
        self.assertEqual(state['memory'], 'corsair-vengeance-ddr5')
        self.assertEqual(state['powerSupply'], 'corsair-rm750x')
        self.assertEqual(state['caseId'], 'atx-mid-tower')
        self.assertEqual(state['budget'], '5000')
        self.assertEqual(state['cost'], '4500 PLN')
        self.assertEqual(state['result'], 'Zestaw zgodny.')
        self.assertEqual(state['budgetResult'], 'Pozostalo 500 PLN.')

    def test_page_opens_saved_configuration_after_app_restart(self):
        payload = {
            'cpuId': 'ryzen-7-7800x3d',
            'motherboardId': 'msi-b650',
            'ramId': 'corsair-vengeance-ddr5',
            'psuId': 'corsair-rm750x',
            'caseId': 'atx-mid-tower',
            'budgetPln': 5000,
        }

        with TemporaryDirectory() as directory:
            store = Path(directory) / 'configurations.json'
            store.write_text('{}', encoding='utf-8')
            with patch.object(server, 'CONFIGURATION_STORE', store):
                status, saved = self.post_json('/api/configurations', payload)
                self.assertEqual(status, 201)

                restarted_app = create_app(port=0)
                restarted_thread = Thread(target=restarted_app.serve_forever)
                restarted_thread.start()
                try:
                    restarted_url = f'http://127.0.0.1:{restarted_app.server_port}'
                    with Browser(restarted_url) as browser:
                        state = browser.evaluate(f"""
                            (async () => {{
                                const identifier = document.querySelector('#configuration-id-input');
                                const open = document.querySelector('#open-configuration');
                                identifier.value = '{saved['configuration_id']}';
                                open.click();
                                for (let attempt = 0; attempt < 200 && (document.querySelector('#cpu').value !== 'ryzen-7-7800x3d' || document.querySelector('#total-cost').textContent === '0 PLN'); attempt++) {{
                                    await new Promise(resolve => setTimeout(resolve, 10));
                                }}
                                return {{
                                    cpu: document.querySelector('#cpu').value,
                                    motherboard: document.querySelector('#motherboard').value,
                                    memory: document.querySelector('#memory').value,
                                    powerSupply: document.querySelector('#power-supply').value,
                                    caseId: document.querySelector('#case').value,
                                    budget: document.querySelector('#budget').value,
                                    cost: document.querySelector('#total-cost').textContent,
                                    result: document.querySelector('#result').textContent,
                                    budgetResult: document.querySelector('#budget-result').textContent,
                                }};
                            }})()
                        """)
                finally:
                    restarted_app.shutdown()
                    restarted_thread.join()
                    restarted_app.server_close()

        self.assertEqual(state['cpu'], payload['cpuId'])
        self.assertEqual(state['motherboard'], payload['motherboardId'])
        self.assertEqual(state['memory'], payload['ramId'])
        self.assertEqual(state['powerSupply'], payload['psuId'])
        self.assertEqual(state['caseId'], payload['caseId'])
        self.assertEqual(state['budget'], str(payload['budgetPln']))
        self.assertIn('PLN', state['cost'])
        self.assertTrue(state['result'])
        self.assertTrue(state['budgetResult'])

    def test_page_reports_missing_configuration_without_replacing_current_choices(self):
        with Browser(self.base_url) as browser:
            state = browser.evaluate("""
                (async () => {
                    const open = document.querySelector('#open-configuration');
                    document.querySelector('#cpu').value = 'ryzen-7-7800x3d';
                    document.querySelector('#budget').value = '5000';
                    window.fetch = () => Promise.resolve({
                        ok: false,
                        json: () => Promise.resolve({error: 'Nie znaleziono konfiguracji.'}),
                    });
                    document.querySelector('#configuration-id-input').value = 'does-not-exist';
                    open.click();
                    await new Promise(resolve => setTimeout(resolve, 50));
                    return {
                        cpu: document.querySelector('#cpu').value,
                        budget: document.querySelector('#budget').value,
                        error: document.querySelector('#result').textContent,
                    };
                })()
            """)

        self.assertEqual(state['cpu'], 'ryzen-7-7800x3d')
        self.assertEqual(state['budget'], '5000')
        self.assertIn('Nie znaleziono konfiguracji.', state['error'])

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

    def test_partial_analysis_keeps_blocking_results_with_missing_or_empty_choices(self):
        with self.subTest('empty power supply preserves RAM mismatch and case result'):
            status, analysis = self.get_json(
                '/api/analyze?motherboardId=msi-b650'
                '&ramId=kingston-fury-ddr4&psuId=&caseId=atx-mid-tower'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'blocking')
            self.assertIn('Pamiec RAM DDR4 jest niezgodna', analysis['message'])
            self.assertIn('zasilacz', analysis['message'].lower())
            self.assertIn('Plyta w formacie ATX pasuje do obudowy', analysis['message'])

        with self.subTest('missing RAM preserves case mismatch and missing RAM result'):
            status, analysis = self.get_json(
                '/api/analyze?motherboardId=asus-z790&ramId='
                '&caseId=mini-itx-compact'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'blocking')
            self.assertIn('Plyta w formacie ATX nie pasuje do obudowy', analysis['message'])
            self.assertIn('pamiec RAM', analysis['message'])

        with self.subTest('empty RAM preserves socket and case mismatches with PSU selected'):
            status, analysis = self.get_json(
                '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=asus-z790'
                '&ramId=&psuId=corsair-rm750x&caseId=mini-itx-compact'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'blocking')
            self.assertIn('socketu AM5', analysis['message'])
            self.assertIn('Plyta w formacie ATX nie pasuje do obudowy', analysis['message'])
            self.assertIn('pamiec RAM', analysis['message'])

        with self.subTest('empty RAM without other optional choices preserves socket result'):
            status, analysis = self.get_json(
                '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=asus-z790&ramId='
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'blocking')
            self.assertIn('socketu AM5', analysis['message'])
            self.assertIn('pamiec RAM', analysis['message'])

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

    def test_analysis_reports_cost_for_known_parts_and_excludes_unknown_parts(self):
        base_query = 'cpuId=ryzen-7-7800x3d&motherboardId=msi-b650'

        with self.subTest('known selected parts have their exact total cost'):
            status, analysis = self.get_json(
                f'/api/analyze?{base_query}&ramId=corsair-vengeance-ddr5'
                '&psuId=corsair-rm750x&caseId=atx-mid-tower'
            )
            self.assertEqual(status, 200)
            self.assertIn('total_cost_pln', analysis)
            self.assertEqual(analysis['total_cost_pln'], 3975)

        with self.subTest('adding and removing a part changes the total by its price'):
            status, without_case = self.get_json(f'/api/analyze?{base_query}')
            self.assertEqual(status, 200)
            self.assertIn('total_cost_pln', without_case)
            self.assertEqual(without_case['total_cost_pln'], 2498)

            status, with_case = self.get_json(
                f'/api/analyze?{base_query}&caseId=atx-mid-tower'
            )
            self.assertEqual(status, 200)
            self.assertIn('total_cost_pln', with_case)
            self.assertEqual(with_case['total_cost_pln'] - without_case['total_cost_pln'], 349)

        with self.subTest('unknown identifiers are not included as zero-priced parts'):
            status, analysis = self.get_json(
                '/api/analyze?cpuId=unknown-cpu&motherboardId=msi-b650'
            )
            self.assertEqual(status, 200)
            self.assertIn('total_cost_pln', analysis)
            self.assertEqual(analysis['total_cost_pln'], 899)

        with self.subTest('no selected parts have zero total cost'):
            status, analysis = self.get_json('/api/analyze')
            self.assertEqual(status, 200)
            self.assertIn('total_cost_pln', analysis)
            self.assertEqual(analysis['total_cost_pln'], 0)

    def test_analysis_compares_known_parts_cost_with_budget(self):
        base_query = 'cpuId=ryzen-7-7800x3d&motherboardId=msi-b650'

        with self.subTest('budget covers the selected parts'):
            status, analysis = self.get_json(f'/api/analyze?{base_query}&budgetPln=3000')

            self.assertEqual(status, 200)
            self.assertEqual(analysis['total_cost_pln'], 2498)
            self.assertIn('budget', analysis)
            self.assertEqual(analysis['budget']['level'], 'ok')
            self.assertEqual(analysis['budget']['remaining_pln'], 502)
            self.assertIn('502', analysis['budget']['message'])

        with self.subTest('budget is below the selected parts cost'):
            status, analysis = self.get_json(f'/api/analyze?{base_query}&budgetPln=2000')

            self.assertEqual(status, 200)
            self.assertEqual(analysis['total_cost_pln'], 2498)
            self.assertIn('budget', analysis)
            self.assertEqual(analysis['budget']['level'], 'blocking')
            self.assertEqual(analysis['budget']['overage_pln'], 498)
            self.assertIn('498', analysis['budget']['message'])

        with self.subTest('budget exactly matches the selected parts cost'):
            status, analysis = self.get_json(f'/api/analyze?{base_query}&budgetPln=2498')

            self.assertEqual(status, 200)
            self.assertEqual(analysis['total_cost_pln'], 2498)
            self.assertEqual(analysis['budget']['level'], 'ok')
            self.assertEqual(analysis['budget']['remaining_pln'], 0)

        with self.subTest('budget result does not replace incompatible hardware result'):
            incompatible_query = 'cpuId=ryzen-7-7800x3d&motherboardId=asus-z790'
            status, analysis = self.get_json(
                f'/api/analyze?{incompatible_query}&budgetPln=2698'
            )

            self.assertEqual(status, 200)
            self.assertEqual(analysis['total_cost_pln'], 2698)
            self.assertEqual(analysis['level'], 'blocking')
            self.assertEqual(analysis['budget']['level'], 'ok')
            self.assertEqual(analysis['budget']['remaining_pln'], 0)

        status, without_budget = self.get_json(f'/api/analyze?{base_query}')
        self.assertEqual(status, 200)

        invalid_budget_queries = [
            ('missing budget', ''),
            ('negative budget', '&budgetPln=-1'),
            ('decimal budget', '&budgetPln=2000.0'),
            ('formatted budget', '&budgetPln=2%20000'),
            ('unicode digit budget', '&budgetPln=%C2%B2'),
            ('budget exceeds integer conversion limit', '&budgetPln=' + '9' * 5000),
        ]
        for label, budget_query in invalid_budget_queries:
            with self.subTest(label):
                try:
                    status, analysis = self.get_json(f'/api/analyze?{base_query}{budget_query}')
                except Exception as error:
                    self.fail(f'budget request raised {type(error).__name__}: {error}')

                self.assertEqual(status, 200)
                self.assertEqual(analysis['level'], without_budget['level'])
                self.assertEqual(analysis['total_cost_pln'], without_budget['total_cost_pln'])
                self.assertEqual(analysis['budget']['level'], 'info')
                self.assertIn('nieujemna', analysis['budget']['message'])

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
            exact_supply = ({
                'id': 'exact-psu',
                'name': 'Graniczny zasilacz',
                'power_watts': 210,
                'price_pln': 1,
            },)
            with patch('src.server.POWER_SUPPLIES', exact_supply):
                status, exact = self.get_json(f'/api/analyze?{base_query}&psuId=exact-psu')
            self.assertEqual(status, 200)
            self.assertEqual(exact['level'], 'ok')

        with self.subTest('insufficient power supply'):
            weak_supply = ({
                'id': 'weak-psu',
                'name': 'Slaby zasilacz',
                'power_watts': 100,
                'price_pln': 1,
            },)
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
            self.assertEqual(analysis['level'], 'blocking')
            self.assertIn('zasilacz', analysis['message'].lower())
            self.assertIn('socketu AM5', analysis['message'])

        with self.subTest('unknown power supply preserves incomplete power status'):
            status, analysis = self.get_json(
                '/api/analyze?cpuId=ryzen-7-7800x3d&motherboardId=asus-z790'
                '&ramId=corsair-vengeance-ddr5&psuId=unknown-psu'
            )
            self.assertEqual(status, 200)
            self.assertEqual(analysis['level'], 'blocking')
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

    def test_page_shows_and_refreshes_current_build_cost(self):
        with Browser(self.base_url) as browser:
            costs = browser.evaluate("""
                (async () => {
                    const cost = () => document.querySelector('#total-cost')?.textContent || null;
                    const waitForCost = async expected => {
                        for (let attempt = 0; attempt < 200; attempt++) {
                            if (cost()?.includes(expected)) return;
                            await new Promise(resolve => setTimeout(resolve, 10));
                        }
                        throw new Error(`Timed out waiting for ${expected}; got ${cost()}`);
                    };
                    const initial = cost();
                    const cpu = document.querySelector('#cpu');
                    cpu.value = 'ryzen-7-7800x3d';
                    cpu.dispatchEvent(new Event('change'));
                    await waitForCost('1599 PLN');
                    const afterCpu = cost();
                    const motherboard = document.querySelector('#motherboard');
                    motherboard.value = 'msi-b650';
                    motherboard.dispatchEvent(new Event('change'));
                    await waitForCost('2498 PLN');
                    const afterMotherboard = cost();
                    const realFetch = window.fetch;
                    let requestCount = 0;
                    window.fetch = (...args) => {
                        requestCount++;
                        const response = realFetch(...args);
                        if (requestCount !== 1) return response;
                        return new Promise(resolve => {
                            setTimeout(() => response.then(resolve), 100);
                        });
                    };
                    cpu.value = 'core-i5-14600k';
                    cpu.dispatchEvent(new Event('change'));
                    motherboard.value = 'asus-z790';
                    motherboard.dispatchEvent(new Event('change'));
                    await waitForCost('2348 PLN');
                    await new Promise(resolve => setTimeout(resolve, 150));
                    return {
                        initial,
                        afterCpu,
                        afterMotherboard,
                        afterDelayedPreviousResponse: cost(),
                    };
                })()
            """)

        self.assertIsNotNone(costs['initial'])
        self.assertIn('0 PLN', costs['initial'])
        self.assertIn('1599 PLN', costs['afterCpu'])
        self.assertIn('2498 PLN', costs['afterMotherboard'])
        self.assertIn('2348 PLN', costs['afterDelayedPreviousResponse'])

    def test_page_sends_budget_and_shows_budget_analysis_separately(self):
        with Browser(self.base_url) as browser:
            page_state = browser.evaluate("""
                (async () => {
                    const requests = [];
                    window.fetch = url => {
                        requests.push(url);
                        return Promise.resolve({
                            json: () => Promise.resolve({
                                level: 'blocking',
                                message: 'Sprzet jest niezgodny.',
                                total_cost_pln: 2498,
                                budget: {
                                    level: 'blocking',
                                    message: 'Budzet jest przekroczony o 498 PLN.'
                                }
                            })
                        });
                    };
                    const budget = document.querySelector('#budget');
                    if (budget) {
                        budget.value = '2000';
                        budget.dispatchEvent(new Event('change'));
                    }
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return {
                        requests,
                        hardwareLevel: document.querySelector('#result')?.dataset.level,
                        hardwareMessage: document.querySelector('#result')?.textContent,
                        budgetMessage: document.querySelector('#budget-result')?.textContent,
                        budgetLevel: document.querySelector('#budget-result')?.dataset.level,
                    };
                })()
            """)

        self.assertEqual(len(page_state['requests']), 1)
        self.assertIn('budgetPln=2000', page_state['requests'][0])
        self.assertEqual(page_state['hardwareLevel'], 'blocking')
        self.assertEqual(page_state['hardwareMessage'], 'Sprzet jest niezgodny.')
        self.assertIn('Budzet jest przekroczony o 498 PLN.', page_state['budgetMessage'])
        self.assertEqual(page_state['budgetLevel'], 'blocking')

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
        weak_supply = {
            'id': 'test-weak-psu',
            'name': 'Testowy zasilacz 100 W',
            'power_watts': 100,
            'price_pln': 1,
        }
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

    def test_page_shows_case_status_before_and_after_case_change(self):
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
                    document.querySelector('#motherboard').value = 'msi-b650';
                    const caseSelect = document.querySelector('#case');
                    caseSelect.value = 'atx-mid-tower';
                    caseSelect.dispatchEvent(new Event('change'));
                    await waitForResult('ok', 'pasuje do obudowy');
                    const compatible = {
                        level: document.querySelector('#result').dataset.level,
                        text: document.querySelector('#result').textContent,
                    };
                    caseSelect.value = 'mini-itx-compact';
                    caseSelect.dispatchEvent(new Event('change'));
                    await waitForResult('blocking', 'nie pasuje do obudowy');
                    const motherboard = document.querySelector('#motherboard');
                    const nativeFetch = window.fetch;
                    const motherboardChangeRequests = [];
                    window.fetch = url => {
                        motherboardChangeRequests.push(url);
                        return nativeFetch(url);
                    };
                    caseSelect.value = 'atx-mid-tower';
                    caseSelect.dispatchEvent(new Event('change'));
                    await waitForResult('ok', 'pasuje do obudowy');
                    motherboard.value = 'asus-z790';
                    motherboard.dispatchEvent(new Event('change'));
                    await waitForResult('ok', 'pasuje do obudowy');
                    const afterMotherboardChange = {
                        level: document.querySelector('#result').dataset.level,
                        text: document.querySelector('#result').textContent,
                        requested: motherboardChangeRequests[motherboardChangeRequests.length - 1],
                    };
                    const responses = new Map();
                    const requested = [];
                    window.fetch = url => {
                        requested.push(url);
                        return new Promise(resolve => responses.set(url, resolve));
                    };
                    caseSelect.value = 'atx-mid-tower';
                    caseSelect.dispatchEvent(new Event('change'));
                    caseSelect.value = 'mini-itx-compact';
                    caseSelect.dispatchEvent(new Event('change'));
                    while (requested.length < 2) {
                        await new Promise(resolve => setTimeout(resolve, 0));
                    }
                    responses.get(requested[1])({json: () => Promise.resolve({
                        level: 'blocking',
                        message: 'Plyta ATX nie pasuje do obudowy Mini-ITX.'
                    })});
                    await new Promise(resolve => setTimeout(resolve, 0));
                    responses.get(requested[0])({json: () => Promise.resolve({
                        level: 'ok',
                        message: 'Plyta ATX pasuje do obudowy ATX.'
                    })});
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return {initial, compatible, incompatible: {
                        level: document.querySelector('#result').dataset.level,
                        text: document.querySelector('#result').textContent,
                    }, afterMotherboardChange, race: {
                        level: document.querySelector('#result').dataset.level,
                        text: document.querySelector('#result').textContent,
                        requested,
                    }};
                })()
            """)

        self.assertIn('plyte', statuses['initial']['text'].lower())
        self.assertIn('obudowe', statuses['initial']['text'].lower())
        self.assertEqual(statuses['compatible']['level'], 'ok')
        self.assertIn('ATX', statuses['compatible']['text'])
        self.assertEqual(statuses['incompatible']['level'], 'blocking')
        self.assertIn('ATX', statuses['incompatible']['text'])
        self.assertIn('Mini-ITX', statuses['incompatible']['text'])
        self.assertEqual(statuses['afterMotherboardChange']['level'], 'ok')
        self.assertIn('ATX', statuses['afterMotherboardChange']['text'])
        self.assertIn('motherboardId=asus-z790', statuses['afterMotherboardChange']['requested'])
        self.assertIn('caseId=atx-mid-tower', statuses['afterMotherboardChange']['requested'])
        self.assertEqual(len(statuses['race']['requested']), 2)
        self.assertIn('caseId=atx-mid-tower', statuses['race']['requested'][0])
        self.assertIn('caseId=mini-itx-compact', statuses['race']['requested'][1])
        self.assertEqual(statuses['race']['level'], 'blocking')
        self.assertEqual(
            statuses['race']['text'],
            'Plyta ATX nie pasuje do obudowy Mini-ITX.',
        )

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

        self.assertEqual(analysis['level'], 'blocking')
        self.assertEqual(
            analysis['message'],
            'Procesor wymaga socketu AM5, a plyta ma LGA1700.',
        )
        self.assertEqual(analysis['total_cost_pln'], 0)
