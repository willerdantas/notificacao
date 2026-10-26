"""Configurações do sistema gravadas em arquivo (instance/configuracao_sistema.json).

A conexão do banco de notificações não pode ficar dentro do próprio banco,
por isso todas as configurações editáveis pela tela de Configurações ficam
neste arquivo, fora da pasta static (não é servido pela web).
"""

import copy
import json
import os
import re

import psycopg2

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONFIG_PATH = os.environ.get(
    "CONFIG_SISTEMA_PATH",
    os.path.join(BASE_DIR, "instance", "configuracao_sistema.json"),
)

LOGO_PRINCIPAL_PADRAO = "LOGO HM E SESA - NOVA (1).png"
LOGO_TRANSICAO_PADRAO = "LOGO HM - NOVA (1).png"

CAMPOS_BANCO = ("host", "port", "database", "user", "password", "schema")

_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

DEFAULTS = {
    "banco_notificacoes": {},
    "banco_pacientes": {
        "host": "",
        "port": "5432",
        "database": "",
        "user": "",
        "password": "",
        "schema": "public",
    },
    "banco_materiais": {
        "host": "",
        "port": "5432",
        "database": "",
        "user": "",
        "password": "",
        "schema": "public",
    },
    "logos": {
        "principal": LOGO_PRINCIPAL_PADRAO,
        "transicao": LOGO_TRANSICAO_PADRAO,
    },
}


def carregar_configuracao():
    """Retorna a configuração gravada, completada com os valores padrão."""
    config = copy.deepcopy(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as arquivo:
            gravada = json.load(arquivo)
    except (FileNotFoundError, json.JSONDecodeError):
        return config

    for secao, valores in gravada.items():
        if isinstance(valores, dict) and secao in config:
            config[secao].update(valores)
    return config


def salvar_configuracao(config):
    """Grava a configuração de forma atômica (arquivo temporário + replace)."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    temporario = CONFIG_PATH + ".tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        json.dump(config, arquivo, ensure_ascii=False, indent=2)
    os.replace(temporario, CONFIG_PATH)


def schema_valido(schema):
    return bool(_SCHEMA_RE.match(schema or ""))


def banco_configurado(dados):
    return bool(dados and dados.get("host") and dados.get("database") and dados.get("user"))


TESTE_OK = "ok"
TESTE_AVISO = "aviso"
TESTE_ERRO = "erro"


def testar_conexao(dados):
    """Testa a conexão PostgreSQL e a existência do schema.

    Retorna (status, mensagem), com status TESTE_OK, TESTE_AVISO (conectou, mas o
    schema não existe) ou TESTE_ERRO.
    """
    if not banco_configurado(dados):
        return TESTE_ERRO, "Preencha endereço, banco e usuário."

    schema = dados.get("schema") or "public"
    if not schema_valido(schema):
        return TESTE_ERRO, "Schema inválido. Use letras, números e _ (começando por letra ou _)."

    try:
        conexao = psycopg2.connect(
            host=dados["host"],
            port=int(dados.get("port") or 5432),
            dbname=dados["database"],
            user=dados["user"],
            password=dados.get("password") or "",
            connect_timeout=5,
        )
    except UnicodeDecodeError:
        # Servidores com mensagens em português (LATIN1/WIN1252) quebram a decodificação
        # do erro de autenticação no psycopg2.
        return TESTE_ERRO, "Falha na conexão: usuário ou senha inválidos, ou banco de dados inexistente."
    except (psycopg2.Error, ValueError) as exc:
        detalhe = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        return TESTE_ERRO, f"Falha na conexão: {detalhe}"

    try:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT version()")
            versao = cursor.fetchone()[0].split(",")[0]
            cursor.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                (schema,),
            )
            schema_existe = cursor.fetchone() is not None
    finally:
        conexao.close()

    if not schema_existe:
        return TESTE_AVISO, f"Conexão OK ({versao}), mas o schema '{schema}' não existe."
    return TESTE_OK, f"Conexão OK ({versao}). Schema '{schema}' encontrado."
