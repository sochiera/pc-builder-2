from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
import json
import os
import re
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
from src.catalog import find_product


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


def configuration_share_url(configuration_id):
    return f'/api/configurations/{configuration_id}'


def with_configuration_share_url(configuration_id, configuration):
    if 'share_url' in configuration:
        return configuration
    shared = dict(configuration)
    shared['share_url'] = configuration_share_url(configuration_id)
    return shared


def configuration_cost(configuration):
    parts = configuration.get('parts', {})
    return total_cost(
        tuple(parts.get(field, '') for field in CONFIGURATION_CATALOGS),
        tuple(CONFIGURATION_CATALOGS.values()),
    )


def configuration_differences(first, second):
    first_parts = first.get('parts', {})
    second_parts = second.get('parts', {})
    return {
        field: {
            'first_id': first_parts.get(field),
            'second_id': second_parts.get(field),
            'first_price_pln': configuration_part_price(field, first_parts.get(field)),
            'second_price_pln': configuration_part_price(field, second_parts.get(field)),
            'price_difference_pln': (
                configuration_part_price(field, first_parts.get(field))
                - configuration_part_price(field, second_parts.get(field))
            ),
        }
        for field in CONFIGURATION_CATALOGS
        if first_parts.get(field) != second_parts.get(field)
    }


def configuration_part_price(field, product_id):
    if not product_id:
        return 0
    product = find_product(CONFIGURATION_CATALOGS[field], product_id)
    return product['price_pln'] if product is not None else 0


