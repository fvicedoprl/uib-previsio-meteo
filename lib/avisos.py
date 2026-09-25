# -*- coding: utf-8 -*-
"""Consulta d'avisos d'AEMET per al Protocol FMA de la UIB.

Cadena de consulta: proxy de la UIB (Cloudflare Worker) -> API d'AEMET amb clau ->
mode manual. La logica del TAR i de la codificacio s'importa d'aemet_monitor.py
(rastrejador ja provat en produccio), no es reescriu.

Diferencies respecte del rastrejador:
  - Classifica la zona pel GEOCODE oficial (AEMET-Meteoalerta zona), no pel text
    de l'areaDesc. Evita col.lapsar Interior i Llevant dins "Mallorca Nord".
  - Distingeix zones terrestres de costaneres (sufix C).
  - Llegeix el nivell GROC (context d'escalada) i el valor quantitatiu del
    parametre, que AEMET ja dona dins l'avis.
  - Reporta els fenomens que el Protocol FMA no cobreix en comptes de descartar-los.
"""
from __future__ import annotations
import argparse, importlib.util, json, os, re, sys, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fma_base import DADES, FENOMEN_AEMET, NIVELL_AEMET, MONITOR

PROXY_UIB = os.environ.get("FMA_PROXY_URL", "https://aemet-proxy-uib.prevencio-uib.workers.dev")
AEMET_API = "https://opendata.aemet.es/opendata/api"
AEMET_AVISOS = AEMET_API + "/avisos_cap/ultimoelaborado/area/64"
NS = "{urn:oasis:names:tc:emergency:cap:1.2}"
TIMEOUT = 30


def _log(msg):
    print(msg, file=sys.stderr)


# -- Font 1: proxy de la UIB -------------------------------------------------
USER_AGENT = "UIB-SPREV-ProtocolFMA/1.0 (gestio-episodi-fma)"


def _obre(url, headers=None):
    """urlopen amb User-Agent propi: Cloudflare rebutja el UA per defecte de Python."""
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=TIMEOUT)


def _des_del_proxy():
    try:
        with _obre(PROXY_UIB) as r:
            xml = r.read().decode("utf-8", "replace")
        if "<alert" in xml:
            _log("[font] proxy UIB (%d bytes)" % len(xml))
            return xml
        _log("[font] el proxy respon pero no retorna CAP")
    except Exception as e:
        _log("[font] proxy UIB no disponible: %s" % e)
    return None


