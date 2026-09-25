# -*- coding: utf-8 -*-
"""Previsio meteorologica per a les seus de la UIB — per enviar a qualsevol persona.

Quan AEMET te un avis de nivell taronja o superior damunt alguna zona amb seu de la
UIB, elabora un butlleti meteorologic per seu (Palma, Menorca, Eivissa) i el torna a
enviar cada vegada que la situacio canvia, amb un batec de «sense canvis» si passen
hores sense novetats i un missatge final quan els avisos s'acaben.

QUE ES, I QUE NO ES:

  - Es una SINTESI DE FONTS PUBLIQUES, no una prediccio propia. El cos es la
    prediccio horaria municipal d'AEMET, que en permet la reproduccio citant-la
    com a autora (nota legal que acompanya les dades). Cap xifra del document es
    nostra: totes porten la font.
  - NOMES TEMPS. Aquest document s'envia a qualsevol persona i per aixo no conte
    res del Protocol FMA: ni mesures, ni Gabinet, ni nivells interns, ni criteris de
    suspensio, ni decisions sobre l'activitat. No importa bd_checklist, llindars,
    criteri, registre, episodi ni briefing. `comprova_to()` ho vigila.
  - SENSE IA. El text surt de regles fixes: es reproduible i tracable.

Fonts: avisos CAP d'AEMET (proxy de la UIB o OpenData directa), prediccio horaria
municipal d'AEMET, parametres convectius d'Open-Meteo (context), mapes TRAM del Grup
de Meteorologia de la UIB i radar i llamps d'AEMET (adjunts), i l'aportacio del Grup
de Meteorologia si n'hi ha una de recent a `meteouib.md` (citada literal).

Cal AEMET_API_KEY a l'entorn. Per enviar: EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO
(separats per comes; s'envia en copia oculta), EMAIL_SMTP_HOST i EMAIL_SMTP_PORT.

Us:
    python previsio.py --dryrun              # genera i desa, no envia ni toca l'estat
    python previsio.py --dryrun --forca      # el genera encara que no hi hagi avis
    python previsio.py                       # mode normal (el que fa el workflow)
"""
from __future__ import annotations
import argparse, hashlib, io, json, os, re, smtplib, sys, tarfile, time, urllib.error, urllib.request
from datetime import date, datetime, timedelta
from datetime import time as dtime
from email.message import EmailMessage
from http.client import RemoteDisconnected
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fma_base import PROJECT_DIR   # noqa: E402
import avisos as A                  # noqa: E402
import vigilancia as V              # noqa: E402
import md_html                      # noqa: E402

UA = "UIB-SPREV-Previsio/1.0 (previsio meteorologica)"
AEMET_API = A.AEMET_API
URL_AVISOS = "https://www.aemet.es/ca/eltiempo/prediccion/avisos"

# Les seus, agrupades per municipi d'AEMET. Els codis coincideixen amb
# assets/seus.json (comprovat el 24/09/2026). `punt` es la clau de vigilancia.py.
GRUPS = [
    {"id": "palma", "nom": "Palma", "detall": "campus, Sa Riera, Ca n'Oleo i ParcBit",
     "municipi": "07040", "municipi_nom": "Palma", "punt": "campus_palma",
     "seus": {"campus_palma", "sa_riera", "ca_n_oleo", "parcbit"}},
    {"id": "menorca", "nom": "Menorca", "detall": "seu d'Alaior",
     "municipi": "07002", "municipi_nom": "Alaior", "punt": "seu_menorca",
     "seus": {"seu_menorca"}},
    {"id": "eivissa", "nom": "Eivissa", "detall": "seu d'Eivissa i Formentera",
     "municipi": "07026", "municipi_nom": "Eivissa", "punt": "seu_eivissa",
     "seus": {"seu_eivissa"}},
]

CONFIG_DEFECTE = {
    "llindar": "taronja",          # groc | taronja | vermell
    "batec_hores": 6,              # sense canvis durant tantes hores -> s'envia igualment
    "antirebot_minuts": 90,        # interval minim entre dues emissions (llevat d'escalada)
    "horari_diurn": [8, 21],       # el batec nomes s'envia dins aquesta franja
    "horitzo_hores": 48,           # quantes hores de previsio municipal es mostren
    "meteouib_hores": 24,          # antiguitat maxima de l'aportacio del Grup
    "adjunta_radar": True,
    "adjunta_llamps": True,
    "mapes_tram": [["sr", "WIND_3hPRECIP", 12], ["sr", "WIND_3hPRECIP", 24],
                   ["sr", "WIND_3hPRECIP", 36], ["hr", "TOTAL_PRECIP", 72]],
}

NIV = {"groc": 1, "taronja": 2, "vermell": 3}
EMOJI = {"groc": "🟡", "taronja": "🟠", "vermell": "🔴"}
DIES = ["dilluns", "dimarts", "dimecres", "dijous", "divendres", "dissabte", "diumenge"]

# Fenomens pel codi d'AEMET (eventCode), no per l'etiqueta del protocol.
FENOMEN = {"PR": "pluges", "TO": "tempestes", "VI": "vent", "AT": "temperatures màximes",
           "BT": "temperatures mínimes", "NE": "neu", "CO": "fenòmens costaners",
           "NI": "boira", "VS": "pols en suspensió", "RI": "rissagues"}

# Estat del cel d'AEMET (codi numeric; la 'n' final es nomes 'de nit').
CEL = {
    11: "Serè", 12: "Poc ennuvolat", 13: "Intervals de núvols", 14: "Ennuvolat",
    15: "Molt ennuvolat", 16: "Cobert", 17: "Núvols alts",
    23: "Intervals de núvols amb pluja", 24: "Ennuvolat amb pluja",
    25: "Molt ennuvolat amb pluja", 26: "Cobert amb pluja",
    33: "Intervals de núvols amb neu", 34: "Ennuvolat amb neu",
    35: "Molt ennuvolat amb neu", 36: "Cobert amb neu",
    43: "Intervals de núvols amb pluja escassa", 44: "Ennuvolat amb pluja escassa",
    45: "Molt ennuvolat amb pluja escassa", 46: "Cobert amb pluja escassa",
    51: "Intervals de núvols amb tempesta", 52: "Ennuvolat amb tempesta",
    53: "Molt ennuvolat amb tempesta", 54: "Cobert amb tempesta",
    61: "Intervals de núvols amb tempesta i pluja escassa",
    62: "Ennuvolat amb tempesta i pluja escassa",
    63: "Molt ennuvolat amb tempesta i pluja escassa",
    64: "Cobert amb tempesta i pluja escassa",
    71: "Intervals de núvols amb neu escassa", 72: "Ennuvolat amb neu escassa",
    73: "Molt ennuvolat amb neu escassa", 74: "Cobert amb neu escassa",
    81: "Boira", 82: "Boirina", 83: "Calitja",
}
# Quin estat del cel representa millor una franja de 6 h: el mes significatiu.
_FAMILIA = {5: 9, 6: 8, 2: 7, 3: 6, 7: 5, 4: 4, 8: 3, 1: 0}
_NUVOLS = {11: 1, 12: 2, 17: 3, 13: 4, 14: 5, 15: 6, 16: 7}

MAPA_DESC = {
    "WIND_3hPRECIP": "vent i precipitació en 3 hores",
    "TOTAL_PRECIP": "precipitació total acumulada",
}

