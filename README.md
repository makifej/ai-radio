# AI Rádió

Személyes, 24 órás internetes rádió. A zenét napszaktól függően más online rádiók adásából veszi, minden óra elején saját hírblokkot mond be, amelyet a Claude (Sonnet 5.5) ír meg magyar RSS-forrásokból, és a Piper olvas fel helyben, ingyenesen. Bármikor átveheted élőben, utána magától visszaáll.

## Hogyan működik

A **Liquidsoap** (`radio/radio.liq`) állítja össze az adást, az **Icecast** szolgálja ki egyetlen címen (`/radio.mp3`). A prioritás: élő bemenet, hírblokk, zene. A zene az aktuális napszak állomáslistájának első olyan eleme, amelyik szól. Ha egy állomás nem csatlakozik vagy 15 másodpercig néma, a következő jön; ha egyik sem szól, a helyi tartalék mappa.

Az **airadio** Python csomag minden óra 50. percében (systemd timer) elkészíti a következő óra hírblokkját:

1. letölti a beállított RSS-forrásokat, és csak a dátummal ellátott, 6 óránál frissebb tételeket tartja meg;
2. a Claude ezekből, és csak ezekből, kötött JSON-sémában megírja a felolvasandó szöveget, minden hírnél megjelölve a forrástételt; amelyik hír nem létező tételre hivatkozik, kimarad, a bizonytalan vagy kényes híreket a modell eleve kihagyja;
3. a Piper felolvassa: a felvezetést és a zárást a napszak műsorvezetője (`voices.host`), a híreket a hírolvasó (`voices.news`);
4. az ffmpeg összefűzi és hangosságra normálja, majd ellenőrzés jön: hossz, némaság; ha túl hosszú, a végéről marad ki hír.

A kész fájl `/var/lib/airadio/news/hirek-ÉÉÉÉHHNN-ÓÓ.wav`; a Liquidsoap az óra első öt percében indítja. Ha bármelyik lépés elakad, abban az órában csak zene szól. Élő adás alatt a hír kimarad.

## Irányítópult

Egy teljes szélességű webes stúdiófelületen állítható minden, szerkesztés nélkül. A **Stúdió** nézet közepén keverőpult van. A zene, a hírek, az élő bemenet és az effektek csatornáján fader, némítás, LED-es szintmérő és adásban-jelző lámpa található. A hírek és az élő adás alá halk zenei alap keverhető. A master részen háromsávos hangszínszabályzó, kompresszor, limiter és állítható átúsztatás van. Az állomás-deckről egy gombnyomással adásba küldhető bármelyik állomás, az effekt-kártyákról pedig jingle vagy zenetári hang, a zene fölé keverve. A keverőpult beállításai a `mixer.json` fájlba kerülnek, és újraindítás után is megmaradnak. A Stúdió nézet mutatja, mi szól most (automata, kézi, hírek, élő adás, tartalék), a napi műsorrendet és a hallgatók számát. Innen indítható azonnali hírblokk, megállítható egy éppen szóló hír, választható kézzel állomás, és vissza lehet állni az automatára. A tetején a zenére pulzáló spektrum látszik: oszlop, hullám és kör stílusban, a műsor állapotához igazodó színekkel. Teljes képernyőre is nagyítható. A fejlécben monitor és mini spektrum van. A hallgatói lejátszón a Play gomb körül kör alakú spektrum lüktet.

**Stílusok.** A Stúdió nézetben 171 zenei stílus közül lehet választani, 13 csoportban (rock, metal, elektronikus, hip-hop és R&B, pop, magyar, jazz és blues, klasszikus, világzene és latin, country és folk, relax, évtizedek, egyéb), keresővel. Kiemelve: Rock, Metal, Nu metal, Chill, Dance, Jóga és Meditáció. Egy stílushoz néhány fix állomás és a Radio Browser katalógus azon népszerű adói tartoznak, amelyeknél a stílus címkéje pontosan és elöl szerepel; a minden címkét magukra aggató adók kimaradnak, a reklámszöveggel hosszított neveket rövidíti; a listát naponta frissíti, és a `data/state/styles.json` fájlban tárolja. A kiválasztott stílus egyik adóját az irányítópult ffmpeg-gel továbbítja a Liquidsoap `stilus` bemenetére, így a váltás pár másodperc, újraindítás nélkül, a dalcímmel együtt. Ha egy adó nem szól, magától a következő jön; a deck ilyenkor a stílus állomásait mutatja, bármelyik kiválasztható. Az Automata gomb visszaállít a napszak szerinti zenére. A választás újraindítás után is megmarad. Stílus közben a napszak állomásai a háttérben csatlakozva maradnak, így eggyel több adás töltődik le.

Az **Állomások** lapon napszakonként rendezhetők és kipróbálhatók az adók. Itt lehet keresni a Radio Browser katalógusában, és az „Alkalmazás” gombbal egy pár másodperces újraindítással élesíteni a változást. A **Hírek** lapon beállítható, mely órákban legyen hír, milyen forrásokból, melyik Claude-modellel és milyen gondolkodási szinttel. Itt látszik a hírtörténet a szövegekkel, visszahallgatható hanganyaggal és a Claude-költséggel (napi, havi és összesített bontásban).

