"""
XBee Pro S1 — API Mode (AP=1) frame utilities.

Frame format:
    [0x7E] [Length MSB][Length LSB] [Frame Data ...] [Checksum]

Frame Data starts with a Frame Type byte:
    - 0x00 = TX Request 64-bit    -> Envio de datos con direccion de 64 bits

    - 0x89 = TX Status            -> Respuesta del modulo a un envio, indica si se envio con exito o no, puede ser: 0x00=success, 0x01=no ACK, 0x02=CCA fail, 0x03=purged

    - 0x80 = RX Packet 64-bit     -> Formato de trama para lo que llega, contien la direccion de origen, es creada dinamicamente por el xbee
    
        Frame Data = [0x00] [frame_id] [Dirección de destino 64 bits] [Opciones] [Datos]
                1 byte     1 byte      8 bytes           1 byte      N bytes
        Frame Data: [0x89] [frame_id] [status]
                1 byte     1 byte      1 byte
        Frame Data = [0x80] [Dirección de origen 64 bits] [RSSI] [Opciones] [Datos (payload)]
                1 byte     8 bytes      1 byte         1 byte          N bytes

    * RSSI -> Values closer to 0 indicate a stronger signal (e.g., -30 dBm is excellent, -90 dBm is very poor).

* Entonces por cada paquete que se envia 0x00, se recibe un 0x80 y se vuelve a enviar (desde el receptor) un 0x89?
    - el código en A construye una trama 0x00 con destino B y un frame_id
    - El módulo B recibe la señal de radio. Si todo va bien, B:
        Envía a A un ACK a nivel de radio (esto no genera una trama 0x89 en B, es puramente radio -> transparente para nosotros).
        Además, reconstruye el paquete de datos y lo saca por su propio UART como una trama 0x80 (RX Packet). Esa trama se la entrega al código que esté corriendo en el PC/micro de B. Ahí es donde leemos con read_frame y parseas con parse_rx64

        Donde A y B son Xbees:
        Codigo A -> TX Request 64-bit -> A -> envia -> B -> RX Packet 64-bit -> Codigo B
        * Cuando B envia su ACK (transparente para nosotros) a A, A crea un frame de tipo 0x89 (TX Status) con el mismo frame_id que el 0x00 que se envio, indicando si el envio fue exitoso o no


* por que 0x80 no tiene frame_id?
El frame_id solo aparece en tramas que forman parte del mecanismo de petición-respuesta -> Tx request y Tx status
En la practica es bueno colocar el frame_id en el payload de 0x80 pero en el paquete formalmente por definicion no existe, el xbee no lo genera ni lo espera
    
----------------------------

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

"""
APP_HEADER_FMT  = ">HBB"
# >  = big-endian (byte más significativo primero)
# H  = unsigned short (2 bytes) → total_chunks
# B  = unsigned char  (1 byte)  → chunk_idx
# B  = unsigned char  (1 byte)  → image_id
     * unsigned -> sin signo -> 0 a 255 para B (01 byte), 0 a 65535 (02 bytes) para H


APP_HEADER_SIZE = struct.calcsize(">HBB")  # 2 + 1 + 1 = 4 bytes
"""

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
# en realidad solo valida o descarta un paquete, al final lo devuelve tal cual ingresa 
# Solo el paquete, aun no vemos parametors del frame data
def read_frame(ser, timeout_s: float = 2.0) -> bytes | None:
    """
    Read one complete API frame from the serial port.

    Returns the raw frame bytes (including 0x7E, length, data, checksum)
    or None on timeout / incomplete read.
    """
    # ser es el objeto serial
    # Guardar el timeout original del serial, establecer el nuevo timeout
    # el timeout es el tiempo que tiene para que lleguen datos
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
        if len(length_bytes) < 2: # paquete incompleto
            return None

        # longitud de frame data sin el checksum
        length = struct.unpack(">H", length_bytes)[0]
        remaining = ser.read(length + 1)   # frame_data + checksum
        if len(remaining) < length + 1: # paquete incompleto
            return None

        frame_data = remaining[:length]
        cs = remaining[length]

        # Verify checksum
        if (sum(frame_data) + cs) & 0xFF != 0xFF: # checksum inválido, paquete corrupto
            return None

        return b"\x7E" + length_bytes + remaining
    finally:
        ser.timeout = saved_timeout


# ── Parse frames ──────────────────────────────────────────────────────
# frame: es el paquete completo
# Aqui si validamos el tipo de data frame, aqui en 0x89 -> TX Status
def parse_tx_status(frame: bytes) -> TxStatus | None:
    """Parse a TX Status frame (0x89).  Returns TxStatus or None."""
    if len(frame) < 7 or frame[0] != 0x7E:  # por que TX tiene minimo 7 bytes
        return None
    if frame[3] != FRAME_TX_STATUS: # no es un frame de tipo TX Status
        return None
    return TxStatus(frame_id=frame[4], status=frame[5])

# Aqui si validamos el tipo de data frame, aqui en 0x80 -> RX Packet 64-bit
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
# Enviar un chunk con cabecera de aplicación (image_id, chunk_idx, total_chunks) + datos del chunk
# Paqute -> Frame data -> datos (payload) 
# este chunck es el payload
def build_chunk_payload(image_id: int, chunk_idx: int, total_chunks: int,
                        data: bytes) -> bytes:
    """Pack the 4-byte application header + chunk data."""
    header = struct.pack(APP_HEADER_FMT, image_id, chunk_idx, total_chunks)
    return header + data

# desempaquetar la cabecera de aplicación, devuelve image_id, chunk_idx, total_chunks y los datos del chunk
def parse_chunk_payload(payload: bytes):
    """Unpack application header.  Returns (image_id, chunk_idx, total_chunks, data)."""
    if len(payload) < APP_HEADER_SIZE:
        return None
    image_id, chunk_idx, total_chunks = struct.unpack(
        APP_HEADER_FMT, payload[:APP_HEADER_SIZE]
    )
    data = payload[APP_HEADER_SIZE:]
    return image_id, chunk_idx, total_chunks, data
