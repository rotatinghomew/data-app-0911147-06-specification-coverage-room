from pathlib import Path
from urllib.request import Request, urlopen
import json
import tempfile
import threading

from .service import create_server


def main():
    root = Path(__file__).resolve().parents[1]
    request = json.loads((root / 'data/default-action.json').read_text())
    tolerance_requests = [
        {'operation': 'tolerance', 'mode': 'claim', 'attributes': ['strength'],
         'coverage': 0.95, 'confidence': 0.95, 'side': 'upper'},
        {'operation': 'tolerance', 'mode': 'spec', 'attributes': ['strength', 'thickness'],
         'coverage': 0.95, 'confidence': 0.95},
    ]
    with tempfile.TemporaryDirectory() as directory:
        server = create_server(state_dir=directory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            print('本地产品实时运行：' + base)
            for route in ('/health', '/api/summary'):
                with urlopen(base + route, timeout=5) as response:
                    print(route, json.dumps(json.load(response), ensure_ascii=False, indent=2))
            operation = Request(base + '/api/action', json.dumps(request).encode(), {'Content-Type': 'application/json'})
            with urlopen(operation, timeout=5) as response:
                print('当前操作', json.dumps(json.load(response), ensure_ascii=False, indent=2))
            for tolerance_request in tolerance_requests:
                operation = Request(base + '/api/action', json.dumps(tolerance_request).encode(),
                                    {'Content-Type': 'application/json'})
                with urlopen(operation, timeout=5) as response:
                    print('容忍分析', json.dumps(json.load(response), ensure_ascii=False, indent=2))
            print('运行结束；上述为当前版本的实际响应。')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