A **Napszakok** lapon az idősávok és a műsorvezetői karakterek szerkeszthetők. A **Hangok** lapon a műsorvezető és a hírolvasó Piper-hangja választható, letölthető és meghallgatható. A **Zenetár** a tartalék zene és a hírszignál feltöltésére szolgál. Az **Élő adás** lap megadja a csatlakozási adatokat, a **Napló** pedig az adás és a hírszerkesztő eseményeit mutatja.

Dockerben: `http://localhost:8020`. Ez csak a saját gépről érhető el. Szerveren az irányítópult a 127.0.0.1:8080 címen fut, és SSH-alagúton érhető el: `ssh -L 8020:127.0.0.1:8080 ubuntu@<szerver-ip>`, utána `http://localhost:8020`. Ha `ADMIN_PASSWORD` környezeti változót adsz meg, jelszót is kér (felhasználó: admin).

## Helyi futtatás a saját gépen (Mac, Windows, Linux)

Kell hozzá a Docker Desktop. A kicsomagolt mappában:

```sh
cp .env.example .env        # írd bele az ANTHROPIC_API_KEY-t
docker compose up -d --build
```

Utána böngészőben (Dia, Chrome, Safari): `http://localhost:8010` a hallgatói lejátszó, `http://localhost:8020` az irányítópult. A 8010-es oldal a lejátszó, a nyers stream a `http://localhost:8010/radio.mp3` címen érhető el. A 8010-es portot azért választottuk, mert a 8000-est gyakran más helyi alkalmazás foglalja. Ha a 8010 is foglalt, az `.env` fájlban az `AIRADIO_PORT` értékével átírható.

Az első indulás néhány percig tart: a Docker felépíti a környezetet, letölti a Piper hangokat, és összeállítja az állomáslistát. Minden adat a `data` mappába kerül. A beállítás itt van: `data/config/radio.toml`. A tartalék zenét a `data/backup` mappába másold, a jelszavakat a `data/config/secrets.env` tartalmazza. Beállítás vagy állomáslista módosítása után:

```sh
docker compose exec -u airadio radio airadio stations render
docker compose restart
```

Hírblokk azonnal, adásba: `docker compose exec -u airadio radio airadio news --now`. Ha az `.env` fájlt módosítod (például az API-kulcsot), a `restart` nem elég, `docker compose up -d` kell, mert a környezeti változók a konténer létrehozásakor kerülnek be.

Napló: `docker compose logs -f`. Leállítás: `docker compose down`. Élő adás helyben a 8015-ös porton megy, a többi az Élő adás résznél leírtak szerint.

### Hallgatás telefonon

A Docker-változat a rádió mellett egy Cloudflare-alagutat is indít (`tunnel` szolgáltatás, fiók nélküli Quick Tunnel). Ez nyilvános `https://….trycloudflare.com` címet ad a hallgatói lejátszónak, így mobilnetről is szól. Az irányítópult Stúdió nézetében a „Hallgatás telefonon” kártyán QR-kóddal látszik ez a cím, és az otthoni wifis cím is (`http://<a Mac címe>:8010`, ezt az automatikus frissítő írja a `data/host.json` fájlba). A nyilvános cím a Docker vagy a Mac újraindításakor megváltozik, és csak addig él, amíg a Mac és a Docker fut; a Cloudflare a Quick Tunnelre nem vállal rendelkezésre állást. Csak a lejátszó és az adás kerül ki, az irányítópult nem. Kikapcsolás: `docker compose stop tunnel`.

Telefonon a lejátszó a hangot nem vezeti át a Web Audión, mert iOS-en lezárt képernyőnél így elnémulhat; a spektrum ott ütemre lüktető mintát mutat. A zárolási képernyőn a dalcím és a lejátszás gomb is megjelenik.

### Automatikus frissítés (Mac)

Egyszer kell lefuttatni a Terminálban, miközben a rádió már fut a Dockerben:

```sh
curl -fsSL https://raw.githubusercontent.com/makifej/ai-radio/main/deploy/mac/install-autoupdate.sh | bash
```

A telepítő a rádió mappáját a `~/ai-radio` helyre teszi (a macOS a háttérfolyamatoknak nem engedi a Letöltések, az Asztal és a Dokumentumok mappát), majd egy launchd feladatot állít be. Ez kétpercenként megnézi, van-e új változat a GitHubon, és ha van, letölti, majd újraépíti a konténert; közben az adás fél percre megszakadhat. Az `.env` és a `data` mappa nem változik. Napló: `~/ai-radio/data/log/frissites.log`. Kikapcsolás: `launchctl bootout gui/$(id -u)/hu.airadio.autoupdate`.

## Telepítés Oracle Cloud Always Free szerverre

