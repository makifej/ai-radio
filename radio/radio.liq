# AI Rádió adásvezérlés (Liquidsoap 2.2)
#
# Prioritás: élő bemenet > hírblokk > zene. A zene az aktuális napszakhoz
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

backup = playlist(id="tartalek", mode="randomize", reload_mode="watch", backup_dir)
music = fallback(id="zene", track_sensitive=false, [manual, relay, backup])

news = request.queue(id="hirek")

# Kis puffer, hogy az élő adás pár másodpercen belül átvegye az adást.
live = input.harbor(id="elo", port=live_port, user="source", password=live_password, buffer=3., max=10., "live")

def atmenet(a, b) =
  add(normalize=false, [fade.in(duration=1., b), fade.out(duration=1., a)])
end

radio = fallback(id="adas", track_sensitive=false, transitions=[atmenet, atmenet, atmenet], [live, news, music])
radio = mksafe(radio)

current_meta = ref(([] : [(string * string)]))
radio.on_metadata(fun (m) -> current_meta := m)
started = time.string("%Y-%m-%d %H:%M:%S")

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

def report() =
  hour = current_hour()
  d = list.find(default={name="", start=0, stop=0, stations=[]}, fun (d) -> in_daypart(hour, d), all_dayparts)
  playing = list.find(default={name="", url=""}, fun (s) -> station_source(s.url).is_ready(), d.stations)
  manual_on = override_url() != "" and station_source(override_url()).is_ready()

  on_air =
    if source.is_ready(live) then "elo"
    elsif source.is_ready(news) then "hirek"
    elsif manual_on then "kezi"
    elsif playing.name != "" then "allomas"
    elsif source.is_ready(backup) then "tartalek"
    else "csend" end

  label =
    if manual_on then station_name_for(override_url())
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
      started=started,
      updated=time.string("%Y-%m-%d %H:%M:%S")
    }),
    status_file
  )
end

thread.run(delay=3., every=2., report)

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
