"""Criação do schema, das tabelas e dos dados iniciais do banco de notificações.

Usado na inicialização do sistema (notificacao_app.create_app) e pela tela de
Configurações, que prepara um banco novo antes de salvar a conexão.
"""

import hashlib
import os

import sqlalchemy as sa
from flask import Flask
from sqlalchemy import text, inspect
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

from banco_pacientes import ErroBancoPacientes, buscar_clinicas_ativas
from config_store import banco_configurado, carregar_configuracao, schema_valido
from extensions import db
from models import (
    User,
    Setor,
    CategoriaProfissional,
    TipoNotificacao,
    TipoOcorrencia,
    ResponsavelNotificacao,
    Perfil,
    PERFIL_ADMINISTRADOR,
    TELAS,
    ORIGEM_SETOR_CLINICA,
    ORIGEM_SETOR_MANUAL,
    SECAO_BIOVIGILANCIA,
    SECAO_HEMOTRANSFUSAO,
    SECAO_MEDICACAO,
    SECAO_PRODUTO,
    SECAO_SANEANTES,
    SECOES_EXIGEM_PACIENTE,
)


# Colunas adicionadas em bancos existentes (dados do produto e dados do paciente).
COLUNAS_ADICIONAIS_NOTIFICACAO = [
    ("produto_nome", "VARCHAR(255)"),
    ("produto_cod_seplag", "VARCHAR(50)"),
    ("produto_lote", "VARCHAR(100)"),
    ("produto_fabricante", "VARCHAR(255)"),
    ("produto_data_fabricacao", "DATE"),
    ("produto_validade", "DATE"),
    ("produto_registro_anvisa", "VARCHAR(50)"),
    ("envio_amostra", "VARCHAR(255)"),
    # Dados da hemotransfusão.
    ("hemo_tipo_evento", "VARCHAR(120)"),
    ("hemo_hemocomponente", "VARCHAR(30)"),
    ("hemo_alteracao_sinais_vitais", "BOOLEAN"),
    ("hemo_manifestacoes_clinicas", "BOOLEAN"),
    ("hemo_comunicacao_banco_sangue", "BOOLEAN"),
    ("hemo_bolsa_recolhida", "BOOLEAN"),
    # Dados da medicação.
    ("med_nome", "VARCHAR(255)"),
    ("med_apresentacao", "VARCHAR(255)"),
    ("med_posologia", "VARCHAR(255)"),
    ("med_incidente", "VARCHAR(60)"),
    # Dados da biovigilância.
    ("bio_tipo_transplante", "VARCHAR(30)"),
    ("bio_tipo_reacao", "VARCHAR(60)"),
    # Lote escolhido na consulta ao banco de materiais.
    ("produto_fornecedor", "VARCHAR(255)"),
    ("produto_lote_estoque_id", "BIGINT"),
    ("produto_estoque_id", "BIGINT"),
    # Dados do paciente vindos da t_paciente (ou digitados, sem banco de pacientes).
    ("paciente_data_nascimento", "DATE"),
    ("paciente_nome_mae", "VARCHAR(500)"),
    ("paciente_cpf", "VARCHAR(50)"),
]
# Tipos de ocorrência criados com a seção adicional do formulário já ligada.
TIPOS_COM_SECAO_PADRAO = {
    "Medicação": SECAO_MEDICACAO,
    "Material médico-hospitalar": SECAO_PRODUTO,
    "Hemotransfusão": SECAO_HEMOTRANSFUSAO,
    "Material biológico": SECAO_BIOVIGILANCIA,
    "Saneantes": SECAO_SANEANTES,
}


def inicializar_banco(app):
    """Cria schema, tabelas, ajustes de colunas e dados padrão no banco do app."""
    garantir_schema(app)
    db.create_all()
    aplicar_atualizacoes_schema()
    aplicar_atualizacoes_setores()
    aplicar_atualizacoes_tipos_ocorrencia()
    # Perfis e usuários antes de qualquer consulta a users (o modelo já usa perfil_id).
    criar_perfis_padrao()
    aplicar_atualizacoes_usuarios()
    criar_categorias_profissionais_padrao()
    criar_tipos_notificacao_padrao()
    criar_tipos_ocorrencia_padrao()
    vincular_tipos_ocorrencia_usuarios_existentes()
    criar_responsaveis_notificacao_padrao()
    criar_admin_padrao(app)
    sincronizar_setores_na_inicializacao()


