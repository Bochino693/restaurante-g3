"""Primeira instalação pelo navegador: /instalar/

A Vercel não roda `migrate`. Esta página cria as tabelas no banco e o
primeiro administrador, sem precisar de terminal. Regras:

- criar as tabelas: liberado enquanto não existe nenhum usuário; depois,
  só para administrador logado (atualizações que tragam migration nova);
- criar administrador: só enquanto não existe nenhum usuário.

Não usa templates nem o contexto global, que dependem das tabelas.
"""
import logging

from django.contrib.auth import get_user_model, login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.utils.html import escape
from django.views.decorators.cache import never_cache

logger = logging.getLogger(__name__)


def _estado():
    """(erro de conexão, nº de migrations pendentes, existe usuário?)"""
    conexao = connections[DEFAULT_DB_ALIAS]
    try:
        conexao.ensure_connection()
        executor = MigrationExecutor(conexao)
        pendentes = len(executor.migration_plan(executor.loader.graph.leaf_nodes()))
        tabelas = conexao.introspection.table_names()
    except Exception as erro:   # banco fora do ar, senha errada, host errado
        logger.exception("Instalação: falha ao falar com o banco")
        return f"{type(erro).__name__}: {erro}", 0, False
    Usuario = get_user_model()
    tem_usuario = Usuario._meta.db_table in tabelas and Usuario.objects.exists()
    return "", pendentes, tem_usuario


def _pagina(request, corpo, status=200):
    return HttpResponse(
        "<!doctype html><html lang='pt-br'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Instalação · G3</title><style>"
        "body{font-family:system-ui,sans-serif;background:#f7f4ef;color:#1f1b18;margin:0;padding:24px}"
        ".c{max-width:520px;margin:6vh auto;background:#fff;border-radius:24px;padding:28px;"
        "box-shadow:0 12px 34px rgba(60,40,20,.1)}h1{font-size:22px;margin:0 0 6px}"
        "p{line-height:1.5;color:#4a433d}label{display:block;font-size:12px;font-weight:800;"
        "text-transform:uppercase;letter-spacing:.06em;color:#7a7068;margin:14px 0 5px}"
        "input{width:100%;box-sizing:border-box;padding:13px;border-radius:12px;border:1px solid #e8e1d6;"
        "font-size:16px}button,a.b{display:block;width:100%;box-sizing:border-box;margin-top:18px;padding:15px;"
        "border:0;border-radius:14px;background:#e3353d;color:#fff;font-size:16px;font-weight:800;"
        "text-align:center;text-decoration:none;cursor:pointer}.e{background:#fee2e2;color:#b91c1c;"
        "padding:12px;border-radius:12px;font-weight:600;word-break:break-word}"
        ".ok{background:#dcfce7;color:#15803d;padding:12px;border-radius:12px;font-weight:700}"
        ".s{display:inline-block;background:#e3353d;color:#fff;font-weight:900;border-radius:12px;"
        "padding:8px 11px;margin-bottom:14px}</style>"
        f"<div class='c'><span class='s'>G3</span>{corpo}</div></html>",
        status=status,
    )


def _formulario(request, botao, campos=""):
    return (
        f"<form method='post'><input type='hidden' name='csrfmiddlewaretoken' value='{get_token(request)}'>"
        f"{campos}<button type='submit'>{botao}</button></form>"
    )


@never_cache
def instalar(request):
    erro, pendentes, tem_usuario = _estado()
    if erro:
        return _pagina(request, (
            "<h1>Não consegui falar com o banco</h1>"
            "<p>Confira a variável <b>DATABASE_URL</b> na Vercel (senha certa, sem colchetes) "
            "e faça um Redeploy.</p>"
            f"<p class='e'>{escape(erro)[:600]}</p>"
        ), status=503)

    admin_logado = tem_usuario and request.user.is_authenticated and request.user.is_superuser
    aviso = ""

    # ---- 1. tabelas
    if pendentes:
        if tem_usuario and not admin_logado:
            return _pagina(request, (
                "<h1>Atualização do banco pendente</h1>"
                "<p>Entre como administrador e volte a esta página para aplicar.</p>"
                "<a class='b' href='/login/'>Entrar</a>"
            ))
        if request.method == "POST":
            try:
                call_command("migrate", interactive=False, verbosity=0)
            except Exception as falha:
                logger.exception("Instalação: migrate falhou")
                return _pagina(request, (
                    "<h1>Não deu para preparar o banco</h1>"
                    f"<p class='e'>{escape(f'{type(falha).__name__}: {falha}')[:600]}</p>"
                    + _formulario(request, "Tentar de novo")
                ), status=500)
            return HttpResponseRedirect("/instalar/")
        return _pagina(request, (
            "<h1>Preparar o banco de dados</h1>"
            f"<p>O banco respondeu. Falta criar as tabelas do sistema ({pendentes} passo"
            f"{'' if pendentes == 1 else 's'}). Pode levar alguns segundos.</p>"
            + _formulario(request, "Criar as tabelas")
        ))

    # ---- 2. primeiro administrador
    if not tem_usuario:
        usuario = (request.POST.get("usuario") or "").strip()
        if request.method == "POST":
            senha = request.POST.get("senha") or ""
            try:
                if not usuario:
                    raise ValidationError("Escolha um nome de usuário.")
                if senha != (request.POST.get("senha2") or ""):
                    raise ValidationError("As duas senhas não são iguais.")
                validate_password(senha)
                novo = get_user_model().objects.create_superuser(username=usuario, email="", password=senha)
                login(request, novo, backend="django.contrib.auth.backends.ModelBackend")
                return HttpResponseRedirect("/configuracoes/")
            except ValidationError as falha:
                aviso = f"<p class='e'>{escape(' '.join(falha.messages))}</p>"
        return _pagina(request, (
            "<h1>Criar o administrador</h1>"
            "<p>As tabelas estão prontas. Crie o primeiro usuário, que vai mandar no sistema.</p>"
            + aviso + _formulario(request, "Criar e entrar", (
                f"<label>Usuário</label><input name='usuario' value='{escape(usuario)}' autocomplete='username' required>"
                "<label>Senha (8 ou mais, não só números)</label>"
                "<input name='senha' type='password' autocomplete='new-password' required>"
                "<label>Repita a senha</label><input name='senha2' type='password' autocomplete='new-password' required>"
            ))
        ))

    # ---- 3. tudo pronto
    return _pagina(request, (
        "<h1>Sistema instalado</h1><p class='ok'>Banco pronto e administrador criado.</p>"
        "<a class='b' href='/login/'>Ir para o sistema</a>"
    ))
