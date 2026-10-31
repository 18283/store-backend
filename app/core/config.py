"""Configuración de la aplicación, leída desde variables de entorno / archivo .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    project_name: str = "FashionStore API"

    # Base de datos
    database_url: str

    # Seguridad / JWT
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # Usuario administrador inicial (lo crea el seed)
    first_admin_email: str = "admin@fashionstore.com"
    first_admin_password: str = "Admin123!"
    first_admin_nombre: str = "Administrador"
    first_admin_apellido: str = "General"

    # CORS: orígenes del frontend permitidos (separados por coma)
    cors_origins: str = "http://localhost:4200"
    cors_origin_regex: str = (
        r"https://store-frontend(?:-[a-z0-9]+)*-viveros123\.vercel\.app"
    )

    # Stripe (modo test) — CU27/CU28
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    # A dónde vuelve el cliente después de pagar en Stripe (el frontend Angular).
    frontend_url: str = "http://localhost:4200"

    # Inteligencia artificial (Gemini) — CU29/CU30/CU32
    gemini_api_key: str = ""

    # Firebase Cloud Messaging (push) — CU14/CU15, solo resultado de
    # sincronizar reservas/ventas offline. En local se usa el archivo
    # (firebase_credentials_path); en Render conviene pegar el JSON entero
    # en la variable FIREBASE_CREDENTIALS_JSON (no hay disco persistente).
    firebase_credentials_path: str = "firebase-service-account.json"
    firebase_credentials_json: str = ""

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy necesita el driver explícito. Neon entrega 'postgresql://',
        acá lo normalizamos a 'postgresql+psycopg://' (psycopg v3)."""
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
