# -*- coding: utf-8 -*-
"""Markdown -> HTML per als documents del Protocol FMA.

Cobreix nomes el que generen els briefings: encapcalaments, taules, llistes,
cites, negreta, codi en bloc i en linia, i separadors. No es un convertidor
general i no ho pretén.

El resultat es un document autonom: sense CDN, sense JavaScript i preparat per
imprimir en A4. Ha de poder arribar per correu, obrir-se des del mobil o
projectar-se a la sala sense dependre de res.
"""
from __future__ import annotations
import html as _h
import re

CSS = """
:root{--blau:#003E72;--blau2:#0071BC;--tinta:#1a1a1a;--vora:#d8dee6;--fons:#f4f6f9;
      --paper:#fff;--groc:#FFC72C;--taronja:#FF8200;--vermell:#E8392A;--suau:#5a6c82}
*{box-sizing:border-box}
body{margin:0;background:var(--fons);color:var(--tinta);
     font:16px/1.65 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.full{max-width:900px;margin:0 auto;padding:32px 40px;background:var(--paper);
      min-height:100vh;box-shadow:0 0 24px rgba(0,0,0,.06)}
h1{font-size:27px;color:var(--blau);margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:20px;color:var(--blau);margin:34px 0 12px;padding-bottom:7px;
   border-bottom:2px solid var(--blau2)}
h3{font-size:16px;color:var(--blau);margin:22px 0 8px}
h4{font-size:15px;color:var(--blau);margin:18px 0 6px}
p{margin:9px 0}
table{width:100%;border-collapse:collapse;font-size:14px;margin:12px 0}
th{background:#eef2f7;color:#44546a;font-size:12px;text-transform:uppercase;
   letter-spacing:.04em;text-align:left;padding:8px 10px;border:1px solid var(--vora)}
td{padding:8px 10px;border:1px solid var(--vora);vertical-align:top}
tr:nth-child(even) td{background:#fafbfd}
.scroll{overflow-x:auto}
ul{margin:8px 0;padding-left:22px}
li{margin:4px 0}
li ul{margin:3px 0}
blockquote{border-left:4px solid var(--groc);background:#fff8e6;margin:12px 0;
           padding:10px 16px;border-radius:0 8px 8px 0}
blockquote p{margin:4px 0}
blockquote.greu{border-left-color:var(--vermell);background:#fdecea}
code{background:#eef2f7;padding:1px 6px;border-radius:4px;font-size:13px;
     color:var(--blau);font-family:ui-monospace,Consolas,monospace}
pre{background:#f7f9fc;border:1px solid var(--vora);border-radius:8px;padding:16px;
    white-space:pre-wrap;font:14px/1.6 inherit;margin:12px 0}
hr{border:0;border-top:1px solid var(--vora);margin:26px 0}
em{color:var(--suau)}
.peu{margin-top:26px;padding-top:14px;border-top:1px solid var(--vora);
     font-size:13px;color:var(--suau)}
@media print{
  body{background:#fff}
  .full{max-width:none;padding:0;box-shadow:none}
  h2{break-after:avoid}
  table,blockquote,pre{break-inside:avoid}
  a{color:inherit;text-decoration:none}
}
@media (max-width:640px){ .full{padding:18px} }
"""

_NEGRETA = re.compile(r"\*\*(.+?)\*\*")
_CURSIVA = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
# Cursiva amb guio baix. Els limits de paraula eviten trencar noms com
# bd_checklist.json o els codis de mesura quan no van dins d'un bloc de codi.
_CURSIVA_ = re.compile(r"(?<![\w_])_(?!\s|_)(.+?)(?<!\s|_)_(?![\w_])")
_CODI = re.compile(r"`([^`]+)`")


