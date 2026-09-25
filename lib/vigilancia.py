# -*- coding: utf-8 -*-
"""Butlleti de vigilancia FMA — fase 5.1 del protocol.

Respon una pregunta i prou: **hi ha res que hagi de fer estar alerta els propers dies?**

Fa tres coses que a ma costen temps i es fan malament:
  1. Llegeix parametres convectius NUMERICS (CAPE, Lifted Index, CIN, precipitacio,
     ratxes) per a les tres seus i els tradueix a llenguatge planer.
  2. Consulta els avisos oficials d'AEMET (l'unic que dispara el protocol).
  3. ARXIVA els mapes de TRAM de MeteoUIB, que el web nomes conserva durant
     una execucio. Sense arxivar-los no es pot comparar cap dia amb l'anterior,
     que es la millor comprovacio manual d'incertesa que hi ha. Els passos es
     demanen en HORES DE PREVISIO (--passos); la conversio a index de fotograma
     la fa `idx_pas()`, perque no son la mateixa cosa. S'arxiven dos dominis:
     HR (acumulats, +72 h) i SR (linies convectives amb WIND_3hPRECIP, +48 h),
     que es el que mira el Grup de Meteorologia de la UIB.

I compara amb el butlleti d'ahir: si el senyal es repeteix, es solid; si balla
cada dia, encara no toca decidir res.

LIMITS, que son importants:
  - NO assigna nivells del Protocol FMA. Els nivells els posa AEMET i ningu mes.
  - NO es una prediccio meteorologica. Quan el Grup de Meteorologia de la UIB hi
    aporta la seva lectura, aquella preval; pero es una collaboracio voluntaria i
    no se n'espera una per a cada episodi.
  - Els parametres son d'un PUNT, no d'una area.
"""
from __future__ import annotations
import argparse, json, os, re, sys, urllib.request
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fma_base import DADES, SKILL_DIR, PROJECT_DIR

F_CONV = SKILL_DIR / "assets" / "llindars_convectius.json"
DEST = PROJECT_DIR / "vigilancia"
API = "https://api.open-meteo.com/v1/forecast"
TRAM = "https://meteo.uib.es/tram"
WRF = "https://meteo.uib.es/wrf"
UA = "UIB-SPREV-ProtocolFMA/1.0 (vigilancia)"
CAMPS = ["cape", "convective_inhibition", "lifted_index", "precipitation", "wind_gusts_10m"]

# Coordenades de les seus. Palma cobreix campus, Sa Riera, Ca n'Oleo i ParcBit,
# que son tots al mateix municipi i a la mateixa zona d'avis.
PUNTS = [
    ("campus_palma", "Palma (campus, Sa Riera, Ca n'Oleo, ParcBit)", 39.6386, 2.6483),
    ("seu_menorca", "Menorca (Alaior)", 39.9347, 4.1394),
    ("seu_eivissa", "Eivissa", 38.9067, 1.4361),
]


def _obre(url, timeout=30):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout)


