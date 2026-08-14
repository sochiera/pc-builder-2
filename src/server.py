from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
import json
import os
from pathlib import Path
from threading import Lock
from uuid import uuid4
from urllib.parse import parse_qs, unquote, urlparse

from src.analyze_build import analyze_build
from src.analyze_build import combine_analyses
from src.analyze_build import analyze_memory
from src.analyze_build import analyze_case
from src.analyze_build import analyze_products
from src.analyze_build import analyze_power_supply
from src.analyze_build import total_cost
from src.analyze_build import analyze_budget
from src.analyze_build import RAM_ANALYSIS_REQUIRED_MESSAGE
from src.analyze_build import POWER_ANALYSIS_REQUIRED_MESSAGE
from src.analyze_build import INITIAL_ANALYSIS_REQUIRED_MESSAGE
from src.catalog import CASES, CPUS, MEMORY, MOTHERBOARDS, POWER_SUPPLIES


CONFIGURATION_CATALOGS = {
    'cpuId': CPUS,
    'motherboardId': MOTHERBOARDS,
    'ramId': MEMORY,
    'psuId': POWER_SUPPLIES,
    'caseId': CASES,
}

CONFIGURATION_STORE = Path(
    os.environ.get('PC_BUILDER_CONFIGURATIONS_FILE', '/tmp/pc-builder-configurations.json')
)
CONFIGURATION_STORE_LOCK = Lock()


def load_configurations():
    try:
        with CONFIGURATION_STORE.open(encoding='utf-8') as store:
            configurations = json.load(store)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return configurations if isinstance(configurations, dict) else {}


def save_configuration(configuration_id, configuration):
    with CONFIGURATION_STORE_LOCK:
        configurations = load_configurations()
        configurations[configuration_id] = configuration
        temporary_store = CONFIGURATION_STORE.with_suffix('.tmp')
        with temporary_store.open('w', encoding='utf-8') as store:
            json.dump(configurations, store)
        temporary_store.replace(CONFIGURATION_STORE)


def render_options(products):
    return ''.join(
        f"<option value=\"{escape(product['id'], quote=True)}\">"
        f"{escape(product['name'])}</option>"
        for product in products
    )