def preparar_banco_notificacoes(dados, admin_username, admin_password):
    """Cria schema, tabelas e dados iniciais no banco informado na tela de Configurações.

    Usa um app Flask temporário apontando para o banco novo, para reaproveitar a
    mesma rotina da inicialização sem afetar a conexão em uso. Retorna (ok, mensagem).
    """
    schema = dados.get("schema") or "public"
    if not schema_valido(schema):
        return False, "Schema inválido. Use letras, números e _ (começando por letra ou _)."

    try:
        url = montar_url_postgres(dados)
    except ValueError:
        return False, "Porta inválida."

    app_temporario = Flask(__name__)
    app_temporario.config.update(
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_ENGINE_OPTIONS={
            "connect_args": {"options": f"-csearch_path={schema}", "connect_timeout": 5},
        },
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DB_SCHEMA=schema,
        ADMIN_USERNAME=admin_username,
        ADMIN_PASSWORD=admin_password,
    )
    db.init_app(app_temporario)

    with app_temporario.app_context():
        try:
            inicializar_banco(app_temporario)
            total_tabelas = len(inspect(db.engine).get_table_names())
        except UnicodeDecodeError:
            # Servidores com mensagens em português quebram a decodificação do erro no psycopg2.
            return False, "Falha na conexão: usuário ou senha inválidos, ou banco de dados inexistente."
        except (SQLAlchemyError, ValueError) as exc:
            detalhe = str(getattr(exc, "orig", exc)).strip().splitlines()
            return False, f"Não foi possível preparar o banco: {detalhe[0] if detalhe else exc.__class__.__name__}"
        finally:
            db.session.remove()
            db.engine.dispose()

    return True, f"Schema '{schema}' pronto com {total_tabelas} tabelas e os dados iniciais."


def montar_url_postgres(dados):
    # URL.create garante encoding correto de usuário/senha com caracteres especiais.
    return URL.create(
        drivername="postgresql+psycopg2",
        username=dados["user"],
        password=dados.get("password") or "",
        host=dados["host"],
        port=int(dados.get("port") or 5432),
        database=dados["database"],
    ).render_as_string(hide_password=False)


def chave_conexao(app):
    """Identifica o banco em uso; gravada na sessão do usuário no login."""
    origem = f"{app.config.get('SQLALCHEMY_DATABASE_URI')}|{app.config.get('DB_SCHEMA')}"
    return hashlib.sha256(origem.encode("utf-8")).hexdigest()[:16]


def _conexao_em_uso(app):
    cfg = app.config
    return (
        cfg.get("DB_HOST"), str(cfg.get("DB_PORT")), cfg.get("DB_NAME"),
        cfg.get("DB_USER"), cfg.get("DB_PASSWORD") or "", cfg.get("DB_SCHEMA"),
    )


def _conexao_salva(dados):
    return (
        dados.get("host"), str(dados.get("port") or 5432), dados.get("database"),
        dados.get("user"), dados.get("password") or "", dados.get("schema") or "public",
    )


def trocar_conexao_notificacoes(app, dados):
    """Passa o sistema em execução a usar o banco informado, sem reiniciar."""
    schema = dados.get("schema") or "public"
    url = montar_url_postgres(dados)
    opcoes = {"connect_args": {"options": f"-csearch_path={schema}"}}

    # O Flask-SQLAlchemy 3.1 não tem API pública para trocar o engine de um app já
    # inicializado; o próprio init_app usa este dicionário para guardar os engines.
    engines = db._app_engines[app]
    engine_antigo = engines.get(None)
    engines[None] = sa.create_engine(url, **opcoes)

    app.config.update(
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_ENGINE_OPTIONS=opcoes,
        DB_HOST=dados["host"],
        DB_PORT=str(dados.get("port") or 5432),
        DB_NAME=dados["database"],
        DB_USER=dados["user"],
        DB_PASSWORD=dados.get("password") or "",
        DB_SCHEMA=schema,
    )
    if engine_antigo is not None:
        engine_antigo.dispose()