def analyze_configuration(parts):
    cpu_id = parts.get('cpuId', '')
    motherboard_id = parts.get('motherboardId', '')
    ram_id = parts.get('ramId', '')
    psu_id = parts.get('psuId', '')
    case_id = parts.get('caseId', '')

    if 'psuId' in parts or all(
        field in parts for field in ('cpuId', 'motherboardId', 'ramId')
    ):
        analyses = [
            analyze_products(cpu_id, motherboard_id, CPUS, MOTHERBOARDS),
            analyze_memory(motherboard_id, ram_id, MOTHERBOARDS, MEMORY),
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
        if 'caseId' in parts:
            analyses.append(analyze_selected_case(motherboard_id, case_id))
    elif 'caseId' in parts:
        analyses = [analyze_selected_case(motherboard_id, case_id)]
        if 'ramId' in parts:
            analyses.append(
                analyze_memory(motherboard_id, ram_id, MOTHERBOARDS, MEMORY)
            )
        if 'cpuId' in parts:
            analyses.append(
                analyze_products(cpu_id, motherboard_id, CPUS, MOTHERBOARDS)
            )
    elif 'ramId' in parts or ('motherboardId' in parts and 'cpuId' not in parts):
        analyses = [analyze_memory(motherboard_id, ram_id, MOTHERBOARDS, MEMORY)]
    elif 'cpuId' in parts or 'motherboardId' in parts:
        analyses = [analyze_products(cpu_id, motherboard_id, CPUS, MOTHERBOARDS)]
    else:
        analyses = [analyze_build('', '')]

    return combine_analyses(*analyses)


def configuration_compatibility(configuration):
    return analyze_configuration(configuration.get('parts', {}))


def compare_configuration_costs(first_id, first, second_id, second):
    first_cost = configuration_cost(first)
    second_cost = configuration_cost(second)
    return {
        'first_configuration_id': first_id,
        'second_configuration_id': second_id,
        'first_cost_pln': first_cost,
        'second_cost_pln': second_cost,
        'cheaper': (
            'first' if first_cost < second_cost else
            'second' if second_cost < first_cost else
            'tie'
        ),
        'differences': configuration_differences(first, second),
        'first_compatibility': configuration_compatibility(first),
        'second_compatibility': configuration_compatibility(second),
    }


def normalize_budget(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        try:
            return int(value)
        except ValueError:
            return value
    return value


def render_options(products):
    return ''.join(
        f"<option value=\"{escape(product['id'], quote=True)}\">"
        f"{escape(product['name'])}</option>"
        for product in products
    )


def _page_template():
    cpu_options = render_options(CPUS)
    motherboard_options = render_options(MOTHERBOARDS)
    memory_options = render_options(MEMORY)
    power_supply_options = render_options(POWER_SUPPLIES)
    case_options = render_options(CASES)
    save_handler = (
        "saveConfiguration.addEventListener('click',async()=>{"
        "const generation=++saveGeneration;"
        "const savedConfigurationShareLink=document.querySelector('#saved-configuration-share');"
        "const setSavedConfigurationShareLink=url=>{savedConfigurationShareLink.hidden=!url;"
        "savedConfigurationShareLink.textContent=url||'';if(url)savedConfigurationShareLink.href=url;"
        "else savedConfigurationShareLink.removeAttribute('href')};setSavedConfigurationShareLink('');"
        "const reportSaveError=message=>{refreshGeneration++;"
        "result.textContent=message||'Nie udalo sie zapisac konfiguracji.';"
        "result.dataset.level='blocking'};"
        "try{const response=await fetch('/api/configurations',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify(selectedConfiguration())});const saved=await response.json();"
        "if(generation!==saveGeneration)return;"
        "if(response.ok===false){reportSaveError(saved.error);return}"
        "configurationId.textContent=saved.configuration_id;result.textContent='';result.dataset.level='';"
        "setSavedConfigurationShareLink(saved.share_url)}catch(error){if(generation!==saveGeneration)return;"
        "reportSaveError();"
        "}});"
    )
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
     <p><button id=save-configuration type=button>Zapisz konfiguracje</button> <output id=configuration-id></output></p>
      <p><a id=saved-configuration-share hidden></a></p>
      <label for=configuration-id-input>Identyfikator zapisanej konfiguracji</label><input id=configuration-id-input type=text>
      <button id=open-configuration type=button>Otworz konfiguracje</button>
       <label for=compare-first-id>Pierwszy zapis do porownania</label><input id=compare-first-id type=text>
       <label for=compare-second-id>Drugi zapis do porownania</label><input id=compare-second-id type=text>
       <button id=compare-configurations type=button>Porownaj zapisane konfiguracje</button>
       <output id=comparison-result></output>
      <script>const cpu=document.querySelector('#cpu'),motherboard=document.querySelector('#motherboard'),memory=document.querySelector('#memory'),powerSupply=document.querySelector('#power-supply'),caseSelect=document.querySelector('#case'),budget=document.querySelector('#budget'),result=document.querySelector('#result'),budgetResult=document.querySelector('#budget-result'),totalCost=document.querySelector('#total-cost'),saveConfiguration=document.querySelector('#save-configuration'),configurationId=document.querySelector('#configuration-id'),configurationIdInput=document.querySelector('#configuration-id-input'),openConfiguration=document.querySelector('#open-configuration'),selects=[cpu,motherboard,memory,powerSupply,caseSelect];let refreshGeneration=0;let saveGeneration=0;function selectedConfiguration(){const configuration={};[['cpuId',cpu],['motherboardId',motherboard],['ramId',memory],['psuId',powerSupply],['caseId',caseSelect]].forEach(([name,select])=>{if(select.value)configuration[name]=select.value});if(budget.value)configuration.budgetPln=budget.value;return configuration}function applyConfiguration(configuration){const parts=configuration.parts||{};cpu.value=parts.cpuId||'';motherboard.value=parts.motherboardId||'';memory.value=parts.ramId||'';powerSupply.value=parts.psuId||'';caseSelect.value=parts.caseId||'';budget.value=configuration.budgetPln===undefined?'':configuration.budgetPln}async function refresh(){const generation=++refreshGeneration;const params=new URLSearchParams(selectedConfiguration());const response=await fetch('/api/analyze?'+params);const analysis=await response.json();if(generation!==refreshGeneration)return;result.textContent=analysis.message;result.dataset.level=analysis.level;budgetResult.textContent=analysis.budget.message;budgetResult.dataset.level=analysis.budget.level;totalCost.textContent=analysis.total_cost_pln+' PLN'}''' + save_handler + '''openConfiguration.addEventListener('click',async()=>{const response=await fetch('/api/configurations/'+encodeURIComponent(configurationIdInput.value));const saved=await response.json();applyConfiguration(saved);await refresh()});selects.forEach(select=>select.addEventListener('change',refresh));budget.addEventListener('change',refresh)</script>
      <script>document.addEventListener('click',async event=>{if(event.target!==openConfiguration)return;event.stopImmediatePropagation();const configurationIdValue=configurationIdInput.value.trim();if(!configurationIdValue){result.textContent='Podaj identyfikator konfiguracji.';result.dataset.level='blocking';return}const response=await fetch('/api/configurations/'+encodeURIComponent(configurationIdValue));const configuration=await response.json();if(response.ok===false){result.textContent=configuration.error||'Nie udalo sie otworzyc konfiguracji.';result.dataset.level='blocking';return}applyConfiguration(configuration);refresh()},{capture:true})</script>
        <script>const compareFirst=document.querySelector('#compare-first-id'),compareSecond=document.querySelector('#compare-second-id'),compareButton=document.querySelector('#compare-configurations'),comparisonResult=document.querySelector('#comparison-result');let compareGeneration=0;const comparisonFields={cpuId:['CPU',cpu],motherboardId:['plyta glowna',motherboard],ramId:['RAM',memory],psuId:['zasilacz',powerSupply],caseId:['obudowa',caseSelect]};const productName=(select,id)=>id===null?'Brak wyboru':select?.querySelector('option[value="'+id+'"]')?.textContent||id;const renderCompatibility=(label,compatibility)=>label+': '+compatibility.level+'; '+compatibility.message;const renderComparison=comparison=>{const cheaper=comparison.cheaper==='tie'?'Remis':('Tanszy: '+comparison.cheaper);const compatibility=renderCompatibility('Pierwszy wariant',comparison.first_compatibility)+' | '+renderCompatibility('Drugi wariant',comparison.second_compatibility);const differences=Object.entries(comparison.differences||{}).map(([field,parts])=>{const [category,select]=comparisonFields[field]||[field,null];const firstName=productName(select,parts.first_name===undefined?parts.first_id:parts.first_name);const secondName=productName(select,parts.second_name===undefined?parts.second_id:parts.second_name);const difference=parts.price_difference_pln>0?'+'+parts.price_difference_pln:parts.price_difference_pln;return category+': '+firstName+' ('+parts.first_price_pln+' PLN) vs '+secondName+' ('+parts.second_price_pln+' PLN); roznica: '+difference+' PLN'});return comparison.first_configuration_id+': '+comparison.first_cost_pln+' PLN; '+comparison.second_configuration_id+': '+comparison.second_cost_pln+' PLN; '+cheaper+' | '+compatibility+(differences.length?' | Roznice: '+differences.join('; '):'')};compareButton.addEventListener('click',async()=>{const generation=++compareGeneration;comparisonResult.textContent='';const firstId=compareFirst.value.trim(),secondId=compareSecond.value.trim();if(!firstId||!secondId||firstId===secondId){comparisonResult.textContent='Do porownania potrzebne sa dwa rozne dostepne zapisy.';return}try{const params=new URLSearchParams({firstId,secondId});const response=await fetch('/api/compare?'+params);const comparison=await response.json();if(generation!==compareGeneration)return;if(response.ok===false){comparisonResult.textContent=comparison.error||'Nie udalo sie porownac zapisow.';return}comparisonResult.textContent=renderComparison(comparison)}catch(error){if(generation===compareGeneration)comparisonResult.textContent='Nie udalo sie porownac zapisow.'}});</script>
      </main></body></html>'''


def page(configuration=None, error=None):
    html = _page_template()
    html = re.sub(
        r"<script>document\.addEventListener\('click'.*?</script>",
        '',
        html,
        flags=re.DOTALL,
    )
    html = re.sub(
        r"openConfiguration\.addEventListener\('click',async\(\)=>\{.*?\}\);selects\.forEach",
        "openConfiguration.addEventListener('click',async()=>{const configurationIdValue=configurationIdInput.value.trim();if(!configurationIdValue){result.textContent='Podaj identyfikator konfiguracji.';result.dataset.level='blocking';return}const response=await fetch('/api/configurations/'+encodeURIComponent(configurationIdValue));const saved=await response.json();if(response.ok===false){result.textContent=saved.error||'Nie udalo sie otworzyc konfiguracji.';result.dataset.level='blocking';return}applyConfiguration(saved);await refresh()});selects.forEach",
        html,
        flags=re.DOTALL,
    )
    if configuration is not None:
        html += '<script>applyConfiguration(' + json.dumps(configuration) + ');refresh();</script>'
    elif error is not None:
        html += (
            '<script>result.textContent=' + json.dumps(error) +
            ";result.dataset.level='blocking';</script>"
        )
    return html


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

        budget = normalize_budget(payload.get('budgetPln'))
        if 'budgetPln' in payload and (
            isinstance(budget, bool) or not isinstance(budget, int) or budget < 0
        ):
            self.respond(400, 'application/json; charset=utf-8', json.dumps({
                'error': 'Budzet musi byc nieujemna liczba calkowita w PLN.',
            }))
            return

        configuration_id = uuid4().hex
        saved = {
            'configuration_id': configuration_id,
            'parts': parts,
            'share_url': configuration_share_url(configuration_id),
        }
        if 'budgetPln' in payload:
            saved['budgetPln'] = budget
        self.server.configurations[configuration_id] = saved
        save_configuration(configuration_id, saved)
        self.respond(201, 'application/json; charset=utf-8', json.dumps(saved))

    def do_GET(self):
        request = urlparse(self.path)
        if request.path == '/api/compare':
            query = parse_qs(request.query, keep_blank_values=True)
            first_id = query.get('firstId', [''])[0]
            second_id = query.get('secondId', [''])[0]
            configurations = load_configurations()
            if not first_id or not second_id or first_id == second_id:
                self.respond(400, 'application/json; charset=utf-8', json.dumps({
                    'error': 'Podaj dwa rozne identyfikatory konfiguracji.',
                }))
                return

            first = configurations.get(first_id)
            second = configurations.get(second_id)
            if first is None or second is None:
                self.respond(400, 'application/json; charset=utf-8', json.dumps({
                    'error': 'Nie znaleziono obu konfiguracji do porownania.',
                }))
                return

            comparison = compare_configuration_costs(
                first_id, first, second_id, second
            )
            self.respond(200, 'application/json; charset=utf-8', json.dumps(comparison))
            return
        configuration_prefix = '/api/configurations/'
        if request.path.startswith(configuration_prefix):
            configuration_id = unquote(request.path[len(configuration_prefix):])
            saved = load_configurations().get(configuration_id)
            wants_html = 'text/html' in self.headers.get('Accept', '')
            if saved is None:
                if wants_html:
                    self.respond(
                        200,
                        'text/html; charset=utf-8',
                        page(error='Nie znaleziono konfiguracji.'),
                    )
                    return
                self.respond(404, 'application/json; charset=utf-8', json.dumps({
                    'error': 'Nie znaleziono konfiguracji.',
                }))
                return
            saved = with_configuration_share_url(configuration_id, saved)
            if wants_html:
                self.respond(200, 'text/html; charset=utf-8', page(configuration=saved))
                return
            self.respond(200, 'application/json; charset=utf-8', json.dumps(saved))
            return
        if request.path == '/api/analyze':
            query = parse_qs(request.query, keep_blank_values=True)
            value = lambda name: query.get(name, [''])[0]
            parts = {
                field: value(field)
                for field in CONFIGURATION_CATALOGS
                if field in query
            }
            if parts:
                result = analyze_configuration(parts)
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
