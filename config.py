import os
from dotenv import load_dotenv
from sqlalchemy.engine import URL

from config_store import banco_configurado, carregar_configuracao, schema_valido

load_dotenv()

# Configuração gravada pela tela de Configurações (tem prioridade sobre o .env).
_banco_salvo = carregar_configuracao()["banco_notificacoes"]
if not banco_configurado(_banco_salvo):
    _banco_salvo = {}

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Configurações gerais da aplicação."""

    # Chave secreta usada pelo Flask para sessões / CSRF
    SECRET_KEY = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")

    # --- Configuração do banco PostgreSQL ---
    # Ordem de prioridade: DATABASE_URL > tela de Configurações
    # (instance/configuracao_sistema.json) > variáveis individuais do .env.
    DB_USER = _banco_salvo.get("user") or os.environ.get("DB_USER", "teste")
    # Sem senha padrão no código: ela vem do .env ou da tela de Configurações.
    DB_PASSWORD = _banco_salvo.get("password") if _banco_salvo else os.environ.get("DB_PASSWORD", "")
    DB_HOST = _banco_salvo.get("host") or os.environ.get("DB_HOST", "localhost")
    DB_PORT = str(_banco_salvo.get("port") or os.environ.get("DB_PORT", "5432"))
    DB_NAME = _banco_salvo.get("database") or os.environ.get("DB_NAME", "postgres")
    DB_SCHEMA = _banco_salvo.get("schema") or os.environ.get("DB_SCHEMA", "public")
    if not schema_valido(DB_SCHEMA):
        DB_SCHEMA = "public"

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        SQLALCHEMY_DATABASE_URI = database_url
    else:
        # URL.create garante encoding correto de usuário/senha com caracteres especiais.
        SQLALCHEMY_DATABASE_URI = URL.create(
            drivername="postgresql+psycopg2",
            username=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=int(DB_PORT) if DB_PORT else None,
            database=DB_NAME,
        ).render_as_string(hide_password=False)

    # Grava as tabelas do sistema no schema configurado.
    if SQLALCHEMY_DATABASE_URI.startswith("postgresql"):
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"options": f"-csearch_path={DB_SCHEMA}"}}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Credenciais do usuário administrador interno criado automaticamente
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
