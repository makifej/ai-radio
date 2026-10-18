# AI Rádió adásvezérlés (Liquidsoap 2.2)
#
# Prioritás: élő bemenet > hírblokk > zene. A zene a kiválasztott stílus
# (ha van), egyébként az aktuális napszakhoz
# rendelt online rádiók közül az első, amelyik szól; ha az nem csatlakozik
# vagy elnémul, a lista következő eleme jön, ha egyik sem, a helyi tartalék
# mappa, végső esetben csend (mksafe).
#
# Az állomáslistát az `airadio stations render` írja a stations.json fájlba,
# és induláskor olvassuk be. Minden beállított állomás végig csatlakozva
# marad: a 2.2.4-es input.http leállítás utáni újraindításkor versenyhelyzetbe
# kerül (ffmpeg_io.ml assertion), ezért a választást a fallback/switch végzi,
# nem a bemenetek ki-be kapcsolása.

def env(name, default) =
  environment.get(default=default, name)
end

settings.log.stdout := true
# Az irányítópult a naplófájlt olvassa, ha meg van adva.
log_file = env("AIRADIO_LOG_FILE", "")
settings.log.file := log_file != ""
if log_file != "" then
  settings.log.file.path := log_file
  settings.log.file.perms := 420
end

data_dir = env("AIRADIO_DATA_DIR", "/var/lib/airadio")
stations_file = "#{data_dir}/stations.json"
status_file = "#{data_dir}/status.json"
control_file = "#{data_dir}/control.json"
news_dir = "#{data_dir}/news"
backup_dir = "#{data_dir}/backup"
station_name = env("AIRADIO_NAME", "AI Rádió")

icecast_host = env("ICECAST_HOST", "127.0.0.1")
icecast_port = string.to_int(default=8000, env("ICECAST_PORT", "8000"))
icecast_password = env("ICECAST_SOURCE_PASSWORD", "")
icecast_mount = env("ICECAST_MOUNT", "radio.mp3")
live_port = string.to_int(default=8005, env("LIVE_PORT", "8005"))
live_password = env("LIVE_PASSWORD", "")

# Az irányítópult helyi telnet-kapcsolaton kérdezi le a szinteket és indítja az effekteket.
settings.server.telnet := true
settings.server.telnet.bind_addr := "127.0.0.1"
settings.server.telnet.port := string.to_int(default=1234, env("AIRADIO_TELNET_PORT", "1234"))

if icecast_password == "" or live_password == "" then
  log.critical(label="airadio", "Az ICECAST_SOURCE_PASSWORD és a LIVE_PASSWORD kötelező.")
  exit(1)
end

# --- Állomások ----------------------------------------------------------------

no_dayparts = ([] : [{name: string, start: int, stop: int, stations: [{name: string, url: string}]}])

def load_dayparts() =
  try
    let json.parse (cfg : {dayparts: [{name: string, start: int, stop: int, stations: [{name: string, url: string}]}]}) =
      file.contents(stations_file)
    cfg.dayparts
  catch err do
    log.severe(label="allomasok", "#{stations_file} nem olvasható: #{err.message}")
    no_dayparts
  end
end

# A napszak órái [start, stop); ha start > stop, átnyúlik éjfélen (pl. 22-6).
def in_daypart(hour, d) =
  if d.start <= d.stop then
    hour >= d.start and hour < d.stop
  else
    hour >= d.start or hour < d.stop
  end
end

def current_hour() =
  string.to_int(default=0, time.string("%H"))
end

all_dayparts = load_dayparts()
dayparts = list.filter(fun (d) -> list.length(d.stations) > 0, all_dayparts)

# Ugyanaz az URL több napszakban is szerepelhet, de csak egyszer csatlakozunk rá.
station_urls = list.fold(
  fun (acc, d) -> list.fold(fun (acc, s) -> list.mem(s.url, acc) ? acc : [...acc, s.url], acc, d.stations),
  [],
  dayparts
)

station_sources = list.mapi(
  fun (i, (url : string)) -> begin
    src = input.http(id="allomas_#{i}", max_buffer=10., timeout=10., poll_delay=10., url)
    (url, blank.strip(id="allomas_#{i}_csend", max_blank=15., threshold=-45., track_sensitive=false, src))
  end,
  station_urls
)

