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

Bij een leesfout blijft het vorige uitvoerbestand bewaard. De reader schrijft geen gegevens naar HFSQL. Hij vraagt alleen-lezenmodus en voert één vaste SELECT uit. Gebruik bij voorkeur een databaseaccount met uitsluitend leesrechten.

## Wat dit al doet en wat nog niet

Dit is de eerste praktische verbindingsstap. Het script uploadt nog niets naar Cohvera en bevat geen planning, statusmapping, revisiekeuze of bedrijfstoewijzing. De JSON is geen volledige v4-import voor het tv-scherm. Daarvoor moeten we daarna de echte statusvelden, revisies en afsluitvinkjes koppelen. Zo worden geen verzonnen statussen of onvolledige selecties geïmporteerd.

De gedownloade driver 31 is **x86-64**, vastgesteld aan het ELF-bestand. Op jouw ARM64-Q-box gebruiken we daarom Docker met QEMU-emulatie. Dit is geen native ARM-driver. Python 3.11 op de Q-box is voldoende voor de configuratie; de driver en zijn libraries draaien samen in de container. De driver wordt rechtstreeks van de leverancier gedownload, niet meegestuurd in Git.

Bronnen: [officiële Linux-ODBC-documentatie](https://doc.pcsoft.fr/fr-FR/?9000160), [officiële HFSQL-downloads](https://download.windev.com/fr/download/packs/HFSQL/2026.awp). Gecontroleerde download: `wxpack_iodbclinux316017.zip`, SHA-256 `127eb0c57882d474d4b8e964fd0cbb89edf44e3c20f2f5b96f74f1fad9e1e3ef`.

## Als iets niet lukt

- `exec format error`: emulatie ontbreekt; voer de QEMU-installatiestappen hierboven uit.
- `permission denied` bij Docker: gebruik `sudo` voor `install`, `check`, `test` en `read`; configureer als loxberry.
- `Connection refused`/timeout: controleer het serveradres en of de Q-box die server op poort 4900 kan bereiken. Dit staat los van de verbinding van je Mac naar de Q-box.
- `Aanmelden bij HFSQL mislukt`: de poort is bereikbaar; controleer HFSQL-databasenaam, login en servercompatibiliteit. Alleen de foutcodes worden getoond, zodat driverdiagnostiek geen wachtwoord lekt.
- `Projecten lezen mislukt`: controleer of deze database `PROJ` en de zes opgegeven velden bevat en de gebruiker leesrechten heeft.
- Melding na 90 seconden: de reader is door het begeleidende proces gestopt; het vorige resultaat is behouden.

Deze eerste versie accepteert geen puntkomma in verbindingswaarden (ook niet in het wachtwoord); de configuratie meldt dat expliciet.

Driver apart controleren: `sudo ./plenion.sh check`. Deze controle maakt geen verbinding met een database.

## Uitgevoerde controles

Lokaal getest op 13 oktober 2026: officiële ZIP gedownload en hash gecontroleerd; image gebouwd voor linux/amd64 op een ARM Docker-host; driver geladen en ODBC-omgeving aangemaakt met alleen-lezenbestandssysteem en een niet-rootgebruiker; iODBC bereikt een lokale testserver en meldt een afgebroken verbinding correct; negen Python-tests geslaagd voor verbindingswaarden, private atomische uitvoer , poortvalidatie de timeoutafhandeling, catalogus-aanroep en behoud van accountconfiguratie. Nog niet getest: authenticatie en echte projectresultaten van jouw HFSQL-server, of uitvoering op de fysieke LoxBerry.

## Een bestaand pakket bijwerken

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
