"""
XBee Pro S1 — API Mode (AP=1) frame utilities.

Frame format:
    [0x7E] [Length MSB][Length LSB] [Frame Data ...] [Checksum]

Frame Data starts with a Frame Type byte:
    0x00 = TX Request 64-bit    -> Envio de datos con direccion de 64 bits
    0x89 = TX Status            -> Respuesta del modulo a un envio, indica si se envio con exito o no
    0x80 = RX Packet 64-bit     -> Formato de trama para lo que llega, contien la direccion de origen, es creada dinamicamente por el xbee
    ej:
    Frame Data = [0x00] [frame_id] [Dirección de destino 64 bits] [Opciones] [Datos]
                1 byte     1 byte      8 bytes           1 byte      N bytes
    Frame Data: [0x89] [frame_id] [status]
    Frame Data = [0x80] [Dirección de origen 64 bits] [RSSI (1 byte)] [Opciones (1 byte)] [Datos (payload)]

    * RSSI -> Values closer to 0 indicate a stronger signal (e.g., -30 dBm is excellent, -90 dBm is very poor).

* Entonces por cada paquete que se envia 0x00, se recibe un 0x80 y se vuelve a enviar (desde el receptor) un 0x89?
    
"""

"""
* Notacion big-endian (>H): el byte mas significativo va primero, ej: 0x1234 se guarda como [0x12, 0x34]     tal cual lectura humana

    - > = big-endian (byte alto primero)
    - H = unsigned short = entero de 2 bytes


[0x7E] -> 01 byte de inicio, marca el inicion de trama
[Length MSB] y [Length LSB] -> 02 bytes -> Longitud de Frame Data, usa notacion big-endian 
[Frame Data ...] -> N bytes -> Contenido de la trama, empieza con un byte de tipo de trama (explicado arriba)

"""



from __future__ import annotations # para tipos que se refieren a si mismos, pj: Persona que tiene un atributo hijo de tipo Persona

import struct       # empaquetar datos en estructuras binarias
from dataclasses import dataclass   # decorador dataclass para clases de solo datos

# ── Frame type constants ─────────────────────────────────────────────
FRAME_TX_REQUEST_64 = 0x00
FRAME_TX_STATUS     = 0x89
FRAME_RX_PACKET_64  = 0x80

# ── Application-layer header ─────────────────────────────────────────
# image_id (uint16) + chunk_idx (uint8) + total_chunks (uint8) = 4 bytes
APP_HEADER_FMT  = ">HBB"
APP_HEADER_SIZE = struct.calcsize(APP_HEADER_FMT)   # 4
CHUNK_DATA_SIZE = 96  # bytes of JPEG data per chunk


# ── Parsed result dataclasses ─────────────────────────────────────────

@dataclass
class TxStatus:
    frame_id: int
    status: int          # 0x00=success, 0x01=no ACK, 0x02=CCA fail, 0x03=purged


@dataclass
class RxPacket64:
    src_addr: bytes      # 8 bytes
    rssi: int            # -dBm
    options: int
    data: bytes          # application payload


# ── Checksum ──────────────────────────────────────────────────────────
# suma los bits y se queda solo con el byte menos significativo (mas a la derecha)
# se usa el complemento (0xFF - sum), asi solo el receptor suma los bits y checksum y debe dar 0xFF para ser valido
def _checksum(frame_data: bytes) -> int:
    return (0xFF - (sum(frame_data) & 0xFF)) & 0xFF


# ── Build frames ──────────────────────────────────────────────────────
# Helper to the above function -> Crea todo menos el Frame data 
def build_api_frame(frame_data: bytes) -> bytes:
    """Wrap frame_data with 0x7E delimiter, 2-byte length and checksum.
     --> [0x7E] [Length MSB][Length LSB] [Frame Data ...] [Checksum]"""
    length = len(frame_data)
    cs = _checksum(frame_data)
    return b"\x7E" + struct.pack(">H", length) + frame_data + bytes([cs])

# Build the Frame Data and create the whole API frame for a TX Request 64-bit (0x00)
def build_tx64(frame_id: int, dest64: bytes, payload: bytes) -> bytes:
    """Build a TX Request 64-bit frame (type 0x00)."""
    frame_data = (
        bytes([FRAME_TX_REQUEST_64, frame_id])
        + dest64
        + bytes([0x00])   # options
        + payload
    )
    return build_api_frame(frame_data)


# ── Read one complete frame from serial ───────────────────────────────

def read_frame(ser, timeout_s: float = 2.0) -> bytes | None:
    """
    Read one complete API frame from the serial port.

    Returns the raw frame bytes (including 0x7E, length, data, checksum)
    or None on timeout / incomplete read.
    """
    # Guardar el timeout original del serial, establecer el nuevo timeout
    saved_timeout = ser.timeout
    ser.timeout = timeout_s

    try:
        # Scan for start delimiter
        while True:
            byte = ser.read(1)
            if not byte:
                return None
            if byte[0] == 0x7E:
                break

        length_bytes = ser.read(2)
        if len(length_bytes) < 2:
            return None

        length = struct.unpack(">H", length_bytes)[0]
        remaining = ser.read(length + 1)        # frame_data + checksum
        if len(remaining) < length + 1:
            return None

        frame_data = remaining[:length]
        cs = remaining[length]

        # Verify checksum
        if (sum(frame_data) + cs) & 0xFF != 0xFF:
            return None

        return b"\x7E" + length_bytes + remaining
    finally:
        ser.timeout = saved_timeout


# ── Parse frames ──────────────────────────────────────────────────────

def parse_tx_status(frame: bytes) -> TxStatus | None:
    """Parse a TX Status frame (0x89).  Returns TxStatus or None."""
    if len(frame) < 7 or frame[0] != 0x7E:
        return None
    if frame[3] != FRAME_TX_STATUS:
        return None
    return TxStatus(frame_id=frame[4], status=frame[5])


def parse_rx64(frame: bytes) -> RxPacket64 | None:
    """Parse an RX Packet 64-bit frame (0x80).  Returns RxPacket64 or None."""
    if len(frame) < 15 or frame[0] != 0x7E:
        return None
    if frame[3] != FRAME_RX_PACKET_64:
        return None
    return RxPacket64(
        src_addr=frame[4:12],
        rssi=frame[12],
        options=frame[13],
        data=frame[14:-1],
    )


# ── Application-layer helpers ─────────────────────────────────────────

def build_chunk_payload(image_id: int, chunk_idx: int, total_chunks: int,
                        data: bytes) -> bytes:
    """Pack the 4-byte application header + chunk data."""
    header = struct.pack(APP_HEADER_FMT, image_id, chunk_idx, total_chunks)
    return header + data


def parse_chunk_payload(payload: bytes):
    """Unpack application header.  Returns (image_id, chunk_idx, total_chunks, data)."""
    if len(payload) < APP_HEADER_SIZE:
        return None
    image_id, chunk_idx, total_chunks = struct.unpack(
        APP_HEADER_FMT, payload[:APP_HEADER_SIZE]
    )
    data = payload[APP_HEADER_SIZE:]
    return image_id, chunk_idx, total_chunks, data