def _carrega(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


# ── Interpretacio ────────────────────────────────────────────────────────────
def classifica(param, valor):
    """Situa un valor dins els rangs d'orientacio. Retorna (etiqueta, lectura)."""
    cfg = _carrega(F_CONV)["parametres"].get(param)
    if cfg is None or valor is None:
        return ("—", "")
    invers = cfg.get("invers", False)
    for r in cfg["rangs"]:
        lim = r["fins"]
        if lim is None:
            return (r["etiqueta"], r["lectura"])
        if (valor >= lim) if invers else (valor <= lim):
            return (r["etiqueta"], r["lectura"])
    return ("—", "")


def _dades_punt(lat, lon, dies):
    url = ("%s?latitude=%s&longitude=%s&hourly=%s&timezone=Europe/Madrid&forecast_days=%d"
           % (API, lat, lon, ",".join(CAMPS), dies))
    with _obre(url) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    h = d.get("hourly") or {}
    if not h:
        raise SystemExit("ERROR: l'API de dades numeriques no ha retornat res.\n%s" % d)
    per_dia = {}
    for i, t in enumerate(h["time"]):
        dia = t[:10]
        s = per_dia.setdefault(dia, {"hores": []})
        s["hores"].append({k: (h[k][i] if h[k][i] is not None else None) for k in CAMPS} | {"h": t[11:16]})
    resum = {}
    for dia, s in per_dia.items():
        hh = s["hores"]
        def mx(k):
            v = [x[k] for x in hh if x[k] is not None]
            return max(v) if v else None
        def mn(k):
            v = [x[k] for x in hh if x[k] is not None]
            return min(v) if v else None
        cape = mx("cape")
        # hora del pic de CAPE: es quan l'ambient esta mes carregat
        pic = None
        if cape is not None:
            for x in hh:
                if x["cape"] == cape:
                    pic = x["h"]
                    break
        resum[dia] = {
            "cape": cape, "cape_hora": pic,
            "lifted_index": mn("lifted_index"),
            "convective_inhibition": mn("convective_inhibition"),
            "precipitacio": round(sum(x["precipitation"] or 0 for x in hh), 1),
            "precip_max_h": mx("precipitation"),
            "ratxa_max": mx("wind_gusts_10m"),
        }
    return resum


def recull(dies=5):
    out = {"generat": datetime.now().isoformat(timespec="minutes"), "seus": {}}
    for sid, nom, lat, lon in PUNTS:
        out["seus"][sid] = {"nom": nom, "dies": _dades_punt(lat, lon, dies)}
    return out


def senyal(v):
    """Qualifica el senyal d'un dia i una seu creuant CAPE amb Lifted Index.

    Els dos parametres mesuren el mateix (inestabilitat) per vies diferents. Quan
    coincideixen, el senyal es fiable. Quan es contradiuen — CAPE alta amb LI
    positiu sol ser CAPE nocturna o marina, sense recorregut sobre terra de dia —
    el tool NO tria el mes alarmant: ho diu.

    Retorna None (res a dir) o (categoria, explicacio).
    """
    cape = v.get("cape") or 0
    li = v.get("lifted_index")
    hora = v.get("cape_hora")
    if cape < 300 and (li is None or li > -3):
        return None
    if li is None:
        return ("moderat", "Sense Lifted Index per contrastar.")
    if cape >= 300 and li > 0:
        return ("discrepant",
                "CAPE de %.0f J/kg però Lifted Index positiu (%.1f): els dos paràmetres "
                "es contradiuen. El pic de CAPE és a les %s, i si és de nit o damunt la mar "
                "sol no tenir recorregut sobre terra. **Senyal poc fiable: no en facis cas "
                "sense contrastar-ho.**" % (cape, li, hora or "—"))
    if cape >= 300 and li > -2:
        return ("feble", "CAPE de %.0f J/kg però Lifted Index de només %.1f: senyal fluix."
                % (cape, li))
    return ("clar", None)


def dies_de_risc(dades):
    """Dies amb senyal coherent. Els discrepants no compten com a risc."""
    risc = {}
    for sid, s in dades["seus"].items():
        for dia, v in s["dies"].items():
            sn = senyal(v)
            if sn and sn[0] in ("clar", "moderat"):
                risc.setdefault(dia, []).append(sid)
    return dict(sorted(risc.items()))


# ── Arxiu dels mapes de MeteoUIB ─────────────────────────────────────────────
# DOMINIS. TRAM en publica tres (mr, hr, sr) i fins al 16/09/2026 nomes miravem
# l'HR. El Grup de Meteorologia de la UIB ens va indicar que la seva referencia
# per a les linies convectives es l'SR, amb el camp WIND_3hPRECIP, que a l'HR no
# hi es. Cada domini te els seus camps i el seu horitzo.
DOMINIS = {
    "hr": {
        "horitzo_h": 72,
        "sonda": "SLP_3hPRECIP",
        "camps": ["SLP_3hPRECIP", "CI1050_CAPE", "WIND_PWA", "SRH700_CAPE",
                  "SLP_VIL", "SLP_H500", "TOTAL_PRECIP"],
        "enllacos": ("CI1050_CAPE", "WIND_PWA", "SRH700_CAPE", "SLP_3hPRECIP", "TOTAL_PRECIP"),
    },
    "sr": {
        "horitzo_h": 48,
        "sonda": "WIND_3hPRECIP",
        "camps": ["WIND_3hPRECIP", "CI1050_CAPE", "WIND_PWA", "SRH700_CAPE",
                  "WIND_VIL", "TOTAL_PRECIP"],
        "enllacos": ("WIND_3hPRECIP", "TOTAL_PRECIP", "CI1050_CAPE"),
    },
}
DOMINIS_DEFECTE = ("hr", "sr")

# Compatibilitat amb el codi que esperava la llista plana de l'HR.
CAMPS_TRAM = DOMINIS["hr"]["camps"]

# PASSOS. TRAM publica un fotograma cada 30 minuts, i el numero del fitxer es
# l'INDEX del fotograma, no l'hora: l'index 072 es +36 h, no +72 h. L'ultim es
# el 144, o sigui +72 h d'horitzo. Per no tornar-hi a caure, aqui dins tot es
# compta en HORES DE PREVISIO i la conversio es fa nomes a `idx_pas()`.
#
# ATENCIO amb la validesa: l'hora de previsio 0 NO es la que diu el nom de
# l'execucio. A la passada 260915_12, el fotograma 000 era valid a les 00 UTC
# del 16/09 (12 h despres del que indica el nom). L'origen no es dedueix del
# nom, aixi que la data i hora de validesa d'un mapa nomes son fiables llegides
# a la capcalera de la imatge mateixa.
IDX_PER_HORA = 2
HORITZO_MAX_H = 72
PASSOS_DEFECTE_H = (12, 18, 24, 30, 36, 42, 48)

# Cicles de TRAM, del mes tarda al mes matiner. Nomes se'n publica un, pero no
# sempre es el de les 00: el 15/09/2026 el viu era el _12, i buscar nomes _00
# feia avortar l'arxiu en silenci.
CICLES_TRAM = ("18", "12", "06", "00")


def idx_pas(hores, horitzo_h=HORITZO_MAX_H):
    """Hora de previsio -> index del fotograma, tal com el nomena TRAM."""
    i = int(round(float(hores) * IDX_PER_HORA))
    if not 0 <= i <= horitzo_h * IDX_PER_HORA:
        raise ValueError("hora de previsio fora de l'horitzo (0-%d h)" % horitzo_h)
    return "%03d" % i


def run_tram(dom="hr", quan=None):
    """Troba l'execucio publicada d'un domini. El web nomes en conserva una.

    L'index del domini porta un meta refresh que apunta a l'execucio viva: es la
    font autoritativa i estalvia endevinar. Si no es pot llegir, es proven els
    quatre cicles d'avui i d'ahir, del mes recent al mes antic.
    """
    sonda = DOMINIS[dom]["sonda"]
    try:
        with _obre("%s/%s/" % (TRAM, dom), timeout=15) as r:
            html = r.read().decode("utf-8", "replace")
        m = re.search(r"(\d{6}_\d{2})_[A-Za-z0-9_]+\.html", html)
        if m:
            return m.group(1)
    except Exception:
        pass
    base = quan or date.today()
    for delta in (0, 1):
        dia = (base - timedelta(days=delta)).strftime("%y%m%d")
        for cicle in CICLES_TRAM:
            rid = "%s_%s" % (dia, cicle)
            try:
                with _obre("%s/%s/%s_%s.html" % (TRAM, dom, rid, sonda), timeout=15):
                    return rid
            except Exception:
                continue
    return None


def arxiva_mapes(passos_h=PASSOS_DEFECTE_H, carpeta=None, dominis=DOMINIS_DEFECTE):
    """Arxiva els mapes dels dominis demanats.

    `passos_h` va en HORES DE PREVISIO, no en indexs. Cada domini te el seu
    horitzo: els passos que el depassen se salten i es reporten, no fan fallar
    la resta. Retorna (rid, total, detall) amb detall = {domini: (n, omesos)}.
    """
    rid = None
    dest_base = carpeta or (DEST / date.today().isoformat())
    total, detall = 0, {}

    for dom in dominis:
        if dom not in DOMINIS:
            print("[mapes] domini desconegut: %s" % dom, file=sys.stderr)
            continue
        cfg = DOMINIS[dom]
        r = run_tram(dom)
        if r is None:
            print("[mapes] %s: no s'ha trobat cap execucio publicada" % dom, file=sys.stderr)
            detall[dom] = (0, [])
            continue
        rid = rid or r
        if r != rid:
            print("[mapes] atencio: %s publica %s i un altre domini %s" % (dom, r, rid),
                  file=sys.stderr)

        dest = dest_base / dom
        os.makedirs(dest, exist_ok=True)
        n, omesos = 0, []
        for camp in cfg["camps"]:
            for h in passos_h:
                try:
                    pas = idx_pas(h, cfg["horitzo_h"])
                except ValueError:
                    if h not in omesos:
                        omesos.append(h)
                    continue
                desti = os.path.join(str(dest), "%s_%s_%s.gif" % (r, camp, pas))
                if os.path.exists(desti):
                    continue
                try:
                    with _obre("%s/%s/map/%s_%s_%s.gif" % (TRAM, dom, r, camp, pas),
                               timeout=25) as resp:
                        data = resp.read()
                    if len(data) < 500:
                        continue
                    with open(desti, "wb") as fh:
                        fh.write(data)
                    n += 1
                except Exception:
                    pass
        if omesos:
            print("[mapes] %s: %s h depassen l'horitzo del domini (%d h), omesos"
                  % (dom, ", ".join("+%g" % x for x in omesos), cfg["horitzo_h"]),
                  file=sys.stderr)
        detall[dom] = (n, omesos)
        total += n

    return rid, total, detall


# ── Historic i comparacio ────────────────────────────────────────────────────
def desa_historic(dades):
    os.makedirs(DEST, exist_ok=True)
    with open(DEST / ("%s.json" % date.today().isoformat()), "w", encoding="utf-8") as fh:
        json.dump(dades, fh, ensure_ascii=False, indent=1)


def historic_anterior():
    if not DEST.exists():
        return None
    fitxers = sorted(p for p in os.listdir(DEST) if p.endswith(".json"))
    avui = "%s.json" % date.today().isoformat()
    anteriors = [f for f in fitxers if f < avui]
    if not anteriors:
        return None
    with open(DEST / anteriors[-1], encoding="utf-8") as fh:
        return anteriors[-1][:-5], json.load(fh)


def compara(dades, prev):
    """Compara el CAPE previst per als mateixos dies. El senyal es solid si es repeteix."""
    if not prev:
        return []
    _, ant = prev
    linies = []
    for sid, s in dades["seus"].items():
        a = (ant.get("seus", {}).get(sid) or {}).get("dies", {})
        for dia, v in sorted(s["dies"].items()):
            if dia not in a:
                continue
            nou, vell = v["cape"] or 0, a[dia]["cape"] or 0
            if max(nou, vell) < 300:
                continue
            dif = nou - vell
            if abs(dif) < 200:
                tend = "estable"
            elif dif > 0:
                tend = "a l'alça (+%.0f)" % dif
            else:
                tend = "a la baixa (%.0f)" % dif
            linies.append((dia, s["nom"], vell, nou, tend))
    return linies


# ── Sortida ──────────────────────────────────────────────────────────────────
DIES_CA = ["dilluns", "dimarts", "dimecres", "dijous", "divendres", "dissabte", "diumenge"]


def _dnom(iso):
    d = date.fromisoformat(iso)
    return "%s %s" % (DIES_CA[d.weekday()], d.strftime("%d/%m"))


def butlleti(dades, avisos=None, prev=None, rid=None, nmapes=0, passos_h=None, detall=None):
    cfg = _carrega(F_CONV)
    L = ["# Butlletí de vigilància FMA", "",
         "_%s · Servei de Prevenció, UIB_" % dades["generat"].replace("T", " "), "",
         "> Respon només a: **hi ha res que hagi de fer estar alerta?** "
         "No assigna cap nivell del protocol — això ho fa AEMET — ni substitueix la "
         "lectura del Grup de Meteorologia de la UIB quan aquest hi col·labora.", ""]

    # 1. Avisos oficials
    L += ["## 1. Avisos oficials d'AEMET", ""]
    if avisos is None:
        L.append("_No consultats en aquesta execució._")
    elif not avisos:
        L.append("**Cap avís vigent** per a les Balears. "
                 "AEMET només publica amb 3 dies de marge: més enllà, no hi haurà avís encara.")
    else:
        L.append("| Nivell | Fenomen | Zona | Valor | Vigència |")
        L.append("|---|---|---|---|---|")
        for a in avisos:
            L.append("| **%s** | %s | %s | %s | %s → %s |" % (
                a["nivell"].upper(), a["fenomen"], a["zona_nom"] or a["zona_desc"],
                (a.get("parametre") or {}).get("valor") or "—",
                a["onset"][:16].replace("T", " "), a["expires"][:16].replace("T", " ")))
        L.append("")
        L.append("> Hi ha avisos actius: munta l'episodi amb `episodi.py`.")
    L.append("")

    # 2. Ambient convectiu
    risc = dies_de_risc(dades)
    L += ["## 2. Ambient convectiu previst", ""]
    if not risc:
        L.append("**Atmosfera estable els propers dies** a les tres seus. "
                 "Cap senyal d'inestabilitat rellevant.")
    else:
        L.append("Dies amb ambient no marginal: **%s**." %
                 ", ".join(_dnom(d) for d in risc))
    L.append("")
    L.append("| Dia | Seu | CAPE | Lectura | LI | Precip. | Ratxa |")
    L.append("|---|---|---|---|---|---|---|")
    dies = sorted({d for s in dades["seus"].values() for d in s["dies"]})
    for dia in dies:
        for sid, s in dades["seus"].items():
            v = s["dies"].get(dia)
            if not v:
                continue
            sn = senyal(v)
            if sn and sn[0] == "discrepant":
                et = "senyal contradictori"
            elif sn and sn[0] == "feble":
                et = "senyal fluix"
            else:
                et, _ = classifica("cape", v["cape"])
            marca = "**" if (sn and sn[0] in ("clar", "moderat")) else ""
            L.append("| %s | %s | %s%s%s | %s | %s | %.1f mm | %.0f km/h |" % (
                _dnom(dia), s["nom"].split(" (")[0],
                marca, "%.0f" % v["cape"] if v["cape"] is not None else "—", marca,
                et, "%.1f" % v["lifted_index"] if v["lifted_index"] is not None else "—",
                v["precipitacio"] or 0, v["ratxa_max"] or 0))
    L.append("")

    # 3. Lectura en llenguatge planer
    L += ["## 3. Què vol dir", ""]
    hi_ha_res = False
    for dia in dies:
        frases = []
        for sid, s in dades["seus"].items():
            v = s["dies"].get(dia)
            if not v:
                continue
            pr = v["precipitacio"] or 0
            nom = s["nom"].split(" (")[0]
            sn = senyal(v)
            if sn:
                cat, nota = sn
                if cat == "discrepant":
                    frases.append("**%s**: %s" % (nom, nota))
                    continue
                _, lect = classifica("cape", v["cape"])
                extra = ""
                if cat == "feble":
                    extra = " " + nota
                elif pr < 2:
                    extra = (" El model preveu poca pluja en aquest punt, però amb aquesta "
                             "inestabilitat això vol dir que no sap on posarà la cèl·lula, "
                             "no que no en caurà.")
                elif pr >= 15:
                    extra = " I a més preveu precipitació abundant al punt (%.0f mm)." % pr
                frases.append("**%s**: %s (pic a les %s).%s"
                              % (nom, lect, v.get("cape_hora") or "—", extra))
            elif pr >= 10:
                frases.append("**%s**: pluja prevista (%.0f mm) amb atmosfera estable — "
                              "seria pluja de front, persistent però poc violenta." % (nom, pr))
        if frases:
            hi_ha_res = True
            L.append("**%s**" % _dnom(dia))
            for f in frases:
                L.append("- %s" % f)
            L.append("")
    if not hi_ha_res:
        L.append("_Cap senyal d'inestabilitat rellevant als paràmetres consultats._")
        L.append("")
        L.append("> Això **no és una conclusió tancada**. A la Mediterrània hi ha episodis severs "
                 "amb CAPE de superfície molt baixa: la convecció pot ser elevada, per damunt d'una "
                 "capa estable, i el CAPE de superfície no la veu. Si el Grup de Meteorologia "
                 "diu una cosa i aquest butlletí en diu una altra, **té raó el Grup**.")
        L.append("")

    # 4. Estabilitat del senyal
    L += ["## 4. El senyal es repeteix?", ""]
    comp = compara(dades, prev)
    if prev is None:
        L.append("_Primera execució: encara no hi ha res amb què comparar. "
                 "A partir de demà, aquesta secció dirà si el senyal es manté._")
    elif not comp:
        L.append("_Cap dia amb inestabilitat rellevant per comparar amb el butlletí del %s._" % prev[0])
    else:
        L.append("Comparació del CAPE amb el butlletí del **%s**:" % prev[0])
        L.append("")
        L.append("| Dia | Seu | Abans | Ara | Tendència |")
        L.append("|---|---|---|---|---|")
        for dia, nom, vell, nou, tend in comp:
            L.append("| %s | %s | %.0f | %.0f | %s |" % (_dnom(dia), nom.split(" (")[0], vell, nou, tend))
        L.append("")
        L.append("> Un senyal que es repeteix dia rere dia és sòlid. Un que balla, encara no.")
    L.append("")

    # 5. Mapes arxivats
    L += ["## 5. Mapes de MeteoUIB", ""]
    if rid:
        L.append("Execució **%s**, %d imatges noves a `PROJECTS/Protocol FMA/vigilancia/%s/`."
                 % (rid, nmapes, date.today().isoformat()))
        L.append("")
        if passos_h:
            L.append("Passos: **%s hores de previsió**. Els fitxers duen l'índex del "
                     "fotograma, que és el DOBLE de l'hora (`_096` és +48 h). L'hora de "
                     "validesa consta a la capçalera de cada imatge."
                     % ", ".join("+%g" % h for h in passos_h))
            L.append("")
        L.append("| Domini | Horitzó | Camps | Imatges noves | Per a què |")
        L.append("|---|---|---|---|---|")
        _us = {"hr": "acumulats i situació sinòptica",
               "sr": "línies convectives (referència del Grup de Meteorologia)"}
        for dom in (detall or {}):
            dcfg = DOMINIS.get(dom)
            if not dcfg:
                continue
            ndom, omesos = detall[dom]
            L.append("| `%s` | +%d h | %d | %d%s | %s |"
                     % (dom, dcfg["horitzo_h"], len(dcfg["camps"]), ndom,
                        (" (%s h omeses)" % ", ".join("+%g" % x for x in omesos)) if omesos else "",
                        _us.get(dom, "")))
        L.append("")
        L.append("Per mirar-los en viu (només hi ha l'execució més recent):")
        L.append("")
        for dom in (detall or {}):
            dcfg = DOMINIS.get(dom)
            if not dcfg:
                continue
            for c in dcfg["enllacos"]:
                L.append("- `%s/%s/%s_%s.html`" % (TRAM, dom, rid, c))
    else:
        L.append("_No s'ha pogut arxivar cap mapa._")
    L.append("")

    L += ["---", "",
          "**D'on surten aquests rangs.** De cap organisme oficial: són convenció de la "
          "meteorologia operativa, derivada en bona part de la convecció severa nord-americana. "
          "Els únics llindars amb valor normatiu són els d'AEMET (Decret 106/2006), i són els "
          "de `llindars.json`, en mm/h i km/h.",
          "",
          "**I per què no els pots agafar al peu de la lletra aquí.** A la Mediterrània hi ha "
          "hagut tempestes severes amb CAPE de superfície d'unes 62 J/kg — el que aquesta taula "
          "anomena *estable*. Ho documenten Cohuet, Romero, Homar, Ducrocq i Ramis (2011), "
          "*Initiation of a severe thunderstorm over the Mediterranean Sea*; tres dels autors són "
          "del Departament de Física de la UIB. Aquí pesen tant o més l'aigua precipitable i el "
          "forçament dinàmic que no pas el CAPE.",
          "",
          "**Rangs en versió %s, pendents de validació.** Convindria que els revisàs el Grup de "
          "Meteorologia de la UIB si hi vol col·laborar. Fins llavors són orientació interna: "
          "no són criteri del Servei." % cfg["_versio"]]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Butlletí de vigilància FMA")
    ap.add_argument("--dies", type=int, default=5, help="dies de previsió (per defecte 5)")
    ap.add_argument("--sense-avisos", action="store_true", help="no consultis AEMET")
    ap.add_argument("--sense-mapes", action="store_true", help="no arxivis els mapes de MeteoUIB")
    ap.add_argument("--passos", metavar="HORES", default=None,
                    help="hores de previsió a arxivar, separades per comes (per defecte: %s). "
                         "Van en hores, no en índexs de fotograma. Cada domini té el seu "
                         "horitzó (hr +72 h, sr +48 h) i els passos que el depassen se salten."
                         % ",".join(str(h) for h in PASSOS_DEFECTE_H))
    ap.add_argument("--dominis", metavar="LLISTA", default=",".join(DOMINIS_DEFECTE),
                    help="dominis de TRAM a arxivar, separats per comes (%s). "
                         "`hr` dona acumulats i situació sinòptica; `sr` dona les línies "
                         "convectives (WIND_3hPRECIP), que és la referència del Grup de "
                         "Meteorologia de la UIB." % "|".join(sorted(DOMINIS)))
    ap.add_argument("--desa", metavar="RUTA", help="desa el butlletí en Markdown. "
                    "Conveni: vigilancia/butlleti-vigilancia-AAAA-MM-DD.md")
    ap.add_argument("--json", action="store_true", help="surt en JSON")
    args = ap.parse_args()

    dades = recull(args.dies)

    avisos = None
    if not args.sense_avisos:
        try:
            import avisos as A
            avisos, _ = A.parse_avisos(A.descarrega_cap())
        except SystemExit:
            avisos = []
        except Exception as e:
            print("[avisos] no s'han pogut consultar: %s" % e, file=sys.stderr)

    passos_h = PASSOS_DEFECTE_H
    if args.passos:
        try:
            passos_h = tuple(float(x) for x in args.passos.replace(" ", "").split(",") if x)
            for h in passos_h:
                idx_pas(h)
        except ValueError as e:
            sys.exit("[passos] %s" % e)

    dominis = tuple(d for d in args.dominis.replace(" ", "").split(",") if d)
    desconeguts = [d for d in dominis if d not in DOMINIS]
    if desconeguts:
        sys.exit("[dominis] desconegut(s): %s. Disponibles: %s"
                 % (", ".join(desconeguts), ", ".join(sorted(DOMINIS))))

    rid, n, detall = (None, 0, None)
    if not args.sense_mapes:
        rid, n, detall = arxiva_mapes(passos_h, dominis=dominis)

    prev = historic_anterior()
    desa_historic(dades)

    if args.json:
        print(json.dumps({"dades": dades, "avisos": avisos, "run_tram": rid,
                          "mapes": {d: v[0] for d, v in (detall or {}).items()}},
                         ensure_ascii=False, indent=1))
        return

    txt = butlleti(dades, avisos, prev, rid, n,
                   passos_h if not args.sense_mapes else None, detall)
    if args.desa:
        os.makedirs(os.path.dirname(os.path.abspath(args.desa)), exist_ok=True)
        with open(args.desa, "w", encoding="utf-8") as fh:
            fh.write(txt)
        print("[desat] %s" % args.desa, file=sys.stderr)
    print(txt)


if __name__ == "__main__":
    main()
