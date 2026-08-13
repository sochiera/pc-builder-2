from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from urllib.parse import parse_qs, urlparse

from src.analyze_build import analyze_build
from src.analyze_build import combine_analyses
from src.analyze_build import analyze_memory
from src.analyze_build import analyze_products
from src.analyze_build import RAM_ANALYSIS_REQUIRED_MESSAGE
from src.catalog import CPUS, MEMORY, MOTHERBOARDS


def page():
    def options(products):
        return ''.join(
            f"<option value={product['id']}>{product['name']}</option>"
            for product in products
        )

    cpu_options = options(CPUS)
    motherboard_options = options(MOTHERBOARDS)
    memory_options = options(MEMORY)
    return '''<!doctype html>
<html lang=pl>
<head><meta charset=utf-8><meta name=viewport content=width=device-width,initial-scale=1><title>PC Builder</title>
<style>body{max-width:720px;margin:48px auto;padding:0 20px;font:16px system-ui;color:#17211b;background:#f4f6f1}main{background:white;padding:32px;border-radius:14px}label{display:block;margin:20px 0 6px}select{width:100%;padding:10px}#result{display:block;margin-top:24px;padding:14px;background:#edf4e9;border-radius:8px}</style></head>
<body><main><h1>Konfigurator PC</h1><p>Sprawdz pierwszy warunek kompatybilnosci zestawu.</p>
 <label for=cpu>Procesor</label><select id=cpu><option value>Wybierz procesor</option>''' + cpu_options + '''</select>
 <label for=motherboard>Plyta glowna</label><select id=motherboard><option value>Wybierz plyte</option>''' + motherboard_options + '''</select>
 <label for=memory>Pamiec RAM</label><select id=memory><option value>Wybierz pamiec RAM</option>''' + memory_options + '''</select>
  <output id=result>''' + RAM_ANALYSIS_REQUIRED_MESSAGE + '''</output>
  <script>const cpu=document.querySelector('#cpu'),motherboard=document.querySelector('#motherboard'),memory=document.querySelector('#memory'),result=document.querySelector('#result');let refreshGeneration=0;async function refresh(){const generation=++refreshGeneration;const params=new URLSearchParams({cpuId:cpu.value,motherboardId:motherboard.value,ramId:memory.value});const response=await fetch('/api/analyze?'+params);const analysis=await response.json();if(generation!==refreshGeneration)return;result.textContent=analysis.message;result.dataset.level=analysis.level}cpu.addEventListener('change',refresh);motherboard.addEventListener('change',refresh);memory.addEventListener('change',refresh)</script>
</main></body></html>'''


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        request = urlparse(self.path)
        if request.path == '/api/analyze':
            query = parse_qs(request.query, keep_blank_values=True)
            value = lambda name: query.get(name, [''])[0]
            if (
                'ramId' in query
                and 'cpuId' in query
                and 'motherboardId' in query
                and value('cpuId')
            ):
                socket_result = analyze_products(
                    value('cpuId'),
                    value('motherboardId'),
                    CPUS,
                    MOTHERBOARDS,
                )
                memory_result = analyze_memory(
                    value('motherboardId'),
                    value('ramId'),
                    MOTHERBOARDS,
                    MEMORY,
                )
                result = combine_analyses(socket_result, memory_result)
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