def sincronizar_conexao_notificacoes(app):
    """Aplica a conexão salva na tela de Configurações, se for diferente da que está em uso.

    Chamado a cada requisição para que todos os processos do servidor passem a usar
    o novo banco assim que ele for salvo. Retorna True se a conexão foi trocada.
    """
    if os.environ.get("DATABASE_URL"):
        # DATABASE_URL definida explicitamente tem prioridade (ver config.py).
        return False
    salvo = carregar_configuracao()["banco_notificacoes"]
    if not banco_configurado(salvo) or not schema_valido(salvo.get("schema") or "public"):
        return False
    if _conexao_salva(salvo) == _conexao_em_uso(app):
        return False
    trocar_conexao_notificacoes(app, salvo)
    return True


def garantir_schema(app):
    """Cria o schema configurado para as tabelas do sistema, caso ainda não exista."""
    if db.engine.dialect.name != "postgresql":
        return
    schema = app.config.get("DB_SCHEMA", "public")
    if schema == "public":
        return
    # O nome do schema já foi validado em config.py (somente letras, números e _).
    db.session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    db.session.commit()


def aplicar_atualizacoes_schema():
    """Aplica ajustes de schema compatíveis com bancos já existentes."""
    insp = inspect(db.engine)
    if "notifications" not in insp.get_table_names():
        return

    cols = {col["name"] for col in insp.get_columns("notifications")}

    if db.engine.dialect.name == "sqlite":
        # SQLite nao suporta "ADD COLUMN IF NOT EXISTS".
        if "imagem_path" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN imagem_path VARCHAR(255)"))
        if "setor_notificador" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN setor_notificador VARCHAR(120)"))
        if "local_evento" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN local_evento VARCHAR(120)"))
        if "horario_notificacao" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN horario_notificacao TIME"))
        if "responsavel_id" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN responsavel_id INTEGER"))
        if "status_anvisa" not in cols:
            db.session.execute(
                text("ALTER TABLE notifications ADD COLUMN status_anvisa VARCHAR(20) DEFAULT 'Não Notificado'")
            )
        if "numero_notivisa" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN numero_notivisa VARCHAR(120)"))
        if "numero_vigimed" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN numero_vigimed VARCHAR(120)"))
        if "envolve_paciente" not in cols:
            db.session.execute(
                text("ALTER TABLE notifications ADD COLUMN envolve_paciente BOOLEAN NOT NULL DEFAULT 0")
            )
        if "grau_dano" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN grau_dano VARCHAR(40)"))
        if "tipo_notificacao" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN tipo_notificacao VARCHAR(120)"))
        if "paciente_prontuario" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN paciente_prontuario VARCHAR(50)"))
        if "paciente_nome" not in cols:
            db.session.execute(text("ALTER TABLE notifications ADD COLUMN paciente_nome VARCHAR(500)"))
        for coluna, tipo_sql in COLUNAS_ADICIONAIS_NOTIFICACAO:
            if coluna not in cols:
                db.session.execute(text(f"ALTER TABLE notifications ADD COLUMN {coluna} {tipo_sql}"))
    else:
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS imagem_path VARCHAR(255)"))
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS setor_notificador VARCHAR(120)"))
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS local_evento VARCHAR(120)"))
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS horario_notificacao TIME"))
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS responsavel_id INTEGER REFERENCES responsaveis_notificacao(id)"))
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS status_anvisa VARCHAR(20)"))
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS numero_notivisa VARCHAR(120)"))
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS numero_vigimed VARCHAR(120)"))
        db.session.execute(
            text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS envolve_paciente BOOLEAN NOT NULL DEFAULT FALSE")
        )
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS grau_dano VARCHAR(40)"))
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS tipo_notificacao VARCHAR(120)"))
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS paciente_prontuario VARCHAR(50)"))
        db.session.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS paciente_nome VARCHAR(500)"))
        # Bancos criados com VARCHAR(150): amplia para o tamanho de t_paciente.dsc_nome.
        db.session.execute(text("ALTER TABLE notifications ALTER COLUMN paciente_nome TYPE VARCHAR(500)"))
        for coluna, tipo_sql in COLUNAS_ADICIONAIS_NOTIFICACAO:
            db.session.execute(text(f"ALTER TABLE notifications ADD COLUMN IF NOT EXISTS {coluna} {tipo_sql}"))

    if "setor" in cols:
        db.session.execute(
            text(
                """
                UPDATE notifications
                SET setor_notificador = COALESCE(setor_notificador, setor),
                    local_evento = COALESCE(local_evento, setor_notificador, setor)
                """
            )
        )
    else:
        db.session.execute(
            text(
                """
                UPDATE notifications
                SET setor_notificador = COALESCE(setor_notificador, local_evento),
                    local_evento = COALESCE(local_evento, setor_notificador)
                """
            )
        )

    db.session.execute(
        text(
            """
            UPDATE notifications
            SET horario_notificacao = COALESCE(horario_notificacao, CAST('00:00:00' AS TIME))
            """
        )
    )

    db.session.execute(
        text(
            """
            UPDATE notifications
            SET status_anvisa = COALESCE(status_anvisa, 'Não Notificado')
            """
        )
    )

    # Notificações anteriores vieram do formulário de não conformidade.
    db.session.execute(
        text(
            """
            UPDATE notifications
            SET tipo_notificacao = COALESCE(tipo_notificacao, 'Não Conformidade')
            """
        )
    )

    db.session.commit()


