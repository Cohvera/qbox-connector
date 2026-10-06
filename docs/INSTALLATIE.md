# Q-box → Cohvera: Plenion-projecten

Een kleine uitgaande connector, zonder Docker of extra Python-pakketten. Vereist Python 3.10+ en toegang vanaf de Q-box tot het bedrijfs-LAN en het HTTPS-adres van Cohvera. De meegeleverde automatische service is voor Linux met systemd (Ubuntu/Debian/Raspberry Pi OS). Het script zelf is ook geschikt voor Windows; daar is een andere service-installatie nodig.

## Wat gaat waarheen?

```text
Plenion → bestaande Central Data Hub/export op het LAN
                  ↓ bestaande JSON-bestanden, alleen lezen
             Q-box (Python)
                  ↓ HTTPS + aparte importsleutel
             Cohvera API → projecten onder TOMME
                              ↓ bestaande project-API
                           Inventory
```

De connector schrijft nooit naar Plenion, HFSQL of de LAN-export. In `datahub`-modus leest hij `/api/v1/health`, alle pagina’s van `/api/v1/projects` en de bronmetadata van `/api/v1/tv/projects` op `QBOX_DATAHUB_URL`. De tv-projectselectie zelf wordt niet gebruikt.

De oudere `tv`-modus leest alleen:

- `http://www.tomme-energie.lan/projecten-tv/tv-project-data.json`
- `http://www.tomme-energie.lan/projecten-tv/tv-refresh-status.json`

De oudere tv-modus bevat alleen **In Uitvoering**. De nieuwe datahub-modus bevat alle als huidig gemarkeerde projectversies, met originele status en afsluitindicator. Voertuigen, documenten en andere gegevens worden in deze eerste versie niet verstuurd. Per minuut wordt de bron gecontroleerd. Alleen gewijzigde gegevens worden verstuurd; ongewijzigde gegevens worden na zes uur opnieuw bevestigd. Dit maakt de dagelijkse Plenion-bronkopie niet realtime.

## 1. Bereid Cohvera voor — op de portaalserver

Zet eerst de bijpassende Cohvera/Portal-versie via GitHub online. Ga op de server naar de echte Portal-map, in de eerdere installatie `/root/Portal`:

```sh
cd /root/Portal
git pull --ff-only
nano .env
```

Genereer in een tweede serverterminal een **nieuwe** importsleutel:

```sh
openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'; printf '\n'
```

Voeg aan Portal's `.env` toe, zonder bestaande regels te vervangen:

```dotenv
QBOX_IMPORT_API_KEY=PLAK_HIER_DE_NIEUWE_SLEUTEL
```

Bewaar deze waarde: dezelfde sleutel komt straks op de Q-box. Gebruik niet de Warehouse-sleutel. Opslaan in nano: Ctrl+O, Enter, Ctrl+X.

Maak vóór de update een databasebackup volgens jullie normale deploymentprocedure. Deze versie voegt bronreferenties aan projecten toe en een importstatus-tabel. De bestaande `migrate`-service voert de migratie uit bij deployment.

Bij jullie bestaande installatie met het Warehouse-netwerk:

```sh
docker compose -f docker-compose.yml -f docker-compose.warehouse.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.warehouse.yml ps
```

Behoud eventuele andere bestaande compose-overrides. De importsleutel werkt uitsluitend op `POST /api/integrations/qbox/plenion/projects`. De gebruiker hoeft hiervoor geen nieuwe Entra-rol te krijgen. Het ingestelde doelbedrijf moet bestaan en actief zijn. Standaard is dit `TOMME`.

## 2. Zet de connector op de Q-box — vanaf je Mac

Zorg dat de Q-box op hetzelfde netwerk zit als de `.lan`-website, inclusief werkende lokale DNS. Alleen fysiek naast het netwerk staan is onvoldoende.

Kopieer deze bestanden vanuit de lokale qbox-connector-repository naar de Q-box. Vervang `GEBRUIKER@QBOX_IP` door de echte SSH-login:

```sh
cd /Users/milansaelens/Development/cohvera/qbox-connector
scp qbox_sync.py qbox.env.example cohvera-qbox.service GEBRUIKER@QBOX_IP:/tmp/
ssh GEBRUIKER@QBOX_IP
```

Alle volgende commando's tot stap 5 voer je **op de Q-box** uit:

