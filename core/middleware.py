from django.conf import settings
from django.http import HttpResponse
from django.utils.html import escape


class ConfiguracaoPendente:
    """Falta uma variável de ambiente obrigatória em produção: em vez de um
    erro 500 sem explicação, o site diz o que falta configurar."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.pendencias = list(getattr(settings, "CONFIG_PENDENTE", []))

    def __call__(self, request):
        if not self.pendencias or settings.DEBUG:
            return self.get_response(request)
        itens = "".join(f"<li>{escape(p)}</li>" for p in self.pendencias)
        return HttpResponse(
            "<!doctype html><meta charset='utf-8'><title>Configuração pendente</title>"
            "<body style='font-family:system-ui;max-width:640px;margin:12vh auto;padding:0 20px;line-height:1.5'>"
            "<h1>Falta configurar o sistema</h1>"
            "<p>Na Vercel, abra <b>Settings &gt; Environment Variables</b>, ajuste o que está abaixo "
            f"e faça um <b>Redeploy</b>:</p><ul>{itens}</ul></body>",
            status=503,
        )