def _inline(t):
    """Escapa i aplica el format de linia. L'ordre importa: primer el codi."""
    t = _h.escape(t)
    marques = {}

    def _guarda(m):
        clau = "\x00%d\x00" % len(marques)
        marques[clau] = "<code>%s</code>" % m.group(1)
        return clau

    t = _CODI.sub(_guarda, t)
    t = _NEGRETA.sub(r"<strong>\1</strong>", t)
    t = _CURSIVA.sub(r"<em>\1</em>", t)
    t = _CURSIVA_.sub(r"<em>\1</em>", t)
    for clau, val in marques.items():
        t = t.replace(clau, val)
    return t


def _tanca(pila, out):
    while pila:
        out.append("</%s>" % pila.pop())


def _tanca_fins(pila, out, etiqueta):
    """Tanca fins a haver tancat una ocurrencia de l'etiqueta indicada."""
    while pila:
        t = pila.pop()
        out.append("</%s>" % t)
        if t == etiqueta:
            return


def converteix(md):
    """Cos HTML del document (sense <html> ni <head>)."""
    out, pila = [], []          # pila: etiquetes obertes (ul, blockquote)
    taula, dins_codi = [], False

    def buida_taula():
        if not taula:
            return
        out.append('<div class="scroll"><table>')
        capcalera = True
        for i, cel in enumerate(taula):
            if all(set(c.strip()) <= set("-: ") for c in cel) and i == 1:
                capcalera = False
                continue
            et = "th" if (i == 0 and len(taula) > 1) else "td"
            out.append("<tr>%s</tr>" % "".join("<%s>%s</%s>" % (et, _inline(c), et) for c in cel))
        out.append("</table></div>")
        taula.clear()

    for lin in md.splitlines():
        if lin.startswith("```"):
            buida_taula(); _tanca(pila, out)
            out.append("</pre>" if dins_codi else "<pre>")
            dins_codi = not dins_codi
            continue
        if dins_codi:
            out.append(_h.escape(lin))
            continue

        if lin.startswith("|"):
            _tanca(pila, out)
            taula.append([c.strip() for c in lin.strip().strip("|").split("|")])
            continue
        buida_taula()

        if not lin.strip():
            _tanca(pila, out)
            continue

        if lin.startswith("---") and set(lin.strip()) <= set("-"):
            _tanca(pila, out); out.append("<hr>"); continue

        m = re.match(r"^(#{1,4})\s+(.*)$", lin)
        if m:
            _tanca(pila, out)
            n = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (n, _inline(m.group(2)), n))
            continue

        if lin.startswith("> "):
            cos = lin[2:]
            if "blockquote" not in pila:
                _tanca(pila, out)
                greu = any(k in cos for k in ("ATENCIÓ", "Atenció", "NO ENVIAR", "PENDENT"))
                out.append('<blockquote class="greu">' if greu else "<blockquote>")
                pila.append("blockquote")
            out.append("<p>%s</p>" % _inline(cos))
            continue

        m = re.match(r"^(\s*)-\s+(.*)$", lin)
        if m:
            nivell = 2 if len(m.group(1)) >= 3 else 1
            if "blockquote" in pila:
                _tanca(pila, out)
            # Per baixar de nivell, el <li> del pare ha de quedar OBERT: la
            # llista niada va a dins seu, no com a germana (si no, es HTML invalid).
            while pila.count("ul") > nivell:
                _tanca_fins(pila, out, "ul")
            if pila.count("ul") == nivell and pila and pila[-1] == "li":
                out.append("</li>"); pila.pop()
            while pila.count("ul") < nivell:
                out.append("<ul>"); pila.append("ul")
            out.append("<li>%s" % _inline(m.group(2)))
            pila.append("li")
            continue

        _tanca(pila, out)
        out.append("<p>%s</p>" % _inline(lin))

    buida_taula()
    _tanca(pila, out)
    if dins_codi:
        out.append("</pre>")
    return "\n".join(out)


def document(md, titol="Briefing FMA"):
    """Document HTML complet i autonom."""
    return ("<!doctype html><html lang=ca><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s</style></head><body>"
            '<div class="full">%s</div></body></html>'
            % (_h.escape(titol), CSS, converteix(md)))
