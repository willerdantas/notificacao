"""Consultas ao banco de informações do paciente (configurado na tela de Configurações).

Todas as conexões são somente leitura: o sistema nunca grava nesse banco.
"""

from psycopg2 import sql

from banco_externo import ErroBancoExterno, consultar
from config_store import carregar_configuracao
from utils import cpf_da_base


class ErroBancoPacientes(ErroBancoExterno):
    """Falha ao consultar o banco de pacientes, com mensagem pronta para o usuário."""


def _consultar(dados, tabela, consulta, parametros=None):
    return consultar(dados, "pacientes", tabela, consulta, parametros, ErroBancoPacientes)


def buscar_clinicas_ativas(dados=None):
    """Retorna [(isn_clinica, dsc_clinica)] das clínicas com flg_ativo = 'S' em t_clinica."""
    dados = dados or carregar_configuracao()["banco_pacientes"]
    consulta = sql.SQL(
        "SELECT isn_clinica, dsc_clinica FROM {}.t_clinica "
        "WHERE TRIM(UPPER(flg_ativo)) = 'S' ORDER BY isn_clinica"
    ).format(sql.Identifier(dados.get("schema") or "public"))
    linhas = _consultar(dados, "t_clinica", consulta)
    return [(int(isn), (dsc or "").strip()) for isn, dsc in linhas]


def normalizar_prontuario(valor):
    """Somente dígitos, sem zeros à esquerda (t_paciente não usa zeros à esquerda)."""
    valor = (valor or "").strip()
    if not valor.isdigit() or len(valor) > 20:
        return None
    return valor.lstrip("0") or None


def buscar_paciente(prontuario, dados=None):
    """Dados do paciente na t_paciente pelo num_prontuario, ou None se não existir.

    Retorna {"nome", "data_nascimento" (date ou None), "nome_mae", "cpf"} com textos já
    sem espaços nas pontas; o CPF segue o padrão de utils.cpf_da_base.
    """
    prontuario = normalizar_prontuario(prontuario)
    if prontuario is None:
        return None
    dados = dados or carregar_configuracao()["banco_pacientes"]
    consulta = sql.SQL(
        "SELECT dsc_nome, dat_nascimento, dsc_mae, num_cpf FROM {}.t_paciente WHERE num_prontuario = %s LIMIT 1"
    ).format(sql.Identifier(dados.get("schema") or "public"))
    linhas = _consultar(dados, "t_paciente", consulta, (prontuario,))
    if not linhas:
        return None
    nome, nascimento, nome_mae, cpf = linhas[0]
    nome = (nome or "").strip()
    if not nome:
        return None
    return {
        "nome": nome,
        "data_nascimento": nascimento.date() if hasattr(nascimento, "date") else nascimento,
        "nome_mae": (nome_mae or "").strip() or None,
        "cpf": cpf_da_base(cpf),
    }