# Paraules que no poden apareixer al text generat. Si n'hi apareix alguna, hi ha
# un error de plantilla: aquest document es nomes meteorologic.
PROHIBIDES = [r"\bmesur", r"\bgabinet", r"\bprotocol", r"\bsuspe", r"\btransici",
              r"\bevacu", r"\brecoman", r"\bheu de\b", r"\bha de\b", r"\bcal que\b",
              r"\bevit", r"\bprecauci", r"\bautoprotecci", r"\bconfin", r"\bprealert",
              r"\bcriteri", r"\bdocència", r"\bactivitat"]


def log(m):
    print(m, file=sys.stderr, flush=True)


# ── Xarxa ────────────────────────────────────────────────────────────────────
def _get(url, headers=None, intents=4):
    """GET amb reintents. AEMET retorna 429 si la clau fa massa peticions per minut
    (el rastrejador fa servir la mateixa), i talla connexions de tant en tant."""
    h = {"User-Agent": UA, **(headers or {})}
    for i in range(intents):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < intents - 1:
                log("  [xarxa] 429 d'AEMET, espero %d s" % (15 * (i + 1)))
                time.sleep(15 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, RemoteDisconnected, TimeoutError, ConnectionError):
            if i < intents - 1:
                time.sleep(3)
                continue
            raise