def station_source(url) =
  list.assoc(default=source.fail(), url, station_sources)
end

def daypart_branch(d) =
  stations = fallback(id="napszak_#{d.name}", track_sensitive=false, list.map(fun (s) -> station_source(s.url), d.stations))
  ({in_daypart(current_hour(), d)}, stations)
end

relay =
  if list.length(dayparts) > 0 then
    switch(id="napszak", track_sensitive=false, list.map(daypart_branch, dayparts))
  else
    log.important(label="allomasok", "Nincs beállított állomás, csak a tartalék szól.")
    source.fail()
  end

# Kézi állomásválasztás az irányítópultról (control.json): bármelyik beállított
# állomás, napszaktól függetlenül; ha nem szól, az automata megy tovább.
override_url = ref("")
manual =
  if list.length(station_sources) > 0 then
    switch(id="kezi", track_sensitive=false, list.map(fun (p) -> ({override_url() == fst(p)}, snd(p)), station_sources))
  else
    source.fail()
  end

# Stílus: az irányítópult a kiválasztott stílus egyik online rádióját ffmpeg-gel
# ide továbbítja (airadio/relay.py). Amíg nincs stílus kiválasztva, ez a bemenet
# üres, és a napszak szerinti zene szól. Állomásváltáskor csak a továbbító cserél
# forrást, a Liquidsoapot nem kell újraindítani.
style_in = input.harbor(id="stilus", port=live_port, user="source", password=live_password, buffer=2., max=5., "stilus")
style = blank.strip(id="stilus_csend", max_blank=15., threshold=-45., track_sensitive=false, style_in)

backup = playlist(id="tartalek", mode="randomize", reload_mode="watch", backup_dir)

def keresztattunes(a, b) =
  add(normalize=false, [fade.in(duration=1.5, b), fade.out(duration=1.5, a)])
end
music = fallback(id="zene", track_sensitive=false, transitions=[keresztattunes, keresztattunes, keresztattunes, keresztattunes],
                 [style, manual, relay, backup])

news = request.queue(id="hirek")

# Kis puffer, hogy az élő adás pár másodpercen belül átvegye az adást.
live = input.harbor(id="elo", port=live_port, user="source", password=live_password, buffer=3., max=10., "live")

# --- Keverőpult ------------------------------------------------------------------
# A mixer.json-t az irányítópult írja; negyedmásodpercenként figyeljük, így a
# faderek szinte azonnal hatnak. Minden érték dB-ben, kivéve: comp 0..1,
# xfade másodperc. -60 dB vagy kevesebb = teljes csend.

mixer_file = "#{data_dir}/mixer.json"

mixer_default = {
  music={gain=0., mute=false}, news={gain=0., mute=false}, live={gain=0., mute=false},
  cart={gain=0., mute=false}, master={gain=0., mute=false},
  news_bed=-60., live_bed=-60.,
  eq={low=0., mid=0., high=0.},
  comp=0., limiter=true, xfade=1.
}
mix = ref(mixer_default)
mixer_mtime = ref(-1.)

def poll_mixer() =
  mtime = file.exists(mixer_file) ? file.mtime(mixer_file) : -1.
  if mtime != mixer_mtime() then
    mixer_mtime := mtime
    if mtime >= 0. then
      try
        let json.parse (m : {
          music: {gain: float, mute: bool}, news: {gain: float, mute: bool}, live: {gain: float, mute: bool},
          cart: {gain: float, mute: bool}, master: {gain: float, mute: bool},
          news_bed: float, live_bed: float, eq: {low: float, mid: float, high: float},
          comp: float, limiter: bool, xfade: float
        }) = file.contents(mixer_file)
        mix := m
      catch err do
        log.severe(label="keverespult", "#{mixer_file}: #{err.message}")
      end
    end
  end
end
poll_mixer()
thread.run(every=0.25, poll_mixer)

def lin(db) =
  if db <= -59.9 then 0. else lin_of_dB(db) end
end

def channel(ch) =
  if ch.mute then 0. else lin(ch.gain) end
end

# Csatornák: fader után mérve, ahogy egy keverőpulton.
music_ch = rms(id="m_zene", duration=0.12, amplify(id="ch_zene", {channel(mix().music)}, music))
news_ch = rms(id="m_hirek", duration=0.12, amplify(id="ch_hirek", {channel(mix().news)}, news))
live_ch = rms(id="m_elo", duration=0.12, amplify(id="ch_elo", {channel(mix().live)}, live))

