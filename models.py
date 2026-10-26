from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db

# --- Páginas do menu que podem ser liberadas para cada perfil ---
# chave: (título no menu, ícone, endpoint da página)
TELAS = {
    "usuarios": ("Cadastro de Usuários", "👤", "main.cadastro_usuario"),
    "perfis": ("Perfis de Acesso", "🔐", "main.cadastro_perfis"),
    "setores": ("Cadastro de Setores", "🏥", "main.cadastro_setores"),
    "categorias_profissionais": ("Categorias Profissionais", "🩺", "main.cadastro_categorias_profissionais"),
    "tipos_ocorrencia": ("Tipos de Ocorrência", "📝", "main.cadastro_tipos_ocorrencia"),
    "notificacoes": ("Conferir Notificações", "📋", "main.notificacoes"),
    "gerenciar": ("Gerenciar Notificações", "⚙️", "main.gerenciar"),
    "dashboard": ("Dashboard", "📊", "main.dashboard"),
    "configuracoes": ("Configurações", "🛠️", "main.configuracoes"),
}

PERFIL_ADMINISTRADOR = "Administrador"

user_setores = db.Table(
    "user_setores",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("setor_id", db.Integer, db.ForeignKey("setores.id"), primary_key=True),
)

user_categorias_profissionais = db.Table(
    "user_categorias_profissionais",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("categoria_id", db.Integer, db.ForeignKey("categorias_profissionais.id"), primary_key=True),
)

user_tipos_notificacao = db.Table(
    "user_tipos_notificacao",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("tipo_notificacao_id", db.Integer, db.ForeignKey("tipos_notificacao.id"), primary_key=True),
)

user_tipos_ocorrencia = db.Table(
    "user_tipos_ocorrencia",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("tipo_ocorrencia_id", db.Integer, db.ForeignKey("tipos_ocorrencia.id"), primary_key=True),
)


class Perfil(db.Model):
    """Perfil de acesso: quais páginas do menu o usuário vê e se enxerga todos os dados."""

    __tablename__ = "perfis"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), unique=True, nullable=False, index=True)
    # Chaves de TELAS separadas por vírgula.
    telas = db.Column(db.Text, nullable=False, default="", server_default="")
    # Vê todas as notificações (sem filtro de setor, categoria e tipos) e os dados do notificante.
    acesso_total = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    # Perfil do sistema (Administrador): sempre com todas as páginas e não pode ser inativado.
    sistema = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    ativo = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def lista_telas(self):
        if self.sistema:
            return list(TELAS)
        return [chave for chave in (self.telas or "").split(",") if chave in TELAS]

    def definir_telas(self, chaves):
        self.telas = ",".join(chave for chave in TELAS if chave in chaves)

    def tem_tela(self, chave):
        return chave in self.lista_telas()

    def __repr__(self):
        return f"<Perfil {self.nome}>"


# Setores vindos da tabela t_clinica usam o próprio isn_clinica como id;
# os cadastrados manualmente começam em 1001 para não conflitar com eles.
ORIGEM_SETOR_CLINICA = "t_clinica"
ORIGEM_SETOR_MANUAL = "manual"
ID_INICIAL_SETOR_MANUAL = 1001


class Setor(db.Model):
    __tablename__ = "setores"

    id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    nome = db.Column(db.String(150), unique=True, nullable=False, index=True)
    ativa = db.Column(db.Boolean, default=True, nullable=False)
    origem = db.Column(db.String(20), nullable=False, default=ORIGEM_SETOR_MANUAL)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Setor {self.nome}>"


class CategoriaProfissional(db.Model):
    __tablename__ = "categorias_profissionais"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), unique=True, nullable=False, index=True)
    ativa = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<CategoriaProfissional {self.nome}>"


class TipoNotificacao(db.Model):
    __tablename__ = "tipos_notificacao"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), unique=True, nullable=False, index=True)
    ativa = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<TipoNotificacao {self.nome}>"


