"""BitTorrent Peer Wire Protocol.

Handles TCP connections to peers, protocol handshake,
and message encoding/decoding for the BitTorrent protocol.

Message types (after handshake):
  - keep-alive: no length prefix, just 0-length message
  - choke: <len=0001><id=0>
  - unchoke: <len=0001><id=1>
  - interested: <len=0001><id=2>
  - not interested: <len=0001><id=3>
  - have: <len=0005><id=4><piece_index>
  - bitfield: <len=0001+X><id=5><bitfield>
  - request: <len=000D><id=6><index><begin><length>
  - piece: <len=0009+X><id=7><index><begin><block>
  - cancel: <len=000D><id=8><index><begin><length>
"""

import asyncio
import logging
import random
import struct
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)

# Protocol constants
PROTOCOL_STRING = b"BitTorrent protocol"
PROTOCOL_LEN = 19  # single byte prefix for protocol string length
HANDSHAKE_LEN = 68  # 1 + 19 + 8 + 20 + 20

# Message IDs
MSG_CHOKE = 0
MSG_UNCHOKE = 1
MSG_INTERESTED = 2
MSG_NOT_INTERESTED = 3
MSG_HAVE = 4
MSG_BITFIELD = 5
MSG_REQUEST = 6
MSG_PIECE = 7
MSG_CANCEL = 8
MSG_PORT = 9  # DHT port (BEP 0005)

# Default block size (2^14 = 16384 bytes)
DEFAULT_BLOCK_SIZE = 2**14


class PeerError(Exception):
    """Peer protocol error."""
    pass


def build_handshake(info_hash: bytes, peer_id: bytes) -> bytes:
    """Build a BitTorrent handshake message.
    
    Format:
      - 1 byte: protocol string length (19)
      - 19 bytes: 'BitTorrent protocol'
      - 8 bytes: reserved (all zeros)
      - 20 bytes: info_hash
      - 20 bytes: peer_id
    """
    reserved = b"\x00" * 8
    return struct.pack(
        f">B{PROTOCOL_LEN}s8s20s20s",
        PROTOCOL_LEN,
        PROTOCOL_STRING,
        reserved,
        info_hash,
        peer_id,
    )


def parse_handshake(data: bytes) -> Optional[dict]:
    """Parse a BitTorrent handshake message.
    
    Returns dict with pstr, reserved, info_hash, peer_id or None if invalid.
    """
    if len(data) < HANDSHAKE_LEN:
        return None
    
    if data[0] != PROTOCOL_LEN:
        return None
    
    offset = 1
    pstr = data[offset:offset+PROTOCOL_LEN]
    offset += PROTOCOL_LEN
    
    if pstr != PROTOCOL_STRING:
        return None
    
    reserved = data[offset:offset+8]
    offset += 8
    info_hash = data[offset:offset+20]
    offset += 20
    peer_id = data[offset:offset+20]
    
    return {
        "pstr": pstr,
        "reserved": reserved,
        "info_hash": info_hash,
        "peer_id": peer_id,
    }


def build_message(msg_id: int, payload: bytes = b"") -> bytes:
    """Build a standard message with 4-byte length prefix and 1-byte ID.
    
    Format: <4 bytes length><1 byte ID><payload>
    """
    length = 1 + len(payload)  # 1 for message ID
    return struct.pack(">I", length) + bytes([msg_id]) + payload


def build_keepalive() -> bytes:
    """Build a keep-alive message (0-length message)."""
    return struct.pack(">I", 0)


def build_request(index: int, begin: int, length: int = DEFAULT_BLOCK_SIZE) -> bytes:
    """Build a request message.
    
    Args:
        index: piece index
        begin: byte offset within piece
        length: number of bytes requested (typically 2^14 = 16384)
    """
    payload = struct.pack(">III", index, begin, length)
    return build_message(MSG_REQUEST, payload)


def build_cancel(index: int, begin: int, length: int = DEFAULT_BLOCK_SIZE) -> bytes:
    """Build a cancel message."""
    payload = struct.pack(">III", index, begin, length)
    return build_message(MSG_CANCEL, payload)


def build_have(piece_index: int) -> bytes:
    """Build a 'have' message."""
    payload = struct.pack(">I", piece_index)
    return build_message(MSG_HAVE, payload)


def build_bitfield(bitfield: bytes) -> bytes:
    """Build a bitfield message."""
    return build_message(MSG_BITFIELD, bitfield)


def parse_message(data: bytes) -> Optional[dict]:
    """Parse a single message from the data stream.
    
    Returns dict with msg_id, payload, and consumed_length,
    or None if incomplete.
    """
    if len(data) < 4:
        return None  # Need length prefix
    
    length = struct.unpack(">I", data[:4])[0]
    
    if length == 0:
        # Keep-alive
        return {
            "msg_id": None,
            "payload": b"",
            "consumed": 4,
        }
    
    if len(data) < 4 + length:
        return None  # Incomplete message
    
    msg_id = data[4]
    payload = data[5:4+length]
    
    return {
        "msg_id": msg_id,
        "payload": payload,
        "consumed": 4 + length,
    }


def parse_have(payload: bytes) -> int:
    """Parse a 'have' message payload, returns piece index."""
    return struct.unpack(">I", payload)[0]


def parse_request(payload: bytes) -> tuple:
    """Parse a 'request' message payload, returns (index, begin, length)."""
    return struct.unpack(">III", payload)


