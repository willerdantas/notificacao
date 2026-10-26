"""Consultas ao banco de materiais (configurado na tela de Configurações).

O produto é localizado pelo lote do fornecedor (lote.lotefornecedor):
    lote.produto      -> produto.nome, produto.codigoreferencia (cód. SEPLAG)
    lote.fornecedor   -> fornecedor.pessoa -> pessoa.nome (pessoajuridica.nomefantasia se vazio)
    lote.validade
O mesmo número de lote pode existir para produtos, fornecedores ou validades
diferentes; por isso a busca devolve uma lista para o usuário escolher.
Todas as conexões são somente leitura: o sistema nunca grava nesse banco.
"""

from psycopg2 import sql

from banco_externo import ErroBancoExterno, consultar
from config_store import carregar_configuracao

LIMITE_RESULTADOS_LOTE = 20
TAMANHO_MAXIMO_LOTE_ESTOQUE = 20  # lote.lotefornecedor

_SELECT_LOTE = """
    SELECT l.lote, TRIM(l.lotefornecedor), TRIM(p.nome),
           COALESCE(NULLIF(TRIM(pe.nome), ''), TRIM(pj.nomefantasia)), l.validade, TRIM(p.codigoreferencia)
    FROM {schema}.lote l
    JOIN {schema}.produto p ON p.produto = l.produto
    LEFT JOIN {schema}.fornecedor f ON f.fornecedor = l.fornecedor
    LEFT JOIN {schema}.pessoa pe ON pe.pessoa = f.pessoa
    LEFT JOIN {schema}.pessoajuridica pj ON pj.pessoa = pe.pessoa
"""


class ErroBancoMateriais(ErroBancoExterno):
    """Falha ao consultar o banco de materiais, com mensagem pronta para o usuário."""


def normalizar_lote(valor):
    """Lote como digitado, sem espaços nas pontas; None se vazio ou maior que o campo do formulário."""
    valor = (valor or "").strip()
    if not valor or len(valor) > 100:
        return None
    return valor


def _consulta(dados, filtro):
    return sql.SQL(_SELECT_LOTE + filtro).format(schema=sql.Identifier(dados.get("schema") or "public"))


def _como_dict(linha):
    id_lote, lote, produto, fornecedor, validade, cod_seplag = linha
    return {
        "id": int(id_lote),
        "lote": lote or "",
        "produto": produto or "",
        "fornecedor": fornecedor or "",
        "validade": validade.date() if hasattr(validade, "date") else validade,
        "cod_seplag": cod_seplag or "",
    }


def buscar_lotes(lote, dados=None):
    """Produtos com o lote informado (sem diferenciar maiúsculas e espaços), validade mais recente primeiro.

    Retorna (resultados, limitado): no máximo LIMITE_RESULTADOS_LOTE itens; `limitado`
    indica que havia mais registros do que os devolvidos.
    """
    lote = normalizar_lote(lote)
    if lote is None or len(lote) > TAMANHO_MAXIMO_LOTE_ESTOQUE:
        return [], False
    dados = dados or carregar_configuracao()["banco_materiais"]
    consulta = _consulta(
        dados,
        "WHERE UPPER(TRIM(l.lotefornecedor)) = UPPER(%s) "
        "ORDER BY l.validade DESC NULLS LAST, p.nome LIMIT %s",
    )
    linhas = consultar(dados, "materiais", "lote", consulta, (lote, LIMITE_RESULTADOS_LOTE + 1), ErroBancoMateriais)
    resultados = [_como_dict(linha) for linha in linhas]
    return resultados[:LIMITE_RESULTADOS_LOTE], len(resultados) > LIMITE_RESULTADOS_LOTE


LIMITE_RESULTADOS_PRODUTO = 15
TAMANHO_MINIMO_BUSCA_PRODUTO = 3

# Compara sem acentos e sem diferenciar maiúsculas (os nomes do estoque são quase todos
# em maiúsculas sem acento, mas há exceções).
_SEM_ACENTO = "translate(upper({}), 'ÁÀÂÃÉÊÍÓÔÕÚÇ', 'AAAAEEIOOOUC')"


def buscar_produtos_por_nome(termo, dados=None):
    """Produtos não bloqueados cujo nome contém o termo (autocompletar do formulário).

    Retorna [{"id", "nome", "cod_seplag"}], sem repetir nome + cód. SEPLAG iguais.
    """
    termo = (termo or "").strip()
    if len(termo) < TAMANHO_MINIMO_BUSCA_PRODUTO:
        return []
    dados = dados or carregar_configuracao()["banco_materiais"]
    consulta = sql.SQL(
        "SELECT DISTINCT ON (TRIM(p.nome), TRIM(p.codigoreferencia)) p.produto, TRIM(p.nome), TRIM(p.codigoreferencia) "
        "FROM {schema}.produto p "
        "WHERE COALESCE(p._block, '') <> 'S' "
        "  AND " + _SEM_ACENTO.format("p.nome") + " LIKE '%%' || " + _SEM_ACENTO.format("%s") + " || '%%' "
        "ORDER BY TRIM(p.nome), TRIM(p.codigoreferencia) NULLS LAST, p.produto "
        "LIMIT %s"
    ).format(schema=sql.Identifier(dados.get("schema") or "public"))
    linhas = consultar(dados, "materiais", "produto", consulta, (termo, LIMITE_RESULTADOS_PRODUTO), ErroBancoMateriais)
    return [{"id": int(i), "nome": nome or "", "cod_seplag": cod or ""} for i, nome, cod in linhas]


def buscar_produto_por_id(id_produto, dados=None):
    """Produto pela chave produto.produto (escolhido no autocompletar), ou None."""
    dados = dados or carregar_configuracao()["banco_materiais"]
    consulta = sql.SQL(
        "SELECT p.produto, TRIM(p.nome), TRIM(p.codigoreferencia) FROM {schema}.produto p WHERE p.produto = %s"
    ).format(schema=sql.Identifier(dados.get("schema") or "public"))
    linhas = consultar(dados, "materiais", "produto", consulta, (id_produto,), ErroBancoMateriais)
    if not linhas:
        return None
    i, nome, cod = linhas[0]
    return {"id": int(i), "nome": nome or "", "cod_seplag": cod or ""}


def buscar_lote_por_id(id_lote, dados=None):
    """Registro do lote pela chave lote.lote (escolhido no formulário), ou None."""
    dados = dados or carregar_configuracao()["banco_materiais"]
    consulta = _consulta(dados, "WHERE l.lote = %s")
    linhas = consultar(dados, "materiais", "lote", consulta, (id_lote,), ErroBancoMateriais)
    return _como_dict(linhas[0]) if linhas else None
