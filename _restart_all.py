#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MySQL 修复后，重启全部游戏组件"""
import urllib2, json, time

BASE = 'http://127.0.0.1:8080'

def post(p, n):
    req = urllib2.Request(BASE + p, data=json.dumps({'name': n}),
                          headers={'Content-Type': 'application/json'})
    return json.loads(urllib2.urlopen(req, timeout=30).read().decode('utf-8'))

def get(p):
    return json.loads(urllib2.urlopen(BASE + p, timeout=30).read().decode('utf-8'))

for n in ['gs', 'csa', 'ccs', 'dba', 'aaa', 'driver']:
    r = post('/api/stop', n)
    print('stop ', n, '=>', r.get('ok'))
    time.sleep(1)

time.sleep(3)

for n in ['driver', 'aaa', 'dba', 'ccs', 'csa', 'gs']:
    r = post('/api/start', n)
    print('start', n, '=>', r.get('ok'))
    time.sleep(2)

# 等 GS 就绪（最多 3 分钟）
print('waiting for GS ready (max 180s)...')
for i in range(36):
    time.sleep(5)
    try:
        log = get('/api/log?name=gs&lines=5')['log']
        if 'login now' in log or 'allows user' in log:
            print('GS READY at %ds' % ((i + 1) * 5))
            break
    except Exception:
        pass
else:
    print('GS not ready yet, check log')

st = get('/api/status')
for s in st['services']:
    print(s['name'].ljust(8), 'RUNNING' if s['running'] else 'STOPPED')
