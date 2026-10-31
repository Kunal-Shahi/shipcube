"""Abuse pre-screen patterns: the one file to review (docs/research/profiler-sandbox.md §8).

Each rule has a reason code and a confidence:
- "high": refuse the estimate (and, on Platform's side, the deploy). Only markers that a
  legitimate internal app essentially never contains.
- "low": allowed, but recorded as an abuse signal (a strike) for review.

Matching is case-insensitive. Nothing here is echoed back: a hit records the rule name and the
file it was found in, never the matched text.
"""

# Reason codes. High-confidence hits use the first three; low ones become abuse_signals
# named "prescreen_<code>".
CRYPTO_MINER = "crypto_miner"        # high
ATTACK_TOOL = "attack_tool"          # high: internet-wide scanners, flooders
PHISHING_KIT = "phishing_kit"        # high
MINING_POOL_REFERENCE = "mining_pool_reference"   # low: a pool host named (dashboards query pool APIs)
BULK_MAIL = "bulk_mail"              # low
NETWORK_SCANNER = "network_scanner"  # low: nmap wrappers have admin uses
FLOOD_SCRIPT = "flood_script"        # low: flood/DDoS vocabulary without a known tool
PHISHING_SUSPECT = "phishing_suspect"  # low: a brand's login look-alike without exfiltration

# --- file names (any path component, exact, case-insensitive) -------------------------------
FILE_NAMES = {
    CRYPTO_MINER: {"xmrig", "xmrig.exe", "xmr-stak", "xmr-stak-cpu", "xmr-stak-rx", "cpuminer", "cpuminer-opt",
                   "minerd", "cgminer", "bfgminer", "ethminer", "t-rex", "nbminer", "lolminer", "phoenixminer",
                   "gminer", "srbminer", "srbminer-multi", "teamredminer", "nanominer", "config.json.xmrig"},
    ATTACK_TOOL: {"masscan", "zmap", "hping3", "slowloris", "slowloris.py", "loic.exe", "hoic.exe", "goldeneye.py",
                  "xerxes", "xerxes.c", "torshammer.py", "mhddos"},
}

# --- requirements (package names, PEP 503-normalised) -----------------------------------------
REQUIREMENTS_HIGH = {
    ATTACK_TOOL: {"slowloris", "mhddos", "pyslowloris"},
}
REQUIREMENTS_LOW = {
    NETWORK_SCANNER: {"python-nmap", "python3-nmap", "nmap3", "masscan"},
}

# --- text in any file (regexes) ---------------------------------------------------------------
TEXT_HIGH = {
    CRYPTO_MINER: [
        ("stratum_url", r"stratum\+(?:tcp|ssl|tls)://"),
        ("xmrig_donate_flag", r"--donate-level\b"),
        ("xmrig_config", r'"algo"\s*:\s*"(?:rx/0|cn/r|randomx|kawpow|ethash)"'),
    ],
    ATTACK_TOOL: [
        ("masscan_or_zmap_run", r"""(?:subprocess|os\.system|Popen|check_output|run)\s*\(.{0,80}\b(?:masscan|zmap)\b"""),
        ("hping_flood", r"\bhping3?\b.{0,40}--(?:flood|faster)"),
        ("slowloris_import", r"^\s*(?:from|import)\s+slowloris\b"),
    ],
}

TEXT_LOW = {
    MINING_POOL_REFERENCE: [
        ("pool_host", r"\b(?:supportxmr\.com|moneroocean\.stream|minexmr\.com|nanopool\.org|"
                      r"2miners\.com|f2pool\.com|hashvault\.pro|herominers\.com|xmrpool\.eu|c3pool\.com|"
                      r"unmineable\.com|nicehash\.com|ethermine\.org|viabtc\.com|antpool\.com|kryptex\.network)\b"),
    ],
    NETWORK_SCANNER: [
        ("nmap_import", r"^\s*(?:from|import)\s+nmap3?\b"),
        ("nmap_run", r"""(?:subprocess|os\.system|Popen|check_output|run)\s*\(.{0,80}\bnmap\b"""),
    ],
    FLOOD_SCRIPT: [
        ("flood_words", r"\b(?:syn_?flood|udp_?flood|http_?flood|icmp_?flood|ddos_?attack|stress_?target)\b"),
        ("scapy_syn_loop", r"send(?:p)?\s*\(.{0,120}flags\s*=\s*['\"]S['\"]"),
    ],
}

# --- phishing (HTML files) ------------------------------------------------------------------
# Scans inside a tag are bounded ({0,400}): an unbounded [^>]* is quadratic on a page of
# unclosed tags (a 1 MB "<input <input ..." took minutes).
PASSWORD_FIELD = r"""<input\b[^>]{0,400}?type\s*=\s*["']?password"""
BRAND_LOGIN = (r"sign in to (?:your )?(?:microsoft|outlook|office ?365|google|apple|icloud|paypal|amazon|"
               r"facebook|instagram|netflix|linkedin|dropbox|docusign|adobe|chase|wells ?fargo|bank of america|"
               r"citibank|hsbc|coinbase|binance)|(?:microsoft|office ?365|paypal|apple id|google account|netflix|"
               r"chase|wells fargo|bank of america|coinbase|binance) (?:login|sign[ -]?in|log[ -]?in|verification)")
# Where a kit sends stolen credentials (high, with a password field).
CREDENTIAL_EXFIL = (r"api\.telegram\.org/bot|discord(?:app)?\.com/api/webhooks|hooks\.slack\.com/services|"
                    r"formsubmit\.co|getform\.io|webhook\.site|requestbin|ngrok(?:-free)?\.(?:io|app)")

# Budgets: files bigger than MAX_TEXT_BYTES aren't text-scanned (file names still are); the text
# scan stops after MAX_TOTAL_TEXT_BYTES or MAX_SECONDS in all, and the verdict says it's incomplete.
MAX_TEXT_BYTES = 256_000
MAX_TOTAL_TEXT_BYTES = 8_000_000
MAX_SECONDS = 5.0

# Not a security boundary: trivial obfuscation (string concatenation, base64, renamed binaries,
# packed archives) gets past every rule. This catches careless abuse cheaply; the sandbox's own
# limits and run-time abuse signals are what hold for the rest.