# Seções adicionais do formulário de notificação, escolhidas no cadastro de tipos de ocorrência.
SECAO_PRODUTO = "produto"
SECAO_HEMOTRANSFUSAO = "hemotransfusao"
SECAO_MEDICACAO = "medicacao"
SECAO_BIOVIGILANCIA = "biovigilancia"
# Mesmo quadro de dados do produto, com lote e registro ANVISA opcionais.
SECAO_SANEANTES = "saneantes"
SECOES_ADICIONAIS = {
    SECAO_PRODUTO: "Dados do produto (tecnovigilância)",
    SECAO_SANEANTES: "Dados do produto saneante (vigilância de saneantes)",
    SECAO_HEMOTRANSFUSAO: "Dados da hemotransfusão (hemovigilância)",
    SECAO_MEDICACAO: "Dados da medicação (farmacovigilância)",
    SECAO_BIOVIGILANCIA: "Dados da biovigilância (material biológico)",
}
# Seções que, ao serem escolhidas, já marcam o tipo como "exige paciente" (valor inicial;
# a obrigatoriedade fica em TipoOcorrencia.exige_paciente e pode ser alterada na tela).
SECOES_EXIGEM_PACIENTE = {SECAO_HEMOTRANSFUSAO, SECAO_MEDICACAO, SECAO_BIOVIGILANCIA}


class TipoOcorrencia(db.Model):
    __tablename__ = "tipos_ocorrencia"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), unique=True, nullable=False, index=True)
    ativa = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Seção extra exibida no formulário quando este tipo é escolhido (uma das SECOES_ADICIONAIS ou None).
    secao_adicional = db.Column(db.String(30), nullable=True)
    # Obriga a identificação do paciente no formulário. server_default: o banco também precisa
    # do valor padrão (inserts que não enviam a coluna não podem falhar).
    exige_paciente = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    def __repr__(self):
        return f"<TipoOcorrencia {self.nome}>"


