"""Idiomas de la interfaz.

Las plantillas y los mensajes se escriben en español (idioma de origen). Para los demás idiomas hay un catálogo
app/locales/<idioma>.json con pares {"texto en español": "traducción"}; lo que no esté traducido se muestra en español.

Las plantillas NO necesitan marcar los textos: al cargarlas, `I18nLoader` envuelve cada texto visible (y los atributos
placeholder/title/aria-label/alt) en una llamada de traducción. Un texto con valores intercalados, p. ej.
«Hay {{ n }} equipos», se traduce como una sola frase «Hay {0} equipos» para que cada idioma pueda ordenar las palabras.

En Python: `_("texto")` y `_f("Respaldo de {0}", nombre)`. El idioma de cada petición lo fija `LangMiddleware`
(preferencia guardada en la sesión, si no la del navegador, si no la del sistema).
"""
from __future__ import annotations

import json
import re
from contextvars import ContextVar
from pathlib import Path

from jinja2 import FileSystemLoader
from markupsafe import Markup, escape

LANGS = {"es": "Español", "en": "English"}
DEFAULT = "es"
LOCALES = Path(__file__).resolve().parent / "locales"
_lang: ContextVar[str] = ContextVar("lang", default=DEFAULT)
_catalogs: dict[str, dict[str, str]] = {}


def catalog(lang: str) -> dict[str, str]:
    if lang not in _catalogs:
        f = LOCALES / f"{lang}.json"
        _catalogs[lang] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return _catalogs[lang]


def get_lang() -> str:
    return _lang.get()


def set_lang(lang: str):
    return _lang.set(lang if lang in LANGS else DEFAULT)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _(text: str) -> str:
    """Traduce un texto al idioma de la petición actual (o lo deja en español)."""
    lang = _lang.get()
    if lang == DEFAULT or not text:
        return text
    return catalog(lang).get(_norm(text), text)


def N_(text: str) -> str:
    """Marca un texto como traducible sin traducirlo aún (constantes: se traducen al mostrarlas con _())."""
    return text


def tr_pairs(pairs):
    """[(clave, texto)] -> [(clave, texto traducido)] (listas de opciones definidas en Python)."""
    return [(k, _(v)) for k, v in pairs]


def _f(text: str, *args) -> str:
    """Traduce una frase con huecos {0}, {1}… y luego los rellena (texto plano, para Python)."""
    out = _(text)
    for i, a in enumerate(args):
        out = out.replace("{%d}" % i, str(a))
    return out


def _h(text: str, *args) -> Markup:
    """Como _f pero para HTML: el texto de la plantilla es de confianza; los valores se escapan."""
    out = _(text)
    for i, a in enumerate(args):
        out = out.replace("{%d}" % i, str(escape(a)))
    return Markup(out)


def jsq(text) -> Markup:
    """Texto para una cadena JavaScript entre comillas simples dentro de un atributo HTML."""
    s = str(text).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return Markup(escape(s))


def pick_lang(saved: str | None, accept: str | None, default: str = DEFAULT) -> str:
    if saved in LANGS:
        return saved
    for part in (accept or "").split(","):  # «en-US,en;q=0.9,es;q=0.8»: el primero que tengamos
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGS:
            return code
    return default if default in LANGS else DEFAULT


