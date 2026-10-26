"""Conexão somente leitura com os bancos externos (pacientes e materiais).

O sistema nunca grava nesses bancos; as falhas viram exceções com mensagem pronta
para exibir ao usuário.
"""

import psycopg2

from config_store import banco_configurado, schema_valido


class ErroBancoExterno(Exception):
    """Falha ao consultar um banco externo, com mensagem pronta para o usuário."""


def conectar_somente_leitura(dados, descricao, classe_erro=ErroBancoExterno):
    """Abre a conexão com o banco externo. `descricao` entra nas mensagens (ex.: 'pacientes')."""
    if not banco_configurado(dados):
        raise classe_erro(f"O banco de {descricao} não está configurado.")
    if not schema_valido(dados.get("schema") or "public"):
        raise classe_erro(f"Schema do banco de {descricao} inválido.")
    try:
        conexao = psycopg2.connect(
            host=dados["host"],
            port=int(dados.get("port") or 5432),
            dbname=dados["database"],
            user=dados["user"],
            password=dados.get("password") or "",
            connect_timeout=5,
        )
    except UnicodeDecodeError as exc:
        # Servidores com mensagens em português quebram a decodificação do erro no psycopg2.
        raise classe_erro(
            f"Falha na conexão com o banco de {descricao}: usuário ou senha inválidos, ou banco inexistente."
        ) from exc
    except (psycopg2.Error, ValueError) as exc:
        detalhe = str(exc).strip().splitlines()
        raise classe_erro(
            f"Falha na conexão com o banco de {descricao}: {detalhe[0] if detalhe else exc.__class__.__name__}"
        ) from exc
    conexao.set_session(readonly=True, autocommit=True)
    return conexao


def consultar(dados, descricao, tabela, consulta, parametros=None, classe_erro=ErroBancoExterno):
    """Executa a consulta no banco externo e retorna todas as linhas; `tabela` entra na mensagem de erro."""
    conexao = conectar_somente_leitura(dados, descricao, classe_erro)
    try:
        with conexao.cursor() as cursor:
            cursor.execute(consulta, parametros)
            return cursor.fetchall()
    except psycopg2.Error as exc:
        detalhe = str(exc).strip().splitlines()
        raise classe_erro(
            f"Falha ao consultar {tabela}: {detalhe[0] if detalhe else exc.__class__.__name__}"
        ) from exc
    finally:
        conexao.close()
