# -*- coding: utf-8 -*-
"""Punt d'entrada del repositori de previsio meteorologica de la UIB.

El workflow executa aixo cada 30 minuts. Tota la logica es a lib/previsio.py;
aqui nomes es fixen les rutes del repositori perque els moduls no busquin res a
la unitat G: del Servei de Prevencio.

Proves en local (no envia res ni toca l'estat):
    python executa.py --dryrun --forca
"""
import os
import sys
from pathlib import Path

ARREL = Path(__file__).resolve().parent
os.environ["FMA_PROJECT_DIR"] = str(ARREL / "dades")
sys.path.insert(0, str(ARREL / "lib"))

import previsio  # noqa: E402

args = ["--config", str(ARREL / "config.json"),
        "--estat", str(ARREL / "state.json"),
        "--meteouib", str(ARREL / "meteouib.md"),
        "--sortida", str(ARREL / "sortida")] + sys.argv[1:]
sys.exit(previsio.main(args))