def parse_piece(payload: bytes) -> tuple:
    """Parse a 'piece' message payload, returns (index, begin, block_data)."""
    index = struct.unpack(">I", payload[:4])[0]
    begin = struct.unpack(">I", payload[4:8])[0]
    block = payload[8:]
    return (index, begin, block)


def parse_bitfield(payload: bytes, num_pieces: int) -> list[bool]:
    """Parse bitfield payload into a list of booleans (have/don't have)."""
    result = []
    for byte in payload:
        for bit in range(8):
            result.append(bool(byte & (1 << (7 - bit))))
    return result[:num_pieces]


def build_bitfield_from_pieces(have_pieces: set, num_pieces: int) -> bytes:
    """Build a bitfield bytes from a set of piece indices we have."""
    num_bytes = (num_pieces + 7) // 8
    bitfield = bytearray(num_bytes)
    for idx in have_pieces:
        if idx < num_pieces:
            byte_idx = idx // 8
            bit_idx = idx % 8
            bitfield[byte_idx] |= 1 << (7 - bit_idx)
    return bytes(bitfield)


class PeerConnection:
    """Manages a single TCP connection to a BitTorrent peer."""

    def __init__(
        self,
        ip: str,
        port: int,
        info_hash: bytes,
        peer_id: bytes,
        on_message: Optional[Callable] = None,
        on_disconnect: Optional[Callable] = None,
    ):
        self.ip = ip
        self.port = port
        self.info_hash = info_hash
        self.peer_id = peer_id
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.buffer = b""
        self.am_choking = True
        self.am_interested = False
        self.peer_choking = True
        self.peer_interested = False
        self.handshake_done = False
        self.remote_peer_id: Optional[bytes] = None
        self.bitfield: Optional[list[bool]] = None
        self.piece_hashes: list[bytes] = []
        
    async def connect(self, timeout: float = 30) -> bool:
        """Connect to the peer and perform handshake."""
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port),
                timeout=timeout,
            )
            
            # Send handshake
            handshake = build_handshake(self.info_hash, self.peer_id)
            self.writer.write(handshake)
            await self.writer.drain()
            
            # Read handshake response
            resp = await asyncio.wait_for(
                self.reader.readexactly(HANDSHAKE_LEN),
                timeout=timeout,
            )
            
            parsed = parse_handshake(resp)
            if not parsed:
                raise PeerError("Invalid handshake response")
            
            if parsed["info_hash"] != self.info_hash:
                raise PeerError("Info hash mismatch in handshake response")
            
            self.remote_peer_id = parsed["peer_id"]
            self.handshake_done = True
            
            logger.info(f"Connected to peer {self.ip}:{self.port}, peer_id={self.remote_peer_id[:8].hex()}")
            return True
            
        except (asyncio.TimeoutError, ConnectionRefusedError, ConnectionResetError,
                OSError, PeerError) as e:
            logger.warning(f"Failed to connect to {self.ip}:{self.port}: {e}")
            return False

    async def send_interested(self):
        """Send interested message."""
        msg = build_message(MSG_INTERESTED)
        await self._send(msg)
        self.am_interested = True
        
    async def send_unchoke(self):
        """Send unchoke message."""
        msg = build_message(MSG_UNCHOKE)
        await self._send(msg)
        self.am_choking = False

    async def send_request(self, index: int, begin: int, length: int = DEFAULT_BLOCK_SIZE):
        """Send a request for a block."""
        msg = build_request(index, begin, length)
        await self._send(msg)

    async def send_have(self, piece_index: int):
        """Send have message."""
        msg = build_have(piece_index)
        await self._send(msg)

    async def send_keepalive(self):
        """Send keep-alive."""
        msg = build_keepalive()
        await self._send(msg)

    async def _send(self, data: bytes):
        """Send raw data over the connection."""
        if self.writer:
            self.writer.write(data)
            await self.writer.drain()

    async def read_loop(self):
        """Continuously read and parse messages from the peer."""
        if not self.reader:
            return
        
        try:
            while True:
                data = await self.reader.read(4096)
                if not data:
                    break  # Connection closed
                
                self.buffer += data
                await self._process_buffer()
        except (ConnectionResetError, ConnectionAbortedError, OSError) as e:
            logger.debug(f"Peer {self.ip}:{self.port} disconnected: {e}")
        finally:
            await self.disconnect()

    async def _process_buffer(self):
        """Process buffered data, parsing messages."""
        while True:
            msg = parse_message(self.buffer)
            if msg is None:
                break  # Need more data
            
            self.buffer = self.buffer[msg["consumed"]:]
            
            if msg["msg_id"] is not None and self.on_message:
                await self.on_message(self, msg)

    async def disconnect(self):
        """Close the peer connection."""
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None
        
        if self.on_disconnect:
            await self.on_disconnect(self)

    @property
    def is_connected(self) -> bool:
        return self.writer is not None and not self.writer.is_closing()

    def __str__(self):
        return f"Peer({self.ip}:{self.port}, choking={self.peer_choking}, interested={self.am_interested})"


def parse_compact_peers(data: bytes) -> list[tuple[str, int]]:
    """Parse compact peer list (6 bytes per peer: 4 IP, 2 port)."""
    import socket
    peers = []
    for i in range(0, len(data), 6):
        if i + 6 > len(data):
            break
        ip_raw, port = struct.unpack(">IH", data[i:i+6])
        ip = socket.inet_ntoa(struct.pack(">I", ip_raw))
        peers.append((ip, port))
    return peers
