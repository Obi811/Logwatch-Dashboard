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
        # Erlaubte CORS-Origins für die App-/REST-API (komma-separiert).
        # Default "*" erlaubt am Anfang jeden Ursprung; später eng setzen
        # (z.B. "https://app.example.com,https://admin.example.com").
        raw_origins = os.environ.get("API_CORS_ORIGINS", "*").strip()
        self.api_cors_origins = (
            ["*"] if raw_origins in ("", "*")
            else [o.strip() for o in raw_origins.split(",") if o.strip()]
        )
        # Optionales GitHub-Token für die Update-Prüfung (nötig nur bei privatem Repo).
        self.github_pat = os.environ.get("GITHUB_PAT", "").strip()
        # Update-Hinweis (Variante 1) – standardmäßig aktiv; in Tests/Offline abschaltbar.
        self.update_check_enabled = (
            os.environ.get("UPDATE_CHECK_ENABLED", "1").strip().lower()
            not in ("0", "false", "no", "off", "")
        )
        # SMTP-Fallback aus der Umgebung. Die primäre Quelle ist die DB-Tabelle
        # `smtp_settings` (über die Admin-UI pflegbar); ist dort nichts hinterlegt,
        # greift diese Env-Konfiguration. Das Passwort steht hier IM KLARTEXT in der
        # .env (Datei-Rechte beachten) – in der DB wird es verschlüsselt abgelegt.
        self.smtp_host = os.environ.get("SMTP_HOST", "").strip()
        try:
            self.smtp_port = int(os.environ.get("SMTP_PORT", "587") or "587")
        except (ValueError, TypeError):
            self.smtp_port = 587
        self.smtp_user = os.environ.get("SMTP_USER", "").strip()
        self.smtp_password = os.environ.get("SMTP_PASSWORD", "")
        self.smtp_from_email = os.environ.get("SMTP_FROM_EMAIL", "").strip()
        self.smtp_from_name = os.environ.get("SMTP_FROM_NAME", "LogWatch Dashboard").strip()
        # Transportverschlüsselung: "starttls" | "ssl" | "none".
        sec = os.environ.get("SMTP_SECURITY", "starttls").strip().lower()
        self.smtp_security = sec if sec in ("starttls", "ssl", "none") else "starttls"

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
