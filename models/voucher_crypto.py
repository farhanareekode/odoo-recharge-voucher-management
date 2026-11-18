# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import logging
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_logger = logging.getLogger(__name__)

# Default development fallback keys (ONLY for PoC / Local Development)
# WARNING: NEVER use default keys in production! Production must inject keys via
# environment variables (ODOO_VOUCHER_ENCRYPTION_KEY) or a secure KMS key vault.
DEFAULT_DEV_AES_KEY_HEX = "a8f3b2c1d4e5f60718293a4b5c6d7e8f0123456789abcdef0123456789abcdef"
DEFAULT_DEV_HMAC_KEY_HEX = "9876543210fedcba9876543210fedcba0123456789abcdef0123456789abcdef"


class VoucherCryptoService:
    """
    Cryptographic Service for Recharge Voucher PIN Management.
    Implements:
      1. AES-256-GCM authenticated encryption for PIN storage.
      2. Keyed HMAC-SHA256 fingerprinting for duplicate PIN detection without decrypting.
      3. Key management separated from the database (Environment variables / KMS / Keyfile).
      4. Safe PIN masking utilities.
    """

    @classmethod
    def _get_encryption_key(cls) -> bytes:
        """
        Retrieves the 256-bit (32 bytes) master encryption key.
        Priority:
          1. Environment variable: ODOO_VOUCHER_ENCRYPTION_KEY (hex or base64)
          2. Key file: path specified in ODOO_VOUCHER_KEY_FILE
          3. Development fallback key (logs security warning)
        """
        env_key = os.environ.get("ODOO_VOUCHER_ENCRYPTION_KEY")
        if env_key:
            env_key = env_key.strip()
            try:
                # Try hex decode first
                if len(env_key) == 64:
                    return bytes.fromhex(env_key)
                # Try base64 decode
                decoded = base64.b64decode(env_key)
                if len(decoded) == 32:
                    return decoded
            except Exception as e:
                _logger.error("Failed to parse ODOO_VOUCHER_ENCRYPTION_KEY: %s", e)

        key_file = os.environ.get("ODOO_VOUCHER_KEY_FILE")
        if key_file and os.path.isfile(key_file):
            try:
                with open(key_file, "rb") as f:
                    file_key = f.read().strip()
                if len(file_key) == 32:
                    return file_key
                elif len(file_key) == 64:
                    return bytes.fromhex(file_key.decode("ascii"))
            except Exception as e:
                _logger.error("Failed to read ODOO_VOUCHER_KEY_FILE: %s", e)

        _logger.warning(
            "CRITICAL SECURITY NOTICE: Using development fallback encryption key. "
            "Do NOT deploy to production without setting ODOO_VOUCHER_ENCRYPTION_KEY!"
        )
        return bytes.fromhex(DEFAULT_DEV_AES_KEY_HEX)

    @classmethod
    def _get_hmac_key(cls) -> bytes:
        """
        Retrieves the HMAC secret key for PIN fingerprinting.
        Priority:
          1. Environment variable: ODOO_VOUCHER_HMAC_KEY
          2. Development fallback key
        """
        env_key = os.environ.get("ODOO_VOUCHER_HMAC_KEY")
        if env_key:
            env_key = env_key.strip()
            try:
                if len(env_key) == 64:
                    return bytes.fromhex(env_key)
                decoded = base64.b64decode(env_key)
                if len(decoded) == 32:
                    return decoded
            except Exception as e:
                _logger.error("Failed to parse ODOO_VOUCHER_HMAC_KEY: %s", e)

        return bytes.fromhex(DEFAULT_DEV_HMAC_KEY_HEX)

    @classmethod
    def encrypt_pin(cls, plaintext_pin: str) -> str:
        """
        Encrypts a plaintext PIN using AES-256-GCM.
        Generates a fresh 12-byte random nonce per encryption.
        Returns base64-encoded string containing (nonce + ciphertext + tag).
        """
        if not plaintext_pin:
            return ""
        key = cls._get_encryption_key()
        aesgcm = AESGCM(key)
        # 96-bit (12 bytes) standard GCM nonce
        nonce = os.urandom(12)
        plaintext_bytes = plaintext_pin.encode("utf-8")
        # AESGCM.encrypt appends 16-byte authentication tag to ciphertext
        ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext_bytes, None)
        payload = nonce + ciphertext_and_tag
        return base64.b64encode(payload).decode("ascii")

    @classmethod
    def decrypt_pin(cls, encrypted_pin_b64: str) -> str:
        """
        Decrypts an AES-256-GCM encrypted PIN.
        Verifies the GCM authentication tag before returning the plaintext.
        Raises ValueError if ciphertext is malformed or corrupted / tampered.
        """
        if not encrypted_pin_b64:
            return ""
        key = cls._get_encryption_key()
        aesgcm = AESGCM(key)
        try:
            payload = base64.b64decode(encrypted_pin_b64.encode("ascii"))
            if len(payload) < 28:  # 12 nonce + 16 auth tag minimum
                raise ValueError("Payload too short for AES-256-GCM")
            nonce = payload[:12]
            ciphertext_and_tag = payload[12:]
            decrypted_bytes = aesgcm.decrypt(nonce, ciphertext_and_tag, None)
            return decrypted_bytes.decode("utf-8")
        except Exception as e:
            _logger.error("Decryption failed or PIN tampering detected: %s", e)
            raise ValueError("Unable to decrypt voucher PIN: invalid ciphertext or key")

    @classmethod
    def compute_pin_fingerprint(cls, plaintext_pin: str) -> str:
        """
        Computes a keyed HMAC-SHA256 digest of the plaintext PIN.
        This enables collision-free duplicate detection in the database via an
        indexed UNIQUE column without storing plaintext or deterministic encryption.
        """
        if not plaintext_pin:
            return ""
        hmac_key = cls._get_hmac_key()
        # Normalise pin by stripping leading/trailing whitespace
        pin_clean = plaintext_pin.strip().encode("utf-8")
        digest = hmac.new(hmac_key, pin_clean, hashlib.sha256).hexdigest()
        return digest

    @classmethod
    def mask_pin(cls, plaintext_pin: str) -> str:
        """
        Masks a PIN for safe UI display and receipt previews:
        - If len <= 4: returns '****'
        - If len > 4: returns '*' * (len - 4) + last 4 chars
        """
        if not plaintext_pin:
            return ""
        pin_str = str(plaintext_pin).strip()
        if len(pin_str) <= 4:
            return "*" * len(pin_str)
        return "*" * (len(pin_str) - 4) + pin_str[-4:]

