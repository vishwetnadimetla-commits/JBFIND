#!/usr/bin/env python3
"""Verify the Google service account can read AND write the target sheet."""
import json, time, urllib.request, urllib.parse, base64, subprocess, sys

SA = json.load(open('keys/google-service-account.json'))
KEY = SA['private_key']
EMAIL = SA['client_email']
SHEET = '1voOTzO8auKbgrP4J4dPACnwf9Tg2MsgUSCU9w2-mPFY'


def b64u(b):
    if isinstance(b, str):
        b = b.encode()
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def sign(msg):
    import tempfile, os
    with tempfile.NamedTemporaryFile('w', suffix='.pem', delete=False) as f:
        f.write(KEY)
        path = f.name
    try:
        p = subprocess.run(
            ['openssl', 'dgst', '-sha256', '-sign', path],
            input=msg.encode(), capture_output=True, check=True)
    finally:
        os.unlink(path)
    return b64u(p.stdout)


now = int(time.time())
hdr = b64u(json.dumps({'alg': 'RS256', 'typ': 'JWT'}, separators=(',', ':')))
claims = b64u(json.dumps({
    'iss': EMAIL, 'scope': 'https://www.googleapis.com/auth/spreadsheets',
    'aud': 'https://oauth2.googleapis.com/token',
    'exp': now + 3600, 'iat': now,
}, separators=(',', ':')))
jwt = f'{hdr}.{claims}.{sign(hdr + "." + claims)}'

body = urllib.parse.urlencode({
    'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
    'assertion': jwt,
}).encode()
try:
    res = json.load(urllib.request.urlopen(
        urllib.request.Request('https://oauth2.googleapis.com/token', data=body)))
except urllib.error.HTTPError as e:
    print('TOKEN EXCHANGE FAILED:', e.code, e.read().decode()[:600])
    sys.exit(1)
tok = res['access_token']
print('service account JWT token OK (expires_in=%s)' % res.get('expires_in'))


def api(url, payload=None, method=None):
    req = urllib.request.Request(
        url, headers={'Authorization': 'Bearer ' + tok,
                      'Content-Type': 'application/json'},
        data=json.dumps(payload).encode() if payload else None,
        method=method)
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        print('API FAILED', method or 'GET', url.split('/v4/')[-1])
        print('  status:', e.code)
        print('  body:', e.read().decode()[:700])
        sys.exit(1)


# 1. read tab metadata
meta = api(f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET}')
title = [s['properties']['title'] for s in meta['sheets']]
print('READ OK. tabs:', title)

# 2. append a probe row
probe = ['n8nsa1', 'SATEST', 'SA Probe', 'Remote', 'PASS', 'svc acct write test']
out = api(f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET}/values/JOBS:append'
          '?valueInputOption=RAW&insertDataOption=INSERT_ROWS',
          {'values': [probe], 'majorDimension': 'ROWS'}, 'POST')
print('WRITE OK. updatedRange:', out['updates']['updatedRange'])

# 3. read it back
vals = api(f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET}/values/JOBS!A1:Z2000'
           ).get('values', [])
hits = [(i, r) for i, r in enumerate(vals) if any('n8nsa1' in c for c in r)]
print('READBACK hits:', len(hits))
for i, r in hits:
    print('  row', i + 1, r[:6])
print('TOTAL rows now:', len(vals))
