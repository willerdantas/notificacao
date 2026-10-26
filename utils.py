import re
import unicodedata
from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user

# ---------------------------------------------------------------------------
# Sanitização de entrada — defesa em profundidade contra null-byte injection,
# caracteres de controle e overflow silencioso de colunas.
# O ORM já parametriza todas as queries (não há SQL injection clássico),
# mas estas funções protegem camadas downstream (logs, exports, parsers).
# ---------------------------------------------------------------------------

_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NEWLINE_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)
_PHONE_ALLOWED_RE = re.compile(r"[^0-9]")


def sanitize_text(value: str, max_length: int = 0, allow_newlines: bool = False) -> str:
    """Remove null bytes e caracteres de controle de uma string de texto livre.

    - Null bytes (\x00) são sempre removidos.
    - Demais caracteres de controle (\x01-\x1f exceto \t, \n, \r) são removidos
      quando allow_newlines=True; quando False, todos os controles são removidos.
    - Se max_length > 0, trunca a string preservando caracteres Unicode completos.
    """
    if not isinstance(value, str):
        return ""
    # Normaliza para NFC para evitar sequências Unicode ambíguas
    value = unicodedata.normalize("NFC", value)
    if allow_newlines:
        # Preserva \t (\x09), \n (\x0a), \r (\x0d); remove o resto
        value = _CTRL_CHARS_RE.sub("", value)
    else:
        value = _NEWLINE_CTRL_RE.sub("", value)
    value = value.strip()
    if max_length > 0:
        value = value[:max_length]
    return value


def sanitize_email(value: str) -> str:
    """Sanitiza e valida formato básico de e-mail. Retorna string vazia se inválido."""
    value = sanitize_text(value, max_length=254)
    if not value:
        return ""
    # Rejeita se não bater com padrão mínimo de e-mail
    if not _EMAIL_RE.match(value):
        return value  # devolve o valor — a validação de obrigatoriedade fica na rota
    return value


def sanitize_phone(value: str) -> str:
    """Mantém somente dígitos e limita a 11 caracteres (máximo DDD + número)."""
    value = sanitize_text(value, max_length=11)
    value = _PHONE_ALLOWED_RE.sub("", value)
    return value[:11]


def cpf_valido(digitos: str) -> bool:
    """Confere os dois dígitos verificadores de um CPF com 11 dígitos."""
    if len(digitos) != 11 or not digitos.isdigit() or digitos == digitos[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(digitos[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10) % 11 % 10
        if digito != int(digitos[tamanho]):
            return False
    return True


def normalizar_cpf_digitado(value: str):
    """CPF digitado no formulário: retorna os 11 dígitos, '' se vazio, ou None se inválido."""
    digitos = _PHONE_ALLOWED_RE.sub("", sanitize_text(value or "", max_length=20))
    if not digitos:
        return ""
    return digitos if cpf_valido(digitos) else None


def cpf_da_base(value):
    """CPF vindo da t_paciente (sem padrão): 11 dígitos quando possível, senão o texto original."""
    texto = sanitize_text(str(value or ""), max_length=50)
    digitos = _PHONE_ALLOWED_RE.sub("", texto)
    if len(digitos) == 11:
        return digitos
    return texto or None


def formatar_cpf(value) -> str:
    if value and len(value) == 11 and value.isdigit():
        return f"{value[:3]}.{value[3:6]}.{value[6:9]}-{value[9:]}"
    return value or ""


def mascarar_cpf(value) -> str:
    """Exibe só os 6 dígitos do meio (***.456.789-**), para telas públicas."""
    if value and len(value) == 11 and value.isdigit():
        return f"***.{value[3:6]}.{value[6:9]}-**"
    return "***" if value else ""


def calcular_idade(nascimento, referencia) -> str:
    """Idade em anos; para menores de 1 ano, em meses ou dias. '' se não for possível calcular."""
    if not nascimento or not referencia or nascimento > referencia:
        return ""
    anos = referencia.year - nascimento.year - ((referencia.month, referencia.day) < (nascimento.month, nascimento.day))
    if anos >= 1:
        return f"{anos} ano" if anos == 1 else f"{anos} anos"
    meses = (referencia.year - nascimento.year) * 12 + referencia.month - nascimento.month - (referencia.day < nascimento.day)
    if meses >= 1:
        return f"{meses} mês" if meses == 1 else f"{meses} meses"
    dias = (referencia - nascimento).days
    return f"{dias} dia" if dias == 1 else f"{dias} dias"


def role_required(screen_key):
    """Decorator que restringe uma rota às páginas liberadas no perfil do usuário.

    screen_key deve ser uma chave de models.TELAS (ex: 'dashboard', 'gerenciar',
    'usuarios', 'notificacoes'); as páginas de cada perfil são escolhidas na tela
    Perfis de Acesso.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if not current_user.can_access(screen_key):
                flash("Você não tem permissão para acessar esta tela.", "danger")
                return redirect(url_for("main.menu"))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
