import os
import threading
import time
from uuid import uuid4
from datetime import datetime
from collections import Counter, defaultdict, deque

from flask import Blueprint, current_app, jsonify, render_template, request, redirect, session, url_for, flash
from flask_login import login_required, current_user, logout_user
from PIL import Image, ImageOps

from config_store import (
    CAMPOS_BANCO,
    LOGO_PRINCIPAL_PADRAO,
    LOGO_TRANSICAO_PADRAO,
    TESTE_AVISO,
    banco_configurado,
    carregar_configuracao,
    salvar_configuracao,
    schema_valido,
    testar_conexao,
)
from banco_materiais import (
    ErroBancoMateriais,
    buscar_lote_por_id,
    buscar_lotes,
    buscar_produto_por_id,
    buscar_produtos_por_nome,
    normalizar_lote,
)
from banco_pacientes import ErroBancoPacientes, buscar_paciente, normalizar_prontuario
from database_setup import (
    chave_conexao,
    preparar_banco_notificacoes,
    sincronizar_conexao_notificacoes,
    sincronizar_setores_t_clinica,
)
from extensions import db
from models import (
    User,
    Notification,
    Setor,
    CategoriaProfissional,
    TipoOcorrencia,
    ResponsavelNotificacao,
    Perfil,
    TELAS,
    ORIGEM_SETOR_CLINICA,
    ORIGEM_SETOR_MANUAL,
    ID_INICIAL_SETOR_MANUAL,
    SECAO_BIOVIGILANCIA,
    SECAO_HEMOTRANSFUSAO,
    SECAO_MEDICACAO,
    SECAO_PRODUTO,
    SECAO_SANEANTES,
    SECOES_ADICIONAIS,
)
from utils import (
    calcular_idade,
    formatar_cpf,
    mascarar_cpf,
    normalizar_cpf_digitado,
    role_required,
    sanitize_email,
    sanitize_phone,
    sanitize_text,
)

main_bp = Blueprint("main", __name__)

STATUS_OPTIONS = ["Pendente", "Em Análise", "Resolvida"]
GRAVIDADE_OPTIONS = ["Baixa", "Média", "Alta"]
STATUS_ANVISA_OPTIONS = ["Não Notificado", "Notificado"]
GRAU_DANO_OPTIONS = [
    ("Leve", "Leve (sintomas leves, danos mínimos, pequeno tratamento ou observação)"),
    ("Moderado", "Moderado (precisou de terapêutica adicional, prolongamento da internação, perda de função, danos permanentes)"),
    ("Grave", "Grave (intervenção para salvar a vida, grandes danos permanentes)"),
    ("Óbito", "Óbito"),
    ("Não houve dano ao paciente", "Não houve dano ao paciente"),
]
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
GRAU_SEM_DANO = "Não houve dano ao paciente"
MED_INCIDENTE_OPTIONS = [
    "Reação adversa a medicamento",
    "Desvio de qualidade de medicamento",
    "Ineficácia terapêutica",
    "Abuso do uso de medicamentos",
    'Uso "off-label" (Uso sem registro)',
    "Superdose",
]
BIO_TIPO_TRANSPLANTE_OPTIONS = ["Coração", "Pulmão", "Válvula"]
BIO_TIPO_REACAO_OPTIONS = [
    "Infecção",
    "Enxerto nunca funcionante",
    "Complicações peri-operatórias",
    "Outras",
]
HEMO_TIPO_EVENTO_OPTIONS = [
    "Reação transfusional",
    "Quase-erro grave (evento detectado antes da transfusão/doação)",
    "Incidente grave sem reação (evento detectado durante ou após a transfusão/doação, que não provocou reação)",
]
HEMOCOMPONENTE_OPTIONS = ["CH", "PLASMA", "CRIO", "Plaquetas"]
# Perguntas sim/não da seção de hemotransfusão: (campo da notificação, pergunta).
HEMO_PERGUNTAS_SIM_NAO = [
    ("hemo_alteracao_sinais_vitais", "Houve alteração dos sinais vitais durante ou após a transfusão?"),
    ("hemo_manifestacoes_clinicas", "Ocorreram manifestações clínicas e laboratoriais (sinais e sintomas)?"),
    ("hemo_comunicacao_banco_sangue", "Houve comunicação ao banco de sangue?"),
    ("hemo_bolsa_recolhida", "A bolsa foi recolhida pelo banco de sangue?"),
]
ENVIO_AMOSTRA_OPTIONS = [
    "Entregue amostra íntegra",
    "Entregue invólucro do produto",
    "Envio de foto ou vídeo com os dados do produto visíveis",
]

# As buscas de paciente (prontuário) e de material (lote) são públicas: o formulário de
# notificação não exige login. O limite por IP, separado para cada tipo de busca, impede
# a varredura de prontuários para descobrir dados de pacientes.
LIMITE_CONSULTAS_PUBLICAS = 30
# O autocompletar do nome do produto consulta a cada pausa na digitação; não envolve dado pessoal.
LIMITES_POR_TIPO_CONSULTA = {"produto": 200}
JANELA_CONSULTAS_PUBLICAS_SEG = 600
_consultas_publicas_por_ip = defaultdict(deque)
_consultas_publicas_lock = threading.Lock()


def _consulta_pacientes_configurada():
    return banco_configurado(carregar_configuracao()["banco_pacientes"])


def _consulta_materiais_configurada():
    return banco_configurado(carregar_configuracao()["banco_materiais"])


def _consulta_publica_permitida(tipo, ip):
    agora = time.monotonic()
    with _consultas_publicas_lock:
        historico = _consultas_publicas_por_ip[(tipo, ip)]
        while historico and agora - historico[0] > JANELA_CONSULTAS_PUBLICAS_SEG:
            historico.popleft()
        if len(historico) >= LIMITES_POR_TIPO_CONSULTA.get(tipo, LIMITE_CONSULTAS_PUBLICAS):
            return False
        historico.append(agora)
        return True


SECOES_BANCO = {
    "banco_notificacoes": "Banco do Sistema de Notificações",
    "banco_pacientes": "Banco de Consulta de Pacientes",
    "banco_materiais": "Banco de Dados de Materiais",
}
# Bancos externos opcionais: sem eles, os dados são digitados no formulário e gravados no banco do sistema.
SECOES_BANCO_OPCIONAIS = ("banco_pacientes", "banco_materiais")
SCHEMA_NOTIFICACOES_PADRAO = "notificacao"
# O formulário público não pergunta o tipo; toda notificação dele entra como não conformidade.
TIPO_NOTIFICACAO_FORMULARIO = "Não Conformidade"
TIPOS_LOGO = {
    "principal": ("Logo principal (topo das páginas e login)", LOGO_PRINCIPAL_PADRAO),
    "transicao": ("Logo da animação de transição entre telas", LOGO_TRANSICAO_PADRAO),
}


# Perfis com acesso total enxergam tudo (None = sem filtro).
def _nomes_setores_permitidos(user):
    if user.acesso_total:
        return None
    return user.setores_permitidos()


def _nomes_categorias_permitidas(user):
    if user.acesso_total:
        return None
    return user.categorias_profissionais_permitidas()


def _nomes_tipos_ocorrencia_permitidos(user):
    if user.acesso_total:
        return None
    return user.tipos_ocorrencia_permitidos()


def _setores_ativos():
    return Setor.query.filter_by(ativa=True).order_by(Setor.nome.asc()).all()


def _nomes_setores_ativos():
    return [s.nome for s in _setores_ativos()]


def _categorias_profissionais_ativas():
    return CategoriaProfissional.query.filter_by(ativa=True).order_by(CategoriaProfissional.nome.asc()).all()


def _nomes_categorias_profissionais_ativas():
    return [c.nome for c in _categorias_profissionais_ativas()]


def _tipos_ocorrencia_ativos():
    return TipoOcorrencia.query.filter_by(ativa=True).order_by(TipoOcorrencia.nome.asc()).all()


def _nomes_tipos_ocorrencia_ativos():
    return [t.nome for t in _tipos_ocorrencia_ativos()]


def _ler_data_opcional(valor):
    valor = (valor or "").strip()
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