def _text(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("iso-8859-15", "replace")


def _aemet(ruta):
    """Crida en dos passos d'OpenData. Retorna (estat, bytes de 'datos' o None)."""
    clau = os.environ.get("AEMET_API_KEY")
    if not clau:
        raise RuntimeError("falta AEMET_API_KEY a l'entorn")
    meta = json.loads(_text(_get("%s/%s" % (AEMET_API, ruta), {"api_key": clau})))
    estat = meta.get("estado")
    if estat != 200 or not meta.get("datos"):
        return estat, None
    return estat, _get(meta["datos"])


# ── Avisos ───────────────────────────────────────────────────────────────────
def _cap_directe():
    """AEMET directa, sense dependre del rastrejador. La URL 'datos' serveix un TAR
    amb un XML CAP per avis (mateixa logica que aemet_monitor.extract_cap_files)."""
    estat, raw = _aemet("avisos_cap/ultimoelaborado/area/64")
    if estat == 404:
        return '<?xml version="1.0"?><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"/>'
    if raw is None:
        return None
    blocs = []
    if len(raw) > 262 and raw[257:262] == b"ustar":
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
            for m in tar.getmembers():
                f = tar.extractfile(m) if m.isfile() else None
                if f is not None:
                    blocs.append(_text(f.read()))
    else:
        blocs.append(_text(raw))
    infos = []
    for x in blocs:
        infos += re.findall(r"<info>.*?</info>", x, re.S)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">' + "".join(infos) + "</alert>")


def obte_avisos(fitxer=None):
    """Tots els avisos d'AEMET a Balears, coberts o no pel protocol: aqui interessa
    el temps, i una rissaga o una boira tambe ho son. Retorna (avisos, font)."""
    if fitxer:
        xml, font = open(fitxer, encoding="utf-8").read(), "fitxer de prova %s" % Path(fitxer).name
    else:
        xml, font = A._des_del_proxy(), "AEMET (via el proxy de la UIB)"
        if not xml:
            try:
                xml, font = _cap_directe(), "AEMET OpenData"
            except Exception as e:
                log("  [avisos] AEMET directa ha fallat: %s" % e)
                xml = None
    if not xml:
        raise SystemExit("ERROR: no s'han pogut consultar els avisos d'AEMET. No es toca l'estat.")
    coberts, altres = A.parse_avisos(xml)
    vist, out = set(), []
    for a in coberts + altres:
        k = (a["nivell"], a["codi_aemet"], a["zona_codi"], a["onset"], a["expires"])
        if k not in vist:
            vist.add(k)
            out.append(a)
    return out, font


def _local(iso):
    """ISO amb zona horaria -> hora local sense zona (la del sistema; al workflow,
    TZ=Europe/Madrid)."""
    if not iso:
        return None
    d = datetime.fromisoformat(iso)
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d


def vigents(avisos, ara):
    return [a for a in avisos if (_local(a["expires"]) or ara) > ara]


def del_grup(avisos, g):
    return [a for a in avisos if g["seus"] & set(a.get("seus") or [])]


def porta_oberta(avisos, llindar):
    return [a for a in avisos if a.get("seus") and NIV.get(a["nivell"], 0) >= NIV[llindar]]


def nivell_max(avisos):
    return max((a["nivell"] for a in avisos), key=lambda n: NIV.get(n, 0), default=None)


# ── Format ───────────────────────────────────────────────────────────────────
def _n(x, dec=0):
    if x is None:
        return "—"
    s = "{:,.{}f}".format(abs(x), dec).replace(",", "X").replace(".", ",").replace("X", ".")
    return ("−" if x < 0 else "") + s


def _dia(d, ara=None, relatiu=True):
    txt = "%s %d" % (DIES[d.weekday()], d.day)
    if relatiu and ara is not None:
        pref = {0: "avui ", 1: "demà "}.get((d - ara.date()).days, "")
        txt = pref + txt
    return txt


def _finestra_avis(a, ara):
    ini, fi = _local(a["onset"]), _local(a["expires"])
    if not ini or not fi:
        return "franja no indicada"
    return _finestra(ini, fi, ara)


def _finestra(ini, fi, ara):
    if ini.date() == fi.date():
        if ini.hour == 0 and ini.minute == 0 and fi.hour == 23:
            return "%s, tot el dia" % _dia(ini.date(), ara)
        return "%s, de %s a %s" % (_dia(ini.date(), ara), ini.strftime("%H:%M"), fi.strftime("%H:%M"))
    return "%s a les %s fins a %s a les %s" % (_des(_dia(ini.date(), ara)), ini.strftime("%H:%M"),
                                               _dia(fi.date(), ara), fi.strftime("%H:%M"))


def _des(txt):
    """«des d'avui», «des de dijous»."""
    return ("des d'" if txt[:1] in "aeiouàèéíòóú" else "des de ") + txt


def _ordre_fen(codi):
    return list(FENOMEN).index(codi) if codi in FENOMEN else len(FENOMEN)


def _fenomens(avs):
    """Noms dels fenomens sense repetir i sempre en el mateix ordre."""
    codis = sorted({a["codi_aemet"] for a in avs}, key=_ordre_fen)
    noms = [FENOMEN.get(c) or next(a.get("event", "") for a in avs if a["codi_aemet"] == c) for c in codis]
    return " i ".join([", ".join(noms[:-1]), noms[-1]]) if len(noms) > 1 else (noms[0] if noms else "")


def agrupa_avisos(avs):
    """Avisos del mateix nivell, franja, zona i probabilitat en una sola linia
    (AEMET emet pluges i tempestes per separat amb la mateixa franja), en ordre
    cronologic."""
    g = {}
    for a in avs:
        g.setdefault((a["nivell"], a["onset"], a["expires"], a.get("zona_codi"), a.get("probabilitat")),
                     []).append(a)
    return sorted(g.values(), key=lambda x: (x[0]["onset"], -NIV.get(x[0]["nivell"], 0)))


def _interval(avs, ara):
    """Quan, per a un conjunt d'avisos del mateix nivell (pot tenir diverses franges)."""
    fr = sorted({(_local(a["onset"]), _local(a["expires"])) for a in avs})
    if len(fr) == 1:
        return _finestra(fr[0][0], fr[0][1], ara)
    ini, fi = min(f[0] for f in fr), max(f[1] for f in fr)
    fins = ("fins a les %s" % fi.strftime("%H:%M") if fi.date() == ara.date()
            else "fins a %s a les %s" % (_dia(fi.date(), ara), fi.strftime("%H:%M")))
    if ini <= ara:
        return "en %d franges, %s" % (len(fr), fins)
    return "en %d franges, %s a les %s %s" % (len(fr), _des(_dia(ini.date(), ara)), ini.strftime("%H:%M"), fins)


def _parametre(a):
    """El valor que AEMET dona dins l'avis, en catala. Si no s'entén, el literal d'AEMET."""
    p = a.get("parametre") or {}
    num, unitat = p.get("num"), (p.get("unitat") or "").strip()
    desc = (p.get("descripcio") or "").lower()
    if num is None:
        return None
    if unitat.lower().startswith("mm"):
        m = re.search(r"(\d+)\s*hora", desc)
        hores = 1 if "una hora" in desc else (int(m.group(1)) if m else None)
        quan = (" en 1 hora" if hores == 1 else " en %d hores" % hores) if hores else ""
        return "precipitació acumulada de %s mm%s" % (_n(num), quan)
    if "km/h" in unitat.lower():
        return "ratxes màximes de %s km/h" % _n(num)
    if "ºc" in unitat.lower() or "°c" in unitat.lower():
        return "temperatura de %s °C" % _n(num)
    if unitat == "m" and "ola" in desc:
        return "onades de fins a %s m" % _n(num, 1 if num % 1 else 0)
    return "«%s» (%s, text d'AEMET)" % (p.get("valor"), p.get("descripcio"))


def _probabilitat(a):
    m = re.findall(r"\d+", a.get("probabilitat") or "")
    if len(m) == 2:
        return "%s–%s %%" % tuple(m)
    return "%s %%" % m[0] if m else None


def linia_avis(grup, ara):
    """Una linia per a un grup d'agrupa_avisos(): mateix nivell, franja i zona."""
    a = grup[0]
    vals = [v for v in dict.fromkeys(_parametre(x) for x in sorted(grup, key=lambda x: _ordre_fen(x["codi_aemet"])))
            if v]
    quan = _finestra_avis(a, ara)
    txt = "%s **%s per %s**%s. %s." % (EMOJI.get(a["nivell"], ""), a["nivell"].capitalize(), _fenomens(grup),
                                       (" — " + "; ".join(vals)) if vals else "", quan[0].upper() + quan[1:])
    pr = _probabilitat(a)
    if pr:
        txt += " Probabilitat: %s." % pr
    return txt + " _Zona d'avís: %s._" % (a.get("zona_nom") or a.get("zona_desc"))


# ── Previsio municipal d'AEMET ───────────────────────────────────────────────
def prediccio_municipal(mun, carpeta_fixtures=None):
    """JSON de la prediccio horaria d'AEMET per a un municipi, o None."""
    if carpeta_fixtures:
        p = Path(carpeta_fixtures) / ("aemet_horaria_%s.json" % mun)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    try:
        estat, raw = _aemet("prediccion/especifica/municipio/horaria/%s" % mun)
        if raw is None:
            log("  [municipi %s] AEMET no la serveix ara (estat %s)" % (mun, estat))
            return None
        return json.loads(_text(raw))
    except Exception as e:
        log("  [municipi %s] ha fallat: %s" % (mun, e))
        return None


def _int(v):
    try:
        return int(float(str(v).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _codi_cel(v):
    m = re.match(r"\d+", v or "")
    return int(m.group()) if m else None


def _gravetat_cel(v):
    c = _codi_cel(v)
    if c is None:
        return -1
    return _FAMILIA.get(c // 10, 0) * 100 + (_NUVOLS.get(c, 0) if c // 10 == 1 else c % 10)


def text_cel(v, desc_aemet=""):
    c = _codi_cel(v)
    return CEL.get(c) or ("_%s_" % desc_aemet if desc_aemet else "—")


def franges(js, ara, horitzo_h):
    """Resum per franges de 6 h (les de probabilitat d'AEMET) dins l'horitzo."""
    dies = js[0]["prediccion"]["dia"]
    hores, fr = {}, {}
    for dia in dies:
        d = date.fromisoformat(dia["fecha"][:10])

        def rec(e):
            h = _int(e.get("periodo"))
            return hores.setdefault(datetime.combine(d, dtime(h)), {}) if h is not None and h < 24 else {}
        for e in dia.get("estadoCielo", []):
            rec(e)["cel"] = (e.get("value", ""), e.get("descripcion", ""))
        for e in dia.get("precipitacion", []):
            v = str(e.get("value", ""))
            r = rec(e)
            r["ip"] = v.lower() == "ip"
            r["precip"] = 0.0 if r["ip"] else (float(v.replace(",", ".")) if v.replace(",", ".").replace(".", "", 1).isdigit() else 0.0)
        for e in dia.get("temperatura", []):
            rec(e)["temp"] = _int(e.get("value"))
        for e in dia.get("vientoAndRachaMax", []):
            r = rec(e)
            if "direccion" in e:
                r["dir"] = (e.get("direccion") or [""])[0]
                r["vel"] = _int((e.get("velocidad") or [None])[0])
            elif "value" in e:
                r["ratxa"] = _int(e.get("value"))
        for camp, clau in (("probPrecipitacion", "prob_pluja"), ("probTormenta", "prob_tempesta")):
            for e in dia.get(camp, []):
                per = str(e.get("periodo", ""))
                if len(per) != 4:
                    continue
                ini = datetime.combine(d, dtime(int(per[:2])))
                fi = datetime.combine(d, dtime(0)) + timedelta(hours=int(per[2:]))
                if fi <= ini:
                    fi += timedelta(days=1)
                fr.setdefault(ini, {"ini": ini, "fi": fi})[clau] = _int(e.get("value"))
    limit = ara + timedelta(hours=horitzo_h)
    out = []
    for ini in sorted(fr):
        f = fr[ini]
        if f["fi"] <= ara or f["ini"] >= limit:
            continue
        hs = [v for k, v in sorted(hores.items()) if f["ini"] <= k < f["fi"]]
        if not hs:
            continue
        cels = [h["cel"] for h in hs if "cel" in h]
        pitjor = max(cels, key=lambda c: _gravetat_cel(c[0])) if cels else ("", "")
        temps = [h["temp"] for h in hs if h.get("temp") is not None]
        vels = [h for h in hs if h.get("vel") is not None]
        vmax = max(vels, key=lambda h: h["vel"]) if vels else None
        ratxes = [h["ratxa"] for h in hs if h.get("ratxa") is not None]
        out.append({
            "ini": f["ini"], "fi": f["fi"],
            "cel": text_cel(*pitjor), "cel_codi": pitjor[0],
            "precip": round(sum(h.get("precip", 0) for h in hs), 1),
            "ip": any(h.get("ip") for h in hs),
            "prob_pluja": f.get("prob_pluja"), "prob_tempesta": f.get("prob_tempesta"),
            "tmin": min(temps) if temps else None, "tmax": max(temps) if temps else None,
            "vent": vmax["vel"] if vmax else None, "dir": vmax.get("dir") if vmax else None,
            "ratxa": max(ratxes) if ratxes else None,
        })
    return out


def _etiqueta_franja(f, ara=None, relatiu=False):
    return "%s, %02d–%02d h" % (_dia(f["ini"].date(), ara, relatiu), f["ini"].hour, f["fi"].hour)


def _de(nom):
    """Preposicio apostrofada: «de Palma», «d'Alaior», «d'Eivissa»."""
    return ("d'" if nom[:1].lower() in "aeiouàèéíòóúh" else "de ") + nom


def _pct(v):
    return "—" if v is None else "%d %%" % v


def taula_municipal(fs):
    L = ["| Franja | Cel | Precipitació | Prob. pluja | Prob. tempesta | Vent (ratxa màx.) | Temperatura |",
         "|---|---|---|---|---|---|---|"]
    for f in fs:
        prec = ("%s mm" % _n(f["precip"], 1)) if f["precip"] else ("inapreciable" if f["ip"] else "—")
        if f["vent"] is None:
            vent = "—"
        elif (f["dir"] or "").upper() == "C":
            vent = "calma"
        else:
            vent = "%s %d km/h" % (f["dir"] or "", f["vent"])
        if f["ratxa"] is not None:
            vent += " (%d)" % f["ratxa"]
        temp = "—" if f["tmin"] is None else (
            "%d °C" % f["tmin"] if f["tmin"] == f["tmax"] else "%d–%d °C" % (f["tmin"], f["tmax"]))
        L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            _etiqueta_franja(f), f["cel"], prec, _pct(f["prob_pluja"]),
            _pct(f["prob_tempesta"]), vent, temp))
    return "\n".join(L)


def _destacable(f):
    return ((f["prob_tempesta"] or 0) >= 20 or (f["prob_pluja"] or 0) >= 60
            or f["precip"] >= 5 or (f["ratxa"] or 0) >= 50)


def _puntuacio(f):
    return ((f["prob_tempesta"] or 0) * 2 + (f["prob_pluja"] or 0) + f["precip"] * 5
            + max(0, (f["ratxa"] or 0) - 40))


def _detall_franja(f):
    p = []
    if f["prob_pluja"]:
        p.append("probabilitat de pluja del %d %%" % f["prob_pluja"])
    if f["prob_tempesta"]:
        p.append("de tempesta del %d %%" % f["prob_tempesta"] if p else
                 "probabilitat de tempesta del %d %%" % f["prob_tempesta"])
    if f["precip"] >= 1:
        p.append("fins a %s mm a la franja" % _n(f["precip"], 1))
    if (f["ratxa"] or 0) >= 40:
        p.append("ratxes de fins a %d km/h" % f["ratxa"])
    return ", ".join(p[:-1]) + (" i " if len(p) > 1 else "") + p[-1] if p else ""


def resum_grup(g, fs, avisos_grup, ara, amb=None):
    """«En resum»: el que diu AEMET per al municipi, en una o dues frases."""
    if not fs:
        return None
    nom = g["municipi_nom"]
    dest = [f for f in fs if _destacable(f)]
    if not dest:
        hores = round((fs[-1]["fi"] - ara).total_seconds() / 3600)
        txt = ("La previsió d'AEMET per a %s no preveu precipitació ni vent destacables en "
               "les properes %d hores." % (nom, hores))
        if any(a["codi_aemet"] in ("PR", "TO") and NIV[a["nivell"]] >= 2 for a in avisos_grup):
            txt += (" És més moderada que l'avís de zona: en situacions de tempesta és habitual, "
                    "perquè els xàfecs afecten punts concrets i no es poden localitzar amb antelació.")
    else:
        pitjor = max(dest, key=_puntuacio)
        txt = ("Segons la previsió d'AEMET per a %s, el moment més desfavorable és %s de %02d a %02d h: %s."
               % (nom, _dia(pitjor["ini"].date(), ara), pitjor["ini"].hour, pitjor["fi"].hour,
                  _detall_franja(pitjor)))
        altres = [f for f in sorted(dest, key=lambda f: f["ini"]) if f is not pitjor][:2]
        if altres:
            txt += " També: " + "; ".join(
                "%s de %02d a %02d h (%s)" % (_dia(f["ini"].date(), ara), f["ini"].hour, f["fi"].hour,
                                              _detall_franja(f)) for f in altres) + "."
    # Energia per a tempestes que AEMET no tradueix en tempesta: s'ha de dir, perque
    # el lector veu les dues coses i no les ha de conciliar tot sol.
    energia = amb and any(c in ("alt", "molt_alt") for c, _ in amb.values())
    if energia and max((f["prob_tempesta"] or 0) for f in fs) < 20:
        txt += (" Els paràmetres atmosfèrics mostren energia disponible per a tempestes, però AEMET "
                "no en preveu al municipi: sense un element que les desencadeni, aquesta energia no "
                "arriba a formar tempesta.")
    return txt


# ── Ambient atmosferic (Open-Meteo, via vigilancia.py) ───────────────────────
def categoria_ambient(v):
    """Categoria publica d'un dia. Reaprofita el criteri de vigilancia.senyal(), pero
    no el seu text, que es per a us intern."""
    s = V.senyal(v)
    if s is None:
        return "baix"
    cat = s[0]
    if cat == "clar":
        cape = v.get("cape") or 0
        return "molt_alt" if cape > 2500 else "alt" if cape > 1000 else "moderat"
    return {"moderat": "moderat", "feble": "feble", "discrepant": "incert"}.get(cat, "baix")


FRASE_AMBIENT = {
    "baix": "paràmetres d'inestabilitat baixos al punt; a la Mediterrània això no descarta del tot xàfecs locals",
    "feble": "senyal d'inestabilitat feble",
    "moderat": "ambient inestable: hi pot haver tempestes si es donen les condicions per desencadenar-les",
    "alt": "hi ha energia per a tempestes, que podrien ser localment intenses si es desencadenen",
    "molt_alt": "hi ha molta energia per a tempestes, que podrien ser fortes si es desencadenen",
    "incert": "els indicadors d'inestabilitat no coincideixen: la probabilitat de tempesta és incerta",
}
NOM_AMBIENT = {"baix": "baix", "feble": "feble", "moderat": "inestable", "alt": "favorable a tempestes",
               "molt_alt": "molt favorable a tempestes fortes", "incert": "incert"}


def ambient_grup(vig, g, ara, horitzo_h):
    """{dia_iso: (categoria, v)} per als dies dins l'horitzo."""
    if not vig:
        return None
    s = (vig.get("seus") or {}).get(g["punt"])
    if not s:
        return None
    fins = (ara + timedelta(hours=horitzo_h)).date()
    out = {}
    for dia, v in sorted(s["dies"].items()):
        d = date.fromisoformat(dia)
        if ara.date() <= d <= fins:
            out[dia] = (categoria_ambient(v), v)
    return out


def linies_ambient(amb, ara):
    if not amb:
        return []
    if all(c == "baix" for c, _ in amb.values()):
        return ["- Paràmetres d'inestabilitat baixos tots els dies; a la Mediterrània això no "
                "descarta del tot xàfecs locals."]
    L = []
    for dia, (cat, v) in amb.items():
        L.append("- **%s:** %s (CAPE %s J/kg, Lifted Index %s)." % (
            _dia(date.fromisoformat(dia), ara), FRASE_AMBIENT[cat], _n(v.get("cape")),
            _n(v.get("lifted_index"), 1)))
    return L


# ── Grup de Meteorologia de la UIB ───────────────────────────────────────────
def llegeix_meteouib(ruta, ara, hores):
    """Aportacio del Grup. Primera linia `data: AAAA-MM-DDTHH:MM`; els comentaris
    HTML (<!-- -->) s'ignoren. Sense data o massa antiga, no s'inclou."""
    if not ruta or not Path(ruta).exists():
        return None
    txt = re.sub(r"<!--.*?-->", "", Path(ruta).read_text(encoding="utf-8"), flags=re.S).strip()
    m = re.match(r"data:[ \t]*(\S*)[ \t]*\n?", txt)
    if not m or not m.group(1):
        return None
    try:
        quan = datetime.fromisoformat(m.group(1))
    except ValueError:
        log("  [meteouib] data il·legible: %s" % m.group(1))
        return None
    cos = re.sub(r"^\s*---\s*\n", "", txt[m.end():]).strip()
    if not cos:
        return None
    if ara - quan > timedelta(hours=hores):
        log("  [meteouib] aportació del %s: massa antiga, no s'inclou" % quan.isoformat())
        return None
    if quan - ara > timedelta(minutes=30):
        log("  [meteouib] aportació amb data futura (%s): segurament és un error, no s'inclou"
            % quan.isoformat())
        return None
    return {"data": quan, "text": cos, "hash": hashlib.sha256(cos.encode("utf-8")).hexdigest()[:12]}


# ── Imatges adjuntes ─────────────────────────────────────────────────────────
def mapes_tram(cfg, carpeta):
    out, rids = [], {}
    for dom, camp, h in cfg["mapes_tram"]:
        if dom not in V.DOMINIS:
            continue
        if dom not in rids:
            rids[dom] = V.run_tram(dom)
        rid = rids[dom]
        if not rid:
            continue
        try:
            pas = V.idx_pas(h, V.DOMINIS[dom]["horitzo_h"])
            data = _get("%s/%s/map/%s_%s_%s.gif" % (V.TRAM, dom, rid, camp, pas), intents=2)
        except Exception as e:
            log("  [tram] %s %s +%sh: %s" % (dom, camp, h, e))
            continue
        if len(data) < 500:
            continue
        ruta = Path(carpeta) / ("tram-%s-%s-%02dh.gif" % (dom, camp, h))
        ruta.write_bytes(data)
        out.append((str(ruta), "%s, +%d h" % (MAPA_DESC.get(camp, camp), h)))
    return out, {d: r for d, r in rids.items() if r}


def imatges_aemet(cfg, carpeta):
    import radar as R
    out = []
    for prod, clau in (("radar", "adjunta_radar"), ("llamps", "adjunta_llamps")):
        if not cfg.get(clau):
            continue
        try:
            ruta = R.descarrega(prod, carpeta=str(carpeta))
        except (Exception, SystemExit) as e:
            log("  [%s] %s" % (prod, e))
            ruta = None
        if ruta:
            out.append((prod, ruta))
    return out


# ── Estat i canvis ───────────────────────────────────────────────────────────
def _banda_precip(mm):
    return 0 if mm < 1 else 1 if mm < 5 else 2 if mm < 15 else 3 if mm < 30 else 4


def foto(avisos_seus, municipal, ambient, meteouib):
    """Estat comparable. Els avisos s'agrupen per fenomen i zona: AEMET els reemet
    sovint amb franges noves, i comparar-los franja a franja omple el correu de
    parelles «nou/retirat» que en realitat son el mateix avis."""
    avs = {}
    for a in avisos_seus:
        e = avs.setdefault("%s|%s" % (a["codi_aemet"], a["zona_codi"]), {
            "nom": "%s a la zona %s" % (FENOMEN.get(a["codi_aemet"], a.get("event", "")),
                                        a.get("zona_nom") or a.get("zona_desc")),
            "finestres": []})
        w = [a["onset"][:16], a["expires"][:16], a["nivell"]]
        if w not in e["finestres"]:
            e["finestres"].append(w)
    for e in avs.values():
        e["finestres"].sort()
    return {
        "avisos": avs,
        "municipal": {gid: {f["ini"].isoformat(timespec="minutes"): {
            "prob_pluja": f["prob_pluja"], "prob_tempesta": f["prob_tempesta"],
            "precip": f["precip"], "ratxa": f["ratxa"],
            "etiqueta": _etiqueta_franja(f)} for f in fs}
            for gid, fs in municipal.items() if fs is not None},
        "ambient": {gid: {d: c for d, (c, _) in amb.items()}
                    for gid, amb in ambient.items() if amb},
        "meteouib": meteouib["hash"] if meteouib else None,
    }


def _finestres_text(ws, ara):
    """Franges d'un avis agrupades per dia: «avui divendres 18: 🟡 groc de 02:00 a 11:59
    i 🟠 taronja de 09:00 a 11:59»."""
    per_dia, llargues = {}, []
    for i, f, n in sorted(ws):
        ini, fi = datetime.fromisoformat(i), datetime.fromisoformat(f)
        if ini.date() == fi.date():
            per_dia.setdefault(ini.date(), []).append(
                "%s %s %s" % (EMOJI.get(n, ""), n, "tot el dia" if ini.hour == 0 and fi.hour == 23
                              else "de %s a %s" % (ini.strftime("%H:%M"), fi.strftime("%H:%M"))))
        else:
            llargues.append("%s %s %s" % (EMOJI.get(n, ""), n, _finestra(ini, fi, ara)))
    parts = ["%s: %s" % (_dia(d, ara), " i ".join([", ".join(x[:-1]), x[-1]]) if len(x) > 1 else x[0])
             for d, x in sorted(per_dia.items())]
    return "; ".join(parts + llargues)


def canvis(prev, nou, ara):
    """Llista de (frase, rellevant). Nomes les rellevants fan enviar un correu; les
    altres (una franja d'avis que s'acaba a l'hora prevista) s'esmenten si s'envia
    per un altre motiu, pero no son noticia per si soles."""
    if not prev:
        return []
    C = []
    pa, na = prev.get("avisos", {}), nou.get("avisos", {})
    for k in sorted(set(pa) | set(na)):
        pf = [w for w in (pa.get(k) or {}).get("finestres", [])]
        nf = [w for w in (na.get(k) or {}).get("finestres", [])]
        nom = (na.get(k) or pa.get(k))["nom"]
        actives = [w for w in pf if datetime.fromisoformat(w[1]) > ara]
        acabades = [w for w in pf if datetime.fromisoformat(w[1]) <= ara]
        if sorted(map(tuple, nf)) == sorted(map(tuple, actives)):
            if acabades:
                C.append(("Ha acabat, a l'hora prevista, l'avís per %s (%s)."
                          % (nom, _finestres_text(acabades, ara)), False))
            continue
        if nf and not actives:
            C.append(("Nou avís d'AEMET per %s: %s." % (nom, _finestres_text(nf, ara)), True))
        elif actives and not nf:
            C.append(("AEMET ha retirat l'avís per %s abans d'hora (era: %s)."
                      % (nom, _finestres_text(actives, ara)), True))
        else:
            C.append(("Canvia l'avís per %s. Ara: %s. Abans: %s."
                      % (nom, _finestres_text(nf, ara), _finestres_text(actives, ara)), True))
    noms = {g["id"]: g["nom"] for g in GRUPS}
    for gid, fr in nou.get("municipal", {}).items():
        antic = prev.get("municipal", {}).get(gid)
        if antic is None:
            continue
        for ini, f in fr.items():
            a = antic.get(ini)
            if a is None:
                if (f["prob_tempesta"] or 0) >= 40 or (f["prob_pluja"] or 0) >= 60 \
                        or f["precip"] >= 10 or (f["ratxa"] or 0) >= 60:
                    C.append(("%s, %s: entra a l'horitzó de previsió amb %s." % (
                        noms[gid], f["etiqueta"], _resum_franja_foto(f)), True))
                continue
            d = []
            for camp, nom in (("prob_tempesta", "probabilitat de tempesta"), ("prob_pluja", "probabilitat de pluja")):
                if a[camp] is not None and f[camp] is not None and abs(f[camp] - a[camp]) >= 20:
                    d.append("%s del %d %% al %d %%" % (nom, a[camp], f[camp]))
            if _banda_precip(a["precip"]) != _banda_precip(f["precip"]):
                d.append("precipitació de %s a %s mm" % (_n(a["precip"], 1), _n(f["precip"], 1)))
            if a["ratxa"] is not None and f["ratxa"] is not None and abs(f["ratxa"] - a["ratxa"]) >= 15:
                d.append("ratxa màxima de %d a %d km/h" % (a["ratxa"], f["ratxa"]))
            if d:
                C.append(("%s, %s: %s (previsió d'AEMET)." % (noms[gid], f["etiqueta"], "; ".join(d)), True))
    for gid, dies in nou.get("ambient", {}).items():
        antic = prev.get("ambient", {}).get(gid, {})
        for dia, cat in dies.items():
            if dia in antic and antic[dia] != cat:
                C.append(("%s, %s: l'ambient atmosfèric passa de «%s» a «%s»." % (
                    noms[gid], _dia(date.fromisoformat(dia)), NOM_AMBIENT[antic[dia]],
                    NOM_AMBIENT[cat]), True))
    if nou.get("meteouib") and nou["meteouib"] != prev.get("meteouib"):
        C.append(("Nova aportació del Grup de Meteorologia de la UIB.", True))
    return C


def _resum_franja_foto(f):
    p = []
    if f["prob_tempesta"]:
        p.append("probabilitat de tempesta del %d %%" % f["prob_tempesta"])
    if f["prob_pluja"]:
        p.append("probabilitat de pluja del %d %%" % f["prob_pluja"])
    if f["precip"] >= 1:
        p.append("%s mm" % _n(f["precip"], 1))
    if f["ratxa"]:
        p.append("ratxes de %d km/h" % f["ratxa"])
    return ", ".join(p) or "valors destacables"


def decideix(porta, canv, estat, ara, cfg, forca, niv_max):
    """Retorna (accio, motiu). Accio: envia | batec | tancament | silenci.
    `canv` es la llista de (frase, rellevant) de canvis(); nomes compten les rellevants."""
    if not porta:
        if forca:
            return "envia", "execució forçada (sense avís que obri el seguiment)"
        if estat.get("actiu"):
            return "tancament", "ja no hi ha avisos del llindar a les zones amb seu"
        return "silenci", "cap avís del llindar a les zones amb seu"
    if forca:
        return "envia", "execució forçada"
    if not estat.get("actiu"):
        return "envia", "primer avís d'un episodi nou"
    try:
        darrer = datetime.fromisoformat(estat["enviat"])
    except Exception:
        return "envia", "estat anterior il·legible"
    minuts = (ara - darrer).total_seconds() / 60
    escalada = NIV.get(niv_max, 0) > NIV.get(estat.get("nivell_max"), 0)
    rell = [c for c, r in canv if r]
    if rell and (escalada or minuts >= cfg["antirebot_minuts"]):
        return "envia", "%d canvi(s)%s" % (len(rell), " i escalada de nivell" if escalada else "")
    if rell:
        return "silenci", "hi ha canvis, però l'últim enviament és de fa %d min (antirebot)" % minuts
    ini, fi = cfg["horari_diurn"]
    if minuts >= cfg["batec_hores"] * 60 and ini <= ara.hour < fi:
        return "batec", "%d h sense canvis" % (minuts // 60)
    return "silenci", "sense canvis rellevants des de fa %d min" % minuts


def cita(text):
    """Citacio literal en Markdown. La fa servir el document i tambe comprova_to(),
    perque el text a excloure sigui exactament el que s'ha escrit."""
    return "\n".join("> " + x if x.strip() else ">" for x in text.splitlines())


def comprova_to(md, excepte=None):
    """Paraules prohibides al text generat (s'exclou la citacio literal de MeteoUIB
    i la signatura, que diu «Servei de Prevenció»)."""
    t = md
    if excepte:
        t = t.replace(excepte, "")
    t = t.replace("Servei de Prevenció", "")
    return sorted({m.group(0) for p in PROHIBIDES for m in re.finditer(p, t, re.I)})


# ── Document ─────────────────────────────────────────────────────────────────
PEU = ("_Elaborat automàticament pel Servei de Prevenció de la Universitat de les Illes Balears "
       "a partir de fonts públiques. La informació d'AEMET es reprodueix citant-ne l'autoria, "
       "tal com permet la seva nota legal._")


def capcalera(ara):
    return ["# Previsió meteorològica — seus de la UIB", "",
            "_Actualització de %s %s a les %s_" % (DIES[ara.weekday()], ara.strftime("%d/%m/%Y"),
                                                   ara.strftime("%H:%M")), ""]


def document(ara, accio, avisos, porta, municipal, elaborats, ambient, meteouib, canv,
             prev_enviat, imatges, tram_rids, fonts):
    L = capcalera(ara)
    L += ["> Síntesi de fonts públiques. Els avisos i la previsió per municipi són d'AEMET, "
          "autoritat competent en predicció meteorològica. Tant els uns com l'altra poden canviar: "
          "la informació oficial més recent és sempre a aemet.es.", ""]
    nm = nivell_max(porta)
    if nm:
        L += ["**Nivell d'avís més alt d'AEMET a les zones amb seu de la UIB:** %s %s" % (EMOJI[nm], nm), ""]

    # En poques paraules
    L += ["## En poques paraules", ""]
    for g in GRUPS:
        ag = del_grup(avisos, g)
        if ag:
            per_niv = {}
            for a in ag:
                per_niv.setdefault(a["nivell"], []).append(a)
            txt = "; ".join("%s avís %s per %s, %s" % (EMOJI[n], n, _fenomens(per_niv[n]), _interval(per_niv[n], ara))
                            for n in sorted(per_niv, key=lambda n: -NIV[n]))
        else:
            txt = "sense avisos d'AEMET a la zona"
        L.append("- **%s:** %s." % (g["nom"], txt))
    L.append("")

    # Que ha canviat
    L += ["## Què ha canviat", ""]
    rell = [c for c, r in canv if r]
    menors = [c for c, r in canv if not r]
    if prev_enviat:
        pe = datetime.fromisoformat(prev_enviat)
        prev_enviat = ("les %s" % pe.strftime("%H:%M") if pe.date() == ara.date()
                       else "%s a les %s" % (_dia(pe.date(), ara, relatiu=False), pe.strftime("%H:%M")))
    if not prev_enviat:
        L.append("Primera actualització d'aquest seguiment.")
    elif rell:
        L += ["Respecte de l'actualització de %s:" % prev_enviat, ""] + ["- " + c for c in rell + menors]
    else:
        L.append("Sense canvis rellevants des de l'actualització de %s." % prev_enviat)
        if menors:
            L += [""] + ["- " + c for c in menors]
    L.append("")

    # Seu per seu
    L += ["## Seu per seu", ""]
    for g in GRUPS:
        ag = del_grup(avisos, g)
        L += ["### %s — %s" % (g["nom"], g["detall"]), ""]
        if ag:
            L += ["**Avisos d'AEMET:**", ""] + ["- " + linia_avis(x, ara) for x in agrupa_avisos(ag)] + [""]
        else:
            L += ["**Avisos d'AEMET:** cap a la zona.", ""]
        fs = municipal.get(g["id"])
        if fs:
            L += ["**Previsió d'AEMET per a %s** _(elaborada %s)_:" % (g["municipi_nom"], elaborats.get(g["id"], "—")),
                  "", taula_municipal(fs), ""]
        else:
            L += ["_No s'ha pogut obtenir la previsió d'AEMET per a %s en aquesta actualització._"
                  % g["municipi_nom"], ""]
        la = linies_ambient(ambient.get(g["id"]), ara)
        if la:
            L += ["**Ambient atmosfèric** _(dades obertes de model, Open-Meteo)_:", ""] + la + [""]
        r = resum_grup(g, fs, ag, ara, ambient.get(g["id"]))
        if r:
            L += ["**En resum:** " + r, ""]

    # Imatges
    if imatges:
        L += ["## Imatges adjuntes", ""]
        for tipus, _ruta, desc in imatges:
            L.append("- " + desc)
        L += ["", "_Les imatges de radar mostren un instant: no indiquen cap a on es desplacen les "
              "precipitacions. L'hora de validesa de cada mapa del model TRAM figura a la mateixa imatge._", ""]

    # MeteoUIB
    if meteouib:
        L += ["## Grup de Meteorologia de la UIB", "",
              "_Aportació del %s a les %s. Quan n'hi ha, aquesta lectura experta preval sobre les "
              "dades de model d'aquest document._" % (meteouib["data"].strftime("%d/%m/%Y"),
                                                      meteouib["data"].strftime("%H:%M")), ""]
        L += [cita(meteouib["text"]), ""]

    # Incertesa
    tempesta = any(a["codi_aemet"] == "TO" for a in porta) or any(
        c in ("alt", "molt_alt", "incert") for amb in ambient.values() if amb for c, _ in amb.values())
    L += ["## Incertesa", "",
          "- El moment, la zona i el nivell dels avisos són d'AEMET i en són la referència oficial."]
    if tempesta:
        L.append("- En situacions de tempesta, on descarregaran exactament els xàfecs no es pot saber "
                 "amb antelació: poden afectar un punt i deixar sec el del costat.")
    L += ["- Els paràmetres d'ambient atmosfèric són els d'un punt concret de cada seu, no d'una àrea.",
          "- AEMET pot modificar avisos i previsions en qualsevol moment: %s" % URL_AVISOS, ""]

    # Fonts
    L += ["## Fonts", ""] + ["- " + f for f in fonts] + ["", "---", "", PEU]
    return "\n".join(L)


def document_tancament(ara, avisos, cfg, fonts):
    L = capcalera(ara)
    L += ["**AEMET ja no manté cap avís de nivell %s o superior a les zones on hi ha seus de la UIB** "
          "(consulta de les %s)." % (cfg["llindar"], ara.strftime("%H:%M")), ""]
    resta = [a for a in avisos if a.get("seus")]
    if resta:
        L += ["Continuen vigents aquests avisos de nivell inferior:", ""]
        L += ["- **%s:** %s" % (", ".join(g["nom"] for g in GRUPS if g["seus"] & set(x[0]["seus"])),
                                linia_avis(x, ara)) for x in agrupa_avisos(resta)] + [""]
    L += ["Aquest és el darrer missatge d'aquest seguiment. Si AEMET torna a activar un avís, "
          "s'iniciarà un seguiment nou automàticament.", "",
          "La informació oficial més recent és sempre a %s" % URL_AVISOS, "",
          "## Fonts", ""] + ["- " + f for f in fonts] + ["", "---", "", PEU]
    return "\n".join(L)


def assumpte(accio, ara, porta):
    quan = ara.strftime("%d/%m %H:%M")
    if accio == "tancament":
        return "Previsió meteorològica UIB · fi dels avisos · %s" % quan
    nm = nivell_max(porta)
    fen = _fenomens([a for a in porta if a["nivell"] == nm]) if nm else ""
    base = ("Previsió meteorològica UIB · %s avís %s (%s)" % (EMOJI[nm], nm, fen)) if nm \
        else "Previsió meteorològica UIB"
    return base + (" · sense canvis" if accio == "batec" else "") + " · " + quan


# ── Enviament ────────────────────────────────────────────────────────────────
def envia(subj, md, html, adjunts):
    """Correu en copia oculta: si el reben persones diferents, no s'han de veure
    les adreces entre elles."""
    host = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
    remitent, clau = os.environ.get("EMAIL_FROM"), os.environ.get("EMAIL_PASSWORD")
    dest = [x.strip() for x in os.environ.get("EMAIL_TO", "").split(",") if x.strip()]
    if not (remitent and clau and dest):
        log("[correu] falten EMAIL_FROM, EMAIL_PASSWORD o EMAIL_TO: no s'envia")
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subj, remitent, remitent
    msg.set_content(md)
    msg.add_alternative(html, subtype="html")
    for ruta in adjunts:
        ext = Path(ruta).suffix.lower().lstrip(".") or "octet-stream"
        msg.add_attachment(Path(ruta).read_bytes(), maintype="image", subtype="jpeg" if ext == "jpg" else ext,
                           filename=Path(ruta).name)
    try:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls()
            s.login(remitent, clau)
            s.send_message(msg, to_addrs=[remitent] + dest)
        log("[correu] enviat a %d destinatari(s) en còpia oculta" % len(dest))
        return True
    except Exception as e:
        log("[correu] ERROR: %s" % e)
        return False


# ── Principal ────────────────────────────────────────────────────────────────
def _config(ruta):
    cfg = dict(CONFIG_DEFECTE)
    if ruta and Path(ruta).exists():
        cfg.update({k: v for k, v in json.loads(Path(ruta).read_text(encoding="utf-8")).items()
                    if not k.startswith("_")})
    if cfg["llindar"] not in NIV:
        raise SystemExit("config: llindar '%s' desconegut (groc | taronja | vermell)" % cfg["llindar"])
    return cfg


def _estat(ruta):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except Exception:
        return {}


def main(argv=None):
    base = PROJECT_DIR / "previsio"
    ap = argparse.ArgumentParser(description="Previsió meteorològica per a les seus de la UIB")
    ap.add_argument("--config", default=str(base / "config.json"))
    ap.add_argument("--estat", default=str(base / "state.json"))
    ap.add_argument("--meteouib", default=str(base / "meteouib.md"))
    ap.add_argument("--sortida", default=str(base / "sortida"), help="on es desa el document generat")
    ap.add_argument("--forca", action="store_true", help="genera'l encara que no hi hagi avís ni canvis")
    ap.add_argument("--dryrun", action="store_true", help="no envia res i no toca l'estat")
    ap.add_argument("--simula-enviament", action="store_true",
                    help="(proves) no envia, però desa l'estat com si hagués enviat")
    ap.add_argument("--sense-imatges", action="store_true", help="no baixis radar ni mapes TRAM")
    ap.add_argument("--cap", metavar="XML", help="(proves) avisos CAP des d'un fitxer")
    ap.add_argument("--municipal-dir", metavar="DIR", help="(proves) aemet_horaria_<municipi>.json")
    ap.add_argument("--vigilancia", metavar="JSON", help="(proves) parametres convectius desats")
    ap.add_argument("--ara", metavar="ISO", help="(proves) hora local simulada")
    a = ap.parse_args(argv)
    forca = a.forca or os.environ.get("FORCA", "").lower() == "true"
    dryrun = a.dryrun or os.environ.get("DRYRUN", "").lower() == "true"

    cfg = _config(a.config)
    ara = datetime.fromisoformat(a.ara) if a.ara else datetime.now().replace(second=0, microsecond=0)
    log("Previsió meteorològica UIB — %s%s%s" % (ara.strftime("%d/%m/%Y %H:%M"),
                                                "  [FORÇADA]" if forca else "", "  [DRYRUN]" if dryrun else ""))

    avisos, font_avisos = obte_avisos(a.cap)
    avisos = vigents(avisos, ara)
    porta = porta_oberta(avisos, cfg["llindar"])
    niv_max = nivell_max(porta)
    log("  avisos vigents: %d; amb seu i nivell >= %s: %d" % (len(avisos), cfg["llindar"], len(porta)))
    estat = _estat(a.estat)
    fonts = ["Avisos: %s, consultats a les %s." % (font_avisos, ara.strftime("%H:%M"))]

    # Tancament o silenci: no cal baixar res mes.
    if not porta and not forca:
        accio, motiu = decideix(porta, [], estat, ara, cfg, forca, niv_max)
        log("  decisió: %s — %s" % (accio, motiu))
        if accio != "tancament":
            return 0
        md = document_tancament(ara, avisos, cfg, fonts)
        return _surt(a, dryrun, accio, md, [], estat_nou={"actiu": False,
                     "tancat": ara.isoformat(timespec="minutes")}, ara=ara, porta=porta, cfg=cfg)

    # Previsio municipal, ambient i MeteoUIB
    municipal, elaborats = {}, {}
    for g in GRUPS:
        js = prediccio_municipal(g["municipi"], a.municipal_dir)
        if js:
            municipal[g["id"]] = franges(js, ara, cfg["horitzo_hores"])
            el = js[0].get("elaborado", "")
            elaborats[g["id"]] = ("el %s a les %s" % (el[8:10] + "/" + el[5:7], el[11:16])) if el else "—"
        else:
            municipal[g["id"]] = None
        if not a.municipal_dir:
            time.sleep(2)   # la clau d'AEMET es comparteix amb el rastrejador
    vig = None
    try:
        vig = json.loads(Path(a.vigilancia).read_text(encoding="utf-8")) if a.vigilancia else V.recull(3)
    except Exception as e:
        log("  [ambient] Open-Meteo no disponible: %s" % e)
    ambient = {g["id"]: ambient_grup(vig, g, ara, cfg["horitzo_hores"]) for g in GRUPS}
    meteouib = llegeix_meteouib(a.meteouib, ara, cfg["meteouib_hores"])

    nova = foto([x for x in avisos if x.get("seus")], municipal, ambient, meteouib)
    prev_foto = estat.get("foto") if estat.get("actiu") else None
    if prev_foto:
        # Si una font no ha respost, es conserva la seva base anterior: si no, a la
        # volta seguent tot semblaria nou i s'enviaria un fals «canvi».
        for camp in ("municipal", "ambient"):
            for gid, v in prev_foto.get(camp, {}).items():
                nova[camp].setdefault(gid, v)
    canv = canvis(prev_foto, nova, ara)
    accio, motiu = decideix(porta, canv, estat, ara, cfg, forca, niv_max)
    log("  decisió: %s — %s" % (accio, motiu))
    for c, r in canv:
        log("    %s %s" % ("·" if r else "(menor)", c))
    if accio == "silenci":
        return 0

    # Imatges (nomes si s'envia)
    imatges, tram_rids = [], {}
    carpeta_img = Path(a.sortida) / "imatges" / ara.strftime("%Y%m%d-%H%M")
    if not a.sense_imatges:
        carpeta_img.mkdir(parents=True, exist_ok=True)
        tram, tram_rids = mapes_tram(cfg, carpeta_img)
        for prod, ruta in imatges_aemet(cfg, carpeta_img):
            desc = ("Radar d'AEMET de les Illes Balears: darrera imatge disponible a les %s "
                    "(l'hora exacta de la imatge hi figura, en UTC)" % ara.strftime("%H:%M")
                    if prod == "radar" else
                    "Mapa de llamps d'AEMET: acumulat de 12 hores, no és en temps real")
            imatges.append((prod, ruta, desc))
        for ruta, desc in tram:
            imatges.append(("tram", ruta, "Model TRAM, Grup de Meteorologia de la UIB: " + desc))

    # Fonts
    for g in GRUPS:
        if municipal.get(g["id"]) is not None:
            fonts.append("Previsió horària municipal %s: © AEMET, elaborada %s."
                         % (_de(g["municipi_nom"]), elaborats[g["id"]]))
    if vig:
        gen = vig.get("generat", "")
        fonts.append("Paràmetres d'ambient atmosfèric: Open-Meteo (dades obertes), consultats el "
                     "%s/%s a les %s." % (gen[8:10], gen[5:7], gen[11:16]))
    if tram_rids:
        fonts.append("Mapes del model TRAM: Grup de Meteorologia de la UIB, execució %s."
                     % ", ".join("%s (%s)" % (r, d.upper()) for d, r in tram_rids.items()))
    if any(t in ("radar", "llamps") for t, _, _ in imatges):
        fonts.append("Radar i llamps: AEMET.")

    prev_env = estat.get("enviat") if estat.get("actiu") else None
    md = document(ara, accio, avisos, porta, municipal, elaborats, ambient, meteouib, canv,
                  prev_env, imatges, tram_rids, fonts)
    nou_estat = {"actiu": bool(porta), "nivell_max": niv_max, "foto": nova}
    return _surt(a, dryrun, accio, md, [r for _, r, _ in imatges], estat_nou=nou_estat,
                 ara=ara, porta=porta, cfg=cfg, excepte=cita(meteouib["text"]) if meteouib else None)


def _surt(a, dryrun, accio, md, adjunts, estat_nou, ara, porta, cfg, excepte=None):
    subj = assumpte(accio, ara, porta)
    html = md_html.document(md, subj)
    dolentes = comprova_to(md, excepte)
    if dolentes:
        log("  ATENCIÓ: el text conté paraules fora de to: %s" % ", ".join(dolentes))
    sortida = Path(a.sortida)
    sortida.mkdir(parents=True, exist_ok=True)
    nom = "previsio-%s" % ara.strftime("%Y%m%d-%H%M")
    (sortida / (nom + ".md")).write_text(md, encoding="utf-8")
    (sortida / (nom + ".html")).write_text(html, encoding="utf-8")
    log("  document: %s (.md i .html), %d adjunt(s)" % (sortida / nom, len(adjunts)))
    log("  assumpte: %s" % subj)
    if dryrun:
        # Al workflow, el fitxer desat desapareix amb la maquina: el document va al registre.
        print(md)
        log("  [dryrun] no s'envia res i no es toca l'estat.")
        return 0
    if a.simula_enviament:
        log("  [simulació] no s'envia, però es desa l'estat.")
    elif not envia(subj, md, html, adjunts):
        return 1
    estat_nou = dict(estat_nou, enviat=ara.isoformat(timespec="minutes"), accio=accio)
    Path(a.estat).parent.mkdir(parents=True, exist_ok=True)
    Path(a.estat).write_text(json.dumps(estat_nou, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
