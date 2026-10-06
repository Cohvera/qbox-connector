# Cohvera Q-box Connector

Lichte Python-connector die Plenion-data vanaf het bedrijfsnetwerk via HTTPS naar Cohvera stuurt. Python 3.10+; geen pip-pakketten of Docker nodig.

## Wat staat waar?

| Bestand | Functie |
| --- | --- |
| `qbox_sync.py` | Bron lezen, controleren en synchroniseren |
| `test_qbox.py` | Tests zonder productieverbinding |
| `qbox.env.example` | Voorbeeldinstellingen, zonder sleutel |
| `cohvera-qbox.service` | Automatisch draaien via systemd |
| `docs/INSTALLATIE.md` | Installatie en updates per machine |

De ontvangende API, bedrijfsrechten en tv-weergave blijven in [Cohvera/Portal](https://github.com/Cohvera/Portal). Deze repository is voortaan de enige onderhoudsplek voor de connectorcode.

## Status

Er is een eerste installeerbare **LoxBerry-plugin (0.1.0)** met configuratiescherm, verbindingscontrole, handmatig synchroniseren, automatische synchronisatie en statusoverzicht. De plugin neemt een bestaande standalone-configuratie over en bewaart instellingen bij een pluginupdate. De code is lokaal getest; installatie op echte LoxBerry-hardware moet nog als pilot worden gecontroleerd.

Meerdere onafhankelijke Q-boxen met verschillende doelbedrijven vereisen ook een sleutel-naar-bedrijfskoppeling in Portal; momenteel bepaalt `QBOX_IMPORT_COMPANY` op de Portal-server het doelbedrijf.

Bestaande Q-box-installaties blijven werken. Het verplaatsen van de broncode wijzigt geen draaiende service of productieconfiguratie.

## Testen

Vanuit deze repository:

```sh
python3 -m unittest discover -s . -p 'test_*.py'
python3 -m compileall -q qbox_sync.py plugin/bin scripts
python3 scripts/build_plugin.py
```

## Installatie en updates

Voor de plugin: volg [LoxBerry installeren en configureren](docs/LOXBERRY.md). Upload het gebouwde `dist/cohvera-qbox-0.1.0.zip` in het pluginbeheer van LoxBerry. Bronbestanden staan in `plugin/`; het pakket neemt `qbox_sync.py` automatisch mee.

Voor de bestaande standalone-installatie: volg [de scripthandleiding](docs/INSTALLATIE.md). Daar staat het script op `/opt/cohvera-qbox/qbox_sync.py`, met instellingen in `/etc/cohvera-qbox/qbox.env`.

Commit nooit echte `.env`-bestanden, API-sleutels, certificaatsleutels, runtime-status of productie-exportbestanden. Kopieer bij een update de bestaande apparaatconfiguratie niet terug naar deze repository.
