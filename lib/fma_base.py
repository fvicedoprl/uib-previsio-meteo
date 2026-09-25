# -*- coding: utf-8 -*-
"""Rutes i carrega de la capa de dades del Protocol FMA.

Cap dada del protocol no viu dins la skill: tot es llegeix de PROJECTS/Protocol FMA,
que es on Xicu regenera els JSON des de l'Excel (font unica). L'unica excepcio es
assets/seus.json, que es el pont zona AEMET <-> seu UIB i no existeix enlloc mes.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

# La consola de Windows sol anar en cp1252: forcem UTF-8 per no trencar accents.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

SKILL_DIR = Path(__file__).resolve().parent.parent

# Es pot sobreescriure amb la variable d'entorn FMA_PROJECT_DIR (util per a proves).
PROJECT_DIR = Path(os.environ.get(
    "FMA_PROJECT_DIR",
    str(Path(__file__).resolve().parent.parent / "dades")))

PLAYBOOK   = PROJECT_DIR / "playbook_work"
GABINET    = PROJECT_DIR / "FMA - Checklist Gabinet"
MONITOR    = PROJECT_DIR / "AVISOS PROTOCOL FMA"
EPISODIS   = PROJECT_DIR / "episodis"

F_MESURES     = PLAYBOOK / "bd_checklist.json"
F_LLINDARS    = PLAYBOOK / "llindars.json"
F_COMUNICATS  = PLAYBOOK / "comunicats.json"
F_ZONES       = PLAYBOOK / "zones_aemet.json"
F_CASUISTIQUES= GABINET  / "casuistiques.json"
F_SEUS        = SKILL_DIR / "assets" / "seus.json"

NIVELLS = ["groc", "taronja", "transicio", "vermell"]
NIVELL_AEMET = {"amarillo": "groc", "naranja": "taronja", "rojo": "vermell"}

# eventCode <AEMET-Meteoalerta fenomeno> -> fenomen del Protocol FMA.
# Els None son fenomens que AEMET emet pero que el protocol NO cobreix:
# no es descarten en silenci, es reporten a l'usuari.
FENOMEN_AEMET = {
    "PR": "pluja_inundacions",   # Lluvias
    "TO": "tempestes",           # Tormentas
    "VI": "vent",                # Vientos
    "AT": "calor",               # Temperaturas maximas
    "BT": "neu_fred",            # Temperaturas minimas
    "NE": "neu_fred",            # Nevadas
    "CO": "costaners",           # Costeros
    "NI": None,                  # Nieblas
    "VS": None,                  # Polvo en suspension
    "RI": None,                  # Rissagues (oscil.lacio del nivell de la mar)
}
FENOMEN_NOM = {
    "pluja_inundacions": "Pluges intenses / inundacions",
    "vent": "Vent fort",
    "tempestes": "Tempestes elèctriques",
    "calor": "Onada de calor",
    "neu_fred": "Fred extrem, neu i gel",
    "costaners": "Fenòmens costaners",
    "general": "Mesures generals",
}
# Fenomens que, segons el protocol, no tanquen mai cap seu (ni en vermell).
FENOMENS_SENSE_TANCAMENT = {"calor", "costaners"}


class DadesFMA:
    """Carrega mandrosa de tota la capa de dades, amb error clar si falta un fitxer."""

    def __init__(self):
        self._cache = {}

    def _load(self, clau, path):
        if clau not in self._cache:
            if not path.exists():
                raise SystemExit(
                    f"ERROR: no es troba {path}\n"
                    f"       Comprova la ruta del projecte o exporta FMA_PROJECT_DIR."
                )
            with open(path, encoding="utf-8") as fh:
                self._cache[clau] = json.load(fh)
        return self._cache[clau]

    @property
    def mesures(self):      return self._load("mesures", F_MESURES)
    @property
    def llindars(self):     return self._load("llindars", F_LLINDARS)
    @property
    def comunicats(self):   return self._load("comunicats", F_COMUNICATS)
    @property
    def zones(self):        return self._load("zones", F_ZONES)
    @property
    def casuistiques(self): return self._load("casuistiques", F_CASUISTIQUES)
    @property
    def seus(self):         return self._load("seus", F_SEUS)

    # ── accessos derivats ────────────────────────────────────────────────────
    def zona_per_codi(self, codi):
        for z in self.zones["zones"]:
            if z["codi"] == codi:
                return z
        return None

    def zona_per_nom(self, nom):
        n = (nom or "").strip().lower()
        for z in self.zones["zones"]:
            if n in (z["nom_aemet"].lower(), z["nom_ca"].lower()):
                return z
        return None

    def seu_per_id(self, sid):
        for s in self.seus["seus"]:
            if s["id"] == sid:
                return s
        return None

    def llindar(self, fenomen, nivell):
        return (self.llindars["llindars"].get(fenomen, {}) or {}).get(nivell, {}).get("text", "")


DADES = DadesFMA()
