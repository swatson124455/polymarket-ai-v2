"""
Secret Manager (P6-02).

Encrypted local vault for sensitive configuration (private keys, API keys).
Uses Fernet symmetric encryption from cryptography package.
Falls back to .env files when vault doesn't exist or package missing.

Usage:
    sm = SecretManager()
    sm.init("my-vault-password")
    sm.set("PRIVATE_KEY", "0xabc...")
    key = sm.get("PRIVATE_KEY")
"""
import os
import json
from typing import Optional, Dict
from pathlib import Path
from structlog import get_logger

logger = get_logger()

VAULT_FILE = ".secrets.vault"


class SecretManager:
    """Encrypted local secret vault with .env fallback."""

    def __init__(self, vault_path: Optional[str] = None):
        self._vault_path = Path(vault_path or VAULT_FILE)
        self._fernet = None
        self._secrets: Dict[str, str] = {}
        self._initialized = False

    def init(self, password: Optional[str] = None) -> bool:
        """
        Initialize vault. If vault file exists, decrypt and load.
        If not, create empty vault.
        Password is derived via PBKDF2 from the provided string.
        Falls back to env vars when cryptography not installed.
        """
        if not password:
            password = os.getenv("VAULT_PASSWORD", "")
        if not password:
            logger.info("SecretManager: no VAULT_PASSWORD, using .env fallback only")
            return False

        try:
            from cryptography.fernet import Fernet
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
            import base64

            salt = b"polymarket-ai-v2-salt"  # Fixed salt (acceptable for local vault)
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=480000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
            self._fernet = Fernet(key)

            if self._vault_path.exists():
                encrypted = self._vault_path.read_bytes()
                decrypted = self._fernet.decrypt(encrypted)
                self._secrets = json.loads(decrypted.decode())
                logger.info("Secret vault loaded: %d secrets", len(self._secrets))
            else:
                self._secrets = {}
                self._save()
                logger.info("Secret vault created at %s", self._vault_path)

            self._initialized = True
            return True

        except ImportError:
            logger.info("cryptography package not installed, using .env fallback")
            return False
        except Exception as e:
            logger.warning("Secret vault init failed: %s", e)
            return False

    def get(self, key: str, default: str = "") -> str:
        """Get a secret. Checks vault first, then env vars, then default."""
        if self._initialized and key in self._secrets:
            return self._secrets[key]
        return os.getenv(key, default)

    def set(self, key: str, value: str) -> None:
        """Store a secret in the encrypted vault."""
        self._secrets[key] = value
        if self._initialized:
            self._save()

    def _save(self) -> None:
        """Encrypt and save vault to disk."""
        if not self._fernet:
            return
        try:
            data = json.dumps(self._secrets).encode()
            encrypted = self._fernet.encrypt(data)
            self._vault_path.write_bytes(encrypted)
        except Exception as e:
            logger.warning("Failed to save vault: %s", e)

    def list_keys(self) -> list:
        """List all secret keys (not values)."""
        return list(self._secrets.keys())
