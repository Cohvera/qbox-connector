# Projecten rechtstreeks uit een Plenion/HFSQL-server lezen

Dit is een kleine zelfstandige lezer voor de Q-box. Hij haalt projectrecords uit de tabel `PROJ` en schrijft `data/projects.json`. Er is geen kopie van de database, PureSync of Central Datahub nodig voor deze verbinding.

```
Plenion/HFSQL-server:4900 → Q-box: reader → data/projects.json
```

De server die je invult bepaalt welke gegevens je krijgt. `192.168.10.90:4900` is vanaf de Q-box bereikbaar bij de eerder onderzochte Tomme-installatie. Voor een nieuwe configuratie moet je zelf het serveradres van het betreffende bedrijf invullen. Of dit de juiste Plenion-database bevat, moet nog worden bevestigd met je account. `192.168.10.228:4900` is de eerder gevonden Synology-reader. Een verbinding met de Synology-reader blijft de gegevens van die reader gebruiken; dit script maakt een dagelijkse kopie niet actueler.

## 1. Op je Mac: overzetten

Bouw het pakket vanuit de qbox-connector-repository met `python3 scripts/build_reader.py`. Voer daarna uit in Terminal op je Mac (vervang het Q-box-adres als je een ander toestel gebruikt):

```sh
scp /Users/milansaelens/Development/cohvera/qbox-connector/dist/cohvera-plenion-reader.tar.gz loxberry@10.121.15.44:~/
ssh loxberry@10.121.15.44
```

## 2. Op de Q-box: installeren

Je terminal moet nu `loxberry@loxberry` tonen. Pak het pakket uit:

```sh
mkdir -p ~/plenion-reader
tar -xzf ~/cohvera-plenion-reader.tar.gz -C ~/plenion-reader
cd ~/plenion-reader
```

Eenmalig op Debian 12/LoxBerry, als Docker/emulatie nog ontbreekt:

```sh
sudo apt-get update
sudo apt-get install -y docker.io qemu-user-static binfmt-support
sudo systemctl enable --now docker
sudo update-binfmts --enable qemu-x86_64
```

Daarna:

```sh
sudo ./plenion.sh install
./plenion.sh configure
sudo ./plenion.sh test
```

`install` haalt de officiële PC SOFT-driver automatisch op, controleert de SHA-256 en test of de driver initialiseert. De eerste installatie kan op een kleine Q-box meerdere minuten duren. Er wordt geen poort geopend en geen permanent proces gestart.

Bij `configure` vul je in:

- **Serveradres:** verplicht invullen bij een nieuwe configuratie; gebruik de Plenion/HFSQL-server van dit bedrijf. Een bestaand ingesteld adres blijft behouden. Pas dit aan als jouw Plenion-server een ander adres heeft.
- **Poort:** normaal `4900`.
- **Databasenaam:** de exacte database op deze HFSQL-server. Nog onbekend? Laat leeg om eerst een metadata-overzicht te proberen. Geen pad naar een map of `.fic`-bestand.
- **Gebruiker/wachtwoord:** een bestaande HFSQL-databaselogin met leesrecht op `PROJ`. Dit is niet je LoxBerry-, Microsoft- of Synology-login. Het script kan deze gegevens niet afleiden of aanmaken.

Bij hetzelfde serveradres, poort en account behoudt Enter het al opgeslagen wachtwoord. Bij een ander account of serveradres moet je het opnieuw invullen. `/leeg` stelt alleen op jouw expliciete keuze een leeg wachtwoord in. Er worden geen standaardwachtwoorden geprobeerd.

Het wachtwoord wordt onzichtbaar ingevoerd en lokaal met bestandsrechten 600 bewaard. Voer `configure` zonder sudo uit. De container leest de configuratie via een alleen-lezenmount; het wachtwoord staat niet in de Docker-commandoregel of image.

## Databasenaam nog onbekend?

Na `configure` met je bestaande HFSQL-account:

```sh
sudo ./plenion.sh databases
```

