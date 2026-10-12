# Govee Pixel Light (H8630) for Home Assistant

Unofficial; not affiliated with or endorsed by Govee.

Shows any picture or animated GIF on a Govee H8630 Gaming Pixel Light (32×32) from Home Assistant
automations, with no need to create it in the Govee app first. It can also draw live weather,
scrolling text and built-in animated scenes on the light.

## How it works

1. The image is decoded (PNG/JPEG/GIF/WebP/APNG…), fitted to 32×32, and encoded as the light's
   "graffiti" effect: one PNG per frame, up to 100 frames, each with its own duration.
2. Home Assistant serves that file at `/api/govee_pixel/effect/<random key>.bin` with the
   `ETag: "<md5>"` header the light's firmware checks. Keys expire after 10 minutes.
3. A `ptUrl` command goes to the light through Govee's AWS IoT MQTT broker, using your account's
   IoT certificate. The light downloads the file from Home Assistant over your LAN and shows it.
   The image never goes to Govee; only the short "fetch this URL" command uses the cloud. (The
   light's LAN API has no `ptUrl` handler, so that command cannot be sent locally.)
4. The light replies with the download result, so the action fails with an error when the light
   could not fetch the file.

## Install

**HACS:** add this repository as a custom repository (type: Integration), install, and restart.

**Manual:** copy `custom_components/govee_pixel/` to `/config/custom_components/govee_pixel/` and
restart Home Assistant.

Then go to Settings → Devices & services → Add integration → **Govee Pixel Light**,
and log in with your Govee Home account. The first login from a new client asks for the code
Govee emails you.

This integration runs alongside other Govee integrations. It uses its own login session.

**Options → Effect base URL**: the address the *light* uses to reach Home Assistant. By default
it is `http://<HA LAN IP>:<port>`. Set it by hand (e.g. `http://192.168.1.10:8123`) if HA uses
SSL with a certificate the light would not trust, or if the detected IP is wrong. Plain HTTP on
the LAN works.

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

All four actions return `{devices: {<name>: {confirmed, frames, bytes}}}`.

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
