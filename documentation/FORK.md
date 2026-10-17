# Technický popis forku 79thales

## Inicializace a identita

`__init__.py` sestaví OAuth API, předá ConfigEntry koordinátoru a počká na první
úspěšné načtení. Teprve poté se spouštějí platformy binary_sensor, climate,
number, sensor, switch a water_heater. Neúspěšné přihlášení vyžádá reautentizaci;
chyba hlavních dat využívá standardní retry koordinátoru HA.

Koordinátor registruje nadřazený spotřebič pod jeho původním identifikátorem
API a správnou konfigurační položkou. Vrácené interní ID předává zónám a zdrojům
energie jako `via_device_id`. Stejné zařízení proto zachová ID při restartu,
opakovaném čtení i přechodu z původní integrace. Identifikátory stejného názvu
u jiného účtu se nerozliší odhadem, ale vlastníkem ConfigEntry.

Doména, jedinečná ID původních entit, režim `heat=Manual`, `auto=Scheduling`
a předvolby programů zůstávají původní. Odstranění nebo přejmenování entit se
při startu neprovádí. Změna automatizací není součástí integrace.

## Data, čas a chyby

Hlavní dashboard se čte po 60 s. Energie a volitelné endpointy mají interval
15 minut. Každý požadavek API má limit 30 s, volitelné čtení křivky/aktivit
navíc limit 10 s. I neúspěšné volitelné čtení se opakuje až po intervalu.
Po zápisu do křivky/aktivit se cache odstraní a nové čtení proběhne ihned.

HTTP 401 vyžádá nové přihlášení. Síťová chyba volitelné funkce znepřístupní
její ovladače, ale neznepřístupní hlavní termostat. Při pozdějším potvrzení
podpory se číselné ovladače přidají přes posluchače koordinátoru; jedinečná ID
brání duplicitám a unload posluchače odpojí.

Časy požadavků energetického API a evidence jeho dne jsou explicitně UTC.
Nejde o nezávislý součet pro lokální kalendářní den. Senzory časů zachovávají
časový offset odpovědi, naivní čas se vykládá v nastaveném pásmu HA a převádí
na UTC. Neplatné časy a sentinel roku 1 znamenají neznámý údaj.

Energie jsou kWh, plynové objemy m³. Převod plynu vyžaduje skutečnou kladnou
výhřevnost kWh/m³ z API spotřebiče; bez ní se objem nepočítá. Prázdná odpověď,
null, neplatná čísla, výpadek nebo chybějící zdroj se nepřevádějí na nulu.
Skutečná nula vrácená API se zachová. Cloudové součty nejsou měření živého výkonu.

## Příkazy, priorita a souběh

Neexistuje nová automatická priorita řízení: povely vznikají jen voláním služby
HA, ovladačem nebo existující uživatelskou automatizací. Restart, přidání entity
a obnova dat žádný povel nevytváří. Čekající povely se po restartu neobnovují.

Nové zápisy TUV, boost a číselných parametrů sdílejí asyncio.Lock podle ID zóny.
Dokončí se postupně, chyba i zrušení úlohy zámek uvolní. Zápisy se automaticky
neopakují; po timeoutu nelze předpokládat, zda cloud povel provedl. Následující
čtení určí skutečný stav. Zámek neblokuje mobilní aplikaci nebo jiný HA účet.

Číselný vstup musí být konečné číslo v potvrzeném rozsahu API. Sklon je
bezrozměrný, teploty v °C. Krok vychází z API, u TUV zůstává rozlišení UI 0,5 °C.
Meze ani fyzikální limity se z modelu zařízení neodhadují. Během výpadku smí UI
zobrazit poslední potvrzené meze; nový zápis vyžaduje současná platná data.

Křivka vyžaduje v jednom POST sklon i základ. Před zápisem se oba načtou,
validují a změní se jen vybraný. Nelze zabránit externí změně mezi GET a POST,
protože cloud nenabízí podmíněný zápis. Aktivity obdobně nejprve načtou celý
seznam a změní jednu Heating položku, bez záměny za Cooling stejného čísla.

Boost je dostupný jen při výslovně potvrzené capabilityBoostMode. Start
vyžaduje Scheduling; konec přepne do Scheduling. Neznámý typ TUV se nepovažuje
za Tank. Stav se u nových ovladačů potvrzuje až po úspěšném API; neúspěšný zápis
nesmí zanechat zdánlivě novou hodnotu v UI. U klimatu se převzatý optimistický
stav při chybě vrací na předchozí hodnotu a další poll potvrdí skutečnost.

## Diagnostika a soubory

Diagnostika používá explicitní výběr typů, režimů a příznaků dostupnosti.
Neexportuje konfiguraci OAuth, tokeny, názvy domácnosti, identifikátory zón,
sériová čísla, adresy ani surové cloudové odpovědi. Při rozšiřování je nutné
zachovat tento seznam povolených polí.

Produkční kód je v custom_components/remeha_home. Testy obsahují pouze
syntetická data. Soukromé zálohy, registry a historie domácnosti patří mimo
repozitář a nesmějí se přidávat do Git ani do vydání.

## Testy a vydání

Testovací plugin 0.13.372 je svázán s HA 2026.10.1 a Pythonem 3.14.
Testy pokrývají registry, opakované načtení, izolaci účtů, dostupnost při
výpadku volitelných údajů, zachování heat, chyby zápisů, TUV, boost,
křivku, aktivity, souběh, diagnostiku a načtení všech šesti platforem bez povelu.

Workflow zvlášť kontroluje HACS/hassfest, styl a testy. Release ZIP obsahuje
jen Python a JSON integrace, nikoli testy nebo podklady domácnosti. Manifest
musí odpovídat tagu. Vydání neprovádí instalaci do HA ani restart instance.

Validace HACS je pro vlastní repozitář. Výslovně vynechává katalogové kontroly
brands a license: převzatý upstream neobsahuje LICENSE a fork mu novou licenci
nepřiděluje. Manifest, hacs.json, metadata a ostatní kontroly se ověřují běžně.
