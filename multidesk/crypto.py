"""Canal criptografado entre servidor e cliente.

Handshake:
  1. Cada lado manda uma chave X25519 efêmera + um nonce aleatório.
  2. A chave de sessão sai de HKDF(segredo X25519, sal = scrypt(código)).
  3. Cada lado prova que sabe o código mandando um HMAC do transcript.
Quem não sabe o código não consegue completar o handshake, e quem só escuta
a rede não consegue ler nada (nem sabendo o código, graças ao X25519).

Depois do handshake cada mensagem é JSON cifrado com AES-GCM, num quadro
[4 bytes de tamanho][texto cifrado]. O nonce é um contador por direção.
"""

import hashlib
import hmac
import json
import os
import socket
import struct
import threading

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .link import normalize_code

MAGIC = b"MDSK1"
MAX_FRAME = 32 * 1024 * 1024  # cabe uma imagem de 16 MB em base64


class AuthError(Exception):
    """O outro lado não tem o mesmo código de pareamento."""


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("conexão fechada")
        buf += chunk
    return bytes(buf)


class SecureChannel:
    def __init__(self, sock, send_key, recv_key):
        self.sock = sock
        self._send = AESGCM(send_key)
        self._recv = AESGCM(recv_key)
        self._send_ctr = 0
        self._recv_ctr = 0
        self._send_lock = threading.Lock()
        self._buf = bytearray()  # bytes já recebidos e ainda não lidos

    def send(self, msg):
        self.send_many((msg,))

    def send_many(self, msgs):
        """Várias mensagens num único sendall (um pacote só na rede)."""
        datas = [json.dumps(m, separators=(",", ":")).encode() for m in msgs]
        with self._send_lock:
            frames = []
            for data in datas:
                nonce = self._send_ctr.to_bytes(12, "big")
                self._send_ctr += 1
                ct = self._send.encrypt(nonce, data, None)
                frames.append(struct.pack(">I", len(ct)) + ct)
            self.sock.sendall(b"".join(frames))

    def _read(self, n):
        # Lê em blocos grandes: várias mensagens chegam numa chamada só, em vez
        # de duas chamadas por mensagem (cada uma disputa a vez no Python).
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("conexão fechada")
            self._buf += chunk
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def recv(self):
        (size,) = struct.unpack(">I", self._read(4))
        if size > MAX_FRAME:
            raise ConnectionError("quadro grande demais")
        ct = self._read(size)
        nonce = self._recv_ctr.to_bytes(12, "big")
        self._recv_ctr += 1
        return json.loads(self._recv.decrypt(nonce, ct, None))

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def handshake(sock, code, is_server):
    eph = X25519PrivateKey.generate()
    my_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    my_nonce = os.urandom(16)
    sock.sendall(MAGIC + my_pub + my_nonce)

    hello = _recv_exact(sock, len(MAGIC) + 32 + 16)
    if hello[:len(MAGIC)] != MAGIC:
        raise ConnectionError("o outro lado não é um MultiDesk compatível")
    peer_pub, peer_nonce = hello[len(MAGIC):len(MAGIC) + 32], hello[len(MAGIC) + 32:]

    if is_server:
        transcript = my_pub + my_nonce + peer_pub + peer_nonce
    else:
        transcript = peer_pub + peer_nonce + my_pub + my_nonce
    th = hashlib.sha256(transcript).digest()

    shared = eph.exchange(X25519PublicKey.from_public_bytes(peer_pub))
    code_key = hashlib.scrypt(normalize_code(code).encode(), salt=th, n=2**14, r=8, p=1, dklen=32)
    okm = HKDF(algorithm=hashes.SHA256(), length=96, salt=code_key, info=b"multidesk v1" + th).derive(shared)
    k_c2s, k_s2c, k_confirm = okm[:32], okm[32:64], okm[64:]

    tag_server = hmac.new(k_confirm, b"server" + th, hashlib.sha256).digest()
    tag_client = hmac.new(k_confirm, b"client" + th, hashlib.sha256).digest()

    # O cliente prova primeiro; o servidor só responde se o código bateu.
    if is_server:
        if not hmac.compare_digest(_recv_exact(sock, 32), tag_client):
            raise AuthError("código de pareamento errado")
        sock.sendall(tag_server)
        return SecureChannel(sock, k_s2c, k_c2s)
    sock.sendall(tag_client)
    try:
        ok = hmac.compare_digest(_recv_exact(sock, 32), tag_server)
    except ConnectionError:
        ok = False
    if not ok:
        raise AuthError("código de pareamento errado")
    return SecureChannel(sock, k_c2s, k_s2c)