def criar_admin_padrao(app):
    """Garante a existência do usuário administrador interno (admin/admin)."""
    admin = User.query.filter_by(username=app.config["ADMIN_USERNAME"]).first()
    if admin is None:
        admin = User(
            username=app.config["ADMIN_USERNAME"],
            full_name="Administrador do Sistema",
            perfil=Perfil.query.filter_by(sistema=True).first(),
            ativo=True,
        )
        admin.set_password(app.config["ADMIN_PASSWORD"])
        db.session.add(admin)
        db.session.commit()
        print(">> Usuário administrador padrão criado (admin/admin). Altere a senha em produção!")


def aplicar_atualizacoes_setores():
    """Adiciona a coluna de origem em bancos criados antes da integração com t_clinica."""
    insp = inspect(db.engine)
    if "setores" not in insp.get_table_names():
        return
    cols = {col["name"] for col in insp.get_columns("setores")}
    if "origem" not in cols:
        db.session.execute(
            text(f"ALTER TABLE setores ADD COLUMN origem VARCHAR(20) NOT NULL DEFAULT '{ORIGEM_SETOR_MANUAL}'")
        )
        db.session.commit()


def sincronizar_setores_t_clinica():
    """Traz para setores as clínicas ativas (flg_ativo = 'S') da t_clinica do banco de pacientes.

    - Clínica nova: cria o setor com id = isn_clinica.
    - Clínica com nome alterado ou reativada: atualiza o setor.
    - Clínica que deixou de estar ativa: inativa o setor (não apaga, para manter os
      vínculos de usuários e o histórico das notificações).
    Lança ErroBancoPacientes se o banco de pacientes não estiver acessível.
    """
    clinicas = buscar_clinicas_ativas()
    resultado = {"criados": 0, "atualizados": 0, "inativados": 0, "conflitos": []}
    ids_ativos = set()

    for isn_clinica, nome in clinicas:
        nome = nome[:150]
        if not nome:
            continue
        setor = db.session.get(Setor, isn_clinica)
        if setor is not None and setor.origem != ORIGEM_SETOR_CLINICA:
            resultado["conflitos"].append(
                f"{isn_clinica} - {nome}: id já usado pelo setor manual '{setor.nome}'"
            )
            continue
        mesmo_nome = Setor.query.filter(
            db.func.lower(Setor.nome) == nome.lower(), Setor.id != isn_clinica
        ).first()
        if mesmo_nome is not None:
            resultado["conflitos"].append(
                f"{isn_clinica} - {nome}: nome já usado pelo setor {mesmo_nome.id}"
            )
            continue

        if setor is None:
            db.session.add(Setor(id=isn_clinica, nome=nome, ativa=True, origem=ORIGEM_SETOR_CLINICA))
            resultado["criados"] += 1
        elif setor.nome != nome or not setor.ativa:
            setor.nome = nome
            setor.ativa = True
            resultado["atualizados"] += 1
        ids_ativos.add(isn_clinica)

    for setor in Setor.query.filter_by(origem=ORIGEM_SETOR_CLINICA, ativa=True).all():
        if setor.id not in ids_ativos:
            setor.ativa = False
            resultado["inativados"] += 1

    db.session.commit()
    return resultado


