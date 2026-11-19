"""Разбор потока fMP4: заголовок ловится целиком, границы кусков находятся при любой нарезке данных."""
import random

import pytest

from streamclipper.mp4stream import Mp4Boxes, sniff


def box(kind: bytes, payload: bytes, large: bool = False) -> bytes:
    if large:
        return (1).to_bytes(4, "big") + kind + (16 + len(payload)).to_bytes(8, "big") + payload
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def test_sniff():
    assert sniff(box(b"ftyp", b"mp42" + bytes(8))) == "mp4"
    assert sniff(box(b"styp", b"msdh")) == "mp4"
    assert sniff((b"\x47" + bytes(187)) * 2) == "ts"
    assert sniff(b"") == "unknown" and sniff(b"<html>") == "unknown"


def test_boxes_survive_any_chunking():
    rng = random.Random(5)
    ftyp, moov = box(b"ftyp", b"mp42" + bytes(20)), box(b"moov", rng.randbytes(900))
    stream = ftyp + moov
    for i in range(12):
        stream += box(b"styp", b"msdh") + box(b"moof", rng.randbytes(300)) + box(b"mdat", rng.randbytes(40_000), large=i % 4 == 0)
    stream += box(b"free", b"")
    for seed in range(20):
        r = random.Random(seed)
        p, pos, rebuilt, inits, starts = Mp4Boxes(), 0, bytearray(), [], []
        while pos < len(stream):
            n = r.choice([1, 3, 7, 8, 9, 100, 5000, 70_000])
            for kind, piece, is_start in p.feed(stream[pos:pos + n]):
                rebuilt += piece
                if kind in (b"ftyp", b"moov"):
                    inits.append(piece)
                    assert is_start
                if is_start:
                    starts.append(kind)
            pos += n
        assert bytes(rebuilt) == stream                        # ни байта не потеряно и не переставлено
        assert inits == [ftyp, moov]                           # заголовок пришёл целыми блоками
        assert starts == [b"ftyp", b"moov"] + [b"styp", b"moof", b"mdat"] * 12 + [b"free"]


def test_lost_sync_is_reported():
    p = Mp4Boxes()
    with pytest.raises(ValueError):
        p.feed(box(b"moof", bytes(10)) + bytes(64))            # после блока идёт мусор
