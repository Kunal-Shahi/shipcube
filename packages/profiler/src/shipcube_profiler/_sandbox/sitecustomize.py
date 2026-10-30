# Loaded automatically by the profiled Python process (it's first on PYTHONPATH).
# Counts outbound connections and socket bytes, and when SHIPCUBE_NO_EGRESS=1 refuses
# connections to anything but loopback. This is a measurement aid and a soft guard
# for local runs, not a security boundary: on GCP the profiler job has no egress route.
import atexit
import json
import os
import signal
import socket
import sys

_stats = {"connect_attempts": 0, "blocked": 0, "remote_hosts": [], "remote_ports": [], "bytes_sent": 0, "bytes_recv": 0,
          "tamper_attempts": 0}
_out = os.environ.get("SHIPCUBE_STATS_FILE")
_no_egress = os.environ.get("SHIPCUBE_NO_EGRESS") == "1"
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_send, _orig_sendall, _orig_recv = socket.socket.send, socket.socket.sendall, socket.socket.recv
_orig_getaddrinfo = socket.getaddrinfo


def _host(address):
    if isinstance(address, tuple) and address:
        return str(address[0])
    return None


def _is_local(host):
    return host is None or host in ("localhost", "::1") or host.startswith("127.")


def _check(self, address):
    host = _host(address)
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(host):
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        _record(host, port)


def _record(host, port=None):
    _stats["connect_attempts"] += 1
    if isinstance(port, int) and port not in _stats["remote_ports"] and len(_stats["remote_ports"]) < 50:
        _stats["remote_ports"].append(port)
    if host not in _stats["remote_hosts"] and len(_stats["remote_hosts"]) < 50:
        _stats["remote_hosts"].append(host)
    if _no_egress:
        _stats["blocked"] += 1
        raise OSError(101, "Network is unreachable (ShipCube profiler: no egress)")


def getaddrinfo(host, *a, **kw):
    # A name lookup is the first sign of an outbound call, and DNS is egress too.
    h = host.decode() if isinstance(host, bytes) else host
    if h and not _is_local(h) and not h.replace(".", "").isdigit() and ":" not in h:
        _record(h)
    return _orig_getaddrinfo(host, *a, **kw)


def connect(self, address):
    _check(self, address)
    return _orig_connect(self, address)


def connect_ex(self, address):
    _check(self, address)
    return _orig_connect_ex(self, address)


def send(self, data, *a):
    n = _orig_send(self, data, *a)
    _stats["bytes_sent"] += n
    return n


def sendall(self, data, *a):
    _stats["bytes_sent"] += len(data)
    return _orig_sendall(self, data, *a)


def recv(self, *a):
    data = _orig_recv(self, *a)
    _stats["bytes_recv"] += len(data)
    return data


# Signals aimed at the profiler's agent (or the container's PID 1). The agent runs as
# another uid, so the kernel refuses these anyway; counting them makes the attempt an
# abuse signal. Like the socket hooks, a determined app can bypass this (ctypes).
_guard = {int(p) for p in os.environ.get("SHIPCUBE_GUARD_PIDS", "").split(",") if p.isdigit()}
_orig_kill, _orig_killpg = os.kill, os.killpg


def kill(pid, sig):
    if pid in _guard or pid == -1:
        _stats["tamper_attempts"] += 1
    return _orig_kill(pid, sig)


def killpg(pgid, sig):
    if pgid in _guard:
        _stats["tamper_attempts"] += 1
    return _orig_killpg(pgid, sig)


os.kill, os.killpg = kill, killpg
socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
socket.socket.send, socket.socket.sendall, socket.socket.recv = send, sendall, recv
socket.getaddrinfo = getaddrinfo


def _dump():
    if _out:
        try:
            with open(f"{_out}.{os.getpid()}", "w") as f:
                json.dump(_stats, f)
        except OSError:
            pass


atexit.register(_dump)
# Servers are stopped with SIGTERM; turn it into a clean exit so atexit runs.
try:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
except ValueError:
    pass
