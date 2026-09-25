# Previsió meteorològica — seus de la UIB

Quan AEMET activa un avís de nivell **taronja o superior** damunt una zona on hi ha
una seu de la Universitat de les Illes Balears (Palma, Menorca, Eivissa), aquest
repositori elabora una previsió meteorològica per seu i l'envia per correu. La torna a
enviar cada vegada que la situació canvia, envia un «sense canvis» si passen 6 hores
sense novetats, i un darrer missatge quan els avisos s'acaben.

Funciona sol amb GitHub Actions, és gratuït i no fa servir intel·ligència artificial:
el text surt de regles fixes.

## Què és i què no és

- **És una síntesi de fonts públiques**, no una predicció pròpia. El cos és la
  predicció horària municipal d'AEMET, que en permet la reproducció citant-la com a
  autora. Cada xifra porta la font.
- **Només parla del temps.** No conté mesures, instruccions ni decisions sobre
  l'activitat, i es pot reenviar a qualsevol persona.
- **No depèn del rastrejador d'avisos** del Protocol FMA ni el modifica.

| Font | Paper |
|---|---|
| Avisos d'AEMET (CAP) | Què hi ha activat, on i fins quan |
| Predicció horària municipal d'AEMET | Cos de la previsió, per franges de 6 hores |
| Open-Meteo (dades obertes) | Ambient atmosfèric: si hi ha energia per a tempestes |
| Model TRAM, Grup de Meteorologia de la UIB | Mapes adjunts |
| Radar i llamps d'AEMET | Imatges adjuntes (observació, no predicció) |
| Grup de Meteorologia de la UIB | Citació literal, si n'hi ha una aportació recent |

## Posada en marxa

1. **Crea el repositori** a GitHub, **públic** (als privats del pla gratuït, GitHub
   descarta moltes execucions programades).
2. **Secrets** (Settings → Secrets and variables → Actions → New repository secret):

   | Secret | Valor |
   |---|---|
   | `AEMET_API_KEY` | Clau d'AEMET OpenData. **Recomanat: una clau pròpia**, diferent de la del rastrejador (és gratuïta a opendata.aemet.es): comparteixen quota per minut. |
   | `EMAIL_FROM` | Compte que envia |
   | `EMAIL_PASSWORD` | Contrasenya d'aplicació d'aquest compte |
   | `EMAIL_SMTP_HOST` | p. ex. `smtp.gmail.com` |
   | `EMAIL_SMTP_PORT` | p. ex. `587` |
   | `EMAIL_TO` | Destinataris, separats per comes. S'envia en **còpia oculta**: ningú no veu les adreces dels altres. |

3. **Puja el contingut** d'aquesta carpeta a la branca principal.
4. **Primera prova:** Actions → *Previsió meteorològica — seus de la UIB* → *Run
   workflow*, amb `dryrun` marcat. Mira el registre: hi surt el document sencer i no
   s'envia res.
5. **Prova real:** *Run workflow* amb `forca` marcat i `dryrun` desmarcat. Arriba el
   correu encara que no hi hagi avís.

A partir d'aquí, s'executa sol cada 30 minuts.

## Ús diari

- **Aportació del Grup de Meteorologia:** edita `meteouib.md` (des del mòbil, a la web
  o l'app de GitHub): posa-hi la data i enganxa el text del correu. Surt citat a la
  previsió següent i compta com a canvi.
- **Enviar-la ara:** *Run workflow* amb `forca`.
- **Aturar-ho:** Actions → el workflow → `···` → *Disable workflow*.
- **Canviar el llindar, el batec o els mapes:** `config.json`.
- **Afegir o treure destinataris:** el Secret `EMAIL_TO`.

## Quan envia

| Situació | Què fa |
|---|---|
| Cap avís del llindar damunt una seu | Res |
| Primer avís | Envia |
| Canvia un avís, la previsió d'AEMET, l'ambient o hi ha aportació nova del Grup | Envia (mínim 90 min entre correus; una pujada de nivell se'l salta) |
| Un avís s'acaba a l'hora prevista | Ho diu al correu següent, però no n'envia un només per això |
| 6 hores sense canvis, entre les 8 i les 21 h | Envia «sense canvis» |
| S'acaben els avisos | Envia un darrer missatge i s'atura |

## Manteniment

El codi font canònic és a la skill `gestio-episodi-fma` del Servei de Prevenció
(`scripts/previsio.py`). Per actualitzar aquest repositori, es regenera amb
`scripts/empaqueta_previsio.py` i es puja. No editis `lib/` directament.
