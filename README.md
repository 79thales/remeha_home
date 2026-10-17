# Remeha Home — fork 79thales

Samostatně spravovaný fork integrace Remeha Home pro Home Assistant **2026.8 a novější**.
Zachovává doménu `remeha_home`, identifikátory zařízení a původní režimy
`heat`, `auto`, `off` i předvolby `clock_program_1` až `clock_program_3`.
Nainstalovaný termostat musí fungovat v oficiální aplikaci Remeha Home.

## Funkce

- Opravená registrace zařízení přes `via_device_id`, včetně zón TUV a jednotlivých zdrojů energie.
- Termostat, stav topení/chlazení, pokojová a venkovní teplota, tlak a programy.
- Ovládání TUV: program, Comfort, Eco, cílové teploty a přepínač boost, pokud zařízení potvrdí podporu.
- Sklon a základ topné křivky s mezemi a krokem z API.
- Teploty jednotlivých topných aktivit programu, oddělené od aktivit chlazení.
- Energie jednotlivých zdrojů, pokud je cloud vrací; chybějící údaje jsou neznámé a nevytvářejí falešné nuly.
- Diagnostika dostupnosti funkcí bez identifikátorů domácnosti a přihlašovacích údajů.
- Sériové zpracování zápisů TUV/číselných parametrů stejné zóny a kontrola hodnot před odesláním.

Instalace, načtení a restart integrace pouze čtou data. Nastavení se mění až
výslovným použitím ovladače nebo zavoláním služby. Dostupnost konkrétních funkcí
určuje odpověď zařízení; typ tepelného čerpadla sám o sobě nepotvrzuje jejich podporu.

## Instalace přes HACS

1. V HACS přidejte vlastní repozitář `https://github.com/79thales/remeha_home`, kategorie **Integration**.
2. Nainstalujte vydání tohoto forku a restartujte Home Assistant.
3. Při nové instalaci přidejte integraci **Remeha Home** a přihlaste se údaji oficiální aplikace.

Při přechodu z původní integrace zachovejte její konfigurační položku v
**Nastavení → Zařízení a služby**. Instalované soubory v
`custom_components/remeha_home` musí pocházet z jednoho vydání tohoto forku.
Původní repozitář a tento fork používají stejnou doménu a nemají se instalovat
souběžně. Neodstraňujte existující zařízení ani entity. Před přechodem vytvořte
zálohu a ověřte, že HACS následně sleduje tento repozitář.

## Ovládání a omezení

Boost lze spustit z programu; vypnutí boost vrátí zónu do programu. Délku
určuje zařízení a API. Eco teplotu lze nastavovat pouze u potvrzeného zásobníku
`dhwType=Tank`. Pokud v programu není známá aktivní teplota Comfort/Eco,
obecný ovladač cílové teploty zápis odmítne; lze použít výslovný číselný ovladač.

Topná křivka se nenastavuje automaticky. Sklon je bezrozměrný, základ v °C.
Každá změna čte aktuální křivku a zachová druhý parametr. Výpadek API,
neznámé meze nebo požadavek mimo rozsah zápis zastaví.

Hlavní data se obnovují po 60 s, energie, křivka a aktivity po 15 minutách.
Po změně se data načtou znovu. API může energetické hodnoty vracet se zpožděním;
jejich nula není nezávislým měřením příkonu. Neznámé údaje se neodhadují.
Podrobnosti jsou v [technické dokumentaci](documentation/FORK.md).

## Vývoj a ověření

Testy používají Home Assistant 2026.10.1, Python 3.14 a Linux. Cloudové API je
simulováno; registry a platformy jsou skutečné. Spuštění:

```sh
python -m pip install -r requirements_test.txt
pytest -q
ruff check custom_components tests scripts/build_release.py
```

Úspěšné testy nepotvrzují podporu každého modelu nebo skutečné provedení povelu
cloudem. Provozní ověření na zařízení musí zkontrolovat jeho data a logy.

## Původ změn

