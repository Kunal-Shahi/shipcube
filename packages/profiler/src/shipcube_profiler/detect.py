"""Static inspection of an app directory: entrypoint, framework, routes, dependencies.

Everything here is read with plain parsing. User code is never imported, and nothing
in it is treated as an instruction.
"""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

WEB_FRAMEWORKS = {"flask": "flask", "fastapi": "fastapi", "django": "django",
                  "streamlit": "streamlit", "dash": "dash", "starlette": "starlette",
                  "aiohttp": "aiohttp", "http.server": "http.server"}
GPU_LIBS = {"torch", "tensorflow", "jax", "cupy", "vllm", "transformers", "diffusers"}
DATA_LIBS = {"pandas", "polars", "duckdb", "numpy", "pyarrow", "sqlalchemy", "psycopg", "psycopg2"}
NET_LIBS = {"requests", "httpx", "urllib3", "aiohttp", "boto3", "google.cloud", "openai"}
ENTRY_NAMES = ["main.py", "app.py", "server.py", "run.py", "job.py", "__main__.py"]
_ROUTE = re.compile(r"""@\w+\.(?:route|get)\(\s*['"]([^'"]+)['"]""")
_INPUT = re.compile(
    r"""@\w+\.(?:post|put|patch|delete|websocket)\s*\(|methods\s*=\s*[\[(][^\])]*['"](?:POST|PUT|PATCH|DELETE)['"]"""
    r"""|request\.(?:form|args|values|json|get_json|files|data|query_params|body)\b|\b(?:Query|Form|Body|File)\s*\("""
    r"""|<form\b|<input\b|st\.(?:slider|text_input|number_input|button|file_uploader|selectbox)\b""", re.I)
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages"}


@dataclass
class CodeSummary:
    language: str = "python"
    entrypoint: str | None = None
    kind: str = "script"                 # "web" or "script"
    framework: str | None = None
    routes: list[str] = field(default_factory=list)
    # The app takes input per request (POST/PUT/..., form or JSON bodies, query parameters,
    # websockets): its responses can't be precomputed into a static snapshot.
    accepts_input: bool = False
    api_only: bool = False               # every route is under /api, /v1 ... (no pages)
    imports: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    files: int = 0
    lines: int = 0
    uses_gpu_libs: bool = False
    uses_data_libs: bool = False
    uses_network_libs: bool = False
    writes_outputs: list[str] = field(default_factory=list)  # file suffixes the code writes
    excerpt: str = ""                    # first lines of the entrypoint (untrusted text)

    def to_dict(self) -> dict:
        return asdict(self)


def _inside(root: Path, p: Path) -> bool:
    """A regular file under root, reached without following any symlink: tenant code must never
    make the reader open something outside its checkout (e.g. /proc/self/environ)."""
    try:
        rel = p.relative_to(root)
    except ValueError:
        return False
    cur = root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            return False
    return p.is_file()


def _py_files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*.py")):
        if not _SKIP_DIRS.intersection(p.relative_to(root).parts) and _inside(root, p):
            out.append(p)
    return out[:500]


def _imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _has_main_guard(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            t = node.test
            if isinstance(t.left, ast.Name) and t.left.id == "__name__":
                return True
    return False


def summarize(root: Path, excerpt_lines: int = 60) -> CodeSummary:
    s = CodeSummary()
    files = _py_files(root)
    s.files = len(files)
    imports: set[str] = set()
    mains: list[Path] = []
    text_all = []
    for p in files:
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        s.lines += text.count("\n") + 1
        text_all.append(text)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        imports |= _imports(tree)
        if _has_main_guard(tree):
            mains.append(p)
        s.routes += _ROUTE.findall(text)
    s.imports = sorted(imports)
    tops = {i.split(".")[0] for i in imports} | imports

    req = root / "requirements.txt"
    if _inside(root, req):
        s.requirements = [ln.strip() for ln in req.read_text().splitlines()
                          if ln.strip() and not ln.startswith("#")][:100]
    req_names = {re.split(r"[<>=\[; ]", r, maxsplit=1)[0].lower() for r in s.requirements}

    for mod, fw in WEB_FRAMEWORKS.items():
        if mod in tops or mod in req_names:
            s.framework, s.kind = fw, "web"
            break
    s.uses_gpu_libs = bool(GPU_LIBS & (tops | req_names))
    s.uses_data_libs = bool(DATA_LIBS & (tops | req_names))
    s.uses_network_libs = bool(NET_LIBS & (tops | req_names))

    body = "\n".join(text_all)
    templates = [t.read_text(errors="replace") for t in root.rglob("*.html") if _inside(root, t)][:50]
    s.accepts_input = bool(_INPUT.search(body) or any(_INPUT.search(t) for t in templates))
    s.writes_outputs = sorted(set(re.findall(r"""\.(html|csv|json|parquet|md|png)['"]""", body)))

    candidates = [root / n for n in ENTRY_NAMES if _inside(root, root / n)]
    entry = candidates[0] if candidates else (mains[0] if mains else (files[0] if files else None))
    if entry is not None:
        s.entrypoint = str(entry.relative_to(root))
        s.excerpt = "\n".join(entry.read_text(errors="replace").splitlines()[:excerpt_lines])
    s.routes = sorted(set(s.routes))[:50]
    api = re.compile(r"^/(api|v\d+|rpc|graphql|health|healthz|metrics)(/|$)")
    s.api_only = s.kind == "web" and bool(s.routes) and all(api.match(r) for r in s.routes)
    return s
