# Sistema de Notificação de Não Conformidade Hospitalar

Sistema de notificação de não conformidade integrado com o sistema Integrash, para dados de paciente, e com o Guardião, para dados de produtos.

Sistema web (Python/Flask + HTML5/CSS3 + PostgreSQL) para notificação, análise e
gestão de não conformidades hospitalares, com perfis de acesso e dashboard de
indicadores.

## Estrutura de pastas

```
notificacao_sistema/
├── app.py                  # Fábrica da aplicação Flask, cria tabelas e usuário admin
├── config.py                # Configurações (SECRET_KEY, conexão PostgreSQL)
├── extensions.py             # Instâncias do SQLAlchemy e do LoginManager
├── models.py                 # Modelos User e Notification, perfis e permissões
├── utils.py                  # Decorator role_required (controle de acesso por tela)
├── auth_routes.py            # Rotas de login/logout
├── main_routes.py            # Rotas: home, menu, cadastro de usuários,
│                              #        conferir/gerenciar notificações, dashboard
├── requirements.txt
├── schema.sql                 # Script SQL de referência (opcional, manual)
├── .env.example
├── static/
│   ├── css/style.css          # Estilos com a paleta de cores do hospital
│   ├── js/main.js             # Animação de transição entre telas (logo)
│   └── img/                   # (reservado para imagens adicionais)
└── templates/
    ├── base.html               # Layout base (topbar, overlay de transição, flashes)
    ├── index.html               # Página inicial: formulário de não conformidade
    ├── login.html                # Tela de login
    ├── menu.html                 # Menu principal (botões por perfil)
    ├── cadastro_usuario.html      # Cadastro de usuários (somente Administrador)
    ├── notificacoes.html          # Conferir notificações (todos os perfis)
    ├── gerenciar.html              # Gerenciar/tratar notificações (Admin/Supervisor)
    └── dashboard.html               # Dashboard com gráficos (Admin/Supervisor)
```

## Perfis de acesso

| Perfil        | Cadastro de Usuários | Conferir Notificações | Gerenciar Notificações | Dashboard |
|---------------|:--------------------:|:---------------------:|:----------------------:|:---------:|
| Administrador | ✅                   | ✅                    | ✅                     | ✅        |
| Supervisor    | ❌                   | ✅                    | ✅                     | ✅        |
| Operador      | ❌                   | ✅                    | ❌                     | ❌        |

Um usuário administrador interno é criado automaticamente na primeira execução:
- **Usuário:** `admin`
- **Senha:** `admin`

> Altere essa senha (ou as variáveis `ADMIN_USERNAME`/`ADMIN_PASSWORD`) antes de
> colocar o sistema em produção.

## Pré-requisitos

- Debian 12+ (ou derivado compatível)
- Python 3.10+
- PostgreSQL 13+ em execução

## Instalação

```bash
# 1. Instalar dependências de sistema (Debian)
sudo apt update
sudo apt install -y python3 python3-full python3-venv python3-pip postgresql postgresql-contrib

# 2. Garantir que o PostgreSQL esteja ativo
sudo systemctl enable --now postgresql

# 3. Criar a pasta do ambiente virtual
mkdir -p .venv

# 4. Criar e ativar um ambiente virtual
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 5. Atualizar o pip e instalar dependências Python
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --prefer-binary -r requirements.txt

# 6. Criar senha do usuário postgres e banco de dados
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'sua_senha_aqui';"
sudo -u postgres psql -c "CREATE DATABASE hospital_notificacoes;"

# 7. Configurar variáveis de ambiente
cp .env.example .env
# edite o arquivo .env com as credenciais do seu PostgreSQL (DB_PASSWORD=sua_senha_aqui)

# 8. Testar conexão com o banco (deve retornar sem erro)
PGPASSWORD='sua_senha_aqui' psql -h localhost -U postgres -d hospital_notificacoes -c '\q'

# 9. Executar a aplicação
.venv/bin/python app.py
```

### Observações importantes para Debian

- Se aparecer `Comando 'python' não encontrado`, use sempre `python3` ou `.venv/bin/python`.
- Se aparecer `externally-managed-environment`, o `pip` está sendo executado fora do ambiente virtual. Refaça os comandos da etapa 3 e execute apenas `.venv/bin/python -m pip ...`.
- Se o `psycopg2-binary==2.9.9` tentar compilar no Python 3.13, a instalação falha por incompatibilidade dessa versão com o Python 3.13. Por isso este projeto usa `psycopg2-binary>=2.9` com `--prefer-binary`, permitindo ao pip baixar uma wheel compatível.
- Não use `--break-system-packages` neste projeto.

Ao iniciar, o Flask cria automaticamente as tabelas (`db.create_all()`) e o
usuário administrador padrão. A aplicação ficará disponível em:
`http://localhost:5000`

## Fluxo do sistema

1. **Página inicial (`/`)** — qualquer pessoa pode preencher o formulário de
   notificação de não conformidade (com opção de notificação anônima). Um
   botão **Login** no canto superior direito leva à tela de autenticação.
2. **Login (`/login`)** — autenticação de usuários cadastrados.
3. **Menu (`/menu`)** — exibe apenas os botões permitidos para o perfil do
   usuário logado: Cadastro de Usuários, Conferir Notificações, Gerenciar
   Notificações e Dashboard.
4. **Cadastro de Usuários (`/usuarios`)** — apenas Administrador. Cria novos
   usuários com perfil Administrador, Supervisor ou Operador.
5. **Conferir Notificações (`/notificacoes`)** — todos os perfis. Lista e
   filtra as notificações registradas.
6. **Gerenciar Notificações (`/gerenciar`)** — Administrador e Supervisor.
   Permite atualizar status (Pendente / Em Análise / Resolvida) e registrar
   observações de tratamento.
7. **Dashboard (`/dashboard`)** — Administrador e Supervisor. Indicadores
   (KPIs) e gráficos (Chart.js) por mês, setor e gravidade.

## Animação entre telas

O arquivo `static/js/main.js` intercepta a navegação por links e formulários
internos, exibindo por instantes um overlay com a logo do hospital
(`#page-transition` em `style.css`) antes de carregar a próxima página,
criando uma transição suave e consistente em todo o sistema.

## Paleta de cores utilizada

| Cor            | Hex        |
|-----------------|------------|
| Teal            | `#00A1A2`  |
| Teal 2          | `#3BB5A3`  |
| Cyan            | `#00B2C9`  |
| Verde escuro    | `#007C6B`  |
| Laranja         | `#EB600A`  |
| Laranja claro   | `#F3A133`  |
| Cinza escuro    | `#38464D`  |
| Azul escuro     | `#2E758C`  |
| Verde 2         | `#0F9688`  |
| Laranja 2       | `#F7975B`  |
| Cyan 2          | `#53B7D9`  |

## Segurança

- Senhas armazenadas com hash (Werkzeug `generate_password_hash`).
- Controle de sessão via Flask-Login.
- Controle de acesso por tela através do decorator `role_required`
  (`utils.py`), baseado no dicionário `MENU_PERMISSIONS` em `models.py`.