# -- Font 2: AEMET directa (reutilitza la logica del rastrejador) ------------
def _carrega_monitor():
    ruta = MONITOR / "aemet_monitor.py"
    if not ruta.exists():
        return None
    spec = importlib.util.spec_from_file_location("aemet_monitor", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _des_daemet():
    clau = os.environ.get("AEMET_API_KEY")
    if not clau:
        _log("[font] AEMET directa: falta AEMET_API_KEY a l'entorn")
        return None
    mod = _carrega_monitor()
    if mod is None:
        _log("[font] AEMET directa: no es troba aemet_monitor.py")
        return None
    try:
        with _obre(AEMET_AVISOS + "?api_key=" + clau,
                   {"Accept": "application/json"}) as r:
            meta = json.loads(r.read().decode("utf-8", "replace"))
        if meta.get("estado") == 404:
            _log("[font] AEMET: no hi ha avisos vigents")
            return '<?xml version="1.0"?><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"/>'
        with _obre(meta["datos"]) as r:
            cru = r.read()
        # extract_cap_files resol el TAR i la codificacio ISO-8859-15
        blocs = mod.extract_cap_files(cru)
        infos = []
        for x in blocs:
            infos += re.findall(r"<info>.*?</info>", x, re.S)
        _log("[font] AEMET directa (%d blocs info)" % len(infos))
        return ('<?xml version="1.0" encoding="UTF-8"?>'
                '<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">'
                + "".join(infos) + "</alert>")
    except Exception as e:
        _log("[font] AEMET directa ha fallat: %s" % e)
    return None


def _fatal():
    raise SystemExit(
        "ERROR: cap font d'avisos disponible.\n"
        "  - El proxy de la UIB no respon i no hi ha AEMET_API_KEY a l'entorn.\n"
        "  - Alternativa: consulta la pagina d'avisos d'AEMET per a les Illes Balears\n"
        "    i passa les dades a ma amb episodi.py --simula.")


def descarrega_cap(fitxer=None):
    if fitxer:
        _log("[font] fitxer local %s" % fitxer)
        return open(fitxer, encoding="utf-8").read()
    return _des_del_proxy() or _des_daemet() or _fatal()


# -- Parseig -----------------------------------------------------------------
def _param(info, sub):
    for p in info.findall(NS + "parameter"):
        if sub.lower() in (p.findtext(NS + "valueName") or "").lower():
            return (p.findtext(NS + "value") or "").strip()
    return ""


_NUM = re.compile(r"(-?\d+(?:[.,]\d+)?)")


def _valor_parametre(brut):
    """'P1;Precipitacion acumulada en una hora;50 mm' -> codi/descripcio/valor."""
    parts = [p.strip() for p in (brut or "").split(";")]
    codi = parts[0] if parts else ""
    desc = parts[1] if len(parts) > 1 else ""
    valor = parts[2] if len(parts) > 2 else ""
    num = unitat = None
    if valor:
        m = _NUM.search(valor)
        if m:
            num = float(m.group(1).replace(",", "."))
            unitat = valor[m.end():].strip() or None
    return {"codi": codi, "descripcio": desc, "valor": valor or None,
            "num": num, "unitat": unitat, "brut": brut}


def parse_avisos(xml):
    """Retorna (avisos coberts pel protocol, avisos de fenomens no coberts)."""
    root = ET.fromstring(xml)
    coberts, altres = [], []
    for info in root.findall(NS + "info"):
        if not (info.findtext(NS + "language") or "es").lower().startswith("es"):
            continue
        niv_es = (_param(info, "nivel") or "").lower()
        nivell = NIVELL_AEMET.get(niv_es)
        if nivell is None:          # verd o sense nivell
            continue
        codi_fen = (info.findtext(NS + "eventCode/" + NS + "value") or "").split(";")[0].strip()
        fenomen = FENOMEN_AEMET.get(codi_fen, "?")
        param = _valor_parametre(_param(info, "parametro"))
        base = {
            "fenomen": fenomen, "codi_aemet": codi_fen, "nivell": nivell,
            "event": info.findtext(NS + "event") or "",
            "descripcio": info.findtext(NS + "description") or "",
            "probabilitat": _param(info, "probabilidad") or "-",
            "onset": info.findtext(NS + "onset") or "",
            "expires": info.findtext(NS + "expires") or "",
            "parametre": param,
        }
        for area in info.findall(NS + "area"):
            zona_codi = ""
            for gc in area.findall(NS + "geocode"):
                if "zona" in (gc.findtext(NS + "valueName") or "").lower():
                    zona_codi = (gc.findtext(NS + "value") or "").strip()
            z = DADES.zona_per_codi(zona_codi)
            av = dict(base)
            av.update({
                "zona_codi": zona_codi,
                "zona_desc": area.findtext(NS + "areaDesc") or "",
                "zona_nom": z["nom_ca"] if z else None,
                "zona_tipus": z["tipus"] if z else None,
                "seus": list(z["seus"]) if z else [],
                "zona_desconeguda": z is None,
            })
            if fenomen not in (None, "?"):
                coberts.append(av)
            else:
                altres.append(av)
    return coberts, altres


# -- Comparacio amb els llindars del protocol --------------------------------
def _norm(t):
    return (t or "").replace("º", "°").lower()


def _clausula(text, param):
    """Tria la clausula del llindar que correspon al parametre d'AEMET.

    Els llindars de pluja porten dues finestres dins la mateixa frase
    ('30-40 mm en 1 hora o de 80-100 mm en 12 hores'): AEMET diu quina toca
    amb el codi del parametre (P1 = 1 hora, P2 = 12 hores).
    """
    trossos = [t for t in re.split(r"\s+o\s+", text) if t.strip()]
    desc = _norm(param.get("descripcio"))
    # Marques de finestra temporal. '/h' compta com a finestra d'1 hora:
    # el llindar de transicio esta escrit com a 'mm/h', no com a 'en 1 hora'.
    finestra = None
    if "12 hor" in desc:
        finestra = [r"12\s*hor"]
    elif "una hora" in desc or "1 hora" in desc:
        finestra = [r"1\s*hora", r"una\s*hora", r"/\s*h\b"]
    if finestra:
        amb = [t for t in trossos if any(re.search(p, _norm(t)) for p in finestra)]
        if amb:
            return " o ".join(amb)
        # Cap clausula no parla d'aquesta finestra: el protocol no hi te llindar.
        # Millor no comparar que comparar contra una finestra diferent.
        return None
    if len(trossos) < 2:
        return text
    u = _norm(param.get("unitat"))
    ambu = [t for t in trossos if u and u in _norm(t)]
    if len(ambu) == 1:
        return ambu[0]
    return " o ".join(ambu) if ambu else trossos[0]


def _nums_amb_unitat(text, unitat):
    """Numeros que van SEGUITS de la unitat.

    Evita capturar valors que no hi tenen res a veure: a 'Vent de forca 8
    (mes de 62 km/h)' nomes ha de sortir 62, no el 8 de l'escala Beaufort.
    Tambe distingeix el guio de rang ('15-20 mm' -> 15 i 20) del signe
    negatiu real ('entre -8 °C i -14 °C' -> -8 i -14).
    """
    u = re.escape(unitat)
    grup = r"(-?\d+(?:[.,]\d+)?(?:\s*(?:-|a|i)\s*-?\d+(?:[.,]\d+)?)*)"
    out = []
    for m in re.finditer(grup + r"\s*" + u, _norm(text), re.I):
        g = m.group(1)
        for mm in re.finditer(r"-?\d+(?:[.,]\d+)?", g):
            s = mm.group(0)
            if s.startswith("-") and mm.start() > 0 and g[mm.start() - 1].isdigit():
                s = s[1:]          # era un guio de rang, no un signe
            out.append(float(s.replace(",", ".")))
    return out


def compara_llindars(fenomen, param):
    """Situa el valor d'AEMET dins l'escala de llindars.json.

    Es NOMES lectura de dades d'AEMET contra els llindars del protocol.
    No es cap prediccio meteorologica propia.
    """
    esc = (DADES.llindars.get("llindars", {}) or {}).get(fenomen, {})
    textos = {}
    for n in ("groc", "taronja", "transicio", "vermell"):
        textos[n] = (esc.get(n, {}) or {}).get("text", "")
    res = {"valor": param.get("valor"), "unitat": param.get("unitat"),
           "descripcio": param.get("descripcio"), "textos": textos,
           "banda": None, "nota": None, "rangs": {}}
    num = param.get("num")
    unitat = (param.get("unitat") or "").lower()
    if num is None or not unitat:
        res["nota"] = "L'avis no porta valor numeric: comparacio qualitativa (vegeu els textos)."
        return res
    unitat = _norm(unitat)
    rangs, sense_finestra = {}, []
    for niv, txt in textos.items():
        if not txt:
            continue
        cl = _clausula(txt, param)
        if cl is None:
            sense_finestra.append(niv)
            continue
        nums = _nums_amb_unitat(cl, unitat)
        if nums:
            rangs[niv] = (min(nums), max(nums))
    res["sense_finestra"] = sense_finestra
    if not rangs:
        res["nota"] = "No s'ha pogut aillar cap llindar en %s: comparacio qualitativa." % unitat
        return res
    res["rangs"] = rangs
    # Els llindars poden ser negatius (fred): 'mes greu' vol dir mes lluny de zero.
    invers = num < 0 and all(a < 0 for a, _ in rangs.values())
    ordre = ("vermell", "transicio", "taronja", "groc")
    notes = []

    def _dins(r):
        return r[0] <= num <= r[1]

    conte = [n for n in ordre if n in rangs and _dins(rangs[n])]
    if conte:
        banda = conte[0]
        if len(conte) > 1:
            notes.append("Llindars solapats al protocol: %g %s encaixa alhora a %s. "
                         "S'agafa el mes greu, pero la decisio del nivell es del Gabinet."
                         % (num, unitat, " i ".join(c.upper() for c in conte)))
    else:
        banda = None
        for n in ordre:
            if n not in rangs:
                continue
            # En escala inversa (fred) l'extrem greu es el minim, no el maxim.
            extrem = rangs[n][0] if invers else rangs[n][1]
            if (num < extrem) if invers else (num > extrem):
                banda = n
                notes.append("El valor supera l'extrem del llindar %s (%g %s): vigilar l'escalada."
                             % (n.upper(), extrem, unitat))
                break
        if banda is None:
            notes.append("Per sota del llindar mes baix del protocol per a aquest fenomen.")
    res["banda"] = banda
    res["solapats"] = conte if len(conte) > 1 else []
    if banda and re.search(r"persist|dies seguits|dies consecutius", _norm(textos.get(banda, ""))):
        notes.append("Aquest llindar tambe depen de la PERSISTENCIA (nombre de dies), "
                     "que un valor puntual no resol.")
    if sense_finestra:
        notes.append("El protocol no te llindar de %s per a aquesta finestra temporal (%s): "
                     "no s'hi compara." % (", ".join(sense_finestra), param.get("descripcio") or "-"))
    res["nota"] = " ".join(notes) or None
    return res


# -- Prediccio horaria municipal ---------------------------------------------
def prediccio_horaria(municipi):
    clau = os.environ.get("AEMET_API_KEY")
    if not clau:
        _log("[prediccio] cal AEMET_API_KEY a l'entorn")
        return None
    url = (AEMET_API + "/prediccion/especifica/municipio/horaria/"
           + municipi + "?api_key=" + clau)
    try:
        with _obre(url) as r:
            meta = json.loads(r.read().decode("utf-8", "replace"))
        with _obre(meta["datos"]) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        _log("[prediccio] ha fallat: %s" % e)
        return None


# -- CLI ---------------------------------------------------------------------
def _resum(coberts, altres):
    ordre = {"vermell": 0, "taronja": 1, "groc": 2}
    print("\nAVISOS VIGENTS (%s) - %d coberts pel Protocol FMA, %d no coberts\n"
          % (datetime.now().strftime("%d/%m/%Y %H:%M"), len(coberts), len(altres)))
    for a in sorted(coberts, key=lambda x: (ordre.get(x["nivell"], 9), x["zona_nom"] or "")):
        seus = ", ".join(a["seus"]) or "-"
        val = a["parametre"].get("valor") or "-"
        print("  %-8s %-18s %-32s %-10s %s -> %s  seus: %s"
              % (a["nivell"].upper(), a["fenomen"], a["zona_nom"] or a["zona_desc"], val,
                 a["onset"][:16].replace("T", " "), a["expires"][:16].replace("T", " "), seus))
    desc = [a for a in coberts if a["zona_desconeguda"]]
    if desc:
        noms = ", ".join(sorted(set(a["zona_desc"] for a in desc)))
        print("\n  ATENCIO: %d avisos amb zona NO reconeguda (%s)." % (len(desc), noms))
        print("  No s'assigna cap zona per defecte. Cal afegir-la a playbook_work/zones_aemet.json.")
    if altres:
        print("\n  Fenomens FORA del Protocol FMA (%d):" % len(altres))
        vist = set()
        for a in altres:
            k = (a["codi_aemet"], a["event"], a["zona_desc"], a["nivell"])
            if k in vist:
                continue
            vist.add(k)
            print("    [%s] %-8s %s - %s" % (k[0], k[3].upper(), k[1], k[2]))
        print("  El protocol no els cobreix: decisio del Servei de Prevencio cas per cas.")


def main():
    ap = argparse.ArgumentParser(description="Consulta d'avisos AEMET per al Protocol FMA")
    ap.add_argument("--dryrun", action="store_true", help="mostra els avisos vigents")
    ap.add_argument("--zones", action="store_true", help="bolca les zones distintes del CAP viu")
    ap.add_argument("--json", action="store_true", help="surt en JSON (entrada per a episodi.py)")
    ap.add_argument("--prediccio", metavar="MUNICIPI", help="prediccio horaria (p.ex. 07040)")
    ap.add_argument("--fitxer", help="llegeix un CAP local en comptes de la xarxa")
    ap.add_argument("--desa", metavar="RUTA", help="desa el CAP cru descarregat")
    args = ap.parse_args()

    if args.prediccio:
        d = prediccio_horaria(args.prediccio)
        print(json.dumps(d, ensure_ascii=False, indent=1)[:4000] if d else "sense dades")
        return

    xml = descarrega_cap(args.fitxer)
    if args.desa:
        with open(args.desa, "w", encoding="utf-8") as fh:
            fh.write(xml)
        _log("[desat] %s" % args.desa)
    coberts, altres = parse_avisos(xml)

    if args.zones:
        root = ET.fromstring(xml)
        vist = {}
        for info in root.findall(NS + "info"):
            for area in info.findall(NS + "area"):
                c = ""
                for gc in area.findall(NS + "geocode"):
                    if "zona" in (gc.findtext(NS + "valueName") or "").lower():
                        c = gc.findtext(NS + "value") or ""
                vist[c] = area.findtext(NS + "areaDesc")
        print("%d zones al CAP viu:" % len(vist))
        for c, d in sorted(vist.items()):
            z = DADES.zona_per_codi(c)
            estat = ("OK  " + z["nom_ca"]) if z else "*** NO ES AL CATALEG ***"
            print("  %-9s %-38s %s" % (c, d, estat))
        return

    if args.json:
        print(json.dumps({"avisos": coberts, "no_coberts": altres,
                          "generat": datetime.now(timezone.utc).isoformat()},
                         ensure_ascii=False, indent=1))
        return

    _resum(coberts, altres)


if __name__ == "__main__":
    main()
