"""Разбор потока fMP4 на лету.

Twitch отдаёт стримы в двух видах: старом MPEG-TS и новом fMP4. TS можно начать читать с любого места,
а fMP4 нет: в самом начале идёт «заголовок» (блоки ftyp и moov), без которого дальнейшие куски видео
не открываются. Запись перезапускается после каждой рекламы и обрыва, поэтому заголовок нужно запомнить
и отдавать каждому новому процессу записи, а начинать с границы куска (moof), а не с середины.
"""
from __future__ import annotations

WHOLE = (b"ftyp", b"moov")          # блоки заголовка: копим целиком
MP4_STARTS = (b"ftyp", b"styp", b"moov", b"moof", b"sidx", b"emsg", b"prft", b"free")


def sniff(data: bytes) -> str:
    """Вид потока по первым байтам: mp4, ts или unknown."""
    if len(data) >= 8 and data[4:8] in MP4_STARTS:
        return "mp4"
    if len(data) >= 189 and data[0] == 0x47 and data[188] == 0x47:
        return "ts"
    return "unknown"


class Mp4Boxes:
    """Режет поток на блоки верхнего уровня. feed() отдаёт (тип, байты, это_начало_блока).

    Блоки заголовка (ftyp, moov) приходят одним куском целиком, остальные по мере поступления данных.
    """

    def __init__(self) -> None:
        self.head = b""              # недочитанный заголовок блока
        self.need = 8
        self.kind = b""
        self.left = 0                # сколько байт осталось до конца текущего блока
        self.whole: bytearray | None = None

    def feed(self, data: bytes) -> list[tuple[bytes, bytes, bool]]:
        out: list[tuple[bytes, bytes, bool]] = []
        pos, n = 0, len(data)
        while pos < n:
            if self.left == 0:
                take = data[pos:pos + self.need - len(self.head)]
                self.head += take
                pos += len(take)
                if len(self.head) < self.need:
                    break
                size = int.from_bytes(self.head[:4], "big")
                kind = self.head[4:8]
                if size == 1 and self.need == 8:        # размер не влез в 4 байта и записан в следующих восьми
                    self.need = 16
                    continue
                if size == 1:
                    size = int.from_bytes(self.head[8:16], "big")
                hdr, self.head, self.need = self.head, b"", 8
                if size < len(hdr) or not all(48 <= c <= 122 for c in kind):
                    raise ValueError(f"поток fMP4 сбился: блок {kind!r} размером {size}")
                self.kind, self.left = kind, size - len(hdr)
                if kind in WHOLE:
                    if size > 4_000_000:
                        raise ValueError(f"слишком большой заголовок fMP4: {size} байт")
                    self.whole = bytearray(hdr)
                    if self.left == 0:
                        out.append((kind, bytes(self.whole), True))
                        self.whole = None
                else:
                    out.append((kind, hdr, True))
                continue
            take = data[pos:pos + self.left]
            pos += len(take)
            self.left -= len(take)
            if self.whole is not None:
                self.whole += take
                if self.left == 0:
                    out.append((self.kind, bytes(self.whole), True))
                    self.whole = None
            else:
                out.append((self.kind, take, False))
        return out
