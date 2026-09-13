"""Run the real Windows briefing launcher with a local fake service and fake Python provider."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_bat_backup_fallback import SOLO_WINDOWS

SOURCE = Path(__file__).resolve().parents[1] / 'tools/ops/windows/run_briefing_v2.bat'


@SOLO_WINDOWS
@pytest.mark.parametrize('http_status,body,direct_exit,expected_exit,uses_direct', [
    (503, {'error': 'synthetic offline'}, 1, 1, True),
    (200, {'error': 'synthetic generation failure'}, 1, 1, True),
    (200, {'period': 'synthetic', 'briefing_md': '   '}, 1, 1, True),
    (503, {'error': 'synthetic offline'}, 0, 0, True),
    (200, {'period': 'synthetic', 'briefing_md': 'Synthetic briefing'}, 1, 0, False),
])
def test_batch_reports_the_actual_result(tmp_path, http_status, body, direct_exit, expected_exit, uses_direct):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.path)
            encoded = json.dumps(body).encode()
            self.send_response(http_status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    service = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    assert service.server_port >= 8766
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    windows = tmp_path / 'tools/ops/windows'
    windows.mkdir(parents=True)
    raw = SOURCE.read_bytes()
    marker = b'127.0.0.1:8765'
    assert raw.count(marker) == 1, 'Only the external service address is substituted'
    bat = windows / SOURCE.name
    bat.write_bytes(raw.replace(marker, f'127.0.0.1:{service.server_port}'.encode()))
    fake = tmp_path / 'bellomberg/cli'
    fake.mkdir(parents=True)
    (fake.parent / '__init__.py').write_text('', encoding='utf-8')
    (fake / '__init__.py').write_text('', encoding='utf-8')
    (fake / 'briefing_engine.py').write_text(
        'def generate_briefing():\n'
        '    print("SYNTHETIC_DIRECT_CALLED")\n'
        '    return {"error": "synthetic failure"}\n'
        'def main_direct():\n'
        '    print("SYNTHETIC_DIRECT_CALLED")\n'
        f'    return {direct_exit}\n', encoding='utf-8')
    env = dict(os.environ)
    env['BELLOMBERG_DATA_DIR'] = str(tmp_path / 'data')
    env['BELLOMBERG_PROJECT_ROOT'] = str(tmp_path)
    env['PYTHONPATH'] = str(tmp_path)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['NO_PROXY'] = '127.0.0.1,localhost'
    key = next(k for k in env if k.upper() == 'PATH')
    env[key] = str(Path(sys.executable).parent) + os.pathsep + env[key]
    try:
        result = subprocess.run(['cmd.exe', '/d', '/c', str(bat)], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=25)
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)
    log = (tmp_path / 'data/briefing.log').read_text(encoding='utf-8', errors='replace')
    assert requests == ['/news/briefing/refresh']
    assert ('SYNTHETIC_DIRECT_CALLED' in log) is uses_direct
    assert result.returncode == expected_exit, (log, result.stdout, result.stderr)
    assert ('*** ERROR: python fallback failed ***' in log) is bool(expected_exit)
