import json
from threading import Thread
import unittest
from urllib.request import urlopen

from src.server import create_app


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

    def test_running_app_detects_incompatible_cpu_and_motherboard_socket(self):
        with urlopen(f'{self.base_url}/') as response:
            self.assertIn('Konfigurator PC', response.read().decode())

        with urlopen(f'{self.base_url}/api/analyze?cpuSocket=AM5&motherboardSocket=LGA1700') as response:
            analysis = json.loads(response.read())

        self.assertEqual(analysis, {
            'level': 'blocking',
            'message': 'Procesor wymaga socketu AM5, a plyta ma LGA1700.',
        })
