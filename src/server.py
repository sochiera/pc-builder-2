from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from urllib.parse import parse_qs, urlparse

from src.analyze_build import analyze_build


def page():
    return '''<!doctype html>
<html lang=pl>
<head><meta charset=utf-8><meta name=viewport content=width=device-width,initial-scale=1><title>PC Builder</title>
<style>body{max-width:720px;margin:48px auto;padding:0 20px;font:16px system-ui;color:#17211b;background:#f4f6f1}main{background:white;padding:32px;border-radius:14px}label{display:block;margin:20px 0 6px}select{width:100%;padding:10px}#result{display:block;margin-top:24px;padding:14px;background:#edf4e9;border-radius:8px}</style></head>
<body><main><h1>Konfigurator PC</h1><p>Sprawdz pierwszy warunek kompatybilnosci zestawu.</p>
<label for=cpu>Procesor</label><select id=cpu><option value>Wybierz procesor</option><option value=AM5>AMD Ryzen 7 7800X3D - AM5</option><option value=LGA1700>Intel Core i5-14600K - LGA1700</option></select>
<label for=motherboard>Plyta glowna</label><select id=motherboard><option value>Wybierz plyte</option><option value=AM5>MSI B650 - AM5</option><option value=LGA1700>ASUS Z790 - LGA1700</option></select>
<output id=result>Wybierz procesor i plyte glowna, aby sprawdzic socket.</output>
<script>const cpu=document.querySelector('#cpu'),motherboard=document.querySelector('#motherboard'),result=document.querySelector('#result');async function refresh(){const response=await fetch('/api/analyze?cpuSocket='+encodeURIComponent(cpu.value)+'&motherboardSocket='+encodeURIComponent(motherboard.value));const analysis=await response.json();result.textContent=analysis.message;result.dataset.level=analysis.level}cpu.addEventListener('change',refresh);motherboard.addEventListener('change',refresh)</script>
</main></body></html>'''


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        request = urlparse(self.path)
        if request.path == '/api/analyze':
            query = parse_qs(request.query)
            result = analyze_build(
                query.get('cpuSocket', [''])[0],
                query.get('motherboardSocket', [''])[0],
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
