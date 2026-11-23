"""Estoque: toda entrada e saída passa por aqui.

O saldo mora no produto (estoque_atual) e cada mudança deixa um
MovimentoEstoque, com o saldo que ficou. A dose não tem saldo próprio: tira
uma fração da garrafa (Produtos.alvo_estoque).
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import F

from .models import MovimentoEstoque, Produtos

MILESIMO = Decimal("0.001")


def _q(valor):
    return Decimal(str(valor)).quantize(MILESIMO, rounding=ROUND_HALF_UP)


def movimentar(produto, quantidade, tipo, *, pedido=None, usuario="", observacao="",
               custo_unitario=None, validade=None):
    """Soma `quantidade` (negativa = saída) ao saldo do produto e registra."""
    quantidade = _q(quantidade)
    if not quantidade:
        return None
    with transaction.atomic():
        Produtos.objects.filter(pk=produto.pk).update(estoque_atual=F("estoque_atual") + quantidade)
        saldo = Produtos.objects.filter(pk=produto.pk).values_list("estoque_atual", flat=True).get()
        produto.estoque_atual = saldo
        return MovimentoEstoque.objects.create(
            produto=produto, tipo=tipo, quantidade=quantidade, saldo_apos=saldo,
            custo_unitario=custo_unitario, validade=validade,
            observacao=(observacao or "")[:200], pedido=pedido, usuario=(usuario or "")[:150],
        )


def baixar_venda(pedido, itens, usuario=""):
    """itens: [(produto, quantidade vendida)]. Junta por produto com saldo."""
    saidas = {}
    for produto, quantidade in itens:
        alvo, fator = produto.alvo_estoque()
        if alvo is None:
            continue
        registro = saidas.setdefault(alvo.pk, [alvo, Decimal(0)])
        registro[1] += fator * quantidade
    for alvo, total in saidas.values():
        movimentar(alvo, -total, MovimentoEstoque.Tipo.VENDA, pedido=pedido, usuario=usuario,
                   observacao=f"Pedido #{pedido.pk}")


def estornar_pedido(pedido, usuario=""):
    """Devolve ao estoque o que o pedido tirou (pedido cancelado)."""
    saldos = {}
    for mov in pedido.movimentos_estoque.select_related("produto"):
        registro = saldos.setdefault(mov.produto_id, [mov.produto, Decimal(0)])
        registro[1] += mov.quantidade
    for produto, saldo in saldos.values():
        if saldo < 0:
            movimentar(produto, -saldo, MovimentoEstoque.Tipo.ESTORNO, pedido=pedido, usuario=usuario,
                       observacao=f"Cancelamento do pedido #{pedido.pk}")
