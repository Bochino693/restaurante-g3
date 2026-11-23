"""O horário de funcionamento, num lugar só.

Nos dias de LOJA_DIAS, das LOJA_ABRE às LOJA_FECHA (settings). Vale para o
servidor, o cardápio do cliente e o painel.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

NOMES_DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _dias():
    return tuple(getattr(settings, "LOJA_DIAS", (0, 1, 2, 3, 4)))


def _vira_a_noite():
    return settings.LOJA_FECHA <= settings.LOJA_ABRE


def loja_aberta(agora=None):
    agora = agora or timezone.localtime()
    hora, dia = agora.hour, agora.weekday()
    if not _vira_a_noite():
        return dia in _dias() and settings.LOJA_ABRE <= hora < settings.LOJA_FECHA
    # Fecha depois da meia-noite: a madrugada pertence ao dia anterior.
    if hora >= settings.LOJA_ABRE:
        return dia in _dias()
    return hora < settings.LOJA_FECHA and (dia - 1) % 7 in _dias()


def proxima_abertura(agora=None):
    """Quando a loja abre de novo, a partir de `agora` (se já estiver aberta,
    devolve a abertura de hoje)."""
    agora = agora or timezone.localtime()
    dia = agora.replace(hour=settings.LOJA_ABRE, minute=0, second=0, microsecond=0)
    if loja_aberta(agora) or not _dias():
        return dia
    if agora.hour >= settings.LOJA_ABRE or agora.weekday() not in _dias():
        dia += timedelta(days=1)
    while dia.weekday() not in _dias():
        dia += timedelta(days=1)
    return dia


def texto_dias():
    dias = _dias()
    if dias == (0, 1, 2, 3, 4):
        return "segunda a sexta"
    if dias == (0, 1, 2, 3, 4, 5):
        return "segunda a sábado"
    if len(dias) == 7:
        return "todos os dias"
    return ", ".join(NOMES_DIAS[d] for d in dias)


def status_da_loja(agora=None):
    """Tudo o que a tela precisa dizer sobre o horário."""
    agora = agora or timezone.localtime()
    aberta = loja_aberta(agora)
    horario = f"{texto_dias()}, das {settings.LOJA_ABRE}h às {settings.LOJA_FECHA}h"
    if aberta:
        detalhe = f"até {settings.LOJA_FECHA}h"
    else:
        abre = proxima_abertura(agora)
        if abre.date() == agora.date():
            quando = "hoje"
        elif abre.date() == (agora + timedelta(days=1)).date():
            quando = "amanhã"
        else:
            quando = NOMES_DIAS[abre.weekday()]
        detalhe = f"abre {quando} às {settings.LOJA_ABRE}h"
    return {
        "aberta": aberta,
        "detalhe": detalhe,
        "horario": horario,
        "abre": settings.LOJA_ABRE,
        "fecha": settings.LOJA_FECHA,
        "dias": list(_dias()),
    }