Dit probeert één aangemelde verbinding met de standaard ODBC-catalogusfunctie. Het probeert geen databasenamen of andere accounts. **Ondersteuning door de HFSQL-driver/server is nog niet op jouw server bevestigd.** Sommige drivers vereisen al een databasenaam om aan te melden of bieden geen catalogusoverzicht. Een fout of lege lijst bewijst daarom niet dat je account ongeldig is of dat er geen databases bestaan.

Kies een ontvangen naam, of een naam die je van je beheerder hebt:

```sh
./plenion.sh database
sudo ./plenion.sh tables
sudo ./plenion.sh test
```

`database` wijzigt uitsluitend de databasenaam en behoudt je account. `tables` vraagt alleen tabelnamen op; het wijzigt geen gegevens. De naam `tomme_energie_analysis_v2` uit de andere repository hoort bij de replica en wordt niet automatisch ingevuld voor `.90`.

De metadata-aanroep volgt [SQLTables uit de ODBC-specificatie](https://learn.microsoft.com/en-us/sql/odbc/reference/syntax/sqltables-function); dit is geen bevestiging van ondersteuning door de HFSQL-server.

## 3. Resultaat bekijken

De test haalt maximaal vijf records op:

```sh
python3 -m json.tool ~/plenion-reader/data/projects-test.json
```

Alle records ophalen, maximaal 20.000:

```sh
cd ~/plenion-reader
sudo ./plenion.sh read
python3 -m json.tool data/projects.json
```

Het bestand bevat `source_id`, `number`, `customer_id`, `name`, `description` en `created_at`. De gebruikte SELECT komt uit de aangeleverde Central Data Platform-code. Kolomnamen moeten overeenkomen met jouw Plenion-versie. Meerdere versies van een project kunnen afzonderlijke records zijn; dit script maakt daar nog geen selectie in. `truncated: true` betekent dat de limiet bereikt is en het resultaat geen volledige export is. `fetched_at` is het ophaalmoment, niet het backupmoment of bewijs dat de bron actueel is.

Bij een leesfout blijft het vorige uitvoerbestand bewaard. De reader schrijft geen gegevens naar HFSQL: hij voert één vaste SELECT uit, of vraagt catalogusmetadata op. Gebruik een databaseaccount met uitsluitend leesrechten om dat ook op de server af te dwingen. De optionele ODBC-instelling voor alleen-lezenmodus wordt niet vóór de aanmelding gezet: deze blokkeerde de aanmelding in de geteste HFSQL 28 / Linux-driver 31-combinatie. Zonder die instelling lukten aanmelden, lezen en afsluiten op de fysieke x86-64-Q-box.

### Tekstcodering en accenten

De standaardcodering voor opgehaalde tekst is UTF-8. Sommige HFSQL-bronnen leveren via de ANSI-ODBC-interface Windows-1252. Een test met vijf records kan slagen terwijl een volledige leesactie pas bij een later accent vastloopt.

Wijzig alleen de tekstcodering, als gebruiker `loxberry`:

```sh
cd ~/plenion-reader
./plenion.sh encoding
```

Vul `cp1252` in als de bron Windows-1252 gebruikt. Dit bewaart server, database, account en wachtwoord; het wachtwoord wordt niet opgevraagd of getoond. Een ongeldige codering wordt geweigerd voordat de configuratie wordt opgeslagen. Een bestaande coderingskeuze blijft bij `configure`, `database` en een pakketupdate behouden. Er is voor deze configuratiewijziging geen nieuwe containerbuild nodig.

Voer daarna `sudo ./plenion.sh read` uit en controleer enkele bekende namen met accenten tegenover Plenion. Windows-1252 is geen automatische terugvaloptie: de bron moet deze codering daadwerkelijk gebruiken. De reader blijft strikt decoderen, vervangt of negeert geen ongeldige bytes en schrijft het uiteindelijke JSON-bestand altijd als UTF-8. Bij een fout wordt geen gedeeltelijk resultaat gepubliceerd.

### Op de Q-box controleren of alle records per status zijn uitgelezen

Deze controle draait in de reader. Je hoeft geen SQL in Control Center uit te voeren. De gewone `read`-export bevat nog geen statusveld: de audit leest de status op het controlemoment uit dezelfde ingestelde database en vergelijkt de record-IDs met het bestaande `data/projects.json`.

Bekijk eerst de werkelijke kolomnamen:

```sh
cd ~/plenion-reader
sudo ./plenion.sh columns
```

Dit voert alleen `SELECT * FROM PROJ WHERE 1=0` uit en beschrijft de kolommen, zonder projectinhoud op te halen. Gebruik `sudo ./plenion.sh columns BSRT` om alleen de kolomnamen van een andere tabel te bekijken. Deze tabelkeuze verandert de projectexport niet. Zoek de echte statuskolom of laat de kolomlijst controleren. De reader raadt geen veldnamen of statuslabels. Als de status een verwijzing naar een andere tabel is, toont de audit de ruwe code; een naam uit het Plenion-scherm vereist dan nog de bevestigde relatie.

Lees opnieuw en controleer vervolgens met de exacte kolomnaam (vervang `STATUSKOLOM`):

```sh
sudo ./plenion.sh read
sudo ./plenion.sh audit STATUSKOLOM
```

De reader toont per statuswaarde het aantal databaserecords, hoeveel van die IDs in de export aanwezig zijn en hoeveel ontbreken. NULL, lege en andere waarden blijven afzonderlijke groepen. Het rapport staat in `data/audit-status.json`; configuratie en projectexport worden niet overschreven. Behandel ook dit rapport als bedrijfsinformatie.

De audit vergelijkt de IDs zelf en controleert dubbele export-IDs, gewijzigde projectnummers, afkapping en extra/ontbrekende records. Gelijke totaalaantallen zijn dus niet voldoende. Exitcode 0 betekent overeenstemmende IDs/projectnummers, 2 betekent een afwijking en 1 een uitvoeringsfout. Een afgekapt auditresultaat wordt geweigerd. De limiet blijft 20.000 bronrecords.

Deze controle telt **alle records en versies** in `PROJ`, zonder statusfilter. Hij selecteert niet automatisch de actieve revisie of het dagboek Projecten/Offertes. Statuswaarden komen uit de huidige database; ze bewijzen geen historische status in een oudere export en controleren niet de overige tekstvelden. Wijzigingen in de database tussen `read` en `audit` kunnen verschillen veroorzaken. Gebruik dezelfde database en vergelijk in Plenion dezelfde versie- en dagboekselectie. De audit verandert geen HFSQL-data en uploadt niets naar Portal.

## Wat dit al doet en wat nog niet

De basisopdracht read maakt alleen een lokale export. Gebruik portal voor het uitgebreide JSON-formaat met status- en projectmapvelden. De bedrijfsselectie en groepering gebeuren in Cohvera. Automatische HTTPS-upload is optioneel via sync en auto-install, zoals hieronder beschreven.

De gedownloade driver 31 is **x86-64**, vastgesteld aan het ELF-bestand. Op een x86-64-Q-box draait hij native in Docker; op een ARM64-Q-box is QEMU-emulatie nodig. Dit is geen native ARM-driver. Python 3.11 op de Q-box is voldoende voor de configuratie; de driver en zijn libraries draaien samen in de container. De driver wordt rechtstreeks van de leverancier gedownload, niet meegestuurd in Git.

Bronnen: [officiële Linux-ODBC-documentatie](https://doc.pcsoft.fr/fr-FR/?9000160), [officiële HFSQL-downloads](https://download.windev.com/fr/download/packs/HFSQL/2026.awp). Gecontroleerde download: `wxpack_iodbclinux316017.zip`, SHA-256 `127eb0c57882d474d4b8e964fd0cbb89edf44e3c20f2f5b96f74f1fad9e1e3ef`.

## Als iets niet lukt

- `exec format error`: emulatie ontbreekt; voer de QEMU-installatiestappen hierboven uit.
- `permission denied` bij Docker: gebruik `sudo` voor `install`, `check`, `test` en `read`; configureer als loxberry.
- `Connection refused`/timeout: controleer het serveradres en of de Q-box die server op poort 4900 kan bereiken. Dit staat los van de verbinding van je Mac naar de Q-box.
- `Aanmelden bij HFSQL mislukt`: de poort is bereikbaar; controleer HFSQL-databasenaam, login en servercompatibiliteit. Alleen de foutcodes worden getoond, zodat driverdiagnostiek geen wachtwoord lekt.
- `Projecten lezen mislukt`: controleer of deze database `PROJ` en de zes opgegeven velden bevat en de gebruiker leesrechten heeft.
- Melding na 90 seconden: de reader is door het begeleidende proces gestopt; het vorige resultaat is behouden.
- Tekstcoderingsfout: stel de juiste broncodering in met `./plenion.sh encoding`; controleer ook de volledige export en niet alleen de eerste vijf records.

Deze eerste versie accepteert geen puntkomma in verbindingswaarden (ook niet in het wachtwoord); de configuratie meldt dat expliciet.

Driver apart controleren: `sudo ./plenion.sh check`. Deze controle maakt geen verbinding met een database.

## Uitgevoerde controles

Lokaal getest op 13 oktober 2026: officiële ZIP gedownload en hash gecontroleerd; image gebouwd voor linux/amd64 op een ARM Docker-host; driver geladen en ODBC-omgeving aangemaakt met alleen-lezenbestandssysteem en een niet-rootgebruiker; iODBC bereikt een lokale testserver en meldt een afgebroken verbinding correct; negen Python-tests geslaagd voor verbindingswaarden, private atomische uitvoer , poortvalidatie de timeoutafhandeling, catalogus-aanroep en behoud van accountconfiguratie. Nog niet getest: authenticatie en echte projectresultaten van jouw HFSQL-server, of uitvoering op de fysieke LoxBerry.

## Een bestaand pakket bijwerken

Praktijktest op 18 oktober 2026: de beheerder bevestigde aanmelden, een test met vijf records en een volledige export zonder afkapping op een fysieke x86-64-Q-box met HFSQL 28 en driver 31. Dit vereiste het weglaten van de vooraf ingestelde alleen-lezenoptie en `encoding: cp1252` voor de opgehaalde tekst. Deze controle bevestigt het lezen naar lokaal JSON; vergelijking van accenten met de bron en integratie met Portal blijven aparte controles.

Kopieer het nieuwe archief opnieuw met de Mac-opdracht hierboven. Op de Q-box:

```sh
cd ~/plenion-reader
tar -xzf ~/cohvera-plenion-reader.tar.gz
sudo ./plenion.sh install
./plenion.sh configure
```

Het archief bevat geen `data`-map, dus je bestaande configuratie en opgehaalde projecten worden niet vervangen. De container moet opnieuw gebouwd worden met `install`, anders draait nog de vorige reader-code. Er is voor deze stap geen Portal-herstart nodig.

### Debian 13: docker.io geïnstalleerd, docker-commando ontbreekt

Installeer ook `sudo apt-get install -y docker-cli`. Debian 13 verpakt de client apart. Controleer daarna `sudo docker version` en start `sudo systemctl enable --now docker`. Herhaal vervolgens `sudo ./plenion.sh install`. Zie https://packages.debian.org/trixie/admin/docker-cli.


## Projectstatussen opzoeken

Na installatie van dit pakket:

```sh
sudo ./plenion.sh statuses
```

Dit leest alleen `WECLEUNIK, CODE, OMSCHR1` uit `WERK WHERE TYPE='PRS'`.
Het toont de status-ID, code en omschrijving en bewaart ze apart in
`data/project-statuses.json`. De bestaande `data/projects.json` blijft behouden.
Een leeg, afgekapt of dubbelzinnig overzicht vervangt de vorige statuslijst niet.

De historische NAS-mapping in plenion-central-data-platform
(commit `c4f0752286931e4fe5100c01c7d975dd1386cb5c`,
`datahub/backend/src/nas-project-mapping.js`) koppelt `PROJ.STAT`
aan `WERK.WECLEUNIK` binnen type `PRS`. Gebruik de IDs en omschrijvingen
uit de eigen database; IDs uit Tomme mogen niet als vaste Warco-mapping worden gebruikt.
Dit commando wijzigt nog geen projectselectie en uploadt niets naar Portal.

## Algemeen JSON-bestand voor Cohvera

Voer `sudo ./plenion.sh portal` uit. Het resultaat staat in
`data/portal-projects.json`. Dit bestand bevat de geselecteerde projectrecords met de
status-ID, code, omschrijving, VersieAf, UITWIP, IDProjectMap, ProjectMap,
BSRTCD en VersieNr (schema_version 3). De reader kiest geen bedrijf of statusfilter.
De query haalt actuele records zonder parent op (VersieAf=0), ook zelfstandige projecten zonder ProjectMap-vinkje,
en al hun direct gekoppelde records, ongeacht status en Uit WIP. Historische
hoofdversies en wezen worden niet geselecteerd.
Historische versies van gekoppelde documenten blijven wel behouden.

Ook DatCre, CDATUM, DatumPlanning, Deadline, Oplevering, StatDat, DATUMUITWIP,
DatumVerkocht, FAK_FakNr, FAK_CDatum en FAK_AfDatum worden meegestuurd. Portal toont
bronwaarden zonder een werkelijke start, betaling of garantie-einde te veronderstellen.
De bestaande basisexport blijft behouden. De limiet blijft 20.000 records / 20 MB;
een afgekapt resultaat wordt niet geüpload. Een fout behoudt de vorige import.

Update **eerst Portal inclusief de migratie en API/web**, daarna deze reader.
Portal moet v3 ondersteunen. De eerste v3-import vormt een uitgangssituatie;
volgende imports registreren verschillen onder Updates. Downgraden naar v2 na
v3 wordt door Portal geweigerd. De bestaande 15-minutentimer blijft actief.

Kopieer het bestand naar je Mac:
```sh
scp loxberry@10.121.15.44:~/plenion-reader/data/portal-projects.json ~/Downloads/
```
Selecteer het juiste bedrijf in Cohvera en open Projecten → Projecten uit
Plenion importeren. Cohvera bepaalt de bedrijfsregels. Bedrijfsprofielen en statusselectie worden uitsluitend in Cohvera beheerd. De export mag maximaal 24 uur oud
zijn. De opdracht portal maakt alleen het bestand. Voor automatische upload zie hieronder.

## Automatisch elke 15 minuten naar Cohvera

Werk eerst Portal bij. De API biedt POST /api/integrations/qbox/plenion/reader-projects
aan via Caddy. Deze route gebruikt dezelfde import en bedrijfsfilters als de handmatige upload.

### Op de Portal-server

Zet in /root/Portal/.env:

    QBOX_IMPORT_KEY_QHOME=<unieke sleutel voor Q-Home>
    QBOX_IMPORT_KEY_TOMME=
    QBOX_IMPORT_KEY_WARCO=

Gebruik een willekeurige sleutel van 43–128 tekens (letters, cijfers, underscore, streepje),
bijvoorbeeld lokaal gegenereerd met:

    python3 -c 'import secrets; print(secrets.token_urlsafe(32))'

Bewaar die uitsluitend in de serverconfiguratie en op de Q-box; niet in Git of chat.
Genereer voor ieder actief bedrijf een verschillende sleutel; laat ongebruikte sleutels leeg.
Op de Q-box vul je uitsluitend de sleutel van het gewenste doelbedrijf in.
Cohvera bepaalt het bedrijf uit die sleutel, niet uit het JSON-bestand.
QBOX_IMPORT_API_KEY met QBOX_IMPORT_COMPANY blijft optioneel beschikbaar voor oudere
connectoren. Behoud die instellingen zolang een bestaande connector ze gebruikt.
Gebruik een sleutel nooit voor verschillende bedrijven; Cohvera weigert die configuratie.
QHOME en WARCO hebben een reader-importprofiel; uitvoering is code 07. Voor TOMME
moeten de bedrijfsregels eerst in Portal worden ingesteld. Sleutelwijzigingen vereisen een nieuwe API-container.
Bouw de bijgewerkte Portal API en hermaak de container om de env te laden:

    cd /root/Portal
    docker compose -f docker-compose.yml -f docker-compose.warehouse.yml up -d --build api

### Op de Q-box

Update het readerpakket zoals hierboven. Voer daarna als loxberry de uploadconfiguratie uit:

    cd ~/plenion-reader
    ./plenion.sh upload-config

Het HTTPS-adres is standaard https://portal.cohvera.be; je hoeft het niet in te vullen.
Een eerder ingesteld afwijkend adres blijft behouden. Vul optioneel het ZeroTier-IP
van de **Portal-server** in (niet dat van de Q-box), en de bedrijfskey.
Enter behoudt een bestaande route/key; /direct schakelt terug naar internet.
Bij een eerste configuratie betekent een lege route direct via internet.
data/upload.json wordt met rechten 600 opgeslagen.

Vind het ZeroTier-IP op de Portal-server zelf, bijvoorbeeld met:

    ip -4 -o addr show | awk '$2 ~ /^zt/ {print $2, $4}'

Neem alleen het IP over, zonder /24 of ander prefix. Beide machines moeten dat
ZeroTier-netwerk kunnen bereiken en de Portal-server moet HTTPS op poort 443
accepteren. De connector verbindt met dat IP, maar houdt portal.cohvera.be als
HTTP Host en TLS-servernaam: certificaatcontrole blijft ingeschakeld. Bij een
onbereikbare ZeroTier-route wordt niet automatisch naar internet teruggevallen.
Meerdere ZeroTier-adressen worden niet automatisch gekozen; kies het netwerk
waartoe beide machines toegang hebben. Zonder ZeroTier op de Portal-server kun
je de bestaande directe HTTPS-route blijven gebruiken.

Alleen voor een afwijkende installatie kun je het adres expliciet instellen:

    ./plenion.sh upload-config --portal-url https://ander-portaal.example

Updates veranderen de bestaande timer niet. Een actieve timer blijft elke 15
minuten de scripts uit dezelfde installatiemap uitvoeren.
De bestaande HFSQL-configuratie blijft behouden. De API kiest het bedrijf.

Test één volledige export en upload:

    sudo ./plenion.sh sync

Wacht op "Cohvera bevestigt ... projecten ... voor QHOME". Activeer daarna:

    sudo ./plenion.sh auto-install
    systemctl list-timers cohvera-reader-sync.timer

De timer draait op minuut 00, 15, 30 en 45, met maximaal 15 seconden spreiding.
Na een gemist tijdstip door uitschakeling volgt één inhaalrun. Iedere run maakt
een nieuwe export; bij een leesfout wordt het vorige bestand niet alsnog verstuurd.
TLS-certificaten worden gecontroleerd, redirects worden geweigerd en de sleutel
wordt alleen in een HTTP-header verstuurd. Een mislukte upload wordt bij de volgende
cyclus opnieuw geprobeerd met een nieuwe export.

### Controle en beheer op de Q-box

    sudo journalctl -u cohvera-reader-sync.service -n 60 --no-pager
    systemctl status cohvera-reader-sync.timer --no-pager

Eenmalig uitvoeren of uitschakelen:

    sudo systemctl start cohvera-reader-sync.service
    sudo systemctl disable --now cohvera-reader-sync.timer

Gebruik deze timer niet tegelijk met een oudere connector die dezelfde projecten
naar hetzelfde doelbedrijf stuurt. De timer draait als root om Docker te kunnen
starten; het databaseleesproces in Docker blijft draaien als eigenaar van
data/connection.json. De 15-minutencyclus leest de ingestelde HFSQL-server:
als die alleen dagelijks uit een backup ververst wordt, blijft de broninhoud
dagelijks ververst. Uploadfrequentie verandert dat niet.
