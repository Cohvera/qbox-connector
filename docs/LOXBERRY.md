# LoxBerry-plugin installeren

Versie 0.1.0 is een eerste pilot. Vereist: LoxBerry 3+, Python 3.10+ en systemd. De lokale tests controleren configuratie, toegangscontrole, verpakking, servicegedrag en upgradebackup. De volledige installatie moet nog op een echte LoxBerry worden gecontroleerd.

## 1. Op je Mac: installatiepakket

Vanuit de map `qbox-connector`:

```sh
python3 -m unittest discover -s . -p 'test_*.py'
python3 scripts/build_plugin.py
```

Het resultaat staat in `dist/cohvera-qbox-0.1.0.zip`, met een SHA-256-controlebestand ernaast. Upload de ZIP als geheel; uitpakken is niet nodig. De ZIP bevat geen persoonlijke instellingen of API-sleutels.

## 2. Op de Q-box: installeren

1. Open de LoxBerry-webinterface op `192.168.10.78` en meld je aan.
2. Open het pluginbeheer, kies installeren via ZIP en upload het pakket.
3. Wacht op een geslaagde installatie en open **Cohvera Q-box**.

Bij een bestaande standalone-installatie neemt de plugin `/etc/cohvera-qbox/qbox.env` over. Hij schakelt `cohvera-qbox.service` uit voordat de nieuwe service start. Het oude script en env-bestand blijven behouden. Een actieve oude connector wordt automatisch ingeschakeld in de nieuwe plugin. Een nieuwe installatie begint gepauzeerd.

Als de nieuwe service niet start, probeert de installer de vorige standalone-service terug te activeren. Controleer na installatie altijd de status en één bevestigde import; een lokaal gebouwde ZIP is nog geen bevestiging dat de plugin op jouw hardware draait.

## 3. In het configuratiescherm

- **Cohvera-portaaladres:** het publieke HTTPS-adres, zonder `/api` achteraan.
- **Importsleutel:** dezelfde waarde als `QBOX_IMPORT_API_KEY` in de beveiligde Portal-serverconfiguratie. Een leeg veld bij later opslaan behoudt de bestaande sleutel.
- **Bronmodus:** Central Data Hub voor alle huidige projecten, of de oude tv-export als terugvaloptie.
- **Bronadressen:** de bestaande LAN-adressen; HTTP is mogelijk binnen het LAN. Naar Cohvera blijft HTTPS verplicht.
- **Controle-interval:** bijvoorbeeld 60 seconden. Dit versnelt niet de dagelijkse aanlevering vanuit Plenion zelf.
- **Tv-gegevens:** optioneel voertuigen en NAS-metingen meesturen. Het NAS-adres mag leeg blijven.
- **CA-certificaat:** alleen invullen voor een interne HTTPS-bron met eigen certificaatautoriteit. Het bestand moet leesbaar zijn voor gebruiker `loxberry`.

Sla eerst op. Klik **Bron & bereikbaarheid testen**. Dat leest de bron en controleert de bereikbaarheid van Cohvera, zonder te importeren. De importsleutel wordt pas gecontroleerd wanneer je **Nu synchroniseren** gebruikt. Kijk daarna naar het tijdstip van de laatste bevestigde projectimport, het aantal projecten en het doelbedrijf. Eventueel ontbrekende tv-bronnen worden apart gemeld.

Schakel **Automatisch synchroniseren** in en sla op om periodiek door te sturen. Uitschakelen pauzeert volgende automatische synchronisaties; een lopende aanvraag mag nog afronden. Handmatig synchroniseren blijft mogelijk terwijl de automatische werking gepauzeerd is.

Het doelbedrijf komt momenteel van `QBOX_IMPORT_COMPANY` op de Portal-server. Deze plugin voegt nog geen aparte sleutel-naar-bedrijfskoppeling toe voor meerdere Q-boxen van verschillende bedrijven.

## Bestanden op de Q-box

LoxBerry bepaalt de uiteindelijke pluginmap. Bij de gebruikelijke installatie onder `/opt/loxberry` staan de bestanden hier:

| Locatie | Inhoud |
| --- | --- |
| `/opt/loxberry/bin/plugins/<pluginmap>/` | Connector en servicecode |
| `/opt/loxberry/config/plugins/<pluginmap>/settings.json` | Instellingen inclusief sleutel; niet delen |
| `/opt/loxberry/data/plugins/<pluginmap>/` | Synchronisatiestatus en opdrachten |
| `/etc/systemd/system/cohvera-qbox-plugin.service` | Nieuwe achtergrondservice |
| `/var/lib/cohvera-qbox-plugin-upgrade/` | Afgeschermde backup tijdens updates |

De webpagina gebruikt de bestaande LoxBerry-aanmelding. Er wordt geen extra webpoort geopend. De achtergrondservice draait als `loxberry`, zonder runtime-sudo. De sleutel wordt niet teruggestuurd naar het browserformulier.

## Status controleren via SSH op de Q-box

```sh
sudo systemctl status cohvera-qbox-plugin.service --no-pager -l
sudo journalctl -u cohvera-qbox-plugin.service -n 60 --no-pager
sudo systemctl is-active cohvera-qbox.service
```

De nieuwe service moet `active` zijn; de oude hoort `inactive` te zijn. Een actieve service bewijst alleen dat de worker draait. De bevestiging in het scherm bewijst dat Cohvera de projecten heeft geaccepteerd.

## Updates en herstel

Installeer een volgende ZIP via pluginbeheer als update. Verwijder de plugin niet eerst: de upgradehook bewaart instellingen en synchronisatiestatus buiten de pluginmappen, omdat LoxBerry deze mappen bij updates vervangt. Naam en auteursadres in `plugin.cfg` vormen de vaste pluginidentiteit. Automatische GitHub-updates zijn voor deze eerste versie uitgeschakeld.

Bij een mislukte update: controleer de installatiemelding en installeer een bekende werkende ZIP opnieuw; de afgeschermde upgradebackup blijft beschikbaar. De plugin verwijdert die backup bij expliciete de-installatie. De-installatie verwijdert ook de plugininstellingen en activeert de oude standalone-service niet automatisch.

Als je bewust terugkeert naar de oude standalone-installatie en het oude env-bestand nog klopt, voer op de Q-box uit:

```sh
sudo systemctl disable --now cohvera-qbox-plugin.service
sudo systemctl enable --now cohvera-qbox.service
```

Laat nooit beide services tegelijk aanleveren. Instellingen die je alleen in de plugin wijzigde, staan niet automatisch in het oude env-bestand.
