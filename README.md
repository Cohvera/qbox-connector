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

De bestaande, geteste connector is overgebracht uit Portal. Dit is **nog geen installeerbare LoxBerry-plugin**. Een pluginconfiguratie, webinstellingen, installatie/update-afhandeling en een release-ZIP moeten nog worden gebouwd. Meerdere onafhankelijke Q-boxen met verschillende doelbedrijven vereisen ook een sleutel-naar-bedrijfskoppeling in Portal; momenteel bepaalt `QBOX_IMPORT_COMPANY` op de Portal-server het doelbedrijf.

Bestaande Q-box-installaties blijven werken. Het verplaatsen van de broncode wijzigt geen draaiende service of productieconfiguratie.

## Testen

Vanuit deze repository:

```sh
python3 -m unittest discover -s . -p 'test_*.py'
python3 -m compileall -q qbox_sync.py
```

## Installatie en updates

Volg [de installatiehandleiding](docs/INSTALLATIE.md). Het script blijft op de Q-box op `/opt/cohvera-qbox/qbox_sync.py` staan; instellingen blijven op `/etc/cohvera-qbox/qbox.env`.

Commit nooit echte `.env`-bestanden, API-sleutels, certificaatsleutels, runtime-status of productie-exportbestanden. Kopieer bij een update de bestaande apparaatconfiguratie niet terug naar deze repository.
