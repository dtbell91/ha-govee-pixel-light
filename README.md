# Govee Pixel Light (H8630) for Home Assistant

Unofficial; not affiliated with or endorsed by Govee.

Shows any picture or animated GIF on a Govee H8630 Gaming Pixel Light (32×32) from Home Assistant
automations, with no need to create it in the Govee app first. It can also draw live weather,
scrolling text and built-in animated scenes, and run a **playlist** that the light rotates by itself,
with interruptions (e.g. a doorbell snapshot) that hand back to the playlist afterwards.

## How it works

1. The image is decoded (PNG/JPEG/GIF/WebP/APNG…), fitted to 32×32, and encoded as the light's
   "graffiti" effect: one PNG per frame, up to 100 frames, each with its own duration.
2. Home Assistant serves that file at `/api/govee_pixel/f/<md5>.bin` with the `ETag: "<md5>"`
   header the light's firmware checks. One-off images stay available for 10 minutes; playlist files
   are kept on disk for as long as a playlist uses them.
3. A `ptUrl` command goes to the light through Govee's AWS IoT MQTT broker, using your account's
   IoT certificate. The light downloads the file from Home Assistant over your LAN and shows it.
   The image never goes to Govee; only the short "fetch this URL" command uses the cloud. (The
   light's LAN API has no `ptUrl` handler, so that command cannot be sent locally.)
4. The light replies with the download result, so the action fails with an error when the light
   could not fetch the file.

With [local MQTT](#local-mqtt-no-cloud) the command in step 3 goes through Home Assistant's own
MQTT broker instead, and nothing uses Govee's cloud.

## Install

**HACS:** add this repository as a custom repository (type: Integration), install, and restart.

**Manual:** copy `custom_components/govee_pixel/` to `/config/custom_components/govee_pixel/` and
restart Home Assistant.

Then go to Settings → Devices & services → Add integration → **Govee Pixel Light**, choose
**Govee account**, and log in with your Govee Home account. The first login from a new client asks
for the code Govee emails you.

This integration runs alongside other Govee integrations. It uses its own login session.

### Local MQTT (no cloud)

For a light running local-MQTT custom firmware: the light's MQTT client is pointed at a broker on
the light itself, which bridges to Home Assistant's MQTT broker. Choose **Local MQTT** when adding
the integration and enter the topic prefix the light's bridge uses (default `govee/h8630/`). Home
Assistant's MQTT integration must be set up. No Govee account is needed.

The light is expected on these topics (under the prefix):

| Topic | |
|---|---|
| `cmd` | Commands to the light (the same JSON it receives from Govee's broker) |
| `state` | The light's replies and status reports |
| `lwt` | The light's MQTT last will |
| `bridge/state` | `1` / `0`, retained: whether the light's bridge to this broker is connected |
| `telemetry` | Optional, retained: device health, e.g. `{"rssi": -65, "uptime": 3600, "mem_available_kb": 71000, "udisk_free_kb": 236000, "ip": "192.168.1.20", "firmware": "3.2.0", "base": "1.00.91", "profile": "local", "slot": "B"}` |
| `button` | Optional: `{"button": "power" \| "mode" \| "volume" \| "customize", "action": "press" \| "long_press"}` |
| `jack` | Optional, retained: `{"headphones": false, "microphone": false}` |

Setup asks the light for its status and uses its reply to identify it. A light set up through a
Govee account entry has to be removed from that entry first. Playlists belong to the entry, so
recreate them on the local entry.

### Options

| Option | |
|---|---|
| Effect base URL | The address the *light* uses to reach Home Assistant. By default `http://<HA LAN IP>:<port>`. Set it by hand (e.g. `http://192.168.1.10:8123`) if HA uses SSL with a certificate the light would not trust, or if the detected IP is wrong. Plain HTTP on the LAN works. Keep it short: the light stores playlist URLs in ~128-character slots |
| Playlist playback | **On the light** (default): one command sends the whole playlist and the light rotates by itself. **From Home Assistant**: HA sends each item in turn (one MQTT command per item). If the light rejects a playlist, HA falls back to rotating it automatically |
| Resume after an interruption | **Re-send playlist** (default, confirmed by the light; cached items are not downloaded again) or **Switch back only** (experimental: just re-selects the light's stored playlist) |
| Re-apply the playlist if the light shows something else | On by default. At each refresh HA asks the light what it is showing; if it is on but not showing the playlist (after a power cut the light does not restore its playlist mode, or a scene was picked in the Govee app), the playlist is sent again. Turn off if you want scenes chosen elsewhere to stay |
| Refresh live items every | How often weather / URL / camera items are redrawn (default 15 min). Only items whose picture changed are downloaded again |
| Image encoding | **PNG frames** (default, verified) or **Automatic** (experimental): also uses the light's native GIF format when that is lossless and smaller, and sends 32×32 GIFs unchanged |

## Entities

| Entity | |
|---|---|
| Light | On/off and brightness. Effects: **Playlist** (start or resume the playlist) or one of the built-in animations (pauses the playlist until you pick Playlist again). Updated instantly: the light reports every change itself, including ones made with its buttons or other apps |
| Message (notify) | `notify.send_message` shows the text (with the title in front, if given), then the playlist resumes after 60 s |
| Display | What the light shows: `off`, `playlist`, `image` (something from Home Assistant) or `govee_scene` |
| Playlist / Playlist status | Playlist switch and status (see [Playlist](#playlist)) |
| Last image | Preview of the last picture or animation sent |
| Power / Mode / Volume / Customize button | Event entities (`press`, `long_press`) for the light's physical buttons, for automations. The light still acts on each press itself. Local MQTT with `button` messages only |
| Headphones | Whether headphones are plugged in. Local MQTT with `jack` messages only |
| WiFi signal, Last boot, Memory available, Custom firmware (diagnostic) | From `telemetry` messages (Storage free and IP address are there too, disabled by default). The firmware version also shows on the device page. Local MQTT only |

The light, message and display entities become unavailable when the light can't be reached
(local MQTT: Home Assistant's broker connection, the light's `bridge/state` and its last will;
Govee account: the cloud connection).

## Action: `govee_pixel.show_image`

| Field | |
|---|---|
| `device_id` | The light (device selector), or a list of lights |
| `image_path` | Local file, e.g. `/config/www/govee/rain.gif` (`/config/www` and `/media` are allowed by default) |
| `image_url` | Picture or GIF to download |
| `image_entity` | An `image.*` or `camera.*` entity's current picture |
| `fit` | `contain` (letterbox, default), `cover` (crop), `stretch` |
| `resample` | `lanczos` (default, best for photos), `nearest` (keeps pixel art crisp), `box`, `bilinear` |
| `background` | RGB behind transparency and letterbox bars, default `[0, 0, 0]` |
| `frame_duration` | Override per-frame time in ms (30–30000); default is the GIF's own timing, or 100 ms |
| `max_frames` | Longer animations are thinned to this many frames (≤100) |

Give exactly one of `image_path`, `image_url` or `image_entity`. The action returns
`{devices: {<name>: {confirmed, frames, bytes}}}` and fails if the light reports that the
download failed. Each light also gets an `image.<name>_last_image` entity that shows an
upscaled preview of what was last sent.

Notes: animations up to ~15 KB match Govee's own scenes, and a 24-frame 32×32 GIF is ~12 KB.
Pixel art drawn at 32×32 looks best. Frames identical to the previous one are merged.

## Action: `govee_pixel.show_weather`

An animated weather icon with the temperature underneath, or a 3-day forecast, drawn live.

| Field | |
|---|---|
| `device_id` | The light(s) |
| `weather_entity` | A `weather.*` entity: its condition, temperature and unit are used |
| `forecast` | `true` shows the next three days (icon, high, low) from the entity's daily forecast |
| `condition` | Any Home Assistant weather condition (`sunny`, `clear-night`, `partlycloudy`, `cloudy`, `fog`, `rainy`, `pouring`, `lightning`, `lightning-rainy`, `snowy`, `snowy-rainy`, `hail`, `windy`, `exceptional`). Overrides the entity's |
| `temperature` | Number to show. Overrides the entity's |
| `temperature_unit` | `°C` / `°F`, picks the temperature colour (defaults to the entity's or the system unit) |

## Action: `govee_pixel.show_text`

A message in a chunky 3×5 pixel font (letters are shown in capitals). Text that fits is centred;
longer text scrolls. The light holds at most 100 frames, so long messages scroll in bigger steps.

| Field | |
|---|---|
| `message` | The text |
| `color` | RGB; leave out for a rainbow |
| `background` | RGB, default black |
| `size` | `large` (about 4 characters at once) or `small` (about 8) |
| `frame_duration` | ms per scroll step, default 70 |

## Action: `govee_pixel.show_animation`

One of the built-in animated scenes: `morning`, `bus`, `maths`, `reading`, `art`, `music`,
`sport`, `playground`, `lunch`, `homework`, `bedtime`, `heart`.

All four actions return `{devices: {<name>: {confirmed, frames, bytes}}}` and take an optional
`duration` (seconds): if the light's playlist is playing, what you show stays up that long (default
60) and then the playlist resumes. `duration: 0` keeps it on screen until
`govee_pixel.playlist_resume`. With no playlist playing, the image simply stays.

## Playlist

Each light has one playlist of up to 20 items, kept by Home Assistant and played by the light
itself. Items are any of the four content types above, with the same options as the matching
`show_*` action, and a duration in seconds (the light rounds animations up to whole loops).

- `switch.<light>_playlist` starts / stops it (stopping clears the screen).
- `sensor.<light>_playlist_status`: `stopped`, `rotating` (on the light), `rotating_ha` (from Home
  Assistant), `interrupted`, `paused` or `error`; attributes list the items, when an interruption
  ends, the last error, and the light's last reported on/off state and mode. (The light does the rotating, so HA does not know which item is on screen.)
- `govee_pixel.playlist_add`: add an item, or replace the item with the same name (`name`,
  `item_type`: image/weather/text/animation, `options`, `duration`, optional `position`).
- `govee_pixel.playlist_remove` (`name`), `govee_pixel.playlist_clear`,
  `govee_pixel.playlist_resume`, `govee_pixel.playlist_refresh` (redraw everything now).

Changes are sent to the light straight away while the playlist is playing. Automatic re-sends
(refreshes, resuming after an interruption, re-applying) wait while the light is switched off, so
they never wake it; they catch up at the next refresh after it is back on. The light caches items
by URL and every file's URL is derived from its content, so only new or changed pictures are
downloaded. Playlist files are served by Home Assistant, so the light needs to be able to reach it
whenever it (re)loads the playlist.

## Generating GIF files

`scripts/generate_gifs.py` renders the same designs to GIF files (only Pillow needed), e.g. to
copy into `/config/www/` or to preview them:

```sh
pip install pillow
python3 scripts/generate_gifs.py all          # every design + preview_sheet.png in ./pixel_displays/
python3 scripts/generate_gifs.py weather rainy 17
python3 scripts/generate_gifs.py forecast sunny:24:13 partlycloudy:21:12 rainy:16:9
python3 scripts/generate_gifs.py text "Dinner time!" --color 255,200,0
python3 scripts/generate_gifs.py animation bedtime
```

## Examples

A playlist (run once, e.g. from Developer tools → Actions), then turn on `switch.<light>_playlist`:

```yaml
action: govee_pixel.playlist_add
data:
  device_id: <your light's device id>
  name: weather
  item_type: weather
  options: {weather_entity: weather.home}
  duration: 20
---
action: govee_pixel.playlist_add
data:
  device_id: <your light's device id>
  name: cat
  item_type: image
  options: {image_path: /config/www/govee/cat.gif, resample: nearest}
  duration: 15
```

Doorbell: show the camera for 30 seconds, then go back to the playlist:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.doorbell
    to: "on"
actions:
  - action: govee_pixel.show_image
    data:
      device_id: <your light's device id>
      image_entity: camera.front_door
      fit: cover
      duration: 30
```

Live weather whenever it changes:

```yaml
triggers:
  - trigger: state
    entity_id: weather.home
actions:
  - action: govee_pixel.show_weather
    data:
      device_id: <your light's device id>
      weather_entity: weather.home
```

Tomorrow's forecast every evening:

```yaml
triggers:
  - trigger: time
    at: "19:00:00"
actions:
  - action: govee_pixel.show_weather
    data:
      device_id: <your light's device id>
      weather_entity: weather.home
      forecast: true
```

A routine reminder:

```yaml
triggers:
  - trigger: time
    at: "07:45:00"
actions:
  - action: govee_pixel.show_animation
    data:
      device_id: <your light's device id>
      animation: bus
  - delay: 30
  - action: govee_pixel.show_text
    data:
      device_id: <your light's device id>
      message: "Shoes on!"
      color: [255, 200, 0]
```

Your own GIFs, picked by a template (here: named after the weather state):

```yaml
actions:
  - action: govee_pixel.show_image
    data:
      device_id: <your light's device id>
      image_path: "/config/www/govee/{{ states('weather.home') }}.gif"
      resample: nearest
```

Doorbell camera snapshot when someone rings:

```yaml
actions:
  - action: govee_pixel.show_image
    data:
      device_id: <your light's device id>
      image_entity: camera.front_door
      fit: cover
```

## Disclaimer

- This is an unofficial project, not affiliated with, endorsed by or supported by Govee.
  "Govee" is used only to say which devices it works with.
- It talks to Govee's undocumented, private app API and MQTT broker. Its protocol details were
  worked out by analysing the public Govee Home Android app and the light's traffic, for
  interoperability. No Govee code, firmware, keys or other assets are included. Govee may
  change or block this at any time, and using unofficial clients may be against Govee's terms
  of service. Use it at your own risk.
- Much of the code was written with an AI assistant (Claude) and then tested by hand against
  a real H8630.
- Tested with the H8630 only. Other devices of the same type (goodsType 285/286) are detected
  but untested.