def _ler_paciente_digitado(form, exige_nome):
    """Dados do paciente digitados no formulário. Retorna (dados, mensagem_de_erro)."""
    nome = sanitize_text(form.get("paciente_nome", ""), max_length=500) or None
    if exige_nome and nome is None:
        return None, "Informe o nome do paciente."

    try:
        nascimento = _ler_data_opcional(form.get("paciente_data_nascimento"))
    except ValueError:
        return None, "Data de nascimento inválida."
    if nascimento and nascimento > datetime.now().date():
        return None, "A data de nascimento não pode ser futura."

    cpf = normalizar_cpf_digitado(form.get("paciente_cpf", ""))
    if cpf is None:
        return None, "CPF inválido. Confira os números digitados."

    return {
        "paciente_nome": nome,
        "paciente_data_nascimento": nascimento,
        "paciente_nome_mae": sanitize_text(form.get("paciente_nome_mae", ""), max_length=500) or None,
        "paciente_cpf": cpf or None,
    }, None


def _secao_adicional_do_form():
    """Seção adicional escolhida no cadastro de tipos de ocorrência (None = nenhuma)."""
    secao = request.form.get("secao_adicional", "")
    return secao if secao in SECOES_ADICIONAIS else None


def _ler_dados_biovigilancia(form):
    """Lê a seção de biovigilância (material biológico). Retorna (dados, mensagem_de_erro)."""
    transplante = form.get("bio_tipo_transplante", "")
    if transplante not in BIO_TIPO_TRANSPLANTE_OPTIONS:
        return None, "Selecione o tipo de transplante."
    reacao = form.get("bio_tipo_reacao", "")
    if reacao not in BIO_TIPO_REACAO_OPTIONS:
        return None, "Selecione o tipo de reação adversa."
    return {"bio_tipo_transplante": transplante, "bio_tipo_reacao": reacao}, None


def _ler_dados_medicacao(form, grau_dano):
    """Lê a seção de medicação. O incidente só é perguntado quando houve dano ao paciente."""
    dados = {
        "med_nome": sanitize_text(form.get("med_nome", ""), max_length=255),
        "med_apresentacao": sanitize_text(form.get("med_apresentacao", ""), max_length=255),
        "med_posologia": sanitize_text(form.get("med_posologia", ""), max_length=255),
        "med_incidente": None,
    }
    if not dados["med_nome"] or not dados["med_apresentacao"] or not dados["med_posologia"]:
        return None, "Informe o nome do medicamento, a apresentação e a posologia."

    if grau_dano and grau_dano != GRAU_SEM_DANO:
        incidente = form.get("med_incidente", "")
        if incidente not in MED_INCIDENTE_OPTIONS:
            return None, "Selecione o incidente relacionado a medicamentos (farmacovigilância)."
        dados["med_incidente"] = incidente
    return dados, None


def _ler_dados_hemotransfusao(form):
    """Lê a seção de hemotransfusão do formulário. Retorna (dados, mensagem_de_erro)."""
    tipo_evento = form.get("hemo_tipo_evento", "")
    if tipo_evento not in HEMO_TIPO_EVENTO_OPTIONS:
        return None, "Selecione o tipo de evento adverso da hemotransfusão."
    # Pode haver mais de um hemocomponente; ficam gravados juntos, na ordem das opções.
    hemocomponentes = [opcao for opcao in HEMOCOMPONENTE_OPTIONS if opcao in form.getlist("hemo_hemocomponente")]
    if not hemocomponentes:
        return None, "Selecione ao menos um tipo de hemocomponente."

    dados = {"hemo_tipo_evento": tipo_evento, "hemo_hemocomponente": "; ".join(hemocomponentes)}
    for campo, pergunta in HEMO_PERGUNTAS_SIM_NAO:
        resposta = form.get(campo, "")
        if resposta not in ("sim", "nao"):
            return None, f"Responda: {pergunta}"
        dados[campo] = resposta == "sim"
    return dados, None


def _aplicar_lote_do_estoque(dados, id_informado):
    """Se um lote do banco de materiais foi escolhido, usa os dados dele (o enviado pelo navegador é ignorado).

    O registro é buscado de novo pela chave e só é aceito se o número de lote for o mesmo
    digitado. Sem banco de materiais, com o banco fora do ar ou com a chave inválida,
    ficam os dados digitados.
    """
    id_informado = (id_informado or "").strip()
    if not id_informado.isdigit() or not _consulta_materiais_configurada():
        return
    try:
        registro = buscar_lote_por_id(int(id_informado))
    except ErroBancoMateriais as exc:
        current_app.logger.warning("Lote não conferido no banco de materiais: %s", exc)
        return
    if registro is None or registro["lote"].upper() != dados["produto_lote"].strip().upper():
        return
    dados.update({
        "produto_lote": registro["lote"],
        "produto_nome": registro["produto"][:255],
        "produto_fornecedor": registro["fornecedor"][:255] or None,
        "produto_validade": registro["validade"],
        "produto_lote_estoque_id": registro["id"],
    })
    # Parte dos produtos não tem codigoreferencia; nesses casos fica o cód. SEPLAG digitado.
    if registro["cod_seplag"]:
        dados["produto_cod_seplag"] = registro["cod_seplag"][:50]


def _aplicar_produto_do_estoque(dados, id_informado):
    """Se um produto foi escolhido pelo nome (autocompletar), confere no banco de materiais.

    Só é aceito se o nome enviado for o do produto; aí o cód. SEPLAG vem do estoque (quando
    existir). Sem banco, fora do ar ou com outro nome digitado, ficam os dados digitados.
    """
    id_informado = (id_informado or "").strip()
    if not id_informado.isdigit() or not _consulta_materiais_configurada():
        return
    try:
        produto = buscar_produto_por_id(int(id_informado))
    except ErroBancoMateriais as exc:
        current_app.logger.warning("Produto não conferido no banco de materiais: %s", exc)
        return
    if produto is None or produto["nome"].upper() != dados["produto_nome"].strip().upper():
        return
    dados["produto_nome"] = produto["nome"][:255]
    dados["produto_estoque_id"] = produto["id"]
    if produto["cod_seplag"]:
        dados["produto_cod_seplag"] = produto["cod_seplag"][:50]


def _ler_dados_produto(form, exige_lote_e_registro=True):
    """Lê a seção de dados do produto do formulário. Retorna (dados, mensagem_de_erro).

    Em saneantes (exige_lote_e_registro=False) o lote e o registro ANVISA são opcionais.
    """
    dados = {
        "produto_nome": sanitize_text(form.get("produto_nome", ""), max_length=255),
        "produto_cod_seplag": sanitize_text(form.get("produto_cod_seplag", ""), max_length=50) or None,
        "produto_lote": sanitize_text(form.get("produto_lote", ""), max_length=100),
        "produto_fabricante": sanitize_text(form.get("produto_fabricante", ""), max_length=255),
        "produto_fornecedor": sanitize_text(form.get("produto_fornecedor", ""), max_length=255) or None,
        "produto_registro_anvisa": sanitize_text(form.get("produto_registro_anvisa", ""), max_length=50) or None,
        "produto_lote_estoque_id": None,
        "produto_estoque_id": None,
    }
    try:
        dados["produto_data_fabricacao"] = _ler_data_opcional(form.get("produto_data_fabricacao"))
        dados["produto_validade"] = _ler_data_opcional(form.get("produto_validade"))
    except ValueError:
        return None, "Data de fabricação ou validade inválida."

    # O lote, quando escolhido, é mais específico que o produto e prevalece sobre ele.
    _aplicar_produto_do_estoque(dados, form.get("produto_estoque_id", ""))
    if dados["produto_lote"]:
        _aplicar_lote_do_estoque(dados, form.get("produto_lote_estoque_id", ""))
    dados["produto_lote"] = dados["produto_lote"] or None

    if not dados["produto_nome"] or not dados["produto_fabricante"]:
        return None, "Informe o nome do produto e o fabricante."
    if exige_lote_e_registro and not dados["produto_lote"]:
        return None, "Informe o lote do produto."
    if exige_lote_e_registro and not dados["produto_registro_anvisa"]:
        return None, "Informe o número de registro ANVISA do produto."
    if not dados["produto_data_fabricacao"]:
        return None, "Informe a data de fabricação do produto."
    fabricacao, validade = dados["produto_data_fabricacao"], dados["produto_validade"]
    if fabricacao and fabricacao > datetime.now().date():
        return None, "A data de fabricação não pode ser futura."
    if fabricacao and validade and validade < fabricacao:
        return None, "A validade não pode ser anterior à data de fabricação."

    amostras = [opcao for opcao in ENVIO_AMOSTRA_OPTIONS if opcao in form.getlist("envio_amostra")]
    if not amostras:
        return None, "Selecione como a amostra do produto será enviada."
    dados["envio_amostra"] = "; ".join(amostras)
    return dados, None