```sh
python3 --version
sudo install -d -m 755 /opt/cohvera-qbox /etc/cohvera-qbox
sudo install -m 644 /tmp/qbox_sync.py /opt/cohvera-qbox/qbox_sync.py
sudo install -m 600 /tmp/qbox.env.example /etc/cohvera-qbox/qbox.env
sudo install -m 644 /tmp/cohvera-qbox.service /etc/systemd/system/cohvera-qbox.service
sudo install -d -m 700 /var/lib/cohvera-qbox
```

Python moet versie 3.10 of hoger zijn. Op een recente Debian/Ubuntu-installatie kan je zo nodig `sudo apt install python3 ca-certificates` uitvoeren. Er is geen `pip install` nodig.

## 3. Stel de Q-box in — interne bron via HTTP

De LAN-export is getest via HTTP en geeft `200 OK`. Hiervoor is geen CA-certificaat nodig. Alleen het lezen op het bedrijfsnetwerk gebruikt HTTP; de overdracht naar Cohvera blijft HTTPS.

**Op de Q-box:**

```sh
sudo nano /etc/cohvera-qbox/qbox.env
```

Vul in (behoud je bestaande importsleutel als die al ingesteld is):

```dotenv
QBOX_SOURCE_MODE=datahub
QBOX_DATAHUB_URL=http://192.168.10.228:3210
QBOX_SOURCE_URL=http://www.tomme-energie.lan/projecten-tv
QBOX_SOURCE_CA_FILE=
QBOX_PORTAL_URL=https://portal.cohvera.be
QBOX_IMPORT_API_KEY=DEZELFDE_NIEUWE_SLEUTEL_ALS_IN_PORTAL
QBOX_POLL_SECONDS=60
QBOX_STATE_FILE=/var/lib/cohvera-qbox/state.json
```

Gebruik jullie echte publieke portaaladres, zonder `/api`. Opslaan: Ctrl+O, Enter, Ctrl+X. De importsleutel wordt uitsluitend naar Cohvera gestuurd, nooit naar de LAN-bron. Redirects worden geweigerd.

Gebruik je later HTTPS voor de LAN-bron, stel dan `QBOX_SOURCE_URL=https://...` in. Als het interne certificaat niet standaard vertrouwd wordt, vraag dan de uitgevende CA in PEM-formaat aan de netwerkbeheerder. Plaats het leesbaar voor de service op `/etc/cohvera-qbox/lan-ca.pem` en vul dat pad in bij `QBOX_SOURCE_CA_FILE`. Certificaatcontrole naar Cohvera blijft altijd aan.

**Bestaande installatie bijwerken:** kopieer de nieuwe `qbox_sync.py` naar de Q-box zoals in stap 2 en vervang `/opt/cohvera-qbox/qbox_sync.py` zoals onder Onderhoud. Alleen de URL aanpassen werkt niet met de eerdere scriptversie die uitsluitend HTTPS toeliet. Voer daarna de controle uit stap 4 uit en herstart de service met `sudo systemctl restart cohvera-qbox`.

## 4. Controleer de bron zonder gegevens te versturen

**Op de Q-box:**

```sh
sudo sh -c 'set -a; . /etc/cohvera-qbox/qbox.env; set +a; python3 /opt/cohvera-qbox/qbox_sync.py --dry-run'
```

Verwacht: `Bron geldig: ... projecten; niets verstuurd`. Hierbij worden geen projecten of klantgegevens in de logs gezet. De voorbeeldconfiguratie is ook geschikt om als shell-config te laden; gebruik bij eigen waarden met spaties shell-aanhalingstekens.

Bij certificaatfouten: controleer de CA, hostname en tijd van de Q-box. Bij DNS-fouten: controleer of de Q-box dezelfde interne DNS gebruikt als de werkende computers. Bij een verlopen bronstand: herstel de bestaande export; de connector stuurt die niet als nieuwe gegevens door.

## 5. Start de automatische service

