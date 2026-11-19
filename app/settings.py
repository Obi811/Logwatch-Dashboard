"""Zentrale Einstellungen des Dashboards (aus Umgebungsvariablen/.env)."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

# .env aus dem Projektwurzelverzeichnis laden (falls vorhanden).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


class Settings:
    def __init__(self) -> None:
        self.project_root = _PROJECT_ROOT
        self.database_url = os.environ.get("DATABASE_URL", "")
        self.admin_user = os.environ.get("ADMIN_USER", "admin")
        self.admin_password = os.environ.get("ADMIN_PASSWORD", "")
        self.upload_token = os.environ.get("UPLOAD_TOKEN", "")
        self.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-secret-change-me")
        self.public_host = os.environ.get("PUBLIC_HOST", "")
        upload_dir = os.environ.get("UPLOAD_DIR", os.path.join(_PROJECT_ROOT, "data", "uploads"))
        if not os.path.isabs(upload_dir):
            upload_dir = os.path.join(_PROJECT_ROOT, upload_dir)
        self.upload_dir = upload_dir
        os.makedirs(self.upload_dir, exist_ok=True)

    @property
    def sqlalchemy_url(self) -> str:
        """Normalisiert die URL auf den psycopg(v3)-Treiber."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://"):]
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://"):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