class ResponsavelNotificacao(db.Model):
    __tablename__ = "responsaveis_notificacao"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), unique=True, nullable=False, index=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ResponsavelNotificacao {self.nome}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(150), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    perfil_id = db.Column(db.Integer, db.ForeignKey("perfis.id"), nullable=True)
    perfil = db.relationship("Perfil", backref="usuarios")
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    setores = db.relationship("Setor", secondary=user_setores, lazy="select", backref="usuarios")
    categorias_profissionais = db.relationship(
        "CategoriaProfissional",
        secondary=user_categorias_profissionais,
        lazy="select",
        backref="usuarios",
    )
    tipos_notificacao = db.relationship(
        "TipoNotificacao",
        secondary=user_tipos_notificacao,
        lazy="select",
        backref="usuarios",
    )
    tipos_ocorrencia = db.relationship(
        "TipoOcorrencia",
        secondary=user_tipos_ocorrencia,
        lazy="select",
        backref="usuarios",
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def acesso_total(self):
        """Vê todas as notificações e os dados do notificante (antes: perfil administrador)."""
        return bool(self.perfil and self.perfil.ativo and self.perfil.acesso_total)

    def can_access(self, tela):
        """A página (chave de TELAS) está liberada no perfil do usuário."""
        return bool(self.perfil and self.perfil.ativo and self.perfil.tem_tela(tela))

    def tipos_ocorrencia_permitidos(self):
        return sorted([t.nome for t in self.tipos_ocorrencia if t.ativa])

    def setores_permitidos(self):
        return sorted([s.nome for s in self.setores if s.ativa])

    def categorias_profissionais_permitidas(self):
        return sorted([c.nome for c in self.categorias_profissionais if c.ativa])

    def __repr__(self):
        return f"<User {self.username} ({self.perfil.nome if self.perfil else 'sem perfil'})>"


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)

    unidade_hospitalar = db.Column(db.String(150), nullable=False)
    setor = db.Column(db.String(120), nullable=False)
    setor_notificador = db.Column(db.String(120), nullable=False)
    local_evento = db.Column(db.String(120), nullable=False)
    categoria_profissional = db.Column(db.String(120), nullable=False)
    tipo_notificacao = db.Column(db.String(120), nullable=False, default="Não Conformidade")
    tipo_ocorrencia = db.Column(db.String(120), nullable=False)
    gravidade = db.Column(db.String(20), nullable=False, default="Baixa")
    data_ocorrencia = db.Column(db.Date, nullable=False)
    horario_notificacao = db.Column(db.Time, nullable=False)
    descricao = db.Column(db.Text, nullable=False)
    envolve_paciente = db.Column(db.Boolean, default=False, nullable=False)
    grau_dano = db.Column(db.String(40), nullable=True)
    paciente_prontuario = db.Column(db.String(50), nullable=True)
    # Mesmo tamanho de t_paciente.dsc_nome, de onde o nome é buscado.
    paciente_nome = db.Column(db.String(500), nullable=True)
    paciente_data_nascimento = db.Column(db.Date, nullable=True)
    paciente_nome_mae = db.Column(db.String(500), nullable=True)
    # 11 dígitos quando válido; dados antigos da t_paciente sem padrão ficam como vieram.
    paciente_cpf = db.Column(db.String(50), nullable=True)

    # Dados do produto, preenchidos quando o tipo de ocorrência exige (ex.: equipamento médico-hospitalar).
    produto_nome = db.Column(db.String(255), nullable=True)
    produto_cod_seplag = db.Column(db.String(50), nullable=True)
    produto_lote = db.Column(db.String(100), nullable=True)
    produto_fabricante = db.Column(db.String(255), nullable=True)
    produto_fornecedor = db.Column(db.String(255), nullable=True)
    # Chave lote.lote do banco de materiais, quando o lote foi escolhido na consulta.
    produto_lote_estoque_id = db.Column(db.BigInteger, nullable=True)
    # Chave produto.produto do banco de materiais, quando o produto foi escolhido pelo nome.
    produto_estoque_id = db.Column(db.BigInteger, nullable=True)
    produto_data_fabricacao = db.Column(db.Date, nullable=True)
    produto_validade = db.Column(db.Date, nullable=True)
    produto_registro_anvisa = db.Column(db.String(50), nullable=True)
    envio_amostra = db.Column(db.String(255), nullable=True)

    # Dados da hemotransfusão, preenchidos quando o tipo de ocorrência abre essa seção.
    hemo_tipo_evento = db.Column(db.String(120), nullable=True)
    hemo_hemocomponente = db.Column(db.String(30), nullable=True)
    hemo_alteracao_sinais_vitais = db.Column(db.Boolean, nullable=True)
    hemo_manifestacoes_clinicas = db.Column(db.Boolean, nullable=True)
    hemo_comunicacao_banco_sangue = db.Column(db.Boolean, nullable=True)
    hemo_bolsa_recolhida = db.Column(db.Boolean, nullable=True)

    # Dados da medicação, preenchidos quando o tipo de ocorrência abre essa seção.
    med_nome = db.Column(db.String(255), nullable=True)
    med_apresentacao = db.Column(db.String(255), nullable=True)
    med_posologia = db.Column(db.String(255), nullable=True)
    # Só quando houve dano ao paciente.
    med_incidente = db.Column(db.String(60), nullable=True)

    # Dados da biovigilância (material biológico), quando o tipo de ocorrência abre essa seção.
    bio_tipo_transplante = db.Column(db.String(30), nullable=True)
    bio_tipo_reacao = db.Column(db.String(60), nullable=True)

    notificante_nome = db.Column(db.String(150), nullable=True)
    notificante_contato = db.Column(db.String(120), nullable=True)
    imagem_path = db.Column(db.String(255), nullable=True)
    anonimo = db.Column(db.Boolean, default=False, nullable=False)

    status = db.Column(db.String(30), nullable=False, default="Pendente")
    responsavel_id = db.Column(db.Integer, db.ForeignKey("responsaveis_notificacao.id"), nullable=True)
    responsavel = db.relationship("ResponsavelNotificacao", foreign_keys=[responsavel_id])
    status_anvisa = db.Column(db.String(20), nullable=False, default="Não Notificado")
    numero_notivisa = db.Column(db.String(120), nullable=True)
    numero_vigimed = db.Column(db.String(120), nullable=True)
    observacao_tratamento = db.Column(db.Text, nullable=True)
    tratado_por_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    tratado_por = db.relationship("User", foreign_keys=[tratado_por_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Notification {self.id} - {self.unidade_hospitalar}/{self.setor_notificador}>"
