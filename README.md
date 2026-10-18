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

## Helyi futtatás a saját gépen (Mac, Windows, Linux)

Kell hozzá a Docker Desktop. A kicsomagolt mappában:

```sh
cp .env.example .env        # írd bele az ANTHROPIC_API_KEY-t
docker compose up -d --build
```

Utána böngészőben (Dia, Chrome, Safari): `http://localhost:8010`. Ez a lejátszóoldal, a nyers stream a `http://localhost:8010/radio.mp3` címen érhető el. A 8010-es portot azért választottuk, mert a 8000-est gyakran más helyi alkalmazás foglalja. Ha a 8010 is foglalt, az `.env` fájlban az `AIRADIO_PORT` értékével átírható.

Az első indulás néhány percig tart: a Docker felépíti a környezetet, letölti a Piper hangokat, és összeállítja az állomáslistát. Minden adat a `data` mappába kerül. A beállítás itt van: `data/config/radio.toml`. A tartalék zenét a `data/backup` mappába másold, a jelszavakat a `data/config/secrets.env` tartalmazza. Beállítás vagy állomáslista módosítása után:

```sh
docker compose exec -u airadio radio airadio stations render
docker compose restart
```

Hírblokk azonnal, adásba: `docker compose exec -u airadio radio airadio news --now`. Ha az `.env` fájlt módosítod (például az API-kulcsot), a `restart` nem elég, `docker compose up -d` kell, mert a környezeti változók a konténer létrehozásakor kerülnek be.

Napló: `docker compose logs -f`. Leállítás: `docker compose down`. Élő adás helyben a 8015-ös porton megy, a többi az Élő adás résznél leírtak szerint.

## Telepítés Oracle Cloud Always Free szerverre

Hozz létre egy Ubuntu 24.04 példányt (az Ampere A1, ARM is megfelelő). A konzolban a példány alhálózatának Security Listjébe vegyél fel bejövő TCP szabályt a 8000-es portra (hallgatás), és ha élőben is adnál, a 8005-ösre. A szerveren lévő tűzfalat a telepítő nyitja meg; az Oracle Ubuntu képein ez külön lépés, mert az alap iptables szabály minden mást elutasít.

```sh
git clone https://github.com/<felhasználó>/ai-radio.git
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