Hozz létre egy Ubuntu 24.04 példányt (az Ampere A1, ARM is megfelelő). A konzolban a példány alhálózatának Security Listjébe vegyél fel bejövő TCP szabályt a 8000-es portra (hallgatás), és ha élőben is adnál, a 8005-ösre. A szerveren lévő tűzfalat a telepítő nyitja meg; az Oracle Ubuntu képein ez külön lépés, mert az alap iptables szabály minden mást elutasít.

```sh
git clone https://github.com/makifej/ai-radio.git
cd ai-radio
sudo ANTHROPIC_API_KEY=sk-ant-... ./deploy/install.sh
```

A telepítő feltelepíti a Liquidsoapot, az Icecastot és az ffmpeg-et, létrehozza az `airadio` rendszerfelhasználót, a jelszavakat véletlenszerűen generálja (`/etc/airadio/airadio.env`), letölti a Piper hangokat, összeállítja az állomáslistát, és elindítja a szolgáltatásokat. Újrafuttatható, a meglévő beállításokat nem írja felül. Ha az API-kulcsot nem adtad meg, írd be utólag az `/etc/airadio/airadio.env` fájlba.

Hallgatás: `http://<szerver-ip>:8000/radio.mp3` bármilyen lejátszóban (VLC, böngésző, telefonos rádióalkalmazás).

## Beállítás

Minden a `/etc/airadio/radio.toml` fájlban van (minta: `config/radio.example.toml`): napszakok és állomások, hírforrások, a hírek órái, a Claude modell és effort szintje, a Piper hangok.

Állomások: napszakonként megadhatsz saját stream-címeket (`stations`), és/vagy keresési feltételt a Radio Browser katalógusához (`search`). Az `airadio stations render` mindkettőből összeállítja a listát; a keresési találatok közül csak azok kerülnek be, amelyek éppen szólnak. A változás a rádió újraindításával lép életbe:

```sh
airadio stations search --tag jazz --country HU
airadio stations render
sudo systemctl restart airadio-radio
```

A beállított állomások mindegyike végig csatlakozva marad (lásd: Ismert korlátok), ezért napszakonként két-három állomás elég.

Hírforrások: a mintában csak a Telex címe ellenőrzött. Továbbiakat az `airadio feeds check` paranccsal próbálj ki, mielőtt bekapcsolod őket.

Tartalék zene: bármilyen mp3 vagy ogg fájl a `/var/lib/airadio/backup` mappában; menet közben is bővíthető.

Hírszignál: ha létezik `/var/lib/airadio/jingles/hirek_eleje.wav` vagy `hirek_vege.wav`, a hírblokk elejére, illetve végére kerül.

## Napi használat

```sh
airadio status                                   # mi szól most, kész-e a következő hír
sudo -u airadio airadio news --dry-run           # hírblokk szövege hang nélkül (Claude-hívás!)
sudo -u airadio airadio news --now               # hírblokk most azonnal, egyből adásba megy
journalctl -u airadio-radio -f                   # adásnapló
journalctl -u airadio-news                       # hírszerkesztés naplója, tokenszámokkal
```

## Élő adás

Bármilyen Icecast-kompatibilis küldőprogrammal, például a BUTT-tal: szerver a gép IP-címe, port 8005, mount `/live`, felhasználó `source`, jelszó a `LIVE_PASSWORD` az `/etc/airadio/airadio.env` fájlból. Csatlakozás után pár másodpercen belül átveszi az adást, bontáskor a zene áttűnéssel visszajön.

## Költség

A szoftver ingyenes, a Piper helyben fut, karakterdíj nélkül. A Claude API tokenalapon fizetős. Saját becslésem szerint egy hírblokk kb. 7 ezer bemeneti és 2-4 ezer kimeneti tokenből áll, a gondolkodással együtt; napi 17 blokkal a Sonnet 5.5 ára így nagyjából 20-30 dollár havonta. A valós értéket a `journalctl -u airadio-news` mutatja, minden futás kiírja a tokenszámokat. Csökkenthető kevesebb hírórával (`news.hours`) vagy alacsonyabb `claude.effort` szinttel.

## Ismert korlátok

A Liquidsoap 2.2.4 (az Ubuntu 24.04 csomagja) `input.http` bemenete leállítás utáni újraindításkor versenyhelyzetbe kerül (`ffmpeg_io.ml` assertion), és elnémulhat. Ezért a szkript nem kapcsolgatja a bemeneteket: minden beállított állomás folyamatosan letöltődik, a választást a `fallback` és a `switch` végzi. Ez állomásonként kb. 128 kbit/s folyamatos bejövő forgalom.

A hírblokk az óra elején a forrásrádió éppen szóló dalába vág bele, egy másodperces áttűnéssel.

A Piper hangok licence hangonként eltér, a hang `MODEL_CARD` fájljában áll; használat előtt nézd meg.

A Claude-hívás formáját a hivatalos SDK-val, helyi álszerverrel teszteltük; éles API-hívás a fejlesztés során nem történt, az első valódi futást a `--dry-run` paranccsal érdemes megnézni.

## Fejlesztés

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
ICECAST_SOURCE_PASSWORD=x LIVE_PASSWORD=y liquidsoap --check radio/radio.liq
```
