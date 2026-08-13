from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
import json
import os
from urllib.parse import parse_qs, urlparse

from src.analyze_build import analyze_build
from src.analyze_build import combine_analyses
from src.analyze_build import analyze_memory
from src.analyze_build import analyze_products
from src.analyze_build import analyze_power_supply
from src.analyze_build import RAM_ANALYSIS_REQUIRED_MESSAGE
from src.analyze_build import POWER_ANALYSIS_REQUIRED_MESSAGE
from src.catalog import CPUS, MEMORY, MOTHERBOARDS, POWER_SUPPLIES


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
    return '''<!doctype html>
<html lang=pl>
<head><meta charset=utf-8><meta name=viewport content=width=device-width,initial-scale=1><title>PC Builder</title>
<style>body{max-width:720px;margin:48px auto;padding:0 20px;font:16px system-ui;color:#17211b;background:#f4f6f1}main{background:white;padding:32px;border-radius:14px}label{display:block;margin:20px 0 6px}select{width:100%;padding:10px}#result{display:block;margin-top:24px;padding:14px;background:#edf4e9;border-radius:8px}</style></head>
<body><main><h1>Konfigurator PC</h1><p>Sprawdz pierwszy warunek kompatybilnosci zestawu.</p>
 <label for=cpu>Procesor</label><select id=cpu><option value>Wybierz procesor</option>''' + cpu_options + '''</select>
 <label for=motherboard>Plyta glowna</label><select id=motherboard><option value>Wybierz plyte</option>''' + motherboard_options + '''</select>
 <label for=memory>Pamiec RAM</label><select id=memory><option value>Wybierz pamiec RAM</option>''' + memory_options + '''</select>
  <label for=power-supply>Zasilacz</label><select id=power-supply><option value>Wybierz zasilacz</option>''' + power_supply_options + '''</select>
    <output id=result>''' + POWER_ANALYSIS_REQUIRED_MESSAGE + '''</output>
    <script>const cpu=document.querySelector('#cpu'),motherboard=document.querySelector('#motherboard'),memory=document.querySelector('#memory'),powerSupply=document.querySelector('#power-supply'),result=document.querySelector('#result');let refreshGeneration=0;async function refresh(){const generation=++refreshGeneration;const params=new URLSearchParams();if(cpu.value)params.set('cpuId',cpu.value);if(motherboard.value)params.set('motherboardId',motherboard.value);if(memory.value)params.set('ramId',memory.value);if(powerSupply.value)params.set('psuId',powerSupply.value);const response=await fetch('/api/analyze?'+params);const analysis=await response.json();if(generation!==refreshGeneration)return;result.textContent=analysis.message;result.dataset.level=analysis.level}cpu.addEventListener('change',refresh);motherboard.addEventListener('change',refresh);memory.addEventListener('change',refresh);powerSupply.addEventListener('change',refresh)</script>
</main></body></html>'''


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        request = urlparse(self.path)
        if request.path == '/api/analyze':
            query = parse_qs(request.query, keep_blank_values=True)
            value = lambda name: query.get(name, [''])[0]
            has_all_part_keys = all(
                name in query for name in ('cpuId', 'motherboardId', 'ramId')
            )
            has_all_part_values = all(
                value(name) for name in ('cpuId', 'motherboardId', 'ramId')
            )
            if (
                'psuId' in query
                or (has_all_part_keys and has_all_part_values)
            ):
                cpu_id = value('cpuId')
                motherboard_id = value('motherboardId')
                ram_id = value('ramId')
                psu_id = value('psuId')
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
    return ThreadingHTTPServer((host, port), AppHandler)


if __name__ == '__main__':
    app = create_app(port=int(os.environ.get('PORT', '3000')))
    print(f'PC Builder: http://127.0.0.1:{app.server_port}')
    app.serve_forever()