def _filtrar_query_permissoes_notificacao(query, user):
    setores_permitidos = _nomes_setores_permitidos(user)
    categorias_permitidas = _nomes_categorias_permitidas(user)
    tipos_ocorrencia_permitidos = _nomes_tipos_ocorrencia_permitidos(user)

    if setores_permitidos is not None:
        if not setores_permitidos:
            return query.filter(db.text("1=0"))
        query = query.filter(Notification.setor_notificador.in_(setores_permitidos))

    if categorias_permitidas is not None:
        if not categorias_permitidas:
            return query.filter(db.text("1=0"))
        query = query.filter(Notification.categoria_profissional.in_(categorias_permitidas))

    if tipos_ocorrencia_permitidos is not None:
        if not tipos_ocorrencia_permitidos:
            return query.filter(db.text("1=0"))
        query = query.filter(Notification.tipo_ocorrencia.in_(tipos_ocorrencia_permitidos))

    return query


def _buscar_notificacao_permitida(notification_id, user):
    query = _filtrar_query_permissoes_notificacao(Notification.query, user)
    return query.filter(Notification.id == notification_id).first_or_404()


def _salvar_imagem_comprimida(file_storage):
    if not file_storage or not file_storage.filename:
        return None

    extension = os.path.splitext(file_storage.filename)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Formato inválido. Use JPG, PNG ou WEBP.")

    try:
        image = Image.open(file_storage.stream)
        image = ImageOps.exif_transpose(image)
    except Exception as exc:
        raise ValueError("Não foi possível processar a imagem enviada.") from exc

    if image.mode in ("RGBA", "LA", "P"):
        fundo = Image.new("RGB", image.size, (255, 255, 255))
        fundo.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
        image = fundo
    else:
        image = image.convert("RGB")

    image.thumbnail((1600, 1600))

    upload_dir = os.path.join(current_app.static_folder, "uploads", "notificacoes")
    os.makedirs(upload_dir, exist_ok=True)

    file_name = f"notif_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:10]}.jpg"
    file_path = os.path.join(upload_dir, file_name)
    image.save(file_path, format="JPEG", optimize=True, quality=72)

    return f"uploads/notificacoes/{file_name}"


@main_bp.route("/", methods=["GET", "POST"])
def index():
    setores = _nomes_setores_ativos()
    categorias_profissionais = _nomes_categorias_profissionais_ativas()
    tipos_ocorrencia = _nomes_tipos_ocorrencia_ativos()
    tipos_ativos = _tipos_ocorrencia_ativos()
    tipos_com_produto = [t.nome for t in tipos_ativos if t.secao_adicional == SECAO_PRODUTO]
    tipos_com_saneantes = [t.nome for t in tipos_ativos if t.secao_adicional == SECAO_SANEANTES]
    tipos_com_hemotransfusao = [t.nome for t in tipos_ativos if t.secao_adicional == SECAO_HEMOTRANSFUSAO]
    tipos_com_medicacao = [t.nome for t in tipos_ativos if t.secao_adicional == SECAO_MEDICACAO]
    tipos_com_biovigilancia = [t.nome for t in tipos_ativos if t.secao_adicional == SECAO_BIOVIGILANCIA]
    tipos_exigem_paciente = [t.nome for t in tipos_ativos if t.exige_paciente]

    if request.method == "POST":
        setor_notificador = request.form.get("setor_notificador", "").strip()
        if setor_notificador not in setores:
            flash("Selecione um setor notificador válido.", "danger")
            return redirect(url_for("main.index"))

        local_evento = request.form.get("local_evento", "").strip()
        if local_evento not in setores:
            flash("Selecione um local do evento válido.", "danger")
            return redirect(url_for("main.index"))

        categoria_profissional = request.form.get("categoria_profissional", "").strip()
        if categoria_profissional not in categorias_profissionais:
            flash("Selecione uma categoria profissional válida.", "danger")
            return redirect(url_for("main.index"))

        tipo_ocorrencia = request.form.get("tipo_ocorrencia", "").strip()
        if tipo_ocorrencia not in tipos_ocorrencia:
            flash("Selecione um tipo de ocorrência válido.", "danger")
            return redirect(url_for("main.index"))

        dados_produto = {}
        if tipo_ocorrencia in tipos_com_produto or tipo_ocorrencia in tipos_com_saneantes:
            dados_produto, erro_produto = _ler_dados_produto(
                request.form, exige_lote_e_registro=tipo_ocorrencia in tipos_com_produto
            )
            if erro_produto:
                flash(erro_produto, "danger")
                return redirect(url_for("main.index"))

        dados_biovigilancia = {}
        if tipo_ocorrencia in tipos_com_biovigilancia:
            dados_biovigilancia, erro_bio = _ler_dados_biovigilancia(request.form)
            if erro_bio:
                flash(erro_bio, "danger")
                return redirect(url_for("main.index"))

        dados_hemotransfusao = {}
        if tipo_ocorrencia in tipos_com_hemotransfusao:
            dados_hemotransfusao, erro_hemo = _ler_dados_hemotransfusao(request.form)
            if erro_hemo:
                flash(erro_hemo, "danger")
                return redirect(url_for("main.index"))

        envolve_paciente_form = request.form.get("envolve_paciente", "")
        if envolve_paciente_form not in ("sim", "nao"):
            flash("Informe se a notificação envolve paciente.", "danger")
            return redirect(url_for("main.index"))
        envolve_paciente = envolve_paciente_form == "sim"
        if tipo_ocorrencia in tipos_exigem_paciente and not envolve_paciente:
            flash(f"Para notificações de {tipo_ocorrencia} é obrigatório informar os dados do paciente.", "danger")
            return redirect(url_for("main.index"))

        grau_dano = None
        paciente_prontuario = None
        dados_paciente = {}
        if envolve_paciente:
            paciente_prontuario = normalizar_prontuario(request.form.get("paciente_prontuario", ""))
            if paciente_prontuario is None:
                flash("Informe o número do prontuário do paciente (somente números).", "danger")
                return redirect(url_for("main.index"))

            if not _consulta_pacientes_configurada():
                # Sem banco de pacientes: os dados são digitados e gravados no banco do sistema.
                dados_paciente, erro_paciente = _ler_paciente_digitado(request.form, exige_nome=True)
                if erro_paciente:
                    flash(erro_paciente, "danger")
                    return redirect(url_for("main.index"))
            else:
                # Com banco de pacientes, os dados são buscados de novo no servidor; os digitados são ignorados.
                try:
                    paciente = buscar_paciente(paciente_prontuario)
                except ErroBancoPacientes as exc:
                    # Banco fora do ar: a notificação não pode ser perdida; usa o que foi digitado.
                    current_app.logger.warning("Dados do paciente não consultados: %s", exc)
                    dados_paciente, erro_paciente = _ler_paciente_digitado(request.form, exige_nome=False)
                    if erro_paciente:
                        flash(erro_paciente, "danger")
                        return redirect(url_for("main.index"))
                else:
                    if paciente is None:
                        flash("Prontuário não encontrado. Verifique o número informado.", "danger")
                        return redirect(url_for("main.index"))
                    dados_paciente = {
                        "paciente_nome": paciente["nome"],
                        "paciente_data_nascimento": paciente["data_nascimento"],
                        "paciente_nome_mae": paciente["nome_mae"],
                        "paciente_cpf": paciente["cpf"],
                    }

            grau_dano = request.form.get("grau_dano", "").strip()
            if grau_dano not in [valor for valor, _ in GRAU_DANO_OPTIONS]:
                flash("Selecione o grau do dano causado ao paciente.", "danger")
                return redirect(url_for("main.index"))

        # Depois do paciente: a pergunta de incidente depende do grau do dano.
        dados_medicacao = {}
        if tipo_ocorrencia in tipos_com_medicacao:
            dados_medicacao, erro_medicacao = _ler_dados_medicacao(request.form, grau_dano)
            if erro_medicacao:
                flash(erro_medicacao, "danger")
                return redirect(url_for("main.index"))

        try:
            data_ocorrencia = datetime.strptime(
                request.form.get("data_ocorrencia"), "%Y-%m-%d"
            ).date()
        except (TypeError, ValueError):
            flash("Data da ocorrência inválida.", "danger")
            return redirect(url_for("main.index"))

        try:
            horario_notificacao = datetime.strptime(
                request.form.get("horario_notificacao"), "%H:%M"
            ).time()
        except (TypeError, ValueError):
            flash("Horário da notificação inválido.", "danger")
            return redirect(url_for("main.index"))

        anonimo = request.form.get("anonimo") == "on"
        nome_notificante = sanitize_text(request.form.get("notificante_nome", ""), max_length=150)
        telefone = sanitize_phone(request.form.get("notificante_telefone", ""))
        email = sanitize_email(request.form.get("notificante_email", ""))
        descricao = sanitize_text(request.form.get("descricao", ""), allow_newlines=True)
        imagem_file = request.files.get("imagem_notificacao")

        if len(descricao) < 100:
            flash("A descrição da não conformidade deve ter no mínimo 100 caracteres.", "danger")
            return redirect(url_for("main.index"))

        if not anonimo and (not nome_notificante or not telefone or not email):
            flash("Nome, telefone e e-mail são obrigatórios quando a notificação não for anônima.", "danger")
            return redirect(url_for("main.index"))

        if not anonimo and (len(telefone) < 10 or not telefone.isdigit()):
            flash("Telefone inválido. Informe somente números com 10 ou 11 dígitos.", "danger")
            return redirect(url_for("main.index"))

        try:
            imagem_path = _salvar_imagem_comprimida(imagem_file)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("main.index"))

        if anonimo:
            contato_formatado = None
        else:
            partes_contato = []
            if telefone:
                partes_contato.append(f"Telefone: {telefone}")
            if email:
                partes_contato.append(f"E-mail: {email}")
            contato_formatado = " | ".join(partes_contato) if partes_contato else None

        nova = Notification(
            unidade_hospitalar="Hospital de Messejana",
            setor=setor_notificador,
            setor_notificador=setor_notificador,
            local_evento=local_evento,
            categoria_profissional=categoria_profissional,
            tipo_notificacao=TIPO_NOTIFICACAO_FORMULARIO,
            tipo_ocorrencia=tipo_ocorrencia,
            gravidade="Baixa",
            data_ocorrencia=data_ocorrencia,
            horario_notificacao=horario_notificacao,
            descricao=descricao,
            envolve_paciente=envolve_paciente,
            grau_dano=grau_dano,
            paciente_prontuario=paciente_prontuario,
            **dados_paciente,
            **dados_produto,
            **dados_hemotransfusao,
            **dados_medicacao,
            **dados_biovigilancia,
            notificante_nome=None if anonimo else nome_notificante,
            notificante_contato=contato_formatado,
            imagem_path=imagem_path,
            anonimo=anonimo,
            status="Pendente",
        )
        db.session.add(nova)
        db.session.commit()

        flash("Notificação enviada com sucesso. Obrigado por contribuir com a segurança do paciente!", "success")
        return redirect(url_for("main.index"))

    return render_template(
        "index.html",
        setores=setores,
        categorias_profissionais=categorias_profissionais,
        tipos=tipos_ocorrencia,
        tipos_com_produto=tipos_com_produto,
        tipos_com_saneantes=tipos_com_saneantes,
        tipos_com_hemotransfusao=tipos_com_hemotransfusao,
        tipos_com_medicacao=tipos_com_medicacao,
        tipos_com_biovigilancia=tipos_com_biovigilancia,
        bio_tipo_transplante_options=BIO_TIPO_TRANSPLANTE_OPTIONS,
        bio_tipo_reacao_options=BIO_TIPO_REACAO_OPTIONS,
        tipos_exigem_paciente=tipos_exigem_paciente,
        med_incidente_options=MED_INCIDENTE_OPTIONS,
        grau_sem_dano=GRAU_SEM_DANO,
        hemo_tipo_evento_options=HEMO_TIPO_EVENTO_OPTIONS,
        hemocomponente_options=HEMOCOMPONENTE_OPTIONS,
        hemo_perguntas_sim_nao=HEMO_PERGUNTAS_SIM_NAO,
        envio_amostra_options=ENVIO_AMOSTRA_OPTIONS,
        consulta_pacientes_configurada=_consulta_pacientes_configurada(),
        consulta_materiais_configurada=_consulta_materiais_configurada(),
        grau_dano_options=GRAU_DANO_OPTIONS,
    )