def page():
    cpu_options = render_options(CPUS)
    motherboard_options = render_options(MOTHERBOARDS)
    memory_options = render_options(MEMORY)
    power_supply_options = render_options(POWER_SUPPLIES)
    case_options = render_options(CASES)
    return '''<!doctype html>
<html lang=pl>
<head><meta charset=utf-8><meta name=viewport content=width=device-width,initial-scale=1><title>PC Builder</title>
<style>body{max-width:720px;margin:48px auto;padding:0 20px;font:16px system-ui;color:#17211b;background:#f4f6f1}main{background:white;padding:32px;border-radius:14px}label{display:block;margin:20px 0 6px}select,input{width:100%;padding:10px;box-sizing:border-box}#result,#budget-result{display:block;margin-top:24px;padding:14px;background:#edf4e9;border-radius:8px}</style></head>
<body><main><h1>Konfigurator PC</h1><p>Sprawdz pierwszy warunek kompatybilnosci zestawu.</p>
 <label for=cpu>Procesor</label><select id=cpu><option value>Wybierz procesor</option>''' + cpu_options + '''</select>
 <label for=motherboard>Plyta glowna</label><select id=motherboard><option value>Wybierz plyte</option>''' + motherboard_options + '''</select>
  <label for=memory>Pamiec RAM</label><select id=memory><option value>Wybierz pamiec RAM</option>''' + memory_options + '''</select>
  <label for=power-supply>Zasilacz</label><select id=power-supply><option value>Wybierz zasilacz</option>''' + power_supply_options + '''</select>
  <label for=case>Obudowa</label><select id=case><option value>Wybierz obudowe</option>''' + case_options + '''</select>
      <output id=result>''' + INITIAL_ANALYSIS_REQUIRED_MESSAGE + '''</output>
     <p>Koszt zestawu: <output id=total-cost>0 PLN</output></p>
     <label for=budget>Budzet (PLN)</label><input id=budget type=text inputmode=numeric placeholder="Nie ustawiono">
     <output id=budget-result>Podaj budzet jako nieujemna calkowita kwote w PLN.</output>
     <script>const cpu=document.querySelector('#cpu'),motherboard=document.querySelector('#motherboard'),memory=document.querySelector('#memory'),powerSupply=document.querySelector('#power-supply'),caseSelect=document.querySelector('#case'),budget=document.querySelector('#budget'),result=document.querySelector('#result'),budgetResult=document.querySelector('#budget-result'),totalCost=document.querySelector('#total-cost'),selects=[cpu,motherboard,memory,powerSupply,caseSelect];let refreshGeneration=0;async function refresh(){const generation=++refreshGeneration;const params=new URLSearchParams();if(cpu.value)params.set('cpuId',cpu.value);if(motherboard.value)params.set('motherboardId',motherboard.value);if(memory.value)params.set('ramId',memory.value);if(powerSupply.value)params.set('psuId',powerSupply.value);if(caseSelect.value)params.set('caseId',caseSelect.value);if(budget.value)params.set('budgetPln',budget.value);const response=await fetch('/api/analyze?'+params);const analysis=await response.json();if(generation!==refreshGeneration)return;result.textContent=analysis.message;result.dataset.level=analysis.level;budgetResult.textContent=analysis.budget.message;budgetResult.dataset.level=analysis.budget.level;totalCost.textContent=analysis.total_cost_pln+' PLN'}selects.forEach(select=>select.addEventListener('change',refresh));budget.addEventListener('change',refresh)</script>
    </main></body></html>'''


def analyze_selected_case(motherboard_id, case_id):
    return analyze_case(
        motherboard_id,
        case_id,
        MOTHERBOARDS,
        CASES,
    )


class AppHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        request = urlparse(self.path)
        if request.path != '/api/configurations':
            self.respond(404, 'text/plain; charset=utf-8', 'Not found')
            return

        try:
            length = int(self.headers.get('Content-Length', ''))
            payload = json.loads(self.rfile.read(length))
        except (TypeError, ValueError, json.JSONDecodeError):
            self.respond(400, 'application/json; charset=utf-8', json.dumps({
                'error': 'Podaj dane zestawu w formacie JSON.',
            }))
            return

        if not isinstance(payload, dict):
            self.respond(400, 'application/json; charset=utf-8', json.dumps({
                'error': 'Dane zestawu musza byc obiektem JSON.',
            }))
            return

        parts = {}
        for field, products in CONFIGURATION_CATALOGS.items():
            if field not in payload:
                continue
            product_id = payload[field]
            if not isinstance(product_id, str) or not product_id or not any(
                product['id'] == product_id for product in products
            ):
                self.respond(400, 'application/json; charset=utf-8', json.dumps({
                    'error': f'Niepoprawny identyfikator {field}.',
                }))
                return
            parts[field] = product_id

        budget = payload.get('budgetPln')
        if 'budgetPln' in payload and (
            isinstance(budget, bool) or not isinstance(budget, int) or budget < 0
        ):
            self.respond(400, 'application/json; charset=utf-8', json.dumps({
                'error': 'Budzet musi byc nieujemna liczba calkowita w PLN.',
            }))
            return

        configuration_id = uuid4().hex
        saved = {'configuration_id': configuration_id, 'parts': parts}
        if 'budgetPln' in payload:
            saved['budgetPln'] = budget
        self.server.configurations[configuration_id] = saved
        save_configuration(configuration_id, saved)
        self.respond(201, 'application/json; charset=utf-8', json.dumps(saved))

    def do_GET(self):
        request = urlparse(self.path)
        configuration_prefix = '/api/configurations/'
        if request.path.startswith(configuration_prefix):
            configuration_id = unquote(request.path[len(configuration_prefix):])
            saved = load_configurations().get(configuration_id)
            if saved is None:
                self.respond(404, 'application/json; charset=utf-8', json.dumps({
                    'error': 'Nie znaleziono konfiguracji.',
                }))
                return
            self.respond(200, 'application/json; charset=utf-8', json.dumps(saved))
            return
        if request.path == '/api/analyze':
            query = parse_qs(request.query, keep_blank_values=True)
            value = lambda name: query.get(name, [''])[0]
            has_all_part_keys = all(
                name in query for name in ('cpuId', 'motherboardId', 'ramId')
            )
            if (
                'psuId' in query
                or has_all_part_keys
            ):
                cpu_id = value('cpuId')
                motherboard_id = value('motherboardId')
                ram_id = value('ramId')
                psu_id = value('psuId')
                case_id = value('caseId')
                socket_result = analyze_products(
                    cpu_id,
                    motherboard_id,
                    CPUS,
                    MOTHERBOARDS,
                )
                memory_result = analyze_memory(
                    motherboard_id,
                    ram_id,
                    MOTHERBOARDS,
                    MEMORY,
                )
                analyses = [
                    socket_result,
                    memory_result,
                    analyze_power_supply(
                        cpu_id,
                        motherboard_id,
                        ram_id,
                        psu_id,
                        CPUS,
                        MOTHERBOARDS,
                        MEMORY,
                        POWER_SUPPLIES,
                    ),
                ]
                if 'caseId' in query:
                    analyses.append(analyze_selected_case(motherboard_id, case_id))
                result = combine_analyses(*analyses)
            elif 'caseId' in query:
                motherboard_id = value('motherboardId')
                analyses = [analyze_selected_case(motherboard_id, value('caseId'))]
                if 'ramId' in query:
                    analyses.append(
                        analyze_memory(
                            motherboard_id,
                            value('ramId'),
                            MOTHERBOARDS,
                            MEMORY,
                        )
                    )
                if 'cpuId' in query:
                    analyses.append(
                        analyze_products(
                            value('cpuId'),
                            motherboard_id,
                            CPUS,
                            MOTHERBOARDS,
                        )
                    )
                result = combine_analyses(*analyses)
            elif 'ramId' in query or ('motherboardId' in query and 'cpuId' not in query):
                result = analyze_memory(
                    value('motherboardId'),
                    value('ramId'),
                    MOTHERBOARDS,
                    MEMORY,
                )
            elif 'cpuId' in query or 'motherboardId' in query:
                result = analyze_products(
                    value('cpuId'),
                    value('motherboardId'),
                    CPUS,
                    MOTHERBOARDS,
                )
            else:
                result = analyze_build(
                    value('cpuSocket'),
                    value('motherboardSocket'),
                )
            result['total_cost_pln'] = total_cost(
                (value('cpuId'), value('motherboardId'), value('ramId'), value('psuId'), value('caseId')),
                (CPUS, MOTHERBOARDS, MEMORY, POWER_SUPPLIES, CASES),
            )
            result['budget'] = analyze_budget(value('budgetPln'), result['total_cost_pln'])
            self.respond(200, 'application/json; charset=utf-8', json.dumps(result))
            return
        if request.path == '/':
            self.respond(200, 'text/html; charset=utf-8', page())
            return
        self.respond(404, 'text/plain; charset=utf-8', 'Not found')

    def respond(self, status, content_type, body):
        encoded = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        pass


def create_app(host='127.0.0.1', port=3000):
    app = ThreadingHTTPServer((host, port), AppHandler)
    app.configurations = {}
    return app


if __name__ == '__main__':
    app = create_app(port=int(os.environ.get('PORT', '3000')))
    print(f'PC Builder: http://127.0.0.1:{app.server_port}')
    app.serve_forever()
