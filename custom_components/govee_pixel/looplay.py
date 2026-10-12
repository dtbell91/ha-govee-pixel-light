"""The light's native playlist ("looplay"/"carousel") format. Pure Python, no HA imports.

Decoded from the Govee Home app (CarouselViewModel / CarouselEffectManager) and the light's firmware,
and checked byte-for-byte against real playlists. The light downloads the playlist file via ptUrl
(op 0x61), then fetches each item from its URL and rotates through them on its own; items whose
URL it already has cached are not downloaded again.

  playlist = 00 5F | voiceSwitch u8 | count u8 (1..20) | item*count
  item     = bigData u8 (1) | duration_s LE16 | item mode packet [20] | len LE16 | url | 01 | 58
  item file (what the url serves) = 50 5E + graffiti effect, or 50 5D + GIF89a
  start    = mode packet 33 05 82 | playlist id LE32 | voice id LE32 | voice type | flag
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MAX_ITEMS = 20
MAX_URL_LEN = 120  # firmware item slot holds ~128 chars
PROTOCOL_CODE = 0x58
# 33 05 0A FE 00: the DIY/graffiti sub-mode each item is shown in (as for single images)
ITEM_MODE = bytes([0x33, 0x05, 0x0A, 0xFE, 0x00])


def frame20(data: bytes) -> bytes:
    body = data.ljust(19, b"\x00")
    cs = 0
    for b in body:
        cs ^= b
    return body + bytes([cs])


@dataclass
class Item:
    url: str
    duration: int  # seconds; the light rounds animations up to whole loops


def build_playlist(items: list[Item]) -> bytes:
    if not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError(f"a playlist holds 1-{MAX_ITEMS} items, got {len(items)}")
    out = bytearray([0x00, 0x5F, 0x00, len(items)])
    for item in items:
        url = item.url.encode()
        if len(url) > MAX_URL_LEN:
            raise ValueError(f"item URL longer than {MAX_URL_LEN} characters: {item.url}")
        out += bytes([1]) + struct.pack("<H", max(1, min(0xFFFF, int(item.duration))))
        out += frame20(ITEM_MODE) + struct.pack("<H", len(url)) + url + bytes([1, PROTOCOL_CODE])
    return bytes(out)


def parse_playlist(data: bytes) -> list[tuple[int, int, bytes, bytes, bytes]]:
    """(bigData, duration, mode packet, data/url, trailer) per item. Raises ValueError if malformed."""
    if data[:2] != b"\x00\x5f":
        raise ValueError("not a playlist file")
    count, i, items = data[3], 4, []
    for _ in range(count):
        big, dur = data[i], struct.unpack_from("<H", data, i + 1)[0]
        mode = data[i + 3:i + 23]
        ln = struct.unpack_from("<H", data, i + 23)[0]
        payload = data[i + 25:i + 25 + ln]
        j = i + 25 + ln
        trailer = data[j + 1:j + 1 + data[j]]
        items.append((big, dur, mode, payload, trailer))
        i = j + 1 + data[j]
    if i != len(data):
        raise ValueError(f"{len(data) - i} trailing bytes")
    return items


def start_packet(playlist_id: int, flag: int = 0) -> bytes:
    """Mode packet that switches the light to its playlist (sub-mode 0x82). 19 bytes, checksum added on send."""
    return bytes([0x33, 0x05, 0x82]) + struct.pack("<I", playlist_id) + bytes(4) + bytes([0, flag])