@main_bp.app_template_filter("cpf")
def _filtro_cpf(valor):
    return formatar_cpf(valor)


@main_bp.app_template_filter("idade_em")
def _filtro_idade_em(nascimento, referencia):
    """Idade do paciente na data informada (nas telas, a data da ocorrência)."""
    return calcular_idade(nascimento, referencia)


@main_bp.route("/paciente/nome")
def buscar_paciente_nome():
    """Dados do paciente (t_paciente) pelo número do prontuário, para o formulário público.

    O CPF volta mascarado: o formulário não exige login, e o CPF completo só é gravado
    pelo servidor no envio e exibido nas telas internas.
    """
    if not _consulta_pacientes_configurada():
        resposta = jsonify({"status": "nao_configurado", "mensagem": "Digite o nome do paciente."})
    elif not _consulta_publica_permitida("paciente", request.remote_addr or "desconhecido"):
        resposta = jsonify({"status": "limite", "mensagem": "Muitas consultas. Aguarde alguns minutos."})
        resposta.status_code = 429
    else:
        prontuario = normalizar_prontuario(request.args.get("prontuario", ""))
        if prontuario is None:
            resposta = jsonify({"status": "invalido", "mensagem": "Informe somente números."})
        else:
            try:
                paciente = buscar_paciente(prontuario)
            except ErroBancoPacientes as exc:
                current_app.logger.warning("Busca de paciente indisponível: %s", exc)
                resposta = jsonify({
                    "status": "indisponivel",
                    "mensagem": "Consulta de pacientes indisponível. Digite os dados do paciente.",
                })
            else:
                if paciente is None:
                    resposta = jsonify({"status": "nao_encontrado", "mensagem": "Prontuário não encontrado."})
                else:
                    nascimento = paciente["data_nascimento"]
                    resposta = jsonify({
                        "status": "encontrado",
                        "nome": paciente["nome"],
                        "data_nascimento": nascimento.isoformat() if nascimento else "",
                        "nome_mae": paciente["nome_mae"] or "",
                        "cpf_mascarado": mascarar_cpf(paciente["cpf"]),
                    })
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@main_bp.route("/material/produtos")
def buscar_material_produtos():
    """Sugestões de produtos pelo nome (autocompletar do formulário público)."""
    if not _consulta_materiais_configurada():
        resposta = jsonify({"status": "nao_configurado", "resultados": []})
    elif not _consulta_publica_permitida("produto", request.remote_addr or "desconhecido"):
        resposta = jsonify({"status": "limite", "resultados": []})
        resposta.status_code = 429
    else:
        try:
            resultados = buscar_produtos_por_nome(request.args.get("nome", "")[:100])
        except ErroBancoMateriais as exc:
            current_app.logger.warning("Busca de produto indisponível: %s", exc)
            resposta = jsonify({"status": "indisponivel", "resultados": []})
        else:
            resposta = jsonify({"status": "ok", "resultados": resultados})
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@main_bp.route("/material/lote")
def buscar_material_lote():
    """Produtos do banco de materiais com o lote informado, para o formulário público."""
    if not _consulta_materiais_configurada():
        resposta = jsonify({"status": "nao_configurado", "mensagem": "Preencha os dados do produto."})
    elif not _consulta_publica_permitida("material", request.remote_addr or "desconhecido"):
        resposta = jsonify({"status": "limite", "mensagem": "Muitas consultas. Aguarde alguns minutos."})
        resposta.status_code = 429
    else:
        lote = normalizar_lote(request.args.get("lote", ""))
        if lote is None:
            resposta = jsonify({"status": "invalido", "mensagem": "Informe o lote."})
        else:
            try:
                resultados, limitado = buscar_lotes(lote)
            except ErroBancoMateriais as exc:
                current_app.logger.warning("Busca de lote indisponível: %s", exc)
                resposta = jsonify({
                    "status": "indisponivel",
                    "mensagem": "Consulta de materiais indisponível. Preencha os dados do produto.",
                })
            else:
                if not resultados:
                    resposta = jsonify({
                        "status": "nao_encontrado",
                        "mensagem": "Lote não encontrado no estoque. Preencha os dados do produto.",
                    })
                else:
                    resposta = jsonify({
                        "status": "encontrado",
                        "limitado": limitado,
                        "resultados": [{
                            "id": r["id"],
                            "lote": r["lote"],
                            "produto": r["produto"],
                            "fornecedor": r["fornecedor"],
                            "cod_seplag": r["cod_seplag"],
                            "validade": r["validade"].isoformat() if r["validade"] else "",
                            "validade_br": r["validade"].strftime("%d/%m/%Y") if r["validade"] else "",
                        } for r in resultados],
                    })
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@main_bp.route("/menu")
@login_required
def menu():
    # Um card para cada página liberada no perfil do usuário, na ordem de TELAS.
    cards = [
        {"titulo": titulo, "icone": icone, "url": url_for(endpoint)}
        for chave, (titulo, icone, endpoint) in TELAS.items()
        if current_user.can_access(chave)
    ]
    return render_template("menu.html", cards=cards)