def sincronizar_setores_na_inicializacao():
    """Sincroniza ao iniciar; se o banco de pacientes estiver fora, segue com os setores atuais."""
    if not banco_configurado(carregar_configuracao()["banco_pacientes"]):
        return
    try:
        resultado = sincronizar_setores_t_clinica()
    except ErroBancoPacientes as exc:
        db.session.rollback()
        print(f">> Setores não sincronizados com t_clinica: {exc}")
        return
    print(
        ">> Setores sincronizados com t_clinica: "
        f"{resultado['criados']} criados, {resultado['atualizados']} atualizados, "
        f"{resultado['inativados']} inativados, {len(resultado['conflitos'])} conflitos."
    )


def criar_categorias_profissionais_padrao():
    # Só em banco novo: depois disso o cadastro é mantido pela tela (itens renomeados ou
    # apagados não podem voltar sozinhos a cada inicialização).
    if CategoriaProfissional.query.first() is not None:
        return
    categorias_padrao = [
        "Enfermagem",
        "Medicina",
        "Farmácia",
        "Fisioterapia",
        "Nutrição",
        "Serviço Social",
        "Psicologia",
        "Administrativo",
        "Higienização",
        "Outro",
    ]
    criou_alguma = False

    for nome in categorias_padrao:
        existente = CategoriaProfissional.query.filter_by(nome=nome).first()
        if existente is None:
            db.session.add(CategoriaProfissional(nome=nome, ativa=True))
            criou_alguma = True

    if criou_alguma:
        db.session.commit()


def criar_tipos_notificacao_padrao():
    tipos_padrao = [
        "Tecnovigilância",
        "Hemovigilância",
        "Farmacovigilância",
        "Não Conformidade",
        "Biovigilância",
        "Saneantes",
        "Nutrivigilância",
    ]
    criou_alguma = False

    for nome in tipos_padrao:
        existente = TipoNotificacao.query.filter_by(nome=nome).first()
        if existente is None:
            db.session.add(TipoNotificacao(nome=nome, ativa=True))
            criou_alguma = True

    if criou_alguma:
        db.session.commit()


def vincular_tipos_ocorrencia_usuarios_existentes():
    """Usuários criados antes do vínculo com tipos de ocorrência mantêm o acesso que já tinham."""
    usuarios_sem_tipo = [u for u in User.query.filter(~User.tipos_ocorrencia.any()).all() if not u.acesso_total]
    if not usuarios_sem_tipo:
        return

    tipos_ativos = TipoOcorrencia.query.filter_by(ativa=True).all()
    for usuario in usuarios_sem_tipo:
        usuario.tipos_ocorrencia = list(tipos_ativos)
    db.session.commit()


# Perfis criados em banco novo; equivalem aos antigos perfis fixos do sistema.
PERFIS_PADRAO = [
    # (nome, páginas do menu, acesso total aos dados, perfil do sistema, valor antigo em users.role)
    (PERFIL_ADMINISTRADOR, list(TELAS), True, True, "administrador"),
    ("Supervisor", ["notificacoes", "gerenciar", "dashboard"], False, False, "supervisor"),
    ("Operador", ["notificacoes"], False, False, "operador"),
]


def criar_perfis_padrao():
    """Só em banco novo; depois os perfis são mantidos pela tela de Perfis de Acesso."""
    if Perfil.query.first() is not None:
        return
    for nome, telas, acesso_total, sistema, _ in PERFIS_PADRAO:
        perfil = Perfil(nome=nome, acesso_total=acesso_total, sistema=sistema, ativo=True)
        perfil.definir_telas(telas)
        db.session.add(perfil)
    db.session.commit()


def aplicar_atualizacoes_usuarios():
    """Troca o perfil fixo (users.role) pelo cadastro de perfis (users.perfil_id).

    A coluna role é removida depois da conversão: era NOT NULL sem valor padrão no banco, e
    o cadastro de novos usuários falharia.
    """
    insp = inspect(db.engine)
    if "users" not in insp.get_table_names():
        return
    cols = {col["name"] for col in insp.get_columns("users")}
    if "perfil_id" not in cols:
        referencia = "" if db.engine.dialect.name == "sqlite" else " REFERENCES perfis(id)"
        db.session.execute(text(f"ALTER TABLE users ADD COLUMN perfil_id INTEGER{referencia}"))
    if "role" in cols:
        for nome, _, _, _, role_antigo in PERFIS_PADRAO:
            db.session.execute(
                text("UPDATE users SET perfil_id = (SELECT id FROM perfis WHERE nome = :nome) "
                     "WHERE role = :role AND perfil_id IS NULL"),
                {"nome": nome, "role": role_antigo},
            )
        db.session.execute(text("ALTER TABLE users DROP COLUMN role"))
    db.session.commit()


