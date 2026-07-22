"""HBK binary container reader (port of HbkContainerReader.kt)."""

from __future__ import annotations

import struct
from pathlib import Path


class HbkContainerReader:
    BYTES_BY_FILE_INFOS = 12

    def read_entities(self, path: Path) -> dict[str, bytes]:
        data = path.read_bytes()
        entities_addr = self._entity_addresses(data)
        return {name: self._file_body(data, addr) for name, addr in entities_addr.items()}

    def _entity_addresses(self, data: bytes) -> dict[str, int]:
        pos = 0
        pos += 16  # int * 4
        pos += 2  # short
        payload_size, pos = self._long_string(data, pos)
        block_size, pos = self._long_string(data, pos)
        pos += 11  # long + byte + short
        start = pos
        file_infos = data[pos : pos + payload_size]
        pos = start + block_size

        result: dict[str, int] = {}
        count = len(file_infos) // self.BYTES_BY_FILE_INFOS
        off = 0
        for _ in range(count):
            header_address, body_address, reserved = struct.unpack_from("<iii", file_infos, off)
            off += 12
            if reserved != 0x7FFFFFFF:
                raise RuntimeError("Unexpected HBK reserved field")
            name = self._file_name(data, header_address)
            result[name] = body_address
        return result

    def _file_name(self, data: bytes, header_address: int) -> str:
        pos = header_address
        pos += 2
        payload_size, pos = self._long_string(data, pos)
        pos += 40
        nbytes = payload_size - 24
        return data[pos : pos + nbytes].decode("utf-16-le", errors="replace")

    def _file_body(self, data: bytes, body_address: int) -> bytes:
        pos = body_address
        pos += 2
        payload_size, pos = self._long_string(data, pos)
        pos += 20
        return data[pos : pos + payload_size]

    def _long_string(self, data: bytes, pos: int) -> tuple[int, int]:
        s = data[pos : pos + 8].decode("ascii", errors="replace")
        pos += 8
        pos += 1  # skip separator byte
        return int(s, 16), pos
