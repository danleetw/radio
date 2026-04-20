from flask import Flask, request, jsonify, send_from_directory, Response
import requests
import math
import struct
import threading
import time
import os
import re
import json as json_mod

app = Flask(__name__)

# Verified real stations as last-resort fallback
FALLBACK_STATIONS = [
    {"name": "BV3UN Taoyuan, Taiwan", "host": "114.34.207.164", "port": 8073, "lat": 25.006, "lon": 121.343},
    {"name": "HL5NTR Daegu, Korea", "host": "hl5ntr.ddns.net", "port": 8073, "lat": 35.889, "lon": 128.575},
    {"name": "HL5NTR-2 Daegu, Korea", "host": "hl5ntr-sdr.ddns.net", "port": 8073, "lat": 35.889, "lon": 128.575},
    {"name": "JP7FSO Fukushima, Japan", "host": "jp7fso-kiwisdr.sytes.net", "port": 8073, "lat": 37.670, "lon": 140.492},
    {"name": "JH1HZB Chiba, Japan", "host": "120.143.48.43", "port": 8073, "lat": 35.800, "lon": 140.160},
    {"name": "Hamamatsu Japan", "host": "21083.proxy.kiwisdr.com", "port": 8073, "lat": 34.683, "lon": 137.664},
]

_stations_cache = []
_stations_cache_time = 0
CACHE_TTL = 3600  # 1 hour

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'index.html')

@app.route('/proxy')
def proxy():
    url = request.args.get('url', '')
    if not url:
        return jsonify({'error': 'Missing url'}), 400
    try:
        r = requests.get(url, timeout=6, headers={'User-Agent': 'Mozilla/5.0 RadioRDF/1.0'})
        resp = Response(r.content, status=r.status_code)
        resp.headers['Content-Type'] = r.headers.get('Content-Type', 'application/octet-stream')
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp
    except Exception as e:
        return jsonify({'error': str(e)}), 502

def fetch_all_kiwi_stations():
    """Fetch KiwiSDR station list from receiverbook.de (cached)."""
    global _stations_cache, _stations_cache_time
    if _stations_cache and (time.time() - _stations_cache_time) < CACHE_TTL:
        return _stations_cache

    try:
        r = requests.get('https://www.receiverbook.de/map', timeout=12,
                         headers={'User-Agent': 'Mozilla/5.0 RadioRDF/1.0'})
        m = re.search(r'var receivers = (\[.*?\]);', r.text, re.DOTALL)
        if not m:
            raise ValueError('receivers array not found')
        data = json_mod.loads(m.group(1))
        parsed = []
        for s in data:
            try:
                lon = float(s['location']['coordinates'][0])
                lat = float(s['location']['coordinates'][1])
            except Exception:
                continue
            for rx in s.get('receivers', []):
                if 'kiwi' not in rx.get('type', '').lower():
                    continue
                url = rx.get('url', '')
                clean = url.replace('http://', '').replace('https://', '').rstrip('/')
                parts = clean.split(':')
                host = parts[0].split('/')[0]
                try:
                    port = int(parts[1].split('/')[0]) if len(parts) > 1 else 8073
                except Exception:
                    port = 8073
                if not host:
                    continue
                label = s.get('label', rx.get('label', host)).strip()
                # Remove encoding garbage
                label = label.encode('ascii', 'ignore').decode('ascii').strip() or host
                parsed.append({
                    'name': label[:60],
                    'host': host,
                    'port': port,
                    'lat': lat,
                    'lon': lon,
                })
        _stations_cache = parsed
        _stations_cache_time = time.time()
        app.logger.info('Loaded %d KiwiSDR stations from receiverbook.de', len(parsed))
        return parsed
    except Exception as e:
        app.logger.warning('receiverbook.de fetch failed: %s', e)
        return []

@app.route('/stations')
def stations():
    try:
        lat = float(request.args.get('lat', 25.0))
        lon = float(request.args.get('lon', 121.5))
        radius_km = float(request.args.get('radius_km', 500))
    except ValueError:
        return jsonify({'error': 'Invalid parameters'}), 400

    all_st = fetch_all_kiwi_stations()

    if not all_st:
        all_st = FALLBACK_STATIONS
        app.logger.info('Using hardcoded fallback stations')

    results = []
    for s in all_st:
        dist = haversine(lat, lon, s['lat'], s['lon'])
        if dist <= radius_km:
            results.append({**s, 'distance_km': round(dist, 1)})

    results.sort(key=lambda x: x['distance_km'])
    resp = jsonify(results[:20])
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp

@app.route('/signal')
def signal():
    host = request.args.get('host', '')
    try:
        port = int(request.args.get('port', 8073))
        freq_mhz = float(request.args.get('freq', 7.0))
    except ValueError:
        return jsonify({'error': 'Invalid parameters'}), 400

    if not host:
        return jsonify({'error': 'Missing host'}), 400

    result = measure_signal(host, port, freq_mhz)
    resp = jsonify(result)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp

def measure_signal(host, port, freq_mhz, ws_timeout=7):
    try:
        r = requests.get(f'http://{host}:{port}/status', timeout=4)
        if r.status_code != 200:
            return {'online': False, 'error': f'HTTP {r.status_code}'}
    except Exception as e:
        return {'online': False, 'error': str(e)}

    smeter_values = []
    ws_error = None
    got_monitor = False

    try:
        import websocket

        ts = int(time.time() * 1000)
        uri = f'ws://{host}:{port}/kiwi/{ts}/SND'
        freq_khz = int(freq_mhz * 1000)
        opened = threading.Event()
        done = threading.Event()

        def on_open(ws):
            opened.set()
            ws.send('SET auth t=kiwi p=')

        def on_message(ws, msg):
            nonlocal got_monitor
            if isinstance(msg, (bytes, bytearray)):
                try:
                    txt = msg.decode('utf-8', errors='ignore')
                    if 'MSG monitor' in txt:
                        got_monitor = True
                        done.set()
                        return
                    if 'badp=0' in txt:
                        ws.send(f'SET mod=am low_cut=-5000 high_cut=5000 freq={freq_khz}')
                        ws.send('SET ar_okay=0 squelch=0')
                    return
                except Exception:
                    pass
                # Binary audio frame — try common SMETER byte positions
                if len(msg) < 8:
                    return
                for offset, endian in [(2, '>'), (4, '<'), (5, '<'), (3, '>')]:
                    if len(msg) < offset + 2:
                        continue
                    try:
                        raw = struct.unpack(f'{endian}H', msg[offset:offset+2])[0]
                        dbm = raw * 0.1 - 127.0
                        if -150.0 <= dbm <= 0.0:
                            smeter_values.append(dbm)
                            if len(smeter_values) >= 10:
                                done.set()
                            break
                    except Exception:
                        pass

        def on_error(ws, error):
            nonlocal ws_error
            ws_error = str(error)

        def on_close(ws, code, msg):
            done.set()

        ws_app = websocket.WebSocketApp(
            uri,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        t = threading.Thread(target=ws_app.run_forever, daemon=True)
        t.start()

        if opened.wait(timeout=4):
            done.wait(timeout=ws_timeout)

        ws_app.close()
        t.join(timeout=2)

    except ImportError:
        ws_error = 'websocket-client 未安裝，請執行: pip install websocket-client'
    except Exception as e:
        ws_error = str(e)

    if smeter_values:
        avg = sum(smeter_values) / len(smeter_values)
        return {
            'online': True,
            'dbm': round(avg, 1),
            'samples': len(smeter_values),
            'min_dbm': round(min(smeter_values), 1),
            'max_dbm': round(max(smeter_values), 1),
        }

    if got_monitor:
        return {'online': True, 'busy': True, 'dbm': None,
                'error': '頻道已滿，請手動連線並輸入 S-meter 數值'}

    return {
        'online': True,
        'dbm': None,
        'error': ws_error or '無 SMETER 資料（此站台可能不支援該頻段）',
    }

if __name__ == '__main__':
    try:
        import websocket  # noqa
        print('[OK] websocket-client 已安裝')
    except ImportError:
        print('[!!] 請先執行: pip install websocket-client')

    print('RadioRDF Server 啟動中 → http://localhost:8080')
    print('預先載入站台清單...')
    st = fetch_all_kiwi_stations()
    print(f'[OK] 已載入 {len(st)} 個 KiwiSDR 站台')
    print('開啟瀏覽器訪問 http://localhost:8080')
    app.run(host='127.0.0.1', port=8080, debug=False, threaded=True)