@main_bp.route("/perfis", methods=["GET", "POST"])
@login_required
@role_required("perfis")
def cadastro_perfis():
    if request.method == "POST":
        nome = sanitize_text(request.form.get("nome_perfil", ""), max_length=80)
        if not nome:
            flash("Informe o nome do perfil.", "danger")
        elif Perfil.query.filter(db.func.lower(Perfil.nome) == nome.lower()).first():
            flash("Já existe um perfil com esse nome.", "warning")
        else:
            perfil = Perfil(nome=nome, acesso_total=request.form.get("acesso_total") == "on", ativo=True)
            perfil.definir_telas(request.form.getlist("telas"))
            db.session.add(perfil)
            db.session.commit()
            flash(f"Perfil '{nome}' cadastrado com sucesso.", "success")
        return redirect(url_for("main.cadastro_perfis"))

    perfis = Perfil.query.order_by(Perfil.ativo.desc(), Perfil.nome.asc()).all()
    return render_template("cadastro_perfis.html", perfis=perfis, telas=TELAS)


@main_bp.route("/perfis/<int:perfil_id>/editar", methods=["GET", "POST"])
@login_required
@role_required("perfis")
def editar_perfil(perfil_id):
    perfil = Perfil.query.get_or_404(perfil_id)
    if perfil.sistema:
        flash(f"O perfil '{perfil.nome}' é do sistema: tem acesso a todas as páginas e não pode ser alterado.", "warning")
        return redirect(url_for("main.cadastro_perfis"))

    if request.method == "POST":
        nome = sanitize_text(request.form.get("nome_perfil", ""), max_length=80)
        if not nome:
            flash("Informe o nome do perfil.", "danger")
        elif Perfil.query.filter(db.func.lower(Perfil.nome) == nome.lower(), Perfil.id != perfil.id).first():
            flash("Já existe um perfil com esse nome.", "warning")
        else:
            perfil.nome = nome
            perfil.acesso_total = request.form.get("acesso_total") == "on"
            perfil.definir_telas(request.form.getlist("telas"))
            db.session.commit()
            flash(f"Perfil '{perfil.nome}' atualizado com sucesso.", "success")
            return redirect(url_for("main.cadastro_perfis"))

    return render_template("editar_perfil.html", perfil=perfil, telas=TELAS)


@main_bp.route("/perfis/<int:perfil_id>/status", methods=["POST"])
@login_required
@role_required("perfis")
def alternar_status_perfil(perfil_id):
    perfil = Perfil.query.get_or_404(perfil_id)
    if perfil.sistema:
        flash(f"O perfil '{perfil.nome}' é do sistema e não pode ser inativado.", "warning")
    elif perfil.ativo and any(u.ativo for u in perfil.usuarios):
        ativos = sum(1 for u in perfil.usuarios if u.ativo)
        flash(f"O perfil '{perfil.nome}' tem {ativos} usuário(s) ativo(s); altere o perfil deles antes de inativar.", "warning")
    else:
        perfil.ativo = not perfil.ativo
        db.session.commit()
        estado = "ativado" if perfil.ativo else "inativado"
        flash(f"Perfil '{perfil.nome}' {estado} com sucesso.", "info")
    return redirect(url_for("main.cadastro_perfis"))


@main_bp.route("/usuarios", methods=["GET", "POST"])
@login_required
@role_required("usuarios")
def cadastro_usuario():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")

        perfil, erro = _validar_usuario_form(request.form)
        if erro is None and User.query.filter_by(username=username).first():
            erro = "Já existe um usuário com esse nome de usuário."
        if erro is None and (not username or not password or not full_name):
            erro = "Preencha todos os campos obrigatórios."

        if erro:
            flash(erro, "danger")
        else:
            novo_usuario = User(username=username, full_name=full_name, perfil=perfil)
            novo_usuario.set_password(password)
            db.session.add(novo_usuario)
            _aplicar_vinculos_usuario(novo_usuario, request.form)
            db.session.commit()
            flash(f"Usuário '{username}' cadastrado com sucesso.", "success")
            return redirect(url_for("main.cadastro_usuario"))

    usuarios = User.query.order_by(User.created_at.desc()).all()
    return render_template("cadastro_usuario.html", usuarios=usuarios, **_opcoes_formulario_usuario())


def _perfis_ativos():
    return Perfil.query.filter_by(ativo=True).order_by(Perfil.nome.asc()).all()


def _opcoes_formulario_usuario():
    return {
        "perfis": _perfis_ativos(),
        "setores": _setores_ativos(),
        "categorias_profissionais": _categorias_profissionais_ativas(),
        "tipos_ocorrencia": _tipos_ocorrencia_ativos(),
    }


# (campo do formulário, rótulo na mensagem de erro)
VINCULOS_USUARIO = [
    ("setor_ids", "setor"),
    ("categoria_ids", "categoria profissional"),
    ("tipo_ocorrencia_ids", "tipo de ocorrência"),
]


def _validar_usuario_form(form):
    """Perfil escolhido e vínculos obrigatórios. Retorna (perfil, mensagem_de_erro)."""
    perfil_id = form.get("perfil_id", "")
    perfil = Perfil.query.filter_by(id=int(perfil_id), ativo=True).first() if perfil_id.isdigit() else None
    if perfil is None:
        return None, "Selecione um perfil de acesso válido."
    if not perfil.acesso_total:
        for campo, rotulo in VINCULOS_USUARIO:
            if not form.getlist(campo):
                return perfil, f"Selecione pelo menos um {rotulo} para perfis sem acesso total."
    return perfil, None


def _aplicar_vinculos_usuario(usuario, form):
    """Perfis com acesso total enxergam tudo e não guardam vínculos."""
    if usuario.perfil and usuario.perfil.acesso_total:
        usuario.setores = []
        usuario.categorias_profissionais = []
        usuario.tipos_ocorrencia = []
        return
    usuario.setores = Setor.query.filter(Setor.id.in_(form.getlist("setor_ids")), Setor.ativa.is_(True)).all()
    usuario.categorias_profissionais = CategoriaProfissional.query.filter(
        CategoriaProfissional.id.in_(form.getlist("categoria_ids")),
        CategoriaProfissional.ativa.is_(True),
    ).all()
    usuario.tipos_ocorrencia = TipoOcorrencia.query.filter(
        TipoOcorrencia.id.in_(form.getlist("tipo_ocorrencia_ids")),
        TipoOcorrencia.ativa.is_(True),
    ).all()


@main_bp.route("/setores", methods=["GET", "POST"])
@login_required
@role_required("setores")
def cadastro_setores():
    if request.method == "POST":
        nome_setor = sanitize_text(request.form.get("nome_setor", ""), max_length=150)
        if not nome_setor:
            flash("Informe o nome do setor.", "danger")
        elif Setor.query.filter(db.func.lower(Setor.nome) == nome_setor.lower()).first():
            flash("Já existe um setor com esse nome.", "warning")
        else:
            novo_setor = Setor(id=_proximo_id_setor_manual(), nome=nome_setor, ativa=True, origem=ORIGEM_SETOR_MANUAL)
            db.session.add(novo_setor)
            db.session.commit()
            flash(f"Setor cadastrado com sucesso (id {novo_setor.id}).", "success")
        return redirect(url_for("main.cadastro_setores"))

    setores = Setor.query.order_by(Setor.ativa.desc(), Setor.nome.asc()).all()
    return render_template("cadastro_setores.html", setores=setores, origem_clinica=ORIGEM_SETOR_CLINICA)


def _proximo_id_setor_manual():
    """Setores manuais usam ids a partir de 1001; abaixo disso ficam os isn_clinica da t_clinica."""
    maior_id = db.session.query(db.func.max(Setor.id)).filter(Setor.id >= ID_INICIAL_SETOR_MANUAL).scalar()
    return max(maior_id or 0, ID_INICIAL_SETOR_MANUAL - 1) + 1


@main_bp.route("/setores/sincronizar", methods=["POST"])
@login_required
@role_required("setores")
def sincronizar_setores():
    try:
        resultado = sincronizar_setores_t_clinica()
    except ErroBancoPacientes as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("main.cadastro_setores"))

    flash(
        f"Sincronização com t_clinica concluída: {resultado['criados']} criados, "
        f"{resultado['atualizados']} atualizados, {resultado['inativados']} inativados.",
        "success",
    )
    for conflito in resultado["conflitos"]:
        flash(f"Não importado: {conflito}", "warning")
    return redirect(url_for("main.cadastro_setores"))