# Zenei alap: a hírek, illetve az élő adás alá keverhető halk zene.
news_mix = switch(id="hirek_alap", track_sensitive=false, [
  ({source.is_ready(news)}, add(normalize=false, [news_ch, amplify({lin(mix().news_bed)}, music_ch)]))
])
live_mix = switch(id="elo_alap", track_sensitive=false, [
  ({source.is_ready(live)}, add(normalize=false, [live_ch, amplify({lin(mix().live_bed)}, music_ch)]))
])

def atmenet(a, b) =
  d = mix().xfade
  if d <= 0.05 then b else add(normalize=false, [fade.in(duration=d, b), fade.out(duration=d, a)]) end
end

radio = fallback(id="adas", track_sensitive=false, transitions=[atmenet, atmenet, atmenet], [live_mix, news_mix, music_ch])
radio = mksafe(radio)

current_meta = ref(([] : [(string * string)]))
radio.on_metadata(fun (m) -> current_meta := m)
started = time.string("%Y-%m-%d %H:%M:%S")

# Effekt-kártyák (jinglek, hangminták): minden fölé keverve szólnak.
cart = request.queue(id="cart")
cart_ch = rms(id="m_effekt", duration=0.12, amplify(id="ch_effekt", {channel(mix().cart)}, cart))
radio = add(normalize=false, [radio, cart_ch])

# Master: háromsávos hangszínszabályzó, kompresszor, végerősítés, limiter.
radio = filter.iir.eq.peak(frequency=100., q=0.7, gain={mix().eq.low}, radio)
radio = filter.iir.eq.peak(frequency=1000., q=0.8, gain={mix().eq.mid}, radio)
radio = filter.iir.eq.peak(frequency=8000., q=0.7, gain={mix().eq.high}, radio)
radio = compress(threshold=-20., ratio=3., attack=15., release=250., gain={mix().comp * 6.}, wet={mix().comp}, radio)
radio = amplify(id="ch_master", {channel(mix().master)}, radio)
radio = limit(threshold={mix().limiter ? -1. : 40.}, radio)
master_peak = peak(id="p_master", duration=0.12, radio)
radio = rms(id="m_master", duration=0.12, master_peak)

output.icecast(
  %mp3(bitrate=128, samplerate=44100, stereo=true),
  host=icecast_host,
  port=icecast_port,
  password=icecast_password,
  mount=icecast_mount,
  name=station_name,
  description="Személyes AI-rádió",
  public=false,
  radio
)

# --- Állapot -------------------------------------------------------------------
# Az `airadio status` és az irányítópult ezt olvassa. Az aktív állomás az
# aktuális napszak első szóló állomása, ugyanúgy, ahogy a fallback választ.

all_stations = list.fold(
  fun (acc, d) -> list.fold(fun (acc, s) -> list.exists(fun (x) -> x.url == s.url, acc) ? acc : [...acc, s], acc, d.stations),
  [],
  dayparts
)

def station_name_for(url) =
  list.find(default={name=url, url=url}, fun (s) -> s.url == url, all_stations).name
end

active_station = ref("")
last_on_air = ref("")

def report() =
  hour = current_hour()
  d = list.find(default={name="", start=0, stop=0, stations=[]}, fun (d) -> in_daypart(hour, d), all_dayparts)
  playing = list.find(default={name="", url=""}, fun (s) -> station_source(s.url).is_ready(), d.stations)
  manual_on = override_url() != "" and station_source(override_url()).is_ready()

  on_air =
    if source.is_ready(live) then "elo"
    elsif source.is_ready(news) then "hirek"
    elsif source.is_ready(style) then "stilus"
    elsif manual_on then "kezi"
    elsif playing.name != "" then "allomas"
    elsif source.is_ready(backup) then "tartalek"
    else "csend" end

  # A stílus dalcímét a harbor adja; ha onnan visszaváltunk, ne maradjon kint a régi cím.
  if on_air != last_on_air() then
    if last_on_air() == "stilus" then current_meta := [] end
    last_on_air := on_air
  end

  label =
    if source.is_ready(style) then "stílus"
    elsif manual_on then station_name_for(override_url())
    elsif playing.name != "" then playing.name
    else "tartalék" end
  if label != active_station() then
    daypart = if d.name == "" then "nincs napszak" else d.name end
    log.important(label="allomasok", "#{daypart}: #{label} szól")
    active_station := label
  end

  meta = current_meta()
  file.write(
    data=json.stringify({
      daypart=d.name,
      station=label,
      url=if manual_on then override_url() else playing.url end,
      on_air=on_air,
      override=override_url(),
      title=list.assoc(default="", "title", meta),
      artist=list.assoc(default="", "artist", meta),
      stations=list.map(fun (s) -> {name=s.name, url=s.url, ready=station_source(s.url).is_ready()}, d.stations),
      all_stations=list.map(fun (s) -> {name=s.name, url=s.url, ready=station_source(s.url).is_ready()}, all_stations),
      news=source.is_ready(news),
      live=source.is_ready(live),
      style=source.is_ready(style),
      started=started,
      updated=time.string("%Y-%m-%d %H:%M:%S")
    }),
    status_file
  )