class LangMiddleware:
    """Fija el idioma de cada petición (va dentro de SessionMiddleware para poder leer la sesión)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        accept = headers.get(b"accept-language", b"").decode("latin-1")
        saved = (scope.get("session") or {}).get("lang")
        from .auth import cached_setting
        try:
            default = cached_setting("default_lang", DEFAULT) or DEFAULT
        except Exception:  # noqa: BLE001 - base de datos aún no lista: español
            default = DEFAULT
        token = set_lang(pick_lang(saved, accept, default))
        try:
            await self.app(scope, receive, send)
        finally:
            _lang.reset(token)


# --------------------------------------------------------------------------- plantillas
SKIP_TAGS = ("script", "style", "pre", "textarea", "code")
ATTRS = ("placeholder", "title", "aria-label", "alt")
LETTER = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ¿¡]")
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)


def _lit(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _wrap_run(parts: list[tuple[str, str]]) -> str:
    """parts: [("t", texto) | ("e", expresión jinja sin llaves)]. Devuelve el reemplazo traducible."""
    text = "".join(p for k, p in parts if k == "t")
    if not LETTER.search(re.sub(r"&\w+;", "", text)) or re.fullmatch(r'\s*[\w-]+="[^"]*"\s*', text):
        return "".join(p if k == "t" else "{{" + p + "}}" for k, p in parts)
    raw = "".join(p if k == "t" else "\x00" for k, p in parts)
    lead = raw[:len(raw) - len(raw.lstrip())]
    trail = raw[len(raw.rstrip()):]
    exprs = [p.strip().strip("-") for k, p in parts if k == "e"]
    msg, n = "", 0
    for k, p in parts:
        if k == "t":
            msg += p
        else:
            msg += "{%d}" % n
            n += 1
    msg = _norm(msg)
    args = "".join(f", ({e.strip()})" for e in exprs)
    return f"{lead}{{{{ _h({_lit(msg)}{args}) }}}}{trail}"


def _split_run(text: str) -> list[tuple[str, str]]:
    parts, pos = [], 0
    for m in re.finditer(r"\{\{(.*?)\}\}", text, re.S):
        if m.start() > pos:
            parts.append(("t", text[pos:m.start()]))
        parts.append(("e", m.group(1)))
        pos = m.end()
    if pos < len(text):
        parts.append(("t", text[pos:]))
    return parts


def _attr(m: re.Match) -> str:
    name, val = m.group(1), m.group(2)
    if "{%" in val or not LETTER.search(JINJA.sub("", val)):
        return m.group(0)
    return f'{name}="{_wrap_run(_split_run(val)).strip()}"'


_ATTR_RE = re.compile(r'\b(' + "|".join(ATTRS) + r')="([^"]*)"')
_CONFIRM_RE = re.compile(r"confirm\('((?:[^'\\]|\\.)*)'\)")


def _tag(tag: str) -> str:
    tag = _ATTR_RE.sub(_attr, tag)
    return _CONFIRM_RE.sub(lambda m: "confirm('{{ _(%s)|jsq }}')" % _lit(m.group(1).replace("\\'", "'"))
                           if LETTER.search(m.group(1)) and "{{" not in m.group(1) else m.group(0), tag)


def preprocess(src: str) -> str:
    """Envuelve los textos visibles de una plantilla en llamadas de traducción."""
    out, i, n = [], 0, len(src)
    run: list[str] = []  # texto + {{ }} acumulados hasta la próxima etiqueta o bloque {% %}

    def flush():
        if run:
            out.append(_wrap_run(_split_run("".join(run))))
            run.clear()

    while i < n:
        if src.startswith("{%", i) or src.startswith("{#", i):
            close = "%}" if src[i + 1] == "%" else "#}"
            j = src.index(close, i) + 2
            flush()
            block = src[i:j]
            out.append(block)
            i = j
            if re.match(r"\{%-?\s*raw\b", block):
                j = src.index("endraw", i)
                j = src.index("%}", j) + 2
                out.append(src[i:j])
                i = j
            continue
        if src.startswith("{{", i):
            j = src.index("}}", i) + 2
            run.append(src[i:j])
            i = j
            continue
        if src[i] == "<" and i + 1 < n and (src[i + 1].isalpha() or src[i + 1] in "/!"):
            flush()
            j = i + 1
            while j < n and src[j] != ">":  # saltar {{ }} y valores entre comillas (pueden tener «>»)
                if src.startswith("{{", j) or src.startswith("{%", j):
                    j = src.index("}}" if src[j + 1] == "{" else "%}", j) + 2
                elif src[j] in "\"'" and src[j - 1] == "=":
                    q, j = src[j], j + 1
                    while j < n and src[j] != q:
                        if src.startswith("{{", j) or src.startswith("{%", j):
                            j = src.index("}}" if src[j + 1] == "{" else "%}", j) + 2
                        else:
                            j += 1
                    j += 1
                else:
                    j += 1
            tag = src[i:j + 1]
            out.append(tag if tag.startswith("<!") else _tag(tag))
            i = j + 1
            name = re.match(r"<(\w+)", tag)
            if name and name.group(1).lower() in SKIP_TAGS and not tag.endswith("/>"):
                end = re.compile(r"</%s\s*>" % name.group(1), re.I).search(src, i)
                j = end.start() if end else n
                out.append(src[i:j])
                i = j
            continue
        # texto: hasta el próximo «<», «{{», «{%» o «{#»
        m = re.compile(r"<|\{\{|\{%|\{#").search(src, i)
        j = m.start() if m else n
        if j == i:  # «<» que no abre etiqueta (p. ej. «a < b»)
            j = i + 1
        run.append(src[i:j])
        i = j
    flush()
    return "".join(out)


_MSG_TPL = re.compile(r'\b_[hf]?\(\s*(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\')')
_MSG_PY = re.compile(r'\b(?:_|_f|_h|N_)\(\s*(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\')')


def _unlit(m: re.Match) -> str:
    s = m.group(1) if m.group(1) is not None else m.group(2)
    return s.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")


def extract() -> dict[str, str]:
    """Todos los textos traducibles: {texto: archivo donde aparece}. Lo usan la prueba de cobertura y
    scripts/i18n_missing.py para saber qué falta en cada catálogo."""
    base = Path(__file__).resolve().parent
    found: dict[str, str] = {}
    for f in sorted((base / "templates").glob("*.html")):
        for m in _MSG_TPL.finditer(preprocess(f.read_text(encoding="utf-8"))):
            found.setdefault(_norm(_unlit(m)), f.name)
    for f in sorted(base.glob("*.py")):
        if f.name == "i18n.py":
            continue
        for m in _MSG_PY.finditer(f.read_text(encoding="utf-8")):
            found.setdefault(_norm(_unlit(m)), f.name)
    found.pop("", None)
    return found


class I18nLoader(FileSystemLoader):
    def get_source(self, environment, template):
        src, filename, uptodate = super().get_source(environment, template)
        return preprocess(src), filename, uptodate


def install(env) -> None:
    env.loader = I18nLoader(env.loader.searchpath)
    env.globals.update(_=_, _h=_h, _f=_f, LANGS=LANGS, get_lang=get_lang)
    env.filters["jsq"] = jsq
    env.filters["tr_pairs"] = tr_pairs