def _bloquear_setor_t_clinica(setor):
    if setor.origem == ORIGEM_SETOR_CLINICA:
        flash(
            f"O setor '{setor.nome}' vem da tabela t_clinica e é mantido pela sincronização; "
            "altere-o no sistema de origem.",
            "warning",
        )
        return redirect(url_for("main.cadastro_setores"))
    return None


@main_bp.route("/setores/<int:setor_id>/editar", methods=["GET", "POST"])
@login_required
@role_required("setores")
def editar_setor(setor_id):
    setor = Setor.query.get_or_404(setor_id)
    bloqueio = _bloquear_setor_t_clinica(setor)
    if bloqueio:
        return bloqueio

    if request.method == "POST":
        nome_setor = request.form.get("nome_setor", "").strip()

        if not nome_setor:
            flash("Informe o nome do setor.", "danger")
        elif Setor.query.filter(db.func.lower(Setor.nome) == nome_setor.lower(), Setor.id != setor.id).first():
            flash("Já existe um setor com esse nome.", "warning")
        else:
            setor.nome = nome_setor
            db.session.commit()
            flash("Setor atualizado com sucesso.", "success")
            return redirect(url_for("main.cadastro_setores"))

    return render_template("editar_setor.html", setor=setor)


@main_bp.route("/setores/<int:setor_id>/status", methods=["POST"])
@login_required
@role_required("setores")
def alternar_status_setor(setor_id):
    setor = Setor.query.get_or_404(setor_id)
    bloqueio = _bloquear_setor_t_clinica(setor)
    if bloqueio:
        return bloqueio
    setor.ativa = not setor.ativa
    db.session.commit()
    estado = "ativado" if setor.ativa else "inativado"
    flash(f"Setor '{setor.nome}' {estado} com sucesso.", "info")
    return redirect(url_for("main.cadastro_setores"))


@main_bp.route("/categorias-profissionais", methods=["GET", "POST"])
@login_required
@role_required("categorias_profissionais")
def cadastro_categorias_profissionais():
    if request.method == "POST":
        nome_categoria = request.form.get("nome_categoria", "").strip()
        if not nome_categoria:
            flash("Informe o nome da categoria profissional.", "danger")
        elif CategoriaProfissional.query.filter(db.func.lower(CategoriaProfissional.nome) == nome_categoria.lower()).first():
            flash("Já existe uma categoria profissional com esse nome.", "warning")
        else:
            db.session.add(CategoriaProfissional(nome=nome_categoria, ativa=True))
            db.session.commit()
            flash("Categoria profissional cadastrada com sucesso.", "success")
        return redirect(url_for("main.cadastro_categorias_profissionais"))

    categorias = CategoriaProfissional.query.order_by(CategoriaProfissional.ativa.desc(), CategoriaProfissional.nome.asc()).all()
    return render_template("cadastro_categorias_profissionais.html", categorias=categorias)


@main_bp.route("/categorias-profissionais/<int:categoria_id>/editar", methods=["GET", "POST"])
@login_required
@role_required("categorias_profissionais")
def editar_categoria_profissional(categoria_id):
    categoria = CategoriaProfissional.query.get_or_404(categoria_id)

    if request.method == "POST":
        nome_categoria = request.form.get("nome_categoria", "").strip()
        if not nome_categoria:
            flash("Informe o nome da categoria profissional.", "danger")
        elif CategoriaProfissional.query.filter(
            db.func.lower(CategoriaProfissional.nome) == nome_categoria.lower(),
            CategoriaProfissional.id != categoria.id,
        ).first():
            flash("Já existe uma categoria profissional com esse nome.", "warning")
        else:
            categoria.nome = nome_categoria
            db.session.commit()
            flash("Categoria profissional atualizada com sucesso.", "success")
            return redirect(url_for("main.cadastro_categorias_profissionais"))

    return render_template("editar_categoria_profissional.html", categoria=categoria)


@main_bp.route("/categorias-profissionais/<int:categoria_id>/status", methods=["POST"])
@login_required
@role_required("categorias_profissionais")
def alternar_status_categoria_profissional(categoria_id):
    categoria = CategoriaProfissional.query.get_or_404(categoria_id)
    categoria.ativa = not categoria.ativa
    db.session.commit()
    estado = "ativada" if categoria.ativa else "inativada"
    flash(f"Categoria profissional '{categoria.nome}' {estado} com sucesso.", "info")
    return redirect(url_for("main.cadastro_categorias_profissionais"))


@main_bp.route("/tipos-ocorrencia", methods=["GET", "POST"])
@login_required
@role_required("tipos_ocorrencia")
def cadastro_tipos_ocorrencia():
    if request.method == "POST":
        nome_tipo = request.form.get("nome_tipo", "").strip()
        if not nome_tipo:
            flash("Informe o nome do tipo de ocorrência.", "danger")
        elif TipoOcorrencia.query.filter(db.func.lower(TipoOcorrencia.nome) == nome_tipo.lower()).first():
            flash("Já existe um tipo de ocorrência com esse nome.", "warning")
        else:
            db.session.add(TipoOcorrencia(
                nome=nome_tipo,
                ativa=True,
                secao_adicional=_secao_adicional_do_form(),
                exige_paciente=request.form.get("exige_paciente") == "on",
            ))
            db.session.commit()
            flash("Tipo de ocorrência cadastrado com sucesso.", "success")
        return redirect(url_for("main.cadastro_tipos_ocorrencia"))

    tipos = TipoOcorrencia.query.order_by(TipoOcorrencia.ativa.desc(), TipoOcorrencia.nome.asc()).all()
    return render_template("cadastro_tipos_ocorrencia.html", tipos=tipos, secoes_adicionais=SECOES_ADICIONAIS)


@main_bp.route("/tipos-ocorrencia/<int:tipo_id>/editar", methods=["GET", "POST"])
@login_required
@role_required("tipos_ocorrencia")
def editar_tipo_ocorrencia(tipo_id):
    tipo = TipoOcorrencia.query.get_or_404(tipo_id)

    if request.method == "POST":
        nome_tipo = request.form.get("nome_tipo", "").strip()
        if not nome_tipo:
            flash("Informe o nome do tipo de ocorrência.", "danger")
        elif TipoOcorrencia.query.filter(
            db.func.lower(TipoOcorrencia.nome) == nome_tipo.lower(),
            TipoOcorrencia.id != tipo.id,
        ).first():
            flash("Já existe um tipo de ocorrência com esse nome.", "warning")
        else:
            tipo.nome = nome_tipo
            tipo.secao_adicional = _secao_adicional_do_form()
            tipo.exige_paciente = request.form.get("exige_paciente") == "on"
            db.session.commit()
            flash("Tipo de ocorrência atualizado com sucesso.", "success")
            return redirect(url_for("main.cadastro_tipos_ocorrencia"))

    return render_template("editar_tipo_ocorrencia.html", tipo=tipo, secoes_adicionais=SECOES_ADICIONAIS)


@main_bp.route("/tipos-ocorrencia/<int:tipo_id>/status", methods=["POST"])
@login_required
@role_required("tipos_ocorrencia")
def alternar_status_tipo_ocorrencia(tipo_id):
    tipo = TipoOcorrencia.query.get_or_404(tipo_id)
    tipo.ativa = not tipo.ativa
    db.session.commit()
    estado = "ativado" if tipo.ativa else "inativado"
    flash(f"Tipo de ocorrência '{tipo.nome}' {estado} com sucesso.", "info")
    return redirect(url_for("main.cadastro_tipos_ocorrencia"))


@main_bp.route("/usuarios/<int:user_id>/editar", methods=["GET", "POST"])
@login_required
@role_required("usuarios")
def editar_usuario(user_id):
    usuario = User.query.get_or_404(user_id)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")

        perfil, erro = _validar_usuario_form(request.form)
        if erro is None and User.query.filter(User.username == username, User.id != usuario.id).first():
            erro = "Já existe um usuário com esse nome de usuário."
        if erro is None and (not username or not full_name):
            erro = "Preencha todos os campos obrigatórios."
        if erro is None and usuario.username == "admin" and not perfil.sistema:
            erro = "O usuário administrador interno precisa manter o perfil Administrador."

        if erro:
            flash(erro, "danger")
        else:
            usuario.username = username
            usuario.full_name = full_name
            usuario.perfil = perfil
            if password:
                usuario.set_password(password)
            _aplicar_vinculos_usuario(usuario, request.form)
            db.session.commit()
            flash(f"Usuário '{usuario.username}' atualizado com sucesso.", "success")
            return redirect(url_for("main.cadastro_usuario"))

    return render_template("editar_usuario.html", usuario=usuario, **_opcoes_formulario_usuario())