**Op de Q-box:**

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now cohvera-qbox
sudo systemctl status cohvera-qbox --no-pager
sudo journalctl -u cohvera-qbox -n 40 --no-pager
```

De service verstuurt nu de eerste geldige export en start automatisch opnieuw na een reboot. Een succesvolle logregel bevat `Cohvera bevestigt ... projecten`.

**In Cohvera:** kies Tomme Energie → Projecten. Geïmporteerde projecten dragen hun Plenion-nummer. Bovenaan verschijnt de laatste bronstand en het aantal projecten uit de laatste export. Een bronstand ouder dan 24 uur krijgt een melding. Bestaande gegevens blijven beschikbaar.

Daarna kan Inventory deze projecten via de bestaande Cohvera-projectkoppeling opvragen en selecteren. Dat is een afzonderlijke verbinding met de bestaande Warehouse-sleutel.

## Gedrag bij updates en fouten

- De combinatie bedrijf + bron `PLENION` + projectnummer voorkomt duplicaten.
- Klant, bronomschrijving, bronplanning en laatste bronstand worden bijgewerkt.
- Een nieuw project krijgt intern portaalstatus Actief; het overzicht toont voor Plenion-projecten de originele bronstatus. De verantwoordelijke is eerst leeg; die staat niet in de bron.
- Een lokale titel, status of verantwoordelijke wordt bij volgende imports niet overschreven.
- Bronplanning wordt apart opgeslagen en wordt niet als deadline op de eenvoudige projectenpagina gezet.
- Afgesloten volgt uitsluitend de bronindicator `is_closed`, niet de tekst van het statuslabel en niet het ontbreken uit een selectie. Een verdwenen project wordt niet automatisch verwijderd of afgesloten.
- Een ouder snapshot kan een nieuwere import niet overschrijven. Dezelfde bronstand met afwijkende inhoud wordt geweigerd, behalve de eenmalige contractupgrade van tv-versie 2 naar volledige versie 3. Daarna wordt een oudere tv-import geweigerd.
- Fouten leiden tot vertraagde herpogingen, maximaal iedere 15 minuten. Na succes keert de connector terug naar het ingestelde interval.
- Lokale voortgang wordt pas opgeslagen na bevestiging door Cohvera. Bij een verloren antwoord is opnieuw versturen veilig.
- Er draait één serviceproces. Start niet tegelijkertijd extra handmatige importprocessen met hetzelfde statusbestand.
- Op de Q-box wordt alleen een kleine voortgangsstatus opgeslagen, geen lokale database of exportarchief.
- De ontvangst van de export en de projectupdates gebeuren in één databasetransactie.

De connector heeft geen inkomende poort nodig. Alleen uitgaand HTTPS naar Cohvera en toegang tot de bestaande LAN-website zijn nodig.

## Onderhoud

Nieuwe scriptversie op de Q-box plaatsen en herstarten:

```sh
sudo install -m 644 /tmp/qbox_sync.py /opt/cohvera-qbox/qbox_sync.py
sudo systemctl restart cohvera-qbox
```

Stoppen: `sudo systemctl stop cohvera-qbox`. Configuratie wijzigen: `sudo nano /etc/cohvera-qbox/qbox.env`, gevolgd door een serviceherstart. Roteer de sleutel in zowel Portal als Q-box en herstart beide. Logs bevatten geen sleutels; deel geen `.env`-bestanden.

## Lokale controles voor ontwikkelaars

Vanuit deze connector-repository:

```sh
python3 -m unittest discover -s . -p 'test_*.py'
python3 -m compileall -q qbox_sync.py
```

API-controles voer je afzonderlijk uit vanuit de Portal-repository:

```sh
pnpm --filter @cohvera/api exec tsx --test src/integrations/qbox.test.ts
pnpm --filter @cohvera/api typecheck
pnpm --filter @cohvera/web typecheck
```

De tests gebruiken fictieve gegevens en nagebootste netwerk/database-antwoorden. Ze controleren authenticatie, bronvalidatie, dubbele imports, oudere snapshots, retries en behoud van lokale velden. De echte PostgreSQL-migratie en Q-box-installatie moeten afzonderlijk in de deploymentomgeving gecontroleerd worden.

## Tijdelijk importeren onder Q-Home

Voeg **op de Portal-server** aan de Portal `.env` toe:

```dotenv
QBOX_IMPORT_COMPANY=QHOME
```

Haal eerst de bijgewerkte code op en herbouw zoals in stap 1. Deze instelling hoort niet in de Q-box-configuratie. De bron blijft dezelfde Tomme-export; je maakt hem hiermee tijdelijk zichtbaar voor gebruikers met Q-Home-projectrechten. De importstatus verschijnt ook bij Q-Home.

Een bedrijf wisselen verplaatst eerder geïmporteerde projecten niet: volgende imports worden onder het ingestelde bedrijf verwerkt. Zo blijven bestaande project- en Inventory-verwijzingen intact. Zet de waarde later terug op `TOMME` voor volgende imports daar; eventuele Q-Home-kopieën blijven bestaan.

Heeft de Q-box deze bronstand al bevestigd, dan kan zijn lokale cache het opnieuw versturen uitstellen. Na bijwerken van het script kun je eenmalig forceren op de LoxBerry, zonder de bestaande status te verwijderen:

```sh
sudo systemctl stop cohvera-qbox
sudo sh -c 'set -a; . /etc/cohvera-qbox/qbox.env; set +a; python3 /opt/cohvera-qbox/qbox_sync.py --once --force'
sudo systemctl start cohvera-qbox
```

Nog geen bevestiging gezien? Controleer eerst `sudo systemctl status cohvera-qbox --no-pager` en `sudo journalctl -u cohvera-qbox -n 50 --no-pager`. Een ander doelbedrijf lost een netwerk-, bron- of sleutelfout niet op.

## Bestaande installatie uitbreiden naar alle statussen

Voer dit in deze volgorde uit. Het doelbedrijf blijft de ingestelde `QBOX_IMPORT_COMPANY` (bij jullie voorlopig `QHOME`). Er worden geen bestaande projecten naar een ander bedrijf verplaatst.

1. Zet de gewijzigde Portal-code op GitHub, haal hem op de portaalserver op en bouw **de hele Portal** opnieuw, inclusief web en migraties:

```sh
cd /root/Portal
git pull --ff-only
docker compose -f docker-compose.yml -f docker-compose.warehouse.yml up -d --build
```

De nieuwe migratie `20261006120000_plenion_status_flags` moet geslaagd zijn. Maak zoals gebruikelijk vooraf een databasebackup. Deze migratie voegt status/afsluitvelden toe; hij wijzigt geen bestaande bedrijfsindeling.

2. Kopieer vanaf je Mac het nieuwe script:

```sh
scp /Users/milansaelens/Development/cohvera/qbox-connector/qbox_sync.py loxberry@192.168.10.78:/tmp/qbox_sync.py
```

3. Voer op de **LoxBerry** uit:

```sh
sudo systemctl stop cohvera-qbox
sudo install -m 644 /tmp/qbox_sync.py /opt/cohvera-qbox/qbox_sync.py
sudo nano /etc/cohvera-qbox/qbox.env
```

Voeg deze regels toe of werk ze bij; behoud de andere instellingen en dezelfde sleutel:

```dotenv
QBOX_SOURCE_MODE=datahub
QBOX_DATAHUB_URL=http://192.168.10.228:3210
QBOX_SOURCE_CA_FILE=
```

4. Test op de LoxBerry eerst zonder te versturen:

```sh
sudo sh -c 'set -a; . /etc/cohvera-qbox/qbox.env; set +a; python3 /opt/cohvera-qbox/qbox_sync.py --dry-run'
```

Na `Bron geldig` voer je de import uit en start je de service opnieuw:

```sh
sudo sh -c 'set -a; . /etc/cohvera-qbox/qbox.env; set +a; python3 /opt/cohvera-qbox/qbox_sync.py --once --force'
sudo systemctl start cohvera-qbox
sudo journalctl -u cohvera-qbox -n 30 --no-pager
```

Bij fouten herstart je de service eveneens, zodat de volgende poging automatisch plaatsvindt. Controleer de fout en herstel de configuratie; zet niet stilzwijgend terug naar een tv-import als versie 3 al ontvangen is.

5. Open Cohvera → het ingestelde bedrijf → Projecten. Standaard zie je **Open**. Kies **Afgesloten** of **Alle projecten**, en kies vervolgens de gewenste originele Plenion-status. De zichtbare checkbox is alleen-lezen: hij toont `is_closed` uit de datahub. `is_active` en het statuslabel blijven afzonderlijke bronvelden; een status ‘Afgewerkt’ zonder afsluitvinkje telt dus niet als afgesloten.

De datahub-code in de gedownloade GitHub-kopie is ouder dan de aangetroffen draaiende API. Deze connector vereist de live velden `status_label`, `is_current`, `is_closed`, `is_active`, `customer_name`, `planned_date` en de snapshotmetadata. Bij ontbrekende velden, dubbele huidige projectnummers, onvolledige paginering of een snapshotwissel wordt niets geïmporteerd. Onbekende afsluitinformatie wordt in Cohvera als **Nog niet bevestigd** getoond, niet geraden.

Read-only controle op 6 oktober 2026: 1.670 bronrecords, 1.053 huidige versies, 95 open en 958 afgesloten. Dit zijn controletotalen, geen vaste verwachte aantallen in de code. De precieze Plenion-schermvinkjes zijn niet rechtstreeks in de Plenion-UI geïnspecteerd; de connector gebruikt de door de datahub gepubliceerde `is_closed`-indicator.
