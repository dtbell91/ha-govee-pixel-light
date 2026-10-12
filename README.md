# Govee Pixel Light (H8630) for Home Assistant

Unofficial; not affiliated with or endorsed by Govee.

Shows any picture or animated GIF on a Govee H8630 Gaming Pixel Light (32×32) from Home Assistant
automations, with no need to create it in the Govee app first.

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

## Examples

Weather icon from GIFs you drop into `/config/www/govee/` (named after the weather states):

```yaml
triggers:
  - trigger: state
    entity_id: weather.home
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
