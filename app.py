#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wd-panel — 问道 1.63 服务端控制面板
单文件 HTTP 控制面板，兼容 Python 2.6 (CentOS 6) 与 Python 3。

功能：
  - 默认启动 HTTP 服务（环境变量 PANEL_PORT，默认 8080）
  - 网页上对各服务（mysqld/driver/aaa/dba/ccs/csa/gs 等）进行：
      启动 / 停止 / 状态监控 / 日志查看
  - 服务定义来自 services.json（可用环境变量 SERVICES_CONF 指定）
  - 日志目录：环境变量 LOG_DIR，默认 ./logs

设计说明：
  - 启动服务 = 在配置的工作目录里用 /bin/bash -c 执行 start 命令，
    进程组（setsid）脱离面板独立运行，stdout/stderr 追加写入日志文件
  - 停止服务 = 向进程组发 SIGTERM，5 秒后仍存活则 SIGKILL；
    再用 pgrep -f <match> 兜底清理
  - 状态 = 先查面板记录的进程组，失败后用 pgrep -f <match> 探测
  - 面板重启后状态不丢（pid 记录在 logs/.panel_state.json）
"""
import os
import sys
import json
import time
import signal
import subprocess

# ---------- 平台 ----------
IS_POSIX = (os.name == 'posix')
if IS_POSIX:
    SPAWN_KW = {'preexec_fn': os.setsid}   # 独立进程组，便于整组管理
    SHELL_BIN = '/bin/bash'
else:
    # Windows（仅本机功能测试用）：无进程组概念；优先找 Git Bash
    SPAWN_KW = {'creationflags': getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)}
    SHELL_BIN = 'bash'
    for _cand in (r'C:\Program Files\Git\bin\bash.exe',
                  r'C:\Program Files (x86)\Git\bin\bash.exe',
                  r'C:\Program Files\Git\usr\bin\bash.exe'):
        if os.path.isfile(_cand):
            SHELL_BIN = _cand
            break

# ---------- Python 2 / 3 兼容 ----------
PY2 = sys.version_info[0] == 2
if PY2:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    from SocketServer import ThreadingMixIn
else:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from socketserver import ThreadingMixIn

if PY2:
    str_t = unicode  # noqa: F821

    def _to_unicode(o):
        """py2: 把响应对象里的 str(utf-8 bytes) 递归转为 unicode，避免 json.dumps 隐式 ascii 解码崩溃"""
        if isinstance(o, str):
            return o.decode('utf-8', 'replace')
        if isinstance(o, unicode):  # noqa: F821
            return o
        if isinstance(o, list):
            return [_to_unicode(x) for x in o]
        if isinstance(o, tuple):
            return tuple(_to_unicode(x) for x in o)
        if isinstance(o, dict):
            return dict((_to_unicode(k), _to_unicode(v)) for k, v in o.iteritems())
        return o
else:
    str_t = str

    def _to_unicode(o):
        return o

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PANEL_PORT = int(os.environ.get('PANEL_PORT', '8080'))
CONF_PATH = os.environ.get('SERVICES_CONF') or os.path.join(BASE_DIR, 'services.json')
LOG_DIR = os.environ.get('LOG_DIR') or os.path.join(BASE_DIR, 'logs')
STATE_PATH = os.path.join(LOG_DIR, '.panel_state.json')
TERM_TIMEOUT = 5

if not os.path.isdir(LOG_DIR):
    os.makedirs(LOG_DIR)


def log(msg):
    sys.stderr.write('[panel %s] %s\n' % (time.strftime('%F %T'), msg))
    sys.stderr.flush()


# ---------- 配置 ----------
class Config(object):
    def __init__(self, path):
        with open(path, 'rb') as f:
            data = json.loads(f.read().decode('utf-8'))
        self.title = data.get('title', '服务控制面板')
        self.ports = data.get('ports', [])
        self.services = {}
        self.order = []
        for svc in data.get('services', []):
            name = svc['name']
            svc = dict(svc)
            svc.setdefault('title', name)
            svc.setdefault('deps', [])
            if 'dir' in svc and not os.path.isabs(svc['dir']):
                svc['dir'] = os.path.join(BASE_DIR, svc['dir'])
            svc.setdefault('dir', BASE_DIR)
            if not os.path.isabs(svc.get('log', '')):
                svc['log'] = os.path.join(LOG_DIR, svc.get('log', name + '.log'))
            self.services[name] = svc
            self.order.append(name)

    def get(self, name):
        return self.services.get(name)


CONF = Config(CONF_PATH)


# ---------- 状态记录 ----------
def load_state():
    try:
        with open(STATE_PATH, 'rb') as f:
            return json.loads(f.read().decode('utf-8'))
    except Exception:
        return {}


def save_state(state):
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(json.dumps(state).encode('utf-8'))
    os.rename(tmp, STATE_PATH)


STATE = load_state()  # {name: pgid}


# ---------- 进程管理 ----------
def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def group_alive(pgid):
    if IS_POSIX:
        try:
            os.killpg(int(pgid), 0)
            return True
        except Exception:
            return False
    return pid_alive(pgid)


def kill_group(pgid, sig):
    if IS_POSIX:
        os.killpg(int(pgid), sig)
    else:
        os.kill(int(pgid), sig)


def pgrep_first(match):
    """pgrep -f 全匹配，返回第一个 pid 或 None（仅 POSIX）。"""
    if not IS_POSIX:
        return None
    try:
        p = subprocess.Popen(['pgrep', '-f', match],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = p.communicate()[0]
        for line in out.decode('ascii', 'replace').split():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                if pid != os.getpid():
                    return pid
    except Exception as e:
        log('pgrep error: %s' % e)
    return None


def service_running(name):
    """返回 (running, pid, via)"""
    svc = CONF.get(name)
    if not svc:
        return False, None, None
    pgid = STATE.get(name)
    if pgid and group_alive(pgid):
        return True, int(pgid), 'group'
    if pgid:
        # 记录已失效，清理
        STATE.pop(name, None)
        save_state(STATE)
    pid = pgrep_first(svc.get('match', name))
    if pid:
        return True, pid, 'pgrep'
    return False, None, None


def start_service(name, force=False):
    svc = CONF.get(name)
    if svc is None:
        return False, u'未知服务: %s' % name
    running, _, _ = service_running(name)
    if running:
        return False, u'%s 已在运行' % svc['title']
    if not force:
        for dep in svc.get('deps', []):
            ok, _, _ = service_running(dep)
            if not ok:
                return False, u'依赖服务 [%s] 未运行，请先启动' % dep
    workdir = svc.get('dir', BASE_DIR)
    if not os.path.isdir(workdir):
        return False, u'工作目录不存在: %s' % workdir
    logf = open(svc['log'], 'ab')
    try:
        logf.write((u'\n===== [panel] %s 启动 %s =====\n'
                    % (time.strftime('%F %T'), svc['title'])).encode('utf-8'))
        logf.flush()
        cmd = [SHELL_BIN, '-c', svc['start']]
        proc = subprocess.Popen(
            cmd,
            cwd=workdir, stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE, **SPAWN_KW)
    except Exception as e:
        logf.close()
        return False, u'启动失败: %s' % e
    finally:
        if not logf.closed:
            logf.close()
    STATE[name] = proc.pid
    save_state(STATE)
    log('started %s (pgid=%s)' % (name, proc.pid))
    return True, u'%s 已启动 (pid %d)' % (svc['title'], proc.pid)


def stop_service(name, force=False):
    svc = CONF.get(name)
    if svc is None:
        return False, u'未知服务: %s' % name
    running, pid, via = service_running(name)
    if not running:
        return False, u'%s 未在运行' % svc['title']
    try:
        if via == 'group':
            kill_group(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    # 等待退出
    deadline = time.time() + TERM_TIMEOUT
    while time.time() < deadline:
        r, p, v = service_running_quiet(name)
        if not r:
            break
        time.sleep(0.3)
    r, p, v = service_running_quiet(name)
    if r:
        try:
            if v == 'group':
                kill_group(p, signal.SIGKILL)
            else:
                os.kill(p, signal.SIGKILL)
        except Exception:
            pass
        try:
            subprocess.Popen(['pkill', '-9', '-f', svc.get('match', name)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            pass
        time.sleep(0.5)
    STATE.pop(name, None)
    save_state(STATE)
    log('stopped %s' % name)
    return True, u'%s 已停止' % svc['title']


def service_running_quiet(name):
    try:
        return service_running(name)
    except Exception:
        return False, None, None


def status_all():
    result = []
    for name in CONF.order:
        svc = CONF.services[name]
        running, pid, via = service_running_quiet(name)
        deps_ok = all(service_running_quiet(d)[0] for d in svc.get('deps', []))
        result.append({
            'name': name,
            'title': svc['title'],
            'running': running,
            'pid': pid,
            'deps': svc.get('deps', []),
            'deps_ok': deps_ok,
        })
    return result


def tail_log(name, lines=200):
    svc = CONF.get(name)
    if svc is None:
        return '(未知服务)'
    path = svc['log']
    if not os.path.isfile(path):
        return '(暂无日志)'
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, 512 * 1024)
            f.seek(size - read_size)
            data = f.read()
        out = data.decode('utf-8', 'replace').splitlines()
        return '\n'.join(out[-lines:])
    except Exception as e:
        return '(读取日志失败: %s)' % e


# ---------- 系统信息采集（读 /proc，零依赖） ----------
def _read_line(path):
    try:
        with open(path, 'rb') as f:
            return f.readline().decode('ascii', 'replace').strip()
    except Exception:
        return ''


def cpu_stat():
    """返回 (核心数, (idle, total))；前端用两次采样的差值计算占用率"""
    cores = 0
    try:
        with open('/proc/cpuinfo', 'rb') as f:
            for ln in f:
                if ln.lower().startswith(b'processor'):
                    cores += 1
    except Exception:
        pass
    if not cores:
        cores = 1
    idle = total = 0
    line = _read_line('/proc/stat')
    if line.startswith('cpu'):
        try:
            vals = [int(x) for x in line.split()[1:9]]
            while len(vals) < 8:
                vals.append(0)
            # user nice system idle iowait irq softirq steal
            idle = vals[3] + vals[4]
            total = sum(vals)
        except Exception:
            pass
    return cores, (idle, total)


def mem_info():
    info = {}
    try:
        with open('/proc/meminfo', 'rb') as f:
            for ln in f:
                parts = ln.decode('ascii', 'replace').split(':')
                if len(parts) == 2:
                    try:
                        info[parts[0].strip()] = int(parts[1].strip().split()[0])
                    except Exception:
                        pass
    except Exception:
        pass
    return info


def disk_info(path='/'):
    try:
        st = os.statvfs(path)
        return st.f_blocks * st.f_bsize, st.f_bavail * st.f_bsize
    except Exception:
        return 0, 0


def listening_ports():
    """解析 /proc/net/tcp(6)，返回 LISTEN 状态的端口集合"""
    ports = set()
    for fn in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            with open(fn, 'rb') as f:
                for ln in f:
                    parts = ln.decode('ascii', 'replace').split()
                    if len(parts) < 4 or parts[0] == 'sl':
                        continue
                    if parts[3] == '0A':  # TCP_LISTEN
                        try:
                            ports.add(int(parts[1].split(':')[1], 16))
                        except Exception:
                            pass
        except Exception:
            pass
    return ports


def sysinfo():
    cores, (idle, total) = cpu_stat()
    mem = mem_info()
    dtotal, dfree = disk_info('/')
    mt = mem.get('MemTotal', 0)
    ma = mem.get('MemAvailable', mem.get('MemFree', 0))
    pid_count = 0
    try:
        pid_count = sum(1 for d in os.listdir('/proc') if d.isdigit())
    except Exception:
        pass
    up_parts = _read_line('/proc/uptime').split()
    try:
        uptime_sec = int(float(up_parts[0]))
    except Exception:
        uptime_sec = 0
    return {
        'cores': cores,
        'cpu': {'idle': idle, 'total': total},
        'load': _read_line('/proc/loadavg').split()[:3],
        'uptime_sec': uptime_sec,
        'mem': {'total_kb': mt, 'avail_kb': ma,
                'buffers_kb': mem.get('Buffers', 0), 'cached_kb': mem.get('Cached', 0)},
        'swap': {'total_kb': mem.get('SwapTotal', 0), 'free_kb': mem.get('SwapFree', 0)},
        'disk': {'total': dtotal, 'free': dfree},
        'pid_count': pid_count,
        'listen': sorted(listening_ports()),
    }


# ---------- 数据库服务网络信息 ----------
MYSQL_USER = os.environ.get('PANEL_DB_USER', 'root')
MYSQL_PASS = os.environ.get('PANEL_DB_PASS', '123456')

# (表名, 服务名列, 附加列或 None) —— 问道服务注册表
NET_TABLES = [
    ('server', 'server', 'enable'),
    ('aaa', 'aaa', None),
    ('ccs', 'ccs', None),
    ('csa', 'csa', None),
    ('dba', 'dba', None),
    ('cs_ccs', 'ccs', None),
    ('cta', 'cta', None),
    ('spa', 'spa', None),
    ('tts', 'tts', None),
]


def db_netinfo(show_all=False):
    """查询 dl_adb_all 库各服务注册表的 ip/port，经 mysql 客户端子进程"""
    stmts = []
    for tbl, namecol, extracol in NET_TABLES:
        if extracol:
            extra = "CONCAT('%s=', IFNULL(`%s`,'-'))" % (extracol, extracol)
        else:
            extra = "''"
        stmts.append("SELECT '%s', IFNULL(`%s`,'-'), IFNULL(ip,'-'), "
                     "IFNULL(port,'-'), %s FROM `dl_adb_all`.`%s`;"
                     % (tbl, namecol, extra, tbl))
    cmd = ['mysql', '-u' + MYSQL_USER, '-p' + MYSQL_PASS, '-N', '-B',
           '-e', '\n'.join(stmts)]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate()
    except Exception as e:
        return {'ok': False, 'error': u'无法执行 mysql 客户端: %s' % e, 'rows': []}
    if p.returncode != 0:
        lines = err.decode('gbk', 'replace').strip().splitlines()
        msg = lines[-1] if lines else 'returncode=%s' % p.returncode
        return {'ok': False, 'error': u'MySQL 查询失败: %s' % msg, 'rows': []}
    rows = []
    for line in out.decode('gbk', 'replace').splitlines():
        cols = line.split('\t')
        while len(cols) < 5:
            cols.append('')
        tbl, name, ip, port, extra = cols[:5]
        # 默认只显示本机(127.0.0.1)的服务注册；其余是历史/其他部署记录
        if not show_all and ip != '127.0.0.1':
            continue
        rows.append({'table': tbl, 'name': name, 'ip': ip,
                     'port': port, 'extra': extra})
    return {'ok': True, 'rows': rows}


def query_dict(raw_qs):
    qs = {}
    for part in (raw_qs or '').split('&'):
        if '=' in part:
            k, v = part.split('=', 1)
            qs[k] = v
    return qs


# ---------- HTTP 处理 ----------
class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        log('%s %s' % (self.address_string(), fmt % args))

    # -- helpers --
    def send_bytes(self, code, body, ctype):
        if isinstance(body, str_t):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def send_json(self, obj, code=200):
        self.send_bytes(code, json.dumps(_to_unicode(obj), ensure_ascii=False),
                        'application/json; charset=utf-8')

    def read_body(self):
        try:
            n = int(self.headers.get('Content-Length', '0') or '0')
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    # -- routes --
    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            self.send_bytes(200, HTML, 'text/html; charset=utf-8')
        elif path == '/api/status':
            self.send_json({'ok': True, 'title': CONF.title,
                            'services': status_all()})
        elif path == '/api/sysinfo':
            self.send_json({'ok': True, 'sys': sysinfo()})
        elif path == '/api/netinfo':
            qs = query_dict(self.path.partition('?')[2])
            self.send_json(db_netinfo(qs.get('all') == '1'))
        elif path == '/api/log':
            qs = query_dict(self.path.partition('?')[2])
            name = qs.get('name', '')
            try:
                lines = int(qs.get('lines', '200'))
            except ValueError:
                lines = 200
            self.send_json({'ok': True, 'name': name,
                            'log': tail_log(name, min(max(lines, 1), 2000))})
        else:
            self.send_json({'ok': False, 'error': 'not found'}, 404)

    def do_POST(self):
        path, _, qs = self.path.partition('?')
        qargs = {}
        for part in qs.split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                qargs[k] = v
        body = self.read_body()
        name = body.get('name') or qargs.get('name', '')
        force = bool(body.get('force', qargs.get('force', False)))
        if path == '/api/start':
            ok, msg = start_service(name, force)
            self.send_json({'ok': ok, 'message': msg, 'services': status_all()})
        elif path == '/api/stop':
            ok, msg = stop_service(name, force)
            self.send_json({'ok': ok, 'message': msg, 'services': status_all()})
        elif path == '/api/start_all':
            msgs = []
            for svc_name in CONF.order:
                r, _, _ = service_running_quiet(svc_name)
                if not r:
                    ok, msg = start_service(svc_name)
                    msgs.append('%s: %s' % (svc_name, msg))
                    if not ok and '依赖' in msg:
                        break
                    time.sleep(1)
            self.send_json({'ok': True, 'message': '\n'.join(msgs) or '全部已在运行',
                            'services': status_all()})
        elif path == '/api/stop_all':
            msgs = []
            for svc_name in reversed(CONF.order):
                r, _, _ = service_running_quiet(svc_name)
                if r:
                    ok, msg = stop_service(svc_name)
                    msgs.append('%s: %s' % (svc_name, msg))
            self.send_json({'ok': True, 'message': '\n'.join(msgs) or '全部已停止',
                            'services': status_all()})
        else:
            self.send_json({'ok': False, 'error': 'not found'}, 404)


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    # 连接读超时，避免半开连接线程堆积
    timeout = 60

    def handle_error(self, request, client_address):
        # 客户端断开（浏览器刷新/进程退出）属正常噪音，静默处理
        # 兼容 py2.6：不用 py3 异常类名，改用 errno 判断
        import errno as _errno
        exc = sys.exc_info()[1]
        quiet = (32, 104, 110, 60, 11, 4)  # EPIPE ECONNRESET ETIMEDOUT ETIMEDOUT2 EAGAIN EINTR
        if isinstance(exc, IOError) and getattr(exc, 'errno', None) in quiet:
            return
        try:
            import socket as _s
            if isinstance(exc, _s.timeout):
                return
        except Exception:
            pass
        sys.stderr.write('[panel] handler error: %r\n' % (exc,))
        sys.stderr.flush()


# ---------- 页面 ----------
HTML = u"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
         background: #f0f2f5; color: #333; padding: 24px; }
  h1 { font-size: 20px; margin-bottom: 4px; }
  .sub { color: #888; font-size: 12px; margin-bottom: 16px; }
  .bar { margin-bottom: 16px; display: flex; gap: 8px; align-items: center; }
  .bar button { padding: 6px 14px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
          gap: 14px; }
  .card { background: #fff; border-radius: 10px; padding: 14px 16px;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .head { display: flex; align-items: center; justify-content: space-between; }
  .title { font-size: 15px; font-weight: bold; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
         margin-right: 6px; background: #ccc; }
  .dot.on { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
  .dot.off { background: #d1d5db; }
  .dot.warn { background: #f59e0b; }
  .st { font-size: 12px; color: #666; }
  .btns { margin-top: 12px; display: flex; gap: 8px; }
  button { cursor: pointer; border: 1px solid #d0d5dd; background: #fff;
           border-radius: 6px; padding: 5px 14px; font-size: 13px; }
  button:hover { background: #f5f5f5; }
  button.start { color: #15803d; border-color: #86efac; }
  button.stop  { color: #b91c1c; border-color: #fca5a5; }
  button:disabled { opacity: .45; cursor: not-allowed; }
  .logbox { margin-top: 10px; background: #0b1021; color: #c9d1d9; font-family:
            Consolas, monospace; font-size: 12px; border-radius: 8px; padding: 10px;
            max-height: 260px; overflow: auto; white-space: pre-wrap; display: none; }
  .msg { position: fixed; right: 18px; top: 18px; background: #111827; color: #fff;
         padding: 10px 16px; border-radius: 8px; font-size: 13px; display: none;
         max-width: 420px; white-space: pre-wrap; z-index: 9; }
  .dep { font-size: 11px; color: #b45309; margin-top: 4px; }
  .pid { color: #999; font-size: 11px; }
  .toprow { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
            gap: 14px; margin-bottom: 14px; }
  .wide { margin-bottom: 14px; }
  h2 { font-size: 14px; margin-bottom: 10px; }
  h2 small { font-weight: normal; font-size: 11px; color: #999; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #f0f0f0; }
  th { color: #888; font-weight: normal; white-space: nowrap; }
  td.num { font-family: Consolas, monospace; }
  .meter { height: 8px; background: #e5e7eb; border-radius: 4px; overflow: hidden;
           margin: 4px 0 10px; }
  .meter i { display: block; height: 100%; background: #3b82f6; border-radius: 4px;
             transition: width .4s; }
  .kv { display: flex; justify-content: space-between; font-size: 12px; color: #555;
        margin-top: 2px; }
  .kv b { font-weight: normal; color: #111; }
  .badge { font-size: 11px; padding: 1px 8px; border-radius: 10px; white-space: nowrap; }
  .badge.req { background: #dcfce7; color: #15803d; }
  .badge.opt { background: #fef9c3; color: #a16207; }
  .badge.int { background: #e5e7eb; color: #555; }
  .ipwarn { color: #b45309; }
  .hint { font-size: 11px; color: #999; margin-top: 8px; line-height: 1.6; }
  .toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 8px;
             font-size: 12px; color: #666; }
  /* ---- 教程弹窗 ---- */
  .mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: none;
          z-index: 8; }
  .tutbox { position: fixed; top: 4vh; left: 50%; transform: translateX(-50%);
            width: min(860px, 94vw); max-height: 90vh; overflow: auto;
            background: #fff; border-radius: 12px; padding: 22px 26px; display: none;
            z-index: 9; box-shadow: 0 8px 40px rgba(0,0,0,.25); }
  .tutbox h3 { font-size: 15px; margin: 18px 0 8px; padding-left: 8px;
               border-left: 4px solid #3b82f6; }
  .tutbox h3:first-child { margin-top: 0; }
  .tutbox p, .tutbox li { font-size: 13px; line-height: 1.8; color: #444; }
  .tutbox ul { padding-left: 20px; }
  .tutbox .close { float: right; font-size: 20px; border: none; background: none;
                   color: #888; }
  .tutbox kbd { background: #eef1f5; border: 1px solid #d0d5dd; border-bottom-width: 2px;
                border-radius: 4px; padding: 0 6px; font-family: Consolas, monospace;
                font-size: 12px; }
  .tutbox code { background: #f1f5f9; border-radius: 4px; padding: 1px 6px;
                 font-family: Consolas, monospace; font-size: 12px; color: #b45309; }
  .tutbox .impt { background: #fff7ed; border: 1px solid #fed7aa; color: #9a3412;
                  border-radius: 6px; padding: 8px 12px; font-size: 12px;
                  margin: 8px 0; line-height: 1.7; }
  .tutbox table { margin: 6px 0; }
  .tutbox th, .tutbox td { padding: 5px 10px; }
</style>
</head>
<body>
<h1 id="title"></h1>
<div class="sub">HTTP 面板已启动 · 状态每 3 秒自动刷新 · 日志默认显示末尾 200 行</div>
<div class="bar">
  <button onclick="startAll()">按顺序全部启动</button>
  <button onclick="stopAll()">全部停止</button>
  <button onclick="showTut()">查看教程</button>
  <span style="color:#999;font-size:12px" id="clock"></span>
</div>
<div class="toprow">
  <div class="card">
    <h2>服务器运行状态</h2>
    <div class="kv"><span>CPU 使用率（<span id="cores">-</span> 核）</span><b id="cpupct">采样中…</b></div>
    <div class="meter"><i id="cpubar" style="width:0%"></i></div>
    <div class="kv"><span>内存</span><b id="memtxt">-</b></div>
    <div class="meter"><i id="membar" style="width:0%;background:#8b5cf6"></i></div>
    <div class="kv"><span>磁盘（/）</span><b id="disktxt">-</b></div>
    <div class="meter"><i id="diskbar" style="width:0%;background:#10b981"></i></div>
    <div class="kv"><span>负载（1 / 5 / 15 分钟）</span><b id="loadtxt">-</b></div>
    <div class="kv"><span>运行时间 / 进程数</span><b id="uptxt">-</b></div>
  </div>
  <div class="card">
    <h2>外部端口配置</h2>
    <table><thead><tr><th>端口</th><th>用途</th><th>外部映射</th><th>监听</th></tr></thead>
    <tbody id="portbody"><tr><td colspan="4" style="color:#999">加载中…</td></tr></tbody></table>
    <div class="hint" id="porthint"></div>
  </div>
</div>
<div class="card wide">
  <h2>服务网络信息 <small>来源：MySQL dl_adb_all 库服务注册表（默认只显示本机 127.0.0.1 的记录）</small></h2>
  <div class="toolbar">
    <label><input type="checkbox" id="showall"> 显示全部记录（含历史/其他部署）</label>
    <button onclick="loadNet()">刷新</button>
    <span id="netnote" style="color:#999"></span>
  </div>
  <table><thead><tr><th>表</th><th>服务名</th><th>IP</th><th>端口</th><th>备注</th></tr></thead>
  <tbody id="netbody"><tr><td colspan="5" style="color:#999">加载中…</td></tr></tbody></table>
  <div class="hint">「必须映射」的端口需在 docker 运行时用 -p 映射到宿主机；「无需映射」端口仅容器内部互连（走 127.0.0.1），不映射更安全。内部注册表（aaa/ccs/csa/dba 等）必须保持 127.0.0.1，出现黄色 IP 说明配置被改错；server 表的 ip 是下发给客户端的游戏连接地址，跨机器部署时应改为对外 IP（见教程第二节）。</div>
</div>
<div class="grid" id="grid"></div>
<div class="mask" id="mask" onclick="hideTut()"></div>
<div class="tutbox" id="tutbox">
  <button class="close" onclick="hideTut()">✕</button>
  <h3>一、快速开始（本机 Docker 版流程）</h3>
  <ul>
    <li>原版教程需要 VMware 虚拟机，<b>本版已 Docker 化，无需虚拟机</b>，流程大幅简化。</li>
    <li>1. 打开本面板，点击 <b>「按顺序全部启动」</b>（面板会按 mysqld → driver → aaa → dba → ccs → csa → gs 的依赖顺序拉起，约 2~3 分钟）。</li>
    <li>2. 等 GS 卡片显示「运行中」；GS 完全就绪的标志是日志出现 <code>Game server allows user to login now.</code>（启动后约 1~2 分钟，8160 端口才会开始监听）。</li>
    <li>3. 打开目录 <code>1.63客户端</code>，双击 <b>「1.63版登录器.bat」</b> 启动游戏。</li>
    <li>4. 登录后选择「一线」进入游戏。选服时若提示「服务器处于爆满状态」属正常现象，<b>直接点击进入即可</b>（见下方第六节说明）。</li>
  </ul>

  <h3>二、服务器 IP 配置（局域网 / 公网部署必读）</h3>
  <p>客户端连接哪里，由<b>两处 IP</b> 共同决定，部署到其他机器时都要改：</p>
  <ul>
    <li><b>① 客户端登录 IP</b>：编辑客户端目录下的 <code>data\\Login.ini</code>（以及 <code>1.63版登录器.bat</code> 中的 IP 参数），把 <code>127.0.0.1</code> 改成<b>服务器的实际 IP</b>——本机玩保持 127.0.0.1；局域网玩改成服务器内网 IP（如 192.168.x.x）；公网玩改成公网 IP 或域名。这是客户端连<b>登录入口 8101</b> 用的地址。</li>
    <li><b>② 游戏连接 IP（服务端数据库）</b>：MySQL 的 <code>dl_adb_all.server</code> 表里的 <code>ip</code> 字段（当前为 127.0.0.1），要改成<b>客户端能访问到的服务器 IP</b>（与①相同的外部地址）。因为 AAA 会把这个 IP 下发给客户端，客户端用它连<b>游戏端口 8160</b>。修改方法：<code>UPDATE dl_adb_all.server SET ip='服务器IP' WHERE id=1;</code></li>
    <li><b>③ 服务端内部注册表不要动</b>：<code>dl_adb_all</code> 库中 aaa/ccs/csa/dba 等表必须保持 <code>127.0.0.1</code>（容器内部互连地址），改成外部 IP 反而会导致服务互连失败。</li>
  </ul>
  <div class="impt">跨机器访问时，服务器防火墙需放行 <b>8101（登录）和 8160（进游戏）</b> 两个端口；Docker 运行时这两个端口也要 <code>-p</code> 映射到宿主机。远程玩家改完 Login.ini 后无需改服务端，改完 server 表后重启 aaa/gs 服务生效。</div>

  <h3>三、测试账号</h3>
  <p>以下账号密码均为 <b>1</b>，等级均在 130 级以上：</p>
  <table>
    <tr><th>账号</th><th>密码</th><th>账号</th><th>密码</th><th>账号</th><th>密码</th></tr>
    <tr><td>a1</td><td>1</td><td>3</td><td>1</td><td>14</td><td>1</td></tr>
    <tr><td>1</td><td>1</td><td>4</td><td>1</td><td>15</td><td>1</td></tr>
    <tr><td>2</td><td>1</td><td>12</td><td>1</td><td>16</td><td>1</td></tr>
    <tr><td></td><td></td><td>13</td><td>1</td><td>17</td><td>1</td></tr>
  </table>
  <p>也可用注册工具新建账号（见第五节）；<b>权限不填 = 普通账号；权限填 1000 = GM 账号</b>。</p>

  <h3>四、数据库信息</h3>
  <table>
    <tr><th>项目</th><th>值</th></tr>
    <tr><td>地址 / 端口</td><td><code>127.0.0.1:3306</code>（宿主机已映射 3306）</td></tr>
    <tr><td>账号 / 密码</td><td><code>root</code> / <code>123456</code></td></tr>
    <tr><td>主要数据库</td><td><code>dl_adb_all</code>（服务注册表 + 账号表 account）</td></tr>
    <tr><td>游戏数据库</td><td><code>dl_mdb_1</code>（角色）· <code>dl_ddb_1</code>（宠物/物品）· <code>dl_ldb_1</code>（日志）· <code>dl_tdb_1</code> 等</td></tr>
  </table>
  <div class="impt">注意：<code>dl_adb_all</code> 库里的 aaa/ccs/csa/dba 等表是各服务互连地址，必须保持 127.0.0.1，改错会导致「服务器爆满」等进不去游戏的问题。可用面板「服务网络信息」区块随时检查。</div>

  <h3>五、注册 / 充值工具（充值注册软件.exe）</h3>
  <ul>
    <li>填写：<b>服务器IP</b> <code>127.0.0.1</code>（原版教程写的 192.168.200.128 是虚拟机地址，Docker 版改成 127.0.0.1）、<b>数据库账号</b> <code>root</code>、<b>密码</b> <code>123456</code>、<b>端口</b> <code>3306</code>、<b>数据表</b> <code>dl_adb_all</code>。</li>
    <li><b>注册不经过任何专门服务</b>：两个注册/充值工具都是直连 MySQL(3306) 读写 <code>dl_adb_all.account</code> 表完成注册充值，不存在也不需要"注册服务/注册端口"。面板「服务网络信息」里的 aaa/cta 等条目是游戏内部互连地址，与注册无关；数据库里 activate_url、auth_server 等网址字段是官方遗留配置（指向已失效的网页），可忽略。</li>
    <li>必须先把服务全部启动（MySQL 运行中），否则提示数据库连接失败；各项填写不能有空格。</li>
    <li>金元宝 / 银元宝<b>不能超过一亿</b>，否则角色无法进入游戏。</li>
    <li>1000 权限号创建角色时，<b>名字必须带 GM 字样</b>，否则提示「缺少必要字符」。</li>
    <li>目录里的《问道1.60刷帮清包工具》是易语言程序，杀毒软件可能误报，需放行。</li>
  </ul>
  <p><b>权限等级说明</b>（对应数据库 <code>dl_adb_all.account</code> 表的 <code>privilege</code> 字段，注册工具里填写的"权限"即此值）：</p>
  <table>
    <tr><th>权限值</th><th>等级</th><th>能力</th></tr>
    <tr><td>0（不填）</td><td>普通账号</td><td>正常游戏，无任何管理功能</td></tr>
    <tr><td>200</td><td>初级 GM</td><td>可使用部分基础管理命令（广播、查询类）；<b>不能</b>用管理工具改人物/宠物数据，<b>不能</b>召唤老君；名字无需带 GM</td></tr>
    <tr><td>1000</td><td>超级 GM</td><td>全部管理功能：<kbd>ALT</kbd>+<kbd>F11</kbd> 管理工具改人物/宠物任意数据、召唤老君（复制装备宝宝）、默认隐身；<b>建角色名字必须带 GM</b></td></tr>
  </table>
  <p class="hint" style="font-size:11px;color:#999">注：本服务端实测数据中主要使用 0 / 1000 两档；200 档为低于 1000 的部分权限（具体可用命令以游戏内实际效果为准，可用 200 权限号按 ALT+F11 验证——通常会被拒绝）。</p>

  <h3>六、GM 操作（游戏内）</h3>
  <ul>
    <li><kbd>ALT</kbd>+<kbd>F11</kbd> 呼出 GM 操作工具：角色查询 → 本线查询 → 双击一线 → 数据查看，可改人物/宠物的<b>属性、技能、装备</b>等任何数据。</li>
    <li>若按 <kbd>ALT</kbd>+<kbd>F11</kbd> 无反应：检查快捷键是否被占用，或键盘设置为标准键盘而非功能快捷键。</li>
    <li>1000 权限号默认<b>隐身</b>且遇不到怪，按 <kbd>ALT</kbd>+<kbd>F11</kbd> → 自身状态 可切换隐身。</li>
    <li>召唤老君（可复制游戏内任何物品/装备/宝宝）：先用 GM 工具的「技能查看」给角色添加 <b>急急如律令</b> 技能，进游戏后按 <kbd>F2</kbd> 呼出老君即可复制装备、宝宝、物品等。</li>
    <li>1.63GM工具.exe / 带发货GM工具：可充值、发送装备道具宠物等，具体功能自行摸索。</li>
  </ul>
  <div class="impt">⚠ 改数据不要太过变态，否则会导致数据异常无法进入游戏。若发送装备/宝宝后角色进不去，用 GM 工具的「删除背包道具宠物」功能删掉发送的物品即可恢复。</div>

  <h3>七、常见问题</h3>
  <ul>
    <li><b>选服时显示「服务器处于爆满状态」</b>：这是本服务端的正常现象——客户端选服列表的状态没有实时上报，显示爆满<b>但直接点击仍可正常登录进入游戏</b>。只有反复点击都无法进入时，才按下面排查。</li>
    <li><b>完全连不上 / 无法进入</b>：面板确认 7 个服务全部运行、gs 日志出现 allows user to login 且 8160 已监听（「外部端口配置」表里看）；跨机器访问检查两处 IP 是否配置正确（见第二节）。</li>
    <li><b>端口被占用 / 8101 起不来</b>：重启容器或稍等重试（TIME_WAIT 残留）。</li>
    <li><b>想改服务端口映射</b>：见上方「外部端口配置」区块，必须映射的端口为 8080 / 8101 / 8160。</li>
    <li>原版附带的《单机问道安装教程.mp4》是虚拟机版安装流程视频，其中 GM 工具用法部分仍可参考。</li>
  </ul>
</div>
<div class="msg" id="msg"></div>
<script>
var SVC = {};
var logOpen = {};
var PORTS = __PORTS__;
var lastCpu = null;

function toast(t) {
  var m = document.getElementById('msg');
  m.textContent = t; m.style.display = 'block';
  clearTimeout(m._t); m._t = setTimeout(function(){ m.style.display='none'; }, 4000);
}

/* ---------- 教程弹窗 ---------- */
function showTut() {
  document.getElementById('mask').style.display = 'block';
  document.getElementById('tutbox').style.display = 'block';
}
function hideTut() {
  document.getElementById('mask').style.display = 'none';
  document.getElementById('tutbox').style.display = 'none';
}
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') hideTut();
});

function api(path, body, cb) {
  fetch(path, {method: body?'POST':'GET', headers:{'Content-Type':'application/json'},
               body: body?JSON.stringify(body):undefined})
    .then(function(r){ return r.json(); }).then(cb)
    .catch(function(e){ toast('请求失败: ' + e); });
}

function mkBtn(text, cls, fn, name) {
  var b = document.createElement('button');
  b.textContent = text;
  if (cls) b.className = cls;
  b.onclick = function(){ fn(name); };
  return b;
}

function render(data) {
  if (data.title) document.getElementById('title').textContent = data.title;
  var g = document.getElementById('grid');
  g.innerHTML = '';
  data.services.forEach(function(s) {
    SVC[s.name] = s;
    var open = !!logOpen[s.name];
    var st = s.running ? '运行中' : (s.deps_ok ? '已停止' : '已停止(依赖未满足)');
    var dot = s.running ? 'on' : (s.deps_ok ? 'off' : 'warn');
    var card = document.createElement('div');
    card.className = 'card';

    var head = document.createElement('div'); head.className = 'head';
    var tl = document.createElement('span'); tl.className = 'title';
    var dotEl = document.createElement('span'); dotEl.className = 'dot ' + dot;
    tl.appendChild(dotEl);
    tl.appendChild(document.createTextNode(s.title));
    var stsp = document.createElement('span'); stsp.className = 'st';
    stsp.textContent = st + (s.pid ? '  pid ' + s.pid : '');
    head.appendChild(tl); head.appendChild(stsp);
    card.appendChild(head);

    if (s.deps && s.deps.length) {
      var dep = document.createElement('div'); dep.className = 'dep';
      dep.textContent = '依赖: ' + s.deps.join(', ');
      card.appendChild(dep);
    }

    var btns = document.createElement('div'); btns.className = 'btns';
    var bStart = mkBtn('启动', 'start', svcStart, s.name); bStart.disabled = s.running;
    var bStop = mkBtn('停止', 'stop', svcStop, s.name); bStop.disabled = !s.running;
    var bLog = mkBtn(open ? '收起日志' : '查看日志', '', toggleLog, s.name);
    btns.appendChild(bStart); btns.appendChild(bStop); btns.appendChild(bLog);
    card.appendChild(btns);

    var lb = document.createElement('div'); lb.className = 'logbox';
    lb.id = 'log_' + s.name; lb.style.display = open ? 'block' : 'none';
    card.appendChild(lb);

    g.appendChild(card);
    if (open) refreshLog(s.name);
  });
  document.getElementById('clock').textContent =
    '刷新于 ' + new Date().toLocaleTimeString();
}

function refresh() { api('/api/status', null, render); }

function svcStart(n) {
  api('/api/start', {name: n}, function(r) {
    toast(r.message || (r.ok ? '已启动' : '失败'));
    if (r.services) render(r);
  });
}
function svcStop(n) {
  if (!confirm('确定停止该服务？')) return;
  api('/api/stop', {name: n}, function(r) {
    toast(r.message || (r.ok ? '已停止' : '失败'));
    if (r.services) render(r);
  });
}
function startAll() {
  api('/api/start_all', {name: '_all'}, function(r) {
    toast(r.message || '完成');
    if (r.services) render(r);
  });
}
function stopAll() {
  if (!confirm('确定停止全部服务？')) return;
  api('/api/stop_all', {name: '_all'}, function(r) {
    toast(r.message || '完成');
    if (r.services) render(r);
  });
}
function toggleLog(n) {
  logOpen[n] = !logOpen[n];
  refresh();
}
function refreshLog(n) {
  var box = document.getElementById('log_' + n);
  if (!box) return;
  fetch('/api/log?name=' + n + '&lines=200')
    .then(function(r){ return r.json(); })
    .then(function(r) {
      var atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
      box.textContent = r.log || '(空)';
      if (atBottom) box.scrollTop = box.scrollHeight;
    });
}

/* ---------- 系统状态 / 端口 / 网络信息 ---------- */
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, function(c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
  });
}
function fmtKB(kb) {
  if (kb == null) return '-';
  if (kb >= 1048576) return (kb / 1048576).toFixed(1) + ' GB';
  return Math.round(kb / 1024) + ' MB';
}
function fmtGB(b) {
  if (b == null) return '-';
  return (b / 1073741824).toFixed(1) + ' GB';
}
function fmtDur(s) {
  if (!s && s !== 0) return '-';
  var d = Math.floor(s / 86400); s %= 86400;
  var h = Math.floor(s / 3600); s %= 3600;
  var m = Math.floor(s / 60);
  return (d ? d + '天' : '') + h + '小时' + m + '分';
}

function renderSys(d) {
  if (!d || !d.cpu || !d.mem) return;  // 数据不完整时跳过本次渲染
  /* CPU 占用率 = 两次采样间 idle 差值 / total 差值 */
  var pct = null;
  if (lastCpu && d.cpu.total > lastCpu.total) {
    var di = d.cpu.idle - lastCpu.idle, dt = d.cpu.total - lastCpu.total;
    pct = Math.max(0, Math.min(100, Math.round((1 - di / dt) * 100)));
  }
  lastCpu = d.cpu;
  document.getElementById('cores').textContent = d.cores;
  if (pct !== null) {
    document.getElementById('cpupct').textContent = pct + '%';
    document.getElementById('cpubar').style.width = pct + '%';
  }
  var mt = d.mem.total_kb, ma = d.mem.avail_kb;
  var used = mt - ma, mpct = mt ? Math.round(used * 100 / mt) : 0;
  document.getElementById('memtxt').textContent =
    fmtKB(used) + ' / ' + fmtKB(mt) + '（' + mpct + '%，可用 ' + fmtKB(ma) + '）';
  document.getElementById('membar').style.width = mpct + '%';
  var dtotal = d.disk.total, dfree = d.disk.free;
  var dpct = dtotal ? Math.round((dtotal - dfree) * 100 / dtotal) : 0;
  document.getElementById('disktxt').textContent =
    fmtGB(dtotal - dfree) + ' / ' + fmtGB(dtotal) + '（' + dpct + '%）';
  document.getElementById('diskbar').style.width = dpct + '%';
  document.getElementById('loadtxt').textContent = d.load.join(' / ') || '-';
  document.getElementById('uptxt').textContent =
    fmtDur(d.uptime_sec) + ' / ' + d.pid_count + ' 个';
  renderPorts(d.listen);
}

var LEVELS = { required: ['必须映射', 'req'], optional: ['可选', 'opt'],
               internal: ['无需映射', 'int'] };
function renderPorts(listen) {
  var html = '', need = [];
  PORTS.forEach(function(p) {
    var lv = LEVELS[p.level] || [p.level || '-', 'int'];
    var on = listen.indexOf(p.port) >= 0;
    if (p.level === 'required' || p.level === 'optional') need.push(p.port);
    html += '<tr><td class="num">' + p.port + '</td><td>' + esc(p.name) + '</td>'
          + '<td><span class="badge ' + lv[1] + '">' + lv[0] + '</span></td>'
          + '<td>' + (on ? '<span style="color:#15803d">监听中</span>'
                        : '<span style="color:#b91c1c">未监听</span>') + '</td></tr>';
  });
  document.getElementById('portbody').innerHTML = html;
  document.getElementById('porthint').textContent =
    '建议映射到宿主机的端口：' + need.join(', ')
    + '（其余端口仅容器内部使用，无需 -p）';
}

function pollSys() {
  api('/api/sysinfo', null, function(r) { renderSys(r && r.sys); });
}

function loadNet() {
  var all = document.getElementById('showall').checked ? 1 : 0;
  api('/api/netinfo?all=' + all, null, function(r) {
    var tb = document.getElementById('netbody');
    if (!r.ok) {
      tb.innerHTML = '<tr><td colspan="5" class="ipwarn">'
                   + esc(r.error || '查询失败') + '</td></tr>';
      document.getElementById('netnote').textContent = '';
      return;
    }
    if (!r.rows.length) {
      tb.innerHTML = '<tr><td colspan="5" style="color:#999">无记录</td></tr>';
      document.getElementById('netnote').textContent = '';
      return;
    }
    var html = '';
    r.rows.forEach(function(row) {
      var warn = (row.ip !== '127.0.0.1' && row.ip !== '-') ? ' class="ipwarn"' : '';
      html += '<tr><td>' + esc(row.table) + '</td><td>' + esc(row.name) + '</td>'
            + '<td' + warn + '>' + esc(row.ip) + '</td>'
            + '<td class="num">' + esc(row.port) + '</td>'
            + '<td>' + esc(row.extra) + '</td></tr>';
    });
    tb.innerHTML = html;
    document.getElementById('netnote').textContent = '共 ' + r.rows.length + ' 条记录';
  });
}
document.getElementById('showall').onchange = loadNet;

setInterval(function() {
  refresh();
  pollSys();
}, 3000);
setInterval(loadNet, 30000);
refresh();
pollSys();
loadNet();
</script>
</body>
</html>"""

# 把端口配置注入页面（py2 下 json.dumps 可能返回 unicode 或 utf-8 bytes，按类型处理）
_ports_json = json.dumps(CONF.ports, ensure_ascii=False)
if PY2 and isinstance(_ports_json, str):
    _ports_json = _ports_json.decode('utf-8')
HTML = HTML.replace('__TITLE__', CONF.title).replace('__PORTS__', _ports_json)


def main():
    # 信号留痕：面板意外退出时可在日志里看到是谁发的信号
    def _on_signal(signum, frame):
        try:
            sig_name = signal.Signals(signum).name
        except Exception:
            sig_name = str(signum)
        log('received signal %s, exiting' % sig_name)
        sys.exit(0)
    for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(_sig, _on_signal)
        except Exception:
            pass

    server = ThreadingHTTPServer(('0.0.0.0', PANEL_PORT), Handler)
    log('panel serving on 0.0.0.0:%d, conf=%s' % (PANEL_PORT, CONF_PATH))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log('keyboard interrupt, exiting')
    except Exception as e:
        log('server fatal: %r' % (e,))
        raise


if __name__ == '__main__':
    main()
