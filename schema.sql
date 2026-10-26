-- Script de referência (o Flask/SQLAlchemy já cria estas tabelas
-- automaticamente via db.create_all() ao iniciar a aplicação).
-- Use este script apenas se preferir criar o schema manualmente.

-- Perfis de acesso: telas = chaves das páginas do menu separadas por vírgula (models.TELAS).
CREATE TABLE IF NOT EXISTS perfis (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(80) UNIQUE NOT NULL,
    telas TEXT NOT NULL DEFAULT '',
    acesso_total BOOLEAN NOT NULL DEFAULT FALSE,  -- vê todas as notificações e os dados do notificante
    sistema BOOLEAN NOT NULL DEFAULT FALSE,       -- Administrador: todas as páginas, não pode ser inativado
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO perfis (nome, telas, acesso_total, sistema)
VALUES
    ('Administrador', 'usuarios,perfis,setores,categorias_profissionais,tipos_ocorrencia,notificacoes,gerenciar,dashboard,configuracoes', TRUE, TRUE),
    ('Supervisor', 'notificacoes,gerenciar,dashboard', FALSE, FALSE),
    ('Operador', 'notificacoes', FALSE, FALSE)
ON CONFLICT (nome) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) UNIQUE NOT NULL,
    full_name VARCHAR(150) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    perfil_id INTEGER REFERENCES perfis(id),
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- id = isn_clinica para setores importados da t_clinica; manuais a partir de 1001.
CREATE TABLE IF NOT EXISTS setores (
    id INTEGER PRIMARY KEY,
    nome VARCHAR(150) UNIQUE NOT NULL,
    ativa BOOLEAN NOT NULL DEFAULT TRUE,
    origem VARCHAR(20) NOT NULL DEFAULT 'manual',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_setores (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    setor_id INTEGER NOT NULL REFERENCES setores(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, setor_id)
);

CREATE TABLE IF NOT EXISTS categorias_profissionais (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(150) UNIQUE NOT NULL,
    ativa BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_categorias_profissionais (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    categoria_id INTEGER NOT NULL REFERENCES categorias_profissionais(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, categoria_id)
);

CREATE TABLE IF NOT EXISTS tipos_notificacao (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(150) UNIQUE NOT NULL,
    ativa BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_tipos_notificacao (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tipo_notificacao_id INTEGER NOT NULL REFERENCES tipos_notificacao(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, tipo_notificacao_id)
);

CREATE TABLE IF NOT EXISTS tipos_ocorrencia (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(150) UNIQUE NOT NULL,
    ativa BOOLEAN NOT NULL DEFAULT TRUE,
    secao_adicional VARCHAR(30),  -- 'produto', 'saneantes', 'hemotransfusao', 'medicacao', 'biovigilancia' ou NULL
    exige_paciente BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_tipos_ocorrencia (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tipo_ocorrencia_id INTEGER NOT NULL REFERENCES tipos_ocorrencia(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, tipo_ocorrencia_id)
);

CREATE TABLE IF NOT EXISTS responsaveis_notificacao (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(150) UNIQUE NOT NULL,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    unidade_hospitalar VARCHAR(150) NOT NULL,
    setor VARCHAR(120) NOT NULL,
    setor_notificador VARCHAR(120) NOT NULL,
    local_evento VARCHAR(120) NOT NULL,
    categoria_profissional VARCHAR(120) NOT NULL,
    tipo_notificacao VARCHAR(120) NOT NULL DEFAULT 'Não Conformidade',
    tipo_ocorrencia VARCHAR(120) NOT NULL,
    gravidade VARCHAR(20) NOT NULL DEFAULT 'Baixa',
    data_ocorrencia DATE NOT NULL,
    horario_notificacao TIME NOT NULL,
    descricao TEXT NOT NULL,
    envolve_paciente BOOLEAN NOT NULL DEFAULT FALSE,
    grau_dano VARCHAR(40),
    paciente_prontuario VARCHAR(50),
    paciente_nome VARCHAR(500),
    paciente_data_nascimento DATE,
    paciente_nome_mae VARCHAR(500),
    paciente_cpf VARCHAR(50),
    produto_nome VARCHAR(255),
    produto_cod_seplag VARCHAR(50),
    produto_lote VARCHAR(100),
    produto_fabricante VARCHAR(255),
    produto_fornecedor VARCHAR(255),
    produto_lote_estoque_id BIGINT,
    produto_estoque_id BIGINT,
    produto_data_fabricacao DATE,
    produto_validade DATE,
    produto_registro_anvisa VARCHAR(50),
    envio_amostra VARCHAR(255),
    hemo_tipo_evento VARCHAR(120),
    hemo_hemocomponente VARCHAR(30),
    hemo_alteracao_sinais_vitais BOOLEAN,
    hemo_manifestacoes_clinicas BOOLEAN,
    hemo_comunicacao_banco_sangue BOOLEAN,
    hemo_bolsa_recolhida BOOLEAN,
    med_nome VARCHAR(255),
    med_apresentacao VARCHAR(255),
    med_posologia VARCHAR(255),
    med_incidente VARCHAR(60),
    bio_tipo_transplante VARCHAR(30),
    bio_tipo_reacao VARCHAR(60),
    notificante_nome VARCHAR(150),
    notificante_contato VARCHAR(120),
    imagem_path VARCHAR(255),
    anonimo BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(30) NOT NULL DEFAULT 'Pendente',
    responsavel_id INTEGER REFERENCES responsaveis_notificacao(id),
    status_anvisa VARCHAR(20) NOT NULL DEFAULT 'Não Notificado',
    numero_notivisa VARCHAR(120),
    numero_vigimed VARCHAR(120),
    observacao_tratamento TEXT,
    tratado_por_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Usuário administrador padrão (senha 'admin' com hash gerado pelo Werkzeug)
-- É criado automaticamente pela aplicação em app.py -> criar_admin_padrao()

-- Setores não têm carga inicial: vêm da t_clinica (sincronização do sistema).

INSERT INTO categorias_profissionais (nome, ativa)
VALUES
    ('Enfermagem', TRUE),
    ('Medicina', TRUE),
    ('Farmácia', TRUE),
    ('Fisioterapia', TRUE),
    ('Nutrição', TRUE),
    ('Serviço Social', TRUE),
    ('Psicologia', TRUE),
    ('Administrativo', TRUE),
    ('Higienização', TRUE),
    ('Outro', TRUE)
ON CONFLICT (nome) DO NOTHING;

INSERT INTO tipos_notificacao (nome, ativa)
VALUES
    ('Tecnovigilância', TRUE),
    ('Hemovigilância', TRUE),
    ('Farmacovigilância', TRUE),
    ('Não Conformidade', TRUE),
    ('Biovigilância', TRUE),
    ('Saneantes', TRUE),
    ('Nutrivigilância', TRUE)
ON CONFLICT (nome) DO NOTHING;

INSERT INTO tipos_ocorrencia (nome, ativa)
VALUES
    ('Medicação', TRUE),
    ('Queda de Paciente', TRUE),
    ('Falha de Identificação', TRUE),
    ('Infecção Relacionada à Assistência', TRUE),
    ('Falha em Equipamento', TRUE),
    ('Falha em Processo Administrativo', TRUE),
    ('Outro', TRUE)
ON CONFLICT (nome) DO NOTHING;

INSERT INTO responsaveis_notificacao (nome, ativo)
VALUES
    ('Gerência de Risco', TRUE),
    ('Segurança do Paciente', TRUE),
    ('Escritório da Qualidade', TRUE)
ON CONFLICT (nome) DO NOTHING;
