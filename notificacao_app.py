from flask import Flask, flash, request, session, url_for
from flask_login import current_user, logout_user
from sqlalchemy.exc import OperationalError

from config import Config
from config_store import carregar_configuracao
from database_setup import chave_conexao, inicializar_banco, sincronizar_conexao_notificacoes
from extensions import db, login_manager
from models import User
from auth_routes import auth_bp
from main_routes import main_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @app.context_processor
    def inject_user():
        return {"current_user": current_user}

    @app.before_request
    def aplicar_conexao_configurada():
        if request.endpoint == "static":
            return
        if sincronizar_conexao_notificacoes(app):
            db.session.remove()

        # O usuário logado pertence ao banco em que fez login; se o banco mudou,
        # o mesmo id pode ser outra pessoa no banco novo.
        chave = chave_conexao(app)
        chave_sessao = session.get("conexao_banco")
        if chave_sessao is None:
            session["conexao_banco"] = chave
        elif chave_sessao != chave:
            session["conexao_banco"] = chave
            if current_user.is_authenticated:
                logout_user()
                flash("O banco de dados do sistema foi alterado. Entre novamente.", "info")

    @app.context_processor
    def inject_logos():
        logos = carregar_configuracao()["logos"]
        return {
            "logo_principal_url": url_for("static", filename=logos["principal"]),
            "logo_transicao_url": url_for("static", filename=logos["transicao"]),
        }

    with app.app_context():
        try:
            inicializar_banco(app)
        except OperationalError as exc:
            database_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
            if isinstance(database_uri, str) and database_uri.startswith("sqlite"):
                raise RuntimeError(
                    "Falha ao inicializar o banco SQLite. "
                    "Verifique permissao de escrita no arquivo do banco e no diretorio do projeto."
                ) from exc
            db_user = app.config.get("DB_USER", "(não definido)")
            db_host = app.config.get("DB_HOST", "(não definido)")
            db_port = app.config.get("DB_PORT", "(não definido)")
            db_name = app.config.get("DB_NAME", "(não definido)")
            db_schema = app.config.get("DB_SCHEMA", "(não definido)")
            raise RuntimeError(
                "Falha ao conectar no PostgreSQL. "
                f"Verifique a tela de Configurações (instance/configuracao_sistema.json) "
                f"ou DB_USER/DB_PASSWORD/DB_HOST/DB_PORT/DB_NAME no .env. "
                f"Conexão atual: user={db_user}, host={db_host}, port={db_port}, db={db_name}, schema={db_schema}."
            ) from exc

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=6001)
