from __future__ import annotations

import os
import struct

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gsafe.errors import GSafeError

MAGIC = b"GSAFEv1\0"
VERSION = 1
SALT_LENGTH = 16
NONCE_LENGTH = 12
KEY_LENGTH = 32
HEADER = struct.Struct(">8sB16s12s")
AAD = MAGIC + bytes([VERSION])
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 64 * 1024
ARGON2_PARALLELISM = 4


def derive_key(password: str, salt: bytes) -> bytes:
    if not password:
        raise GSafeError("Password must not be empty.")
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_COST,
        parallelism=ARGON2_PARALLELISM,
        hash_len=KEY_LENGTH,
        type=Type.ID,
    )


def encrypt_bytes(plaintext: bytes, password: str) -> bytes:
    salt = os.urandom(SALT_LENGTH)
    nonce = os.urandom(NONCE_LENGTH)
    key = derive_key(password, salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, AAD)
    return HEADER.pack(MAGIC, VERSION, salt, nonce) + ciphertext


def decrypt_bytes(blob: bytes, password: str) -> bytes:
    minimum_length = HEADER.size + 16
    if len(blob) < minimum_length:
        raise GSafeError("Container is too small to be valid.")
    try:
        magic, version, salt, nonce = HEADER.unpack(blob[: HEADER.size])
    except struct.error as error:
        raise GSafeError("Container header is invalid.") from error
    if magic != MAGIC:
        raise GSafeError("Container magic is invalid.")
    if version != VERSION:
        raise GSafeError(f"Unsupported container version: {version}.")
    try:
        return AESGCM(derive_key(password, salt)).decrypt(
            nonce,
            blob[HEADER.size :],
            AAD,
        )
    except InvalidTag as error:
        raise GSafeError("Wrong password or corrupted container.") from error