def criar_tipos_ocorrencia_padrao():
    # Só em banco novo: depois disso o cadastro é mantido pela tela (itens renomeados ou
    # apagados não podem voltar sozinhos a cada inicialização).
    if TipoOcorrencia.query.first() is not None:
        return
    tipos_padrao = [
        "Queda de Paciente",
        "Falha de Identificação",
        "Infecção Relacionada à Assistência",
        "Falha em Equipamento",
        "Falha em Processo Administrativo",
        "Outro",
    ]
    for nome in tipos_padrao:
        db.session.add(TipoOcorrencia(nome=nome, ativa=True))
    # Tipos que já abrem uma seção adicional do formulário.
    for nome, secao in TIPOS_COM_SECAO_PADRAO.items():
        db.session.add(TipoOcorrencia(
            nome=nome, ativa=True, secao_adicional=secao, exige_paciente=secao in SECOES_EXIGEM_PACIENTE,
        ))
    db.session.commit()


def aplicar_atualizacoes_tipos_ocorrencia():
    """Adiciona a coluna secao_adicional em bancos existentes.

    Bancos anteriores tinham a caixa 'exige dados do produto' (exige_dados_produto); os tipos
    marcados passam para a seção de produto e a coluna antiga é removida. Ela não pode ficar:
    em bancos criados pelo create_all era NOT NULL sem valor padrão no banco, e o cadastro de
    novos tipos falharia.
    """
    insp = inspect(db.engine)
    if "tipos_ocorrencia" not in insp.get_table_names():
        return
    cols = {col["name"] for col in insp.get_columns("tipos_ocorrencia")}

    if "secao_adicional" not in cols:
        db.session.execute(text("ALTER TABLE tipos_ocorrencia ADD COLUMN secao_adicional VARCHAR(30)"))
        if "exige_dados_produto" in cols:
            db.session.execute(
                text("UPDATE tipos_ocorrencia SET secao_adicional = :secao WHERE exige_dados_produto = :sim"),
                {"secao": SECAO_PRODUTO, "sim": True},
            )
        # Tipos já cadastrados com os nomes padrão (ex.: Hemotransfusão) recebem a seção correspondente.
        for nome, secao in TIPOS_COM_SECAO_PADRAO.items():
            db.session.execute(
                text("UPDATE tipos_ocorrencia SET secao_adicional = :secao WHERE nome = :nome AND secao_adicional IS NULL"),
                {"secao": secao, "nome": nome},
            )

    if "exige_dados_produto" in cols:
        db.session.execute(text("ALTER TABLE tipos_ocorrencia DROP COLUMN exige_dados_produto"))

    if "exige_paciente" not in cols:
        valor_falso = "0" if db.engine.dialect.name == "sqlite" else "FALSE"
        db.session.execute(
            text(f"ALTER TABLE tipos_ocorrencia ADD COLUMN exige_paciente BOOLEAN NOT NULL DEFAULT {valor_falso}")
        )
        # Antes a obrigatoriedade vinha da seção; os tipos dessas seções continuam exigindo paciente.
        for secao in SECOES_EXIGEM_PACIENTE:
            db.session.execute(
                text("UPDATE tipos_ocorrencia SET exige_paciente = :sim WHERE secao_adicional = :secao"),
                {"sim": True, "secao": secao},
            )
    db.session.commit()


def criar_responsaveis_notificacao_padrao():
    responsaveis_padrao = [
        "Gerência de Risco",
        "Segurança do Paciente",
        "Escritório da Qualidade",
    ]
    criou_alguma = False

    for nome in responsaveis_padrao:
        existente = ResponsavelNotificacao.query.filter_by(nome=nome).first()
        if existente is None:
            db.session.add(ResponsavelNotificacao(nome=nome, ativo=True))
            criou_alguma = True

    if criou_alguma:
        db.session.commit()