Základem je [msvisser/remeha_home](https://github.com/msvisser/remeha_home).
Funkční rozšíření a jejich autorská historie byla převzata z
[hugo-leij/remeha_home](https://github.com/hugo-leij/remeha_home), commit
`20bf0ffa82055c1b341ae538529af4473fbbb5bf`. Poděkování patří také autorům původních
komunitních příspěvků, jejichž commity jsou zachovány v historii.
Vlastní opravy kompatibility, validace, diagnostika, testy a vydání spravuje tento fork.

## Generovat dashboard Vytápění 66 new

Přibalená funkce `generate_dashboard()` v `custom_components/remeha_home/generate_dashboard.py`
a šablony `dashboard-template.json` / `dashboard-template.yaml` vytvářejí dva pohledy
společně: hlavní přehled `vytapeni-66-new` a podhled `vytapeni-66-new-detaily`.
Tlačítko **Detaily** schová surová měření a diagnostiku Shelly, cloudové energie,
diagnostiku TRV a stav existujících automatizací. Hlavní pohled obsahuje nové
ovládání TUV, boost, topnou křivku, aktivity, termostaty a přehled spotřeby.

Generátor je samostatný offline nástroj standardního Pythonu. Nenačítá integraci,
nevolá služby HA a neřídí topení. Konkrétní entity se předávají soukromým JSON
slovníkem vazeb; všechny názvy mezi `@@...@@` v šabloně musí být navázány na
existující `entity_id`. `DASHBOARD_BASE` je relativní adresa, například `/lovelace`.
Vazby je nutné zkontrolovat pro danou domácnost; přibalené rozložení předpokládá
také měřidla Shelly a doplňkové topné zóny. Ovladače zapisují až při použití.

```sh
python custom_components/remeha_home/generate_dashboard.py --input dashboard-before.json --bindings bindings-private.json --output dashboard-proposed.json
```

Vstup je úplná exportovaná konfigurace Lovelace (případně obálka konektoru s
`config`), výstup je úplná nová konfigurace JSON. Vstup se nemění. Generátor
zachová obsah a pořadí ostatních pohledů; oba vlastní pohledy aktualizuje bez
duplicit. Při prvním vytvoření je vloží za původní pohled `tepelne-cerpadlo`,
případně na konec. Neúplné vazby a duplicitní vlastní cesty odmítne. Před zápisem
přes API je třeba záloha, kontrola aktuální konfigurace a ověření výsledku.

Okamžitý příkon je součet nezáporných činných výkonů TČ a elektrokotle ve W.
Záporné hodnoty se pouze v přehledu zobrazují jako 0 W; hodnoty se neobracejí
absolutní hodnotou. Chybějící, nečíselná nebo déle než 180 s nehlášená data jsou
nedostupná, nikoli nula. Stáří se posuzuje podle `last_reported`, aby stálá hodnota
nebyla zaměněna za výpadek. Markdown se aktualizuje při změně měření a díky
`now()` nejméně každou minutu. Zdrojová historie a měřidla se nemění.

Týdenní, měsíční a roční kWh ukazují změnu samostatných odběrových čítačů TČ a
elektrokotle od začátku probíhajícího kalendářního období v časovém pásmu HA.
Nejde o klouzavých 7/30/365 dní. Karty používají existující statistiky recorderu;
nevytvářejí pomocné měřiče a nezahrnují čítače opačného směru. Chybějící historii
nedopočítávají. Remeha denní energie jsou zvlášť v detailech, protože cloud může
mít zpoždění a nehlásí vždy všechny veličiny.

Při změně rozložení upravte funkci i obě šablony společně a ověřte shodu JSON/YAML,
navigaci, dostupnost vazeb a zachování ostatních pohledů. Zdrojové YAML má české
komentáře; Lovelace storage ukládá JSON a YAML komentáře nepřenáší. Jinja komentář
výpočtu zůstává přímo v uloženém obsahu karty. Toto rozšíření zavádí pouze statické
soubory a nemění běhové řízení integrace.