@main_bp.route("/usuarios/<int:user_id>/status", methods=["POST"])
@login_required
@role_required("usuarios")
def alternar_status_usuario(user_id):
    usuario = User.query.get_or_404(user_id)
    if usuario.username == "admin":
        flash("O usuário administrador interno não pode ser desativado.", "warning")
    else:
        usuario.ativo = not usuario.ativo
        db.session.commit()
        estado = "ativado" if usuario.ativo else "desativado"
        flash(f"Usuário '{usuario.username}' {estado} com sucesso.", "info")
    return redirect(url_for("main.cadastro_usuario"))


@main_bp.route("/notificacoes")
@login_required
@role_required("notificacoes")
def notificacoes():
    status_filtro = request.args.get("status", "")
    setor_notificador_filtro = request.args.get("setor_notificador", "")
    responsavel_filtro = request.args.get("responsavel_id", "").strip()

    query = _filtrar_query_permissoes_notificacao(Notification.query, current_user)

    if status_filtro:
        query = query.filter_by(status=status_filtro)
    if setor_notificador_filtro:
        query = query.filter_by(setor_notificador=setor_notificador_filtro)
    if responsavel_filtro:
        try:
            query = query.filter_by(responsavel_id=int(responsavel_filtro))
        except ValueError:
            flash("Filtro de setor responsável inválido.", "warning")
            return redirect(url_for("main.notificacoes"))

    lista = query.order_by(Notification.created_at.desc()).all()
    setores = _nomes_setores_ativos()
    responsaveis = ResponsavelNotificacao.query.filter_by(ativo=True).order_by(ResponsavelNotificacao.nome.asc()).all()
    return render_template(
        "notificacoes.html",
        notificacoes=lista,
        setores=setores,
        responsaveis=responsaveis,
        status_options=STATUS_OPTIONS,
        status_filtro=status_filtro,
        setor_filtro=setor_notificador_filtro,
        responsavel_filtro=responsavel_filtro,
    )


@main_bp.route("/notificacoes/<int:notification_id>")
@login_required
@role_required("notificacoes")
def visualizar_notificacao(notification_id):
    notificacao = _buscar_notificacao_permitida(notification_id, current_user)
    can_view_notificante = current_user.acesso_total
    return render_template(
        "visualizar_notificacao.html",
        notificacao=notificacao,
        can_view_notificante=can_view_notificante,
    )


@main_bp.route("/gerenciar", methods=["GET", "POST"])
@login_required
@role_required("gerenciar")
def gerenciar():
    setores_permitidos = _nomes_setores_permitidos(current_user)
    categorias_permitidas = _nomes_categorias_permitidas(current_user)
    tipos_ocorrencia_permitidos = _nomes_tipos_ocorrencia_permitidos(current_user)

    if request.method == "POST":
        notif_id = request.form.get("notification_id")
        notificacao = Notification.query.get_or_404(notif_id)
        if setores_permitidos is not None and notificacao.setor_notificador not in setores_permitidos:
            flash("Você não possui permissão para gerenciar notificações desse setor.", "danger")
            return redirect(url_for("main.gerenciar"))
        if categorias_permitidas is not None and notificacao.categoria_profissional not in categorias_permitidas:
            flash("Você não possui permissão para gerenciar notificações dessa categoria profissional.", "danger")
            return redirect(url_for("main.gerenciar"))
        if tipos_ocorrencia_permitidos is not None and notificacao.tipo_ocorrencia not in tipos_ocorrencia_permitidos:
            flash("Você não possui permissão para gerenciar notificações desse tipo de ocorrência.", "danger")
            return redirect(url_for("main.gerenciar"))

        notificacao.status = request.form.get("status", notificacao.status)
        gravidade_form = request.form.get("gravidade", notificacao.gravidade)
        if gravidade_form in GRAVIDADE_OPTIONS:
            notificacao.gravidade = gravidade_form

        responsavel_id = request.form.get("responsavel_id", "").strip()
        if responsavel_id:
            try:
                responsavel_id_int = int(responsavel_id)
            except ValueError:
                flash("Selecione um responsável válido.", "danger")
                return redirect(url_for("main.gerenciar"))

            responsavel = ResponsavelNotificacao.query.filter_by(id=responsavel_id_int, ativo=True).first()
            if responsavel is None:
                flash("Selecione um responsável válido.", "danger")
                return redirect(url_for("main.gerenciar"))
            notificacao.responsavel_id = responsavel.id
        else:
            notificacao.responsavel_id = None

        status_anvisa_form = request.form.get("status_anvisa", notificacao.status_anvisa)
        if status_anvisa_form not in STATUS_ANVISA_OPTIONS:
            flash("Status ANVISA inválido.", "danger")
            return redirect(url_for("main.gerenciar"))
        notificacao.status_anvisa = status_anvisa_form

        notificacao.numero_notivisa = sanitize_text(request.form.get("numero_notivisa", ""), max_length=120) or None
        notificacao.numero_vigimed = sanitize_text(request.form.get("numero_vigimed", ""), max_length=120) or None

        notificacao.observacao_tratamento = sanitize_text(request.form.get("observacao", ""), allow_newlines=True)
        notificacao.tratado_por_id = current_user.id
        db.session.commit()
        flash(f"Notificação #{notificacao.id} atualizada.", "success")
        return redirect(url_for("main.gerenciar"))

    pendentes_query = Notification.query.filter(
        Notification.status != "Resolvida"
    )
    resolvidas_query = Notification.query.filter_by(
        status="Resolvida"
    )

    pendentes_query = _filtrar_query_permissoes_notificacao(pendentes_query, current_user)
    resolvidas_query = _filtrar_query_permissoes_notificacao(resolvidas_query, current_user)

    pendentes = pendentes_query.order_by(Notification.created_at.desc()).all()
    resolvidas = resolvidas_query.order_by(Notification.updated_at.desc()).limit(20).all()
    responsaveis = ResponsavelNotificacao.query.filter_by(ativo=True).order_by(ResponsavelNotificacao.nome.asc()).all()

    return render_template(
        "gerenciar.html",
        pendentes=pendentes,
        resolvidas=resolvidas,
        status_options=STATUS_OPTIONS,
        gravidade_options=GRAVIDADE_OPTIONS,
        status_anvisa_options=STATUS_ANVISA_OPTIONS,
        responsaveis=responsaveis,
        can_view_notificante=current_user.acesso_total,
    )


@main_bp.route("/dashboard")
@login_required
@role_required("dashboard")
def dashboard():
    responsavel_filtro = request.args.get("responsavel_id", "").strip()

    query = _filtrar_query_permissoes_notificacao(Notification.query, current_user)
    if responsavel_filtro:
        try:
            query = query.filter_by(responsavel_id=int(responsavel_filtro))
        except ValueError:
            flash("Filtro de responsável inválido.", "warning")
            return redirect(url_for("main.dashboard"))

    todas = query.all()
    total = len(todas)

    por_status = Counter(n.status for n in todas)
    por_gravidade = Counter(n.gravidade for n in todas)
    por_setor = Counter(n.setor_notificador for n in todas)
    por_categoria = Counter(n.categoria_profissional for n in todas)
    por_tipo = Counter(n.tipo_ocorrencia for n in todas)
    por_mes = Counter(n.data_ocorrencia.strftime("%m/%Y") for n in todas)

    meses_ordenados = sorted(
        por_mes.items(), key=lambda item: datetime.strptime(item[0], "%m/%Y")
    )
    meses_labels = [m for m, _ in meses_ordenados]

    tipos_ordenados = [tipo for tipo, _ in por_tipo.most_common()]
    tipos_por_mes = {tipo: [] for tipo in tipos_ordenados}
    for mes in meses_labels:
        for tipo in tipos_ordenados:
            tipos_por_mes[tipo].append(
                sum(
                    1
                    for notificacao in todas
                    if notificacao.tipo_ocorrencia == tipo
                    and notificacao.data_ocorrencia.strftime("%m/%Y") == mes
                )
            )

    dados = {
        "status_labels": list(por_status.keys()),
        "status_values": list(por_status.values()),
        "gravidade_labels": list(por_gravidade.keys()),
        "gravidade_values": list(por_gravidade.values()),
        "setor_labels": list(por_setor.keys()),
        "setor_values": list(por_setor.values()),
        "categoria_labels": list(por_categoria.keys()),
        "categoria_values": list(por_categoria.values()),
        "tipo_labels": tipos_ordenados,
        "tipo_values": [por_tipo[tipo] for tipo in tipos_ordenados],
        "mes_labels": meses_labels,
        "mes_values": [v for _, v in meses_ordenados],
        "tipos_por_mes": tipos_por_mes,
    }

    return render_template(
        "dashboard.html",
        total=total,
        pendentes=por_status.get("Pendente", 0),
        em_analise=por_status.get("Em Análise", 0),
        resolvidas=por_status.get("Resolvida", 0),
        dados=dados,
        responsaveis=ResponsavelNotificacao.query.filter_by(ativo=True).order_by(ResponsavelNotificacao.nome.asc()).all(),
        responsavel_filtro=responsavel_filtro,
    )


