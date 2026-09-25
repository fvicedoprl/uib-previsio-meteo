# -*- coding: utf-8 -*-
"""Radar i llamps d'AEMET — observacio, no prediccio.

Serveix per tancar el bloc C1 del criteri operatiu de suspensio, que amb
probabilitat 40-70% exigeix "confirmacio per radar i prediccio immediata amb
trajectoria cap al campus durant les sis hores seguents". Fins al 16/09/2026
aquest bloc no es podia acreditar de cap manera.

Cal la variable d'entorn AEMET_API_KEY (JWT gratuit d'opendata.aemet.es).

PRODUCTES I ELS SEUS LIMITS, que son molt diferents entre si:

  - radar regional `pm` (Palma): imatge de reflectivitat PPI que cobreix TOTES
    les Balears, Eivissa i Menorca incloses. Es el producte util per a nosaltres.
    Es una FOTO d'un instant: per veure trajectoria calen diverses passades
    seguides, i per aixo s'arxiven amb l'hora al nom.

  - radar nacional: AEMET retorna 404 de manera intermitent (comprovat el
    16/09/2026 amb clau valida, que hauria donat 401 si el problema fos la clau).
    No s'hi pot confiar; el regional `pm` ja cobreix les illes.

  - mapa de llamps: **NO es temps real**. Es un acumulat de 12 hores que acaba a
    les 00 o les 12 UTC, o sigui que pot anar fins a mitja jornada endarrerit.
    Serveix per saber on hi ha hagut activitat electrica, no per fer nowcasting.
    Digues-ho sempre que el facis servir.

El radar NO diu el que passara. Diu el que hi ha ara. La trajectoria l'ha de
llegir una persona comparant passades, i la decisio es del Gabinet.
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.request
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fma_base import PROJECT_DIR

API = "https://opendata.aemet.es/opendata/api"
DEST = PROJECT_DIR / "vigilancia"
UA = "UIB-SPREV-ProtocolFMA/1.0 (radar)"

# Codis de radar regional d'AEMET, de dues lletres. `pm` = Palma, i es l'unic
# que ens interessa: el seu abast cobreix Mallorca, Menorca i les Pitiuses.
RADAR_BALEARS = "pm"

PRODUCTES = {
    "radar": {"ruta": "red/radar/regional/" + RADAR_BALEARS, "nom": "radar-pm",
              "temps_real": True},
    "llamps": {"ruta": "red/rayos/mapa", "nom": "llamps",
               "temps_real": False},
}


def clau():
    k = os.environ.get("AEMET_API_KEY")
    if not k:
        sys.exit("[radar] falta AEMET_API_KEY a l'entorn. Si l'acabes de posar, "
                 "reinicia l'aplicacio: els processos hereten l'entorn en arrencar.")
    if k.count(".") != 2 or not k.startswith("eyJ"):
        sys.exit("[radar] AEMET_API_KEY no te forma de JWT (tres blocs separats per punts)")
    return k


def _obre(url, headers=None, timeout=30):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})}),
        timeout=timeout)


def descarrega(producte="radar", carpeta=None, reintents=2):
    """Baixa un producte i el desa amb l'hora al nom. Retorna la ruta o None.

    AEMET talla la connexio de tant en tant (RemoteDisconnected) sense que sigui
    un error nostre. Es reintenta un parell de vegades abans de rendir-se, i si
    no se'n surt es retorna None en lloc de petar: una passada perduda no ha de
    tombar tota una sessio de vigilancia que en espera quatre.
    """
    if producte not in PRODUCTES:
        sys.exit("[radar] producte desconegut: %s (n'hi ha: %s)"
                 % (producte, ", ".join(PRODUCTES)))
    p = PRODUCTES[producte]
    ultim_error = None
    for intent in range(reintents + 1):
        try:
            with _obre("%s/%s" % (API, p["ruta"]), {"api_key": clau()}) as r:
                meta = json.loads(r.read().decode("utf-8", "replace"))
            if meta.get("estado") != 200 or not meta.get("datos"):
                print("[radar] AEMET no serveix %s ara mateix: estat=%s, %s"
                      % (producte, meta.get("estado"), meta.get("descripcion")),
                      file=sys.stderr)
                return None
            with _obre(meta["datos"]) as r:      # URL temporal publica, sense clau
                dades = r.read()
            dest = carpeta or (DEST / date.today().isoformat() / "radar")
            os.makedirs(dest, exist_ok=True)
            ruta = os.path.join(str(dest),
                                "%s-%s.gif" % (p["nom"], datetime.now().strftime("%H%M")))
            with open(ruta, "wb") as fh:
                fh.write(dades)
            return ruta
        except Exception as e:
            ultim_error = e
            if intent < reintents:
                time.sleep(3)
    print("[radar] %s: no s'ha pogut baixar despres de %d intent(s): %s"
          % (producte, reintents + 1, ultim_error), file=sys.stderr)
    return None


def passades_arxivades(producte="radar", dia=None):
    """Les imatges ja arxivades avui, en ordre. Comparar-les es l'unica manera
    de deduir trajectoria: una sola imatge no en dona cap."""
    d = DEST / (dia or date.today().isoformat()) / "radar"
    if not d.exists():
        return []
    nom = PRODUCTES[producte]["nom"]
    return sorted(str(p) for p in d.iterdir() if p.name.startswith(nom))


def main():
    ap = argparse.ArgumentParser(description="Radar i llamps d'AEMET per al Protocol FMA")
    ap.add_argument("--producte", default="radar", choices=sorted(PRODUCTES),
                    help="radar (temps real, Balears) o llamps (acumulat 12 h, endarrerit)")
    ap.add_argument("--tots", action="store_true", help="baixa tots els productes")
    ap.add_argument("--llista", action="store_true", help="llista les passades d'avui")
    args = ap.parse_args()

    if args.llista:
        for p in sorted(PRODUCTES):
            fitxers = passades_arxivades(p)
            print("%s: %d passades%s" % (p, len(fitxers),
                  (" — " + ", ".join(os.path.basename(f) for f in fitxers)) if fitxers else ""))
        return

    for p in (sorted(PRODUCTES) if args.tots else [args.producte]):
        ruta = descarrega(p)
        if ruta:
            avis = "" if PRODUCTES[p]["temps_real"] else "  [ATENCIO: acumulat 12 h, NO temps real]"
            print("%s -> %s%s" % (p, ruta, avis))
    n = len(passades_arxivades("radar"))
    if n < 2:
        print("[radar] nomes hi ha %d passada(es) de radar avui: amb una sola imatge "
              "NO es pot deduir trajectoria, que es el que demana el bloc C1." % n,
              file=sys.stderr)


if __name__ == "__main__":
    main()