end

thread.run(delay=3., every=2., report)

# Szintek az irányítópult mérőihez (lineáris 0..1, a felület számol dB-t).
def on_air_now() =
  if source.is_ready(live) then "elo" elsif source.is_ready(news) then "hirek" else "zene" end
end

server.register(namespace="keveres", "szintek", fun (_) -> json.stringify({
  music=music_ch.rms(), news=news_ch.rms(), live=live_ch.rms(), cart=cart_ch.rms(),
  master=radio.rms(), peak=master_peak.peak(), on_air=on_air_now(), cart_playing=source.is_ready(cart)
}))

# --- Vezérlés az irányítópultról ------------------------------------------------
# control.json: {"override": "<stream URL vagy üres>", "skip_news": <számláló>}

skip_seen = ref(-1)

def poll_control() =
  if file.exists(control_file) then
    try
      let json.parse (c : {override: string, skip_news: int}) = file.contents(control_file)
      if c.override != override_url() then
        override_url := c.override
        if c.override == "" then
          log.important(label="vezerles", "Vissza az automatára")
        else
          log.important(label="vezerles", "Kézi állomás: #{station_name_for(c.override)}")
        end
      end
      # az első olvasás csak szinkronizál, induláskor nem állítunk le semmit
      if skip_seen() >= 0 and c.skip_news > skip_seen() then
        log.important(label="vezerles", "Hírblokk leállítva")
        news.skip()
      end
      skip_seen := c.skip_news
    catch err do
      log.severe(label="vezerles", "#{control_file}: #{err.message}")
    end
  end
end

thread.run(delay=2., every=2., poll_control)

# --- Óránkénti hírblokk ---------------------------------------------------------
# Az `airadio news` a hirek-ÉÉÉÉHHNN-ÓÓ.wav fájlt az óra előtt elkészíti; itt
# az egész óra első öt percében indítjuk, ha megvan. Élő adás alatt kimarad.

top_of_hour = {0m-5m}
news_done_for = ref("")

def check_news() =
  stamp = time.string("%Y%m%d-%H")
  if top_of_hour() and news_done_for() != stamp then
    path = "#{news_dir}/hirek-#{stamp}.wav"
    if file.exists(path) then
      news_done_for := stamp
      if source.is_ready(live) then
        log.important(label="hirek", "Élő adás megy, a #{stamp} hírblokk kimarad.")
      else
        log.important(label="hirek", "Hírblokk indul: #{path}")
        news.push(request.create("annotate:title=\"Hírek\",artist=\"#{station_name}\":#{path}"))
      end
    end
  end
end

# Azonnali hírblokk (`airadio news --now`): az azonnal.wav-ot előbb átnevezzük,
# hogy csak egyszer induljon; a régieket az airadio takarítja.
def check_now() =
  path = "#{news_dir}/azonnal.wav"
  if file.exists(path) then
    stamp = time.string("%Y%m%d-%H%M%S")
    playing = "#{news_dir}/azonnal-#{stamp}.wav"
    file.move(path, playing)
    log.important(label="hirek", "Azonnali hírblokk indul: #{playing}")
    news.push(request.create("annotate:title=\"Hírek\",artist=\"#{station_name}\":#{playing}"))
  end
end

thread.run(every=5., fun () -> begin check_news(); check_now() end)