def _conexao_notificacoes_em_uso():
    app_config = current_app.config
    return {
        "host": app_config.get("DB_HOST", ""),
        "port": app_config.get("DB_PORT", "5432"),
        "database": app_config.get("DB_NAME", ""),
        "user": app_config.get("DB_USER", ""),
        "password": app_config.get("DB_PASSWORD", ""),
        "schema": app_config.get("DB_SCHEMA", "public"),
    }


def _banco_notificacoes_atual(config):
    """Configuração salva do banco de notificações ou, se não houver, a do .env com o schema padrão."""
    salvo = config["banco_notificacoes"]
    if banco_configurado(salvo):
        return dict(salvo)
    return {**_conexao_notificacoes_em_uso(), "schema": SCHEMA_NOTIFICACOES_PADRAO}


def _dados_banco_form(secao, atual):
    dados = {}
    for campo in CAMPOS_BANCO:
        if campo == "password":
            # Senha não é sanitizada além do null byte para não alterar caracteres válidos.
            dados[campo] = request.form.get(f"{secao}_password", "").replace("\x00", "")
        else:
            dados[campo] = sanitize_text(request.form.get(f"{secao}_{campo}", ""), max_length=255)
    senha_digitada = dados["password"]
    if not senha_digitada:
        dados["password"] = atual.get("password", "")
    dados["port"] = dados["port"] or "5432"
    dados["schema"] = dados["schema"] or "public"
    return dados, senha_digitada


def _salvar_logo(file_storage, tipo):
    if not file_storage or not file_storage.filename:
        raise ValueError("Selecione um arquivo de imagem.")

    extension = os.path.splitext(file_storage.filename)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Formato inválido. Use JPG, PNG ou WEBP.")

    try:
        image = Image.open(file_storage.stream)
        image = ImageOps.exif_transpose(image)
        image.load()
    except Exception as exc:
        raise ValueError("Não foi possível processar a imagem enviada.") from exc

    # PNG preserva a transparência da logo.
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    image.thumbnail((800, 800))

    upload_dir = os.path.join(current_app.static_folder, "uploads", "logos")
    os.makedirs(upload_dir, exist_ok=True)

    file_name = f"logo_{tipo}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}.png"
    image.save(os.path.join(upload_dir, file_name), format="PNG", optimize=True)
    return f"uploads/logos/{file_name}"


def _remover_logo_enviada(caminho):
    """Apaga do disco uma logo enviada anteriormente (nunca as logos padrão)."""
    if not caminho or not caminho.startswith("uploads/logos/"):
        return
    arquivo = os.path.join(current_app.static_folder, *caminho.split("/"))
    try:
        os.remove(arquivo)
    except OSError:
        pass


@main_bp.route("/configuracoes", methods=["GET", "POST"])
@login_required
@role_required("configuracoes")
def configuracoes():
    config = carregar_configuracao()
    bancos = {
        "banco_notificacoes": _banco_notificacoes_atual(config),
        "banco_pacientes": dict(config["banco_pacientes"]),
        "banco_materiais": dict(config["banco_materiais"]),
    }
    senhas_digitadas = {}
    resultados_teste = {}

    if request.method == "POST":
        secao, _, operacao = request.form.get("acao", "").partition(":")

        if secao in SECOES_BANCO_OPCIONAIS and operacao == "remover":
            config[secao] = {**config[secao], "host": "", "database": "", "user": "", "password": ""}
            salvar_configuracao(config)
            flash(
                f"{SECOES_BANCO[secao]}: configuração removida. "
                "Os dados passam a ser preenchidos manualmente e gravados no banco do sistema.",
                "info",
            )
            return redirect(url_for("main.configuracoes"))

        if secao in SECOES_BANCO and operacao in ("testar", "salvar"):
            dados, senhas_digitadas[secao] = _dados_banco_form(secao, bancos[secao])
            bancos[secao] = dados

            if operacao == "testar":
                status, mensagem = testar_conexao(dados)
                if status == TESTE_AVISO and secao == "banco_notificacoes":
                    mensagem += " Ele será criado ao clicar em Salvar."
                resultados_teste[secao] = {"status": status, "mensagem": mensagem}
            elif not banco_configurado(dados):
                flash("Preencha endereço, banco e usuário.", "danger")
            elif not schema_valido(dados["schema"]):
                flash("Schema inválido. Use letras, números e _ (começando por letra ou _).", "danger")
            else:
                if secao == "banco_notificacoes":
                    # Cria schema, tabelas e dados iniciais antes de gravar a conexão.
                    ok, mensagem = preparar_banco_notificacoes(
                        dados,
                        current_app.config["ADMIN_USERNAME"],
                        current_app.config["ADMIN_PASSWORD"],
                    )
                    if not ok:
                        resultados_teste[secao] = {"status": "erro", "mensagem": f"{mensagem} Configuração não salva."}
                        return _render_configuracoes(config, bancos, senhas_digitadas, resultados_teste)
                    flash(mensagem, "success")

                config[secao] = dados
                salvar_configuracao(config)
                flash(f"{SECOES_BANCO[secao]}: configuração salva.", "success")

                if secao == "banco_notificacoes":
                    app = current_app._get_current_object()
                    if sincronizar_conexao_notificacoes(app):
                        # Usuários do banco anterior não existem no novo: novo login no banco ativo.
                        db.session.remove()
                        logout_user()
                        session["conexao_banco"] = chave_conexao(app)
                        flash(
                            "O sistema já está usando o novo banco. Entre novamente com um usuário desse banco "
                            "(em um banco recém-criado, use o administrador padrão).",
                            "info",
                        )
                        return redirect(url_for("auth.login"))
                    if os.environ.get("DATABASE_URL"):
                        flash("A variável DATABASE_URL está definida e tem prioridade; a conexão em uso não foi alterada.", "warning")
                return redirect(url_for("main.configuracoes"))

        elif secao == "logo" and operacao.partition("_")[2] in TIPOS_LOGO:
            acao_logo, _, tipo = operacao.partition("_")
            caminho_anterior = config["logos"].get(tipo)

            if acao_logo == "enviar":
                try:
                    config["logos"][tipo] = _salvar_logo(request.files.get(f"logo_{tipo}"), tipo)
                except ValueError as exc:
                    flash(str(exc), "danger")
                    return redirect(url_for("main.configuracoes"))
                salvar_configuracao(config)
                _remover_logo_enviada(caminho_anterior)
                flash(f"{TIPOS_LOGO[tipo][0]}: logo atualizada.", "success")
            elif acao_logo == "restaurar":
                config["logos"][tipo] = TIPOS_LOGO[tipo][1]
                salvar_configuracao(config)
                _remover_logo_enviada(caminho_anterior)
                flash(f"{TIPOS_LOGO[tipo][0]}: logo padrão restaurada.", "info")
            return redirect(url_for("main.configuracoes"))

        else:
            flash("Ação inválida.", "danger")
            return redirect(url_for("main.configuracoes"))

    return _render_configuracoes(config, bancos, senhas_digitadas, resultados_teste)


def _render_configuracoes(config, bancos, senhas_digitadas, resultados_teste):
    return render_template(
        "configuracoes.html",
        secoes_banco=SECOES_BANCO,
        secoes_opcionais=SECOES_BANCO_OPCIONAIS,
        bancos_configurados={secao: banco_configurado(config[secao]) for secao in SECOES_BANCO_OPCIONAIS},
        bancos=bancos,
        conexao_em_uso=_conexao_notificacoes_em_uso(),
        senhas_digitadas=senhas_digitadas,
        resultados_teste=resultados_teste,
        tipos_logo=TIPOS_LOGO,
        logos=config["logos"],
    )
