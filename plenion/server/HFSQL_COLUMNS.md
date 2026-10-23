# Kolomkeuze via Middleware (reader 3.2)

## Herkomst en bereik

De openbare [Plenion FAQ](https://www.plenion.be/faq) verwijst voor technische integratiedetails naar Plenion/support. Er is bij deze implementatie geen openbare, volledige HFSQL-datadictionary gevonden. De catalogus is gebaseerd op de PROJ-kolommen die bij de bestaande installatie zijn opgevraagd. De Nederlandse labels zijn werkbeschrijvingen, geen bevestiging van de exacte boekhoudkundige betekenis.

`hfsql-columns.json` bevat 31 optionele PROJ-kolommen: relaties, adres, referenties, facturatie, planning en notities. Portal en reader hebben dezelfde allowlist. Nieuwe ondersteunde velden moeten in beide catalogi worden toegevoegd en getest. Geen vrije SQL, `SELECT *` voor projectinhoud, wachtwoorden of personeelsgegevens. De 24 bestaande velden blijven verplicht, zodat status, relaties, versies en datums niet verdwijnen. `WERK` blijft alleen de bestaande statuslookup leveren.

De Q-box vraagt dagelijks via `SELECT * FROM PROJ WHERE 1=0` uitsluitend kolommetadata op. Er worden daarbij geen records gelezen. De aanwezige namen worden met de bedrijfskey gemeld; alleen aanwezige én ondersteunde kolommen zijn selecteerbaar. De selectie geldt voor alle records in de bestaande export: hoofdprojecten en hun gekoppelde documenten. De bestaande WHERE-clausule en bedrijfsfilters blijven ongewijzigd.

## Werking

1. Portalbeheerder of gebruiker met `projects.manage` kiest onder Tools → Middleware → Q-box de extra velden voor het geselecteerde bedrijf.
2. De reader vraagt vóór iedere sync de actuele configuratie op via `POST /api/integrations/qbox/reader-config`. De server kiest het bedrijf uitsluitend uit de key.
3. De reader valideert catalogus, metadata en configuratieversie, schrijft `data/reader-options.json` met de juiste bestandseigenaar en voert de read-only query uit.
4. Extra waarden komen onder `extra_fields` per JSON-record. Portal bewaart deze in `sourceExtraFields` voor het hoofdproject en `extraFields` voor documenten. Je ziet ze bij Gegevens uit Plenion of het gekoppelde document.
5. Na geslaagde upload bewaart de Q-box lokaal de toegepaste configuratieversie. Deze bevestiging gaat bij de volgende configuratie-opvraag naar Portal; er kan dus één syncinterval vertraging zitten op de weergave.

Extra waarden blijven brontekst of null: geen automatische valuta-, tijdzone- of klantnaamconversie. Lokale titel, eigenaar en andere lokale velden worden niet overschreven. Uitgevinkte extra waarden verdwijnen bij de volgende volledige import uit de opgeslagen brongegevens; HFSQL wordt nooit gewijzigd. Een export blijft maximaal 20 MB en 20.000 records; een extra veld maximaal 16.000 tekens. Een te grote/onvolledige export of mislukte configuratie-opvraag stopt de sync, zonder de vorige bronstand te vervangen. Configureer bij omvangrijke notitievelden eerst een beperkte selectie.

## Bijwerken

Deploy eerst Portal met migratie `20261023120000_qbox_columns` en herbouw API én web. Werk daarna de bestaande installatie op de Q-box bij, vanuit de repository:

```sh
git pull --ff-only
sudo python3 scripts/install_reader.py --target "$HOME/plenion-reader"
cd "$HOME/plenion-reader"
sudo ./plenion.sh sync
```

De updater bouwt een kandidaatimage en test driverinitialisatie als de eigenaar van de HFSQL-configuratie. Daarna neemt hij hetzelfde slot als de sync, maakt een reservekopie en activeert scripts/image samen. Bij een normale activatiefout worden bestanden en image teruggezet. Het is geen garantie tegen stroomuitval midden in de activatie; het geprinte backuppad en de backuptag blijven beschikbaar voor herstel. Een draaiende sync laat de updater veilig stoppen: probeer daarna opnieuw.

De data-map en credentials worden niet vervangen. De bestaande timer wordt niet aan- of uitgezet. Een bestaande service krijgt een timeout van 480 seconden voor metadata/configuratie en export. Voor een installatie zonder timer: `sudo ./plenion.sh auto-install`. De updater vereist een al bestaande standalone installatie; voor eerste installatie geldt README.md.

Het portaal voert geen remote shellcommando's uit en downloadt geen ongecontroleerde software naar de Q-box. Scriptupdates blijven een expliciete lokale installatie. Kolomkeuzes worden daarna automatisch opgehaald.

## Status en Uit WIP (reader 3.3)

Middleware biedt per bedrijf **Alle statussen** of meerdere gekozen statuscodes, plus **Uit WIP: maakt niet uit / alleen uit (0) / alleen aan (1)**. Status en Uit WIP worden met EN gecombineerd. Geen gekozen statuscodes betekent alle statussen; de UI voorkomt een lege keuze in de stand ‘Alleen gekozen statussen’.

De reader meldt de PRS-statuscodes en labels uit WERK; deze worden net als de PROJ-metadata dagelijks ververst. Een nieuwe statuslijst kan lokaal direct worden opgehaald met `sudo ./plenion.sh statuses`, gevolgd door een sync. De labels/codes verschillen per bedrijf. Er worden geen vaste status-ID's in de query gezet.

De SQL-filter geldt uitsluitend voor het hoofdproject. Voor een kindrecord gebruikt de query de status en UITWIP van zijn hoofdproject. Alle gekoppelde offertes/facturen en historische documentversies blijven aanwezig, onafhankelijk van hun eigen status/vinkjes. De bestaande eis ‘actuele projectmap als hoofdrecord’ blijft gelden. Een standalone offerte zonder projectmap wordt hierdoor niet toegevoegd.

Zonder filter blijft de bestaande volledige dossierselectie behouden. Met bijvoorbeeld `statusCodes: ["07"]` en `outOfWip: "off"` worden alleen hoofdprojecten in status 07 met UITWIP=0 opgehaald, met hun documenten. `outOfWip: "on"` selecteert UITWIP=1; het betekent niet automatisch gefactureerd of betaald.

Deploy eerst de aanvullende Portal-migratie `20261023130000_qbox_filters` en API/web, daarna reader 3.3. De keuze verschijnt pas nadat deze reader zijn statuslijst heeft gemeld. Exports dragen de gebruikte selectie en configuratieversie. Portal weigert een afwijkende/verouderde selectie voordat projecten worden aangepast. Een geslaagde lege selectie kan dus alle Plenion-hoofdprojecten uit de actuele importlijst halen, maar verwijdert geen databaseprojecten, taken of Plenion-records. Bij terugschakelen naar alle statussen komen beschikbare dossiers bij de volgende sync terug.

De bestaande schermfilters van Projecten (standaard ‘In uitvoering’) en TV blijven apart gelden. Gebruik ‘Alles’ bij Projecten om alle geïmporteerde fasen te zien. Afwezigheid uit de readerselectie wordt niet als bewezen afsluiting geïnterpreteerd.
