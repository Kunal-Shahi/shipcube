"""Static abuse pre-screen of an app's files, before anything is built or run.

    verdict = prescreen(app_dir)
    verdict.blocked        # any high-confidence hit: refuse (estimate here; deploy on Platform's side)
    verdict.signals        # low-confidence hits as abuse_signals ("prescreen_<code>"): strikes, not blocks
    verdict.to_dict()      # {"verdict": "ok"|"flagged"|"blocked", "reasons": [...], "signals": [...], "hits": [...]}

Cheap and conservative: file names, requirements and a few regexes from prescreen_patterns
(the one file to review), plus two structural checks: mail sent in a loop, and login pages that
send credentials elsewhere. Hits name the rule and the file, never the matched text.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import prescreen_patterns as P

_FLAGS = re.I | re.M
_TEXT_HIGH = {code: [(name, re.compile(rx, _FLAGS)) for name, rx in rules] for code, rules in P.TEXT_HIGH.items()}
_TEXT_LOW = {code: [(name, re.compile(rx, _FLAGS)) for name, rx in rules] for code, rules in P.TEXT_LOW.items()}
_PASSWORD = re.compile(P.PASSWORD_FIELD, re.I)
_BRAND = re.compile(P.BRAND_LOGIN, re.I)
_EXFIL = re.compile(P.CREDENTIAL_EXFIL, re.I)
_EXTERNAL_ACTION = re.compile(r"""<form[^>]*action\s*=\s*["']?https?://""", re.I)
_SERVER_READS_PASSWORD = re.compile(r"""(?:request\.(?:form|json|values|get_json\(\))|req\.body)\W.{0,40}pass""", re.I | re.S)
_PAGE_EXT = {".html", ".htm", ".php", ".jinja", ".j2", ".tmpl"}
_CODE_EXT = {".py", ".js", ".ts", ".php"}
_MAIL_SENDS = {"sendmail", "send_message"}


class AbuseRefused(ValueError):
    """The pre-screen found a high-confidence abuse marker; the estimate is refused.
    `codes` are the reason codes (prescreen_patterns), `verdict` the full verdict."""

    def __init__(self, verdict: "Verdict"):
        self.verdict = verdict
        self.codes = verdict.reasons
        super().__init__("refused by the abuse pre-screen: " + ", ".join(self.codes))


@dataclass
class Hit:
    code: str
    confidence: str          # "high" | "low"
    rule: str
    file: str                # relative path; never the matched text


@dataclass
class Verdict:
    hits: list[Hit] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(h.confidence == "high" for h in self.hits)

    @property
    def reasons(self) -> list[str]:
        return sorted({h.code for h in self.hits if h.confidence == "high"})

    @property
    def signals(self) -> list[str]:
        return sorted({f"prescreen_{h.code}" for h in self.hits if h.confidence == "low"})

    def to_dict(self) -> dict:
        return {"verdict": "blocked" if self.blocked else ("flagged" if self.hits else "ok"),
                "reasons": self.reasons, "signals": self.signals, "hits": [asdict(h) for h in self.hits]}


def _files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "__pycache__", ".venv", "venv"}]
        for n in filenames:
            p = Path(dirpath) / n
            if not p.is_symlink() and p.is_file():
                yield p


def _req_names(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if m:
            out.add(re.sub(r"[-_.]+", "-", m[1]).lower())
    return out


def _mail_in_loop(tree: ast.AST) -> bool:
    """smtplib's sendmail/send_message inside a loop over something other than a small literal list."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            if isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.iter, (ast.List, ast.Tuple, ast.Set)) \
                    and len(node.iter.elts) <= 3:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr in _MAIL_SENDS:
                    return True
    return False


def prescreen(root: Path) -> Verdict:
    v = Verdict()
    seen: set[tuple[str, str, str]] = set()

    def hit(code: str, confidence: str, rule: str, f: Path) -> None:
        key = (code, rule, str(f))
        if key not in seen:
            seen.add(key)
            v.hits.append(Hit(code, confidence, rule, str(f.relative_to(root))))

    for f in _files(root):
        name = f.name.lower()
        for code, names in P.FILE_NAMES.items():
            if name in names:
                hit(code, "high", "file_name", f)
        if name in {"requirements.txt", "requirements.in"}:
            reqs = _req_names(f.read_text(errors="replace"))
            for table, confidence in ((P.REQUIREMENTS_HIGH, "high"), (P.REQUIREMENTS_LOW, "low")):
                for code, pkgs in table.items():
                    if reqs & pkgs:
                        hit(code, confidence, "requirement", f)
        try:
            if f.stat().st_size > P.MAX_TEXT_BYTES:
                continue
            text = f.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        for table, confidence in ((_TEXT_HIGH, "high"), (_TEXT_LOW, "low")):
            for code, rules in table.items():
                for rule, rx in rules:
                    if rx.search(text):
                        hit(code, confidence, rule, f)
        ext = f.suffix.lower()
        if ext in _PAGE_EXT and _PASSWORD.search(text):
            brand, exfil = _BRAND.search(text), _EXFIL.search(text)
            if exfil or (brand and _EXTERNAL_ACTION.search(text)):
                hit(P.PHISHING_KIT, "high", "login_page_sends_credentials_elsewhere", f)
            elif brand:
                hit(P.PHISHING_SUSPECT, "low", "brand_login_lookalike", f)
        if ext in _CODE_EXT and _EXFIL.search(text) and _SERVER_READS_PASSWORD.search(text):
            hit(P.PHISHING_KIT, "high", "forwards_submitted_password", f)
        if ext == ".py" and ("smtplib" in text or "send_message" in text):
            try:
                if _mail_in_loop(ast.parse(text)):
                    hit(P.BULK_MAIL, "low", "mail_sent_in_loop", f)
            except SyntaxError:
                pass
    return v
