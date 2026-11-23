"""Gestão do G3: pedido online com acompanhamento, cancelamento, cadastro de
produtos (com foto), estoque e os dados da casa."""
import logging
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Count, F, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET, require_POST

from .estoque import estornar_pedido, movimentar
from .horario import status_da_loja
from .models import CategoriaProdutos, ConfigLoja, MovimentoEstoque, Pedidos, Produtos
from .views import (
    _decimal_seguro, _detalhe_pedido, _erro_pedido, _gravar_pedido, _limpar_cache_cardapio,
    _numero, _preparar_itens, _reais, _registrar_etapa, _url_imagem, _whatsapp_da_loja,
)
import json

logger = logging.getLogger(__name__)


# ============================================================ PEDIDO ONLINE
PAGAMENTOS_ONLINE = {
    "pix": Pedidos.FormaPagamento.PIX,
    "cartao": Pedidos.FormaPagamento.CARTAO,
    "cartão": Pedidos.FormaPagamento.CARTAO,
    "dinheiro": Pedidos.FormaPagamento.DINHEIRO,
}
LIMITE_PEDIDOS_ONLINE = 8          # por aparelho (IP) ...
JANELA_PEDIDOS_ONLINE = 10 * 60    # ... a cada 10 minutos


def _ip(request):
    return (request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR") or "").split(",")[0].strip()


@require_POST
def pedido_online(request):
    """O cliente fecha o pedido no cardápio: fica gravado como RECEBIDO,
    aparece no painel e ganha um endereço de acompanhamento."""
    config = ConfigLoja.atual()
    if not config.aceita_pedidos_online:
        return _erro_pedido("Os pedidos online estão pausados no momento.", status=403)
    if not status_da_loja()["aberta"]:
        return _erro_pedido("Estamos fechados agora. Volte no horário de funcionamento!", status=403)

    chave = f"pedido-online:{_ip(request)}"
    feitos = cache.get(chave, 0)
    if feitos >= LIMITE_PEDIDOS_ONLINE:
        return _erro_pedido("Muitos pedidos em sequência. Aguarde alguns minutos.", status=429)

    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _erro_pedido("Dados do pedido inválidos.")
    if not isinstance(data, dict):
        return _erro_pedido("Dados do pedido inválidos.")

    def texto(campo, limite):
        return str(data.get(campo) or "").strip()[:limite]

    try:
        nome = texto("nome", 90)
        telefone = re.sub(r"[^\d()+\- ]", "", texto("telefone", 20))
        entrega = texto("tipo", 10).lower() == "entrega"
        rua, numero, complemento = texto("rua", 120), texto("numero", 50), texto("complemento", 120)
        pagamento = PAGAMENTOS_ONLINE.get(texto("pagamento", 12).lower())

        if not nome:
            return _erro_pedido("Informe seu nome.")
        if len(re.sub(r"\D", "", telefone)) < 10:
            return _erro_pedido("Informe um telefone com DDD para falarmos com você.")
        if not pagamento:
            return _erro_pedido("Escolha a forma de pagamento.")
        if entrega and not config.aceita_entrega:
            return _erro_pedido("No momento só estamos com retirada no balcão.")
        if entrega and (not rua or not numero):
            return _erro_pedido("Informe rua e número para a entrega.")

        itens, total_bruto = _preparar_itens(data.get("carrinho"), exigir_estoque=True)
        if total_bruto < config.pedido_minimo:
            return _erro_pedido(f"O pedido mínimo é de R$ {_reais(config.pedido_minimo)}.")

        troco = None
        if pagamento == Pedidos.FormaPagamento.DINHEIRO and data.get("troco"):
            troco = _decimal_seguro(data.get("troco"), "Troco")
            troco = troco if troco > 0 else None

        pedido, _impressao = _gravar_pedido(
            itens, total_bruto, Decimal("0.00"),
            usuario="cardápio online",
            origem=Pedidos.Origem.ONLINE,
            status=Pedidos.StatusPedido.RECEBIDO,
            nome_cliente=nome,
            telefone=telefone,
            descricao=texto("obs", 300) or None,
            forma_pagamento=pagamento,
            entrega=entrega,
            rua=(rua or None) if entrega else None,
            numero=(numero or None) if entrega else None,
            complemento=(complemento or None) if entrega else None,
            taxa_motoca=config.taxa_entrega if entrega else Decimal("0.00"),
            troco_para=troco,
        )
    except Produtos.DoesNotExist:
        return _erro_pedido("Um dos itens saiu do cardápio. Atualize a página e confira o carrinho.", status=409)
    except (ValueError, InvalidOperation) as erro:
        return _erro_pedido(str(erro))
    except DatabaseError:
        logger.exception("Erro de banco ao gravar pedido online")
        return _erro_pedido("Não conseguimos registrar o pedido. Tente de novo em instantes.", status=500)

    cache.set(chave, feitos + 1, JANELA_PEDIDOS_ONLINE)
    return JsonResponse({
        "success": True,
        "pedido_id": pedido.id,
        "acompanhar": f"/acompanhar/{pedido.codigo}/",
    })


def _endereco_do_pedido(pedido):
    return ", ".join(p for p in [pedido.rua or "", pedido.numero or "", pedido.complemento or ""] if p)


def _acompanhamento(pedido):
    """O que o cliente vê: as etapas, a hora de cada uma e a previsão."""
    config = ConfigLoja.atual()
    S = Pedidos.StatusPedido
    cancelado = pedido.status == S.CANCELADO
    historico = pedido.historico or {}

    def hora(*status):
        for s in status:
            if historico.get(s):
                return historico[s][11:16]
        return ""

    indice = {S.RECEBIDO: 0, S.PAGO: 0, S.PREPARO: 1, S.ENTREGA: 2, S.FINALIZADO: 3}.get(pedido.status, -1)
    etapas = [
        {"titulo": "Pedido recebido", "texto": "Aguardando a casa confirmar.", "icone": "fa-receipt",
         "hora": hora(S.RECEBIDO, S.PAGO) or timezone.localtime(pedido.criado_em).strftime("%H:%M")},
        {"titulo": "Em preparo", "texto": "Confirmado! Estamos preparando.", "icone": "fa-fire-burner",
         "hora": hora(S.PREPARO)},
        {"titulo": "Saiu para entrega" if pedido.entrega else "Pronto para retirar",
         "texto": "O motoboy está a caminho." if pedido.entrega else "Pode vir buscar no balcão.",
         "icone": "fa-motorcycle" if pedido.entrega else "fa-bag-shopping", "hora": hora(S.ENTREGA)},
        {"titulo": "Entregue" if pedido.entrega else "Retirado", "texto": "Bom apetite!", "icone": "fa-circle-check",
         "hora": hora(S.FINALIZADO)},
    ]
    previsao = timezone.localtime(pedido.criado_em) + timedelta(minutes=config.tempo_preparo_min)
    return {
        "id": pedido.id,
        "status": pedido.status,
        "etapa": indice,
        "cancelado": cancelado,
        "finalizado": pedido.status == S.FINALIZADO,
        "etapas": etapas,
        "previsao": previsao.strftime("%H:%M"),
        "atualizado": timezone.localtime().strftime("%H:%M:%S"),
    }


def _mapas(pedido, config):
    """Mapa embutido (sem chave de API) e rota da casa até o cliente."""
    destino = " ".join(p for p in [pedido.rua or "", pedido.numero or "", config.cidade] if p)
    origem = " ".join(p for p in [config.endereco, config.cidade] if p)
    if pedido.entrega and pedido.rua:
        embutido = "https://www.google.com/maps?output=embed&q=" + quote(destino)
        rota = "https://www.google.com/maps/dir/?api=1&destination=" + quote(destino)
        if origem:
            rota += "&origin=" + quote(origem)
    elif origem:
        embutido = "https://www.google.com/maps?output=embed&q=" + quote(origem)
        rota = "https://www.google.com/maps/search/?api=1&query=" + quote(origem)
    else:
        embutido = rota = ""
    return {"embutido": embutido, "rota": rota}


def acompanhar_pedido(request, codigo):
    pedido = get_object_or_404(Pedidos.objects.prefetch_related("itens__produto__categoria"), codigo=codigo)
    config = ConfigLoja.atual()
    detalhe = _detalhe_pedido(pedido)
    whatsapp = _whatsapp_da_loja()
    aviso = (
        f"Olá! Fiz o pedido #{pedido.id} pelo cardápio online ({pedido.nome_cliente}). "
        f"Acompanhamento: {request.build_absolute_uri()}"
    )
    return render(request, "acompanhar.html", {
        "pedido": pedido,
        "detalhe": detalhe,
        "acompanhamento": _acompanhamento(pedido),
        "mapas": _mapas(pedido, config),
        "config": config,
        "endereco": _endereco_do_pedido(pedido),
        "whatsapp_link": f"https://wa.me/{whatsapp}?text={quote(aviso)}" if whatsapp else "",
    })


@require_GET
def acompanhar_dados(request, codigo):
    pedido = get_object_or_404(Pedidos, codigo=codigo)
    return JsonResponse(_acompanhamento(pedido))


@login_required
@require_GET
def pedidos_novos(request):
    """Para o sino do painel: pedidos online esperando a casa aceitar."""
    novos = list(
        Pedidos.objects.filter(status=Pedidos.StatusPedido.RECEBIDO)
        .order_by("-id").values("id", "nome_cliente", "total", "entrega")[:10]
    )
    return JsonResponse({
        "quantidade": len(novos),
        "ultimo": novos[0]["id"] if novos else 0,
        "pedidos": [
            {"id": p["id"], "cliente": p["nome_cliente"] or "Sem nome", "total": _reais(p["total"]),
             "entrega": p["entrega"]}
            for p in novos
        ],
    })


@login_required
@require_POST
def cancelar_pedido(request, pedido_id):
    """Cancela o pedido e devolve ao estoque o que ele tinha tirado."""
    pedido = get_object_or_404(Pedidos, id=pedido_id)
    if pedido.status == Pedidos.StatusPedido.CANCELADO:
        return JsonResponse({"status": "sucesso", "mensagem": "O pedido já estava cancelado."})
    if pedido.status == Pedidos.StatusPedido.FINALIZADO and not request.user.is_staff:
        return JsonResponse(
            {"status": "erro", "mensagem": "Só um administrador cancela pedido finalizado."}, status=403
        )
    estornar_pedido(pedido, usuario=request.user.get_username())
    _registrar_etapa(pedido, Pedidos.StatusPedido.CANCELADO)
    return JsonResponse({"status": "sucesso", "mensagem": "Pedido cancelado e estoque devolvido."})


# ================================================================ PRODUTOS
class ProdutoForm(forms.ModelForm):
    class Meta:
        model = Produtos
        fields = [
            "nome_produto", "descricao", "categoria", "codigo", "preco", "custo", "unidade",
            "image_produto", "ativo", "controla_estoque", "estoque_minimo", "produto_base",
            "fracoes_por_unidade",
        ]

    def clean(self):
        dados = super().clean()
        base = dados.get("produto_base")
        if base and self.instance.pk and base.pk == self.instance.pk:
            self.add_error("produto_base", "O produto não pode dar baixa nele mesmo.")
        if base and not base.controla_estoque:
            self.add_error("produto_base", "Escolha um produto que controla estoque (a garrafa).")
        if base and dados.get("controla_estoque"):
            self.add_error("controla_estoque", "Dose usa o estoque da garrafa: desmarque o estoque próprio.")
        if base and (dados.get("fracoes_por_unidade") or 0) < 1:
            self.add_error("fracoes_por_unidade", "Informe quantas doses saem de uma garrafa.")
        if (dados.get("preco") or 0) < 0 or (dados.get("custo") or 0) < 0:
            raise forms.ValidationError("Preço e custo não podem ser negativos.")
        return dados


def _produto_json(produto):
    return {
        "id": produto.id,
        "nome_produto": produto.nome_produto,
        "descricao": produto.descricao,
        "categoria": produto.categoria_id,
        "codigo": produto.codigo,
        "preco": f"{produto.preco:.2f}",
        "custo": f"{produto.custo:.2f}",
        "unidade": produto.unidade,
        "imagem": _url_imagem(produto.image_produto),
        "ativo": produto.ativo,
        "controla_estoque": produto.controla_estoque,
        "estoque_minimo": _numero(produto.estoque_minimo).replace(",", "."),
        "produto_base": produto.produto_base_id or "",
        "fracoes_por_unidade": produto.fracoes_por_unidade,
    }


class ProdutosView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        produtos = list(
            Produtos.objects.select_related("categoria", "produto_base").order_by("-ativo", "nome_produto")
        )
        for produto in produtos:
            custo = produto.custo_por_venda()
            produto.margem = round(float((produto.preco - custo) / produto.preco * 100)) if produto.preco and custo else None
        return render(request, "produtos.html", {
            "produtos": produtos,
            "categorias": CategoriaProdutos.objects.annotate(qtd=Count("produtos")).order_by("ordem", "nome_categoria"),
            "garrafas": [p for p in produtos if p.controla_estoque],
            "unidades": Produtos.Unidade.choices,
            "produtos_json": {str(p.id): _produto_json(p) for p in produtos},
            "total_ativos": sum(1 for p in produtos if p.ativo),
            "sem_foto": sum(1 for p in produtos if p.ativo and not p.image_produto),
        })

    def post(self, request):
        """Cria ou edita (com a foto) e responde em JSON para o modal."""
        produto = get_object_or_404(Produtos, pk=request.POST["id"]) if request.POST.get("id") else None
        dados = request.POST.copy()
        for campo in ("preco", "custo", "estoque_minimo"):
            dados[campo] = (dados.get(campo) or "0").replace(",", ".")
        dados.setdefault("codigo", "0")
        dados["codigo"] = dados.get("codigo") or "0"
        dados["fracoes_por_unidade"] = dados.get("fracoes_por_unidade") or "1"
        form = ProdutoForm(dados, request.FILES, instance=produto)
        if not form.is_valid():
            erros = "; ".join(
                f"{form.fields[c].label or c}: {' '.join(e)}" if c in form.fields else " ".join(e)
                for c, e in form.errors.items()
            )
            return JsonResponse({"ok": False, "erro": erros}, status=400)
        salvo = form.save()
        if request.POST.get("remover_foto") == "1" and not request.FILES.get("image_produto"):
            salvo.image_produto = ""
            salvo.save(update_fields=["image_produto"])
        # Estoque inicial informado no cadastro de um produto novo.
        inicial = (request.POST.get("estoque_inicial") or "").replace(",", ".")
        if not produto and salvo.controla_estoque and inicial:
            try:
                movimentar(salvo, Decimal(inicial), MovimentoEstoque.Tipo.ENTRADA,
                           usuario=request.user.get_username(), observacao="Estoque inicial",
                           custo_unitario=salvo.custo)
            except InvalidOperation:
                pass
        _limpar_cache_cardapio()
        return JsonResponse({"ok": True, "id": salvo.id})


@login_required
@require_POST
def produto_alternar(request, produto_id):
    produto = get_object_or_404(Produtos, pk=produto_id)
    produto.ativo = not produto.ativo
    produto.save(update_fields=["ativo", "atualizado"])
    return JsonResponse({"ok": True, "ativo": produto.ativo})


@login_required
@require_POST
def categoria_salvar(request):
    nome = (request.POST.get("nome") or "").strip()[:150]
    if not nome:
        return JsonResponse({"ok": False, "erro": "Dê um nome para a categoria."}, status=400)
    if request.POST.get("id"):
        categoria = get_object_or_404(CategoriaProdutos, pk=request.POST["id"])
        categoria.nome_categoria = nome
    else:
        if CategoriaProdutos.objects.filter(nome_categoria__iexact=nome).exists():
            return JsonResponse({"ok": False, "erro": "Já existe uma categoria com esse nome."}, status=400)
        categoria = CategoriaProdutos(nome_categoria=nome)
    try:
        categoria.ordem = int(request.POST.get("ordem") or categoria.ordem or 0)
    except ValueError:
        pass
    categoria.save()
    return JsonResponse({"ok": True, "id": categoria.id, "nome": categoria.nome_categoria})


# ================================================================= ESTOQUE
def _linha_estoque(produto):
    valor = produto.estoque_atual * produto.custo if produto.estoque_atual > 0 else Decimal(0)
    doses = []
    for dose in produto.doses:
        fracoes = max(dose.fracoes_por_unidade, 1)
        doses.append({
            "nome": dose.nome_produto,
            "fracoes": fracoes,
            "rende": int(max(produto.estoque_atual, 0) * fracoes),
        })
    return {
        "produto": produto,
        "saldo": _numero(produto.estoque_atual),
        "minimo": _numero(produto.estoque_minimo),
        "valor": _reais(valor),
        "valor_bruto": valor,
        "doses": doses,
        "pct": min(100, round(float(produto.estoque_atual / (produto.estoque_minimo * 3) * 100)))
        if produto.estoque_minimo > 0 and produto.estoque_atual > 0 else (100 if produto.estoque_atual > 0 else 0),
    }


class EstoqueView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        produtos = list(
            Produtos.objects.filter(controla_estoque=True, ativo=True)
            .select_related("categoria").order_by("nome_produto")
        )
        doses = {}
        for dose in Produtos.objects.filter(produto_base__in=produtos, ativo=True):
            doses.setdefault(dose.produto_base_id, []).append(dose)
        for produto in produtos:
            produto.doses = doses.get(produto.id, [])
        linhas = [_linha_estoque(p) for p in produtos]
        ordem = {"zerado": 0, "baixo": 1, "ok": 2}
        linhas.sort(key=lambda l: (ordem[l["produto"].situacao_estoque], l["produto"].nome_produto.casefold()))

        inicio = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29)
        saidas = (
            MovimentoEstoque.objects
            .filter(criado_em__gte=inicio, tipo__in=[MovimentoEstoque.Tipo.VENDA, MovimentoEstoque.Tipo.ESTORNO])
            .values("produto__nome_produto").annotate(total=Sum("quantidade")).order_by("total")[:8]
        )
        perdas = MovimentoEstoque.objects.filter(
            criado_em__gte=inicio, tipo=MovimentoEstoque.Tipo.PERDA
        ).aggregate(valor=Sum(F("quantidade") * F("produto__custo")))["valor"] or Decimal(0)

        return render(request, "estoque.html", {
            "linhas": linhas,
            "categorias": CategoriaProdutos.objects.filter(produtos__controla_estoque=True).distinct(),
            "qtd_itens": len(linhas),
            "qtd_baixo": sum(1 for l in linhas if l["produto"].situacao_estoque == "baixo"),
            "qtd_zerado": sum(1 for l in linhas if l["produto"].situacao_estoque == "zerado"),
            "valor_total": _reais(sum((l["valor_bruto"] for l in linhas), Decimal(0))),
            "perdas_30d": _reais(-perdas),
            "movimentos": MovimentoEstoque.objects.select_related("produto")[:40],
            "sem_controle": Produtos.objects.filter(
                ativo=True, controla_estoque=False, produto_base__isnull=True
            ).count(),
            "grafico_saidas": {
                "labels": [s["produto__nome_produto"] for s in saidas],
                "valores": [round(float(-s["total"]), 2) for s in saidas],
            },
            "grafico_situacao": [
                sum(1 for l in linhas if l["produto"].situacao_estoque == s) for s in ("ok", "baixo", "zerado")
            ],
            "tipos": [MovimentoEstoque.Tipo.ENTRADA, MovimentoEstoque.Tipo.AJUSTE, MovimentoEstoque.Tipo.PERDA],
        })

    def post(self, request):
        """Entrada (compra), ajuste de contagem ou perda."""
        produto = get_object_or_404(Produtos, pk=request.POST.get("produto"), controla_estoque=True)
        tipo = request.POST.get("tipo")
        try:
            quantidade = Decimal((request.POST.get("quantidade") or "").replace(",", "."))
            if not quantidade.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            return JsonResponse({"ok": False, "erro": "Quantidade inválida."}, status=400)

        usuario = request.user.get_username()
        observacao = (request.POST.get("observacao") or "").strip()
        Tipo = MovimentoEstoque.Tipo
        if tipo == Tipo.ENTRADA:
            if quantidade <= 0:
                return JsonResponse({"ok": False, "erro": "Informe quanto entrou."}, status=400)
            custo = None
            if request.POST.get("custo"):
                try:
                    custo = _decimal_seguro(request.POST.get("custo"), "Custo")
                except ValueError as erro:
                    return JsonResponse({"ok": False, "erro": str(erro)}, status=400)
                if custo > 0:
                    # O custo do produto acompanha a última compra.
                    Produtos.objects.filter(pk=produto.pk).update(custo=custo)
            validade = request.POST.get("validade") or None
            movimentar(produto, quantidade, Tipo.ENTRADA, usuario=usuario, observacao=observacao,
                       custo_unitario=custo, validade=validade)
        elif tipo == Tipo.PERDA:
            if quantidade <= 0:
                return JsonResponse({"ok": False, "erro": "Informe quanto se perdeu."}, status=400)
            movimentar(produto, -quantidade, Tipo.PERDA, usuario=usuario, observacao=observacao)
        elif tipo == Tipo.AJUSTE:
            # Na contagem a pessoa informa quanto TEM; o movimento é a diferença.
            if quantidade < 0:
                return JsonResponse({"ok": False, "erro": "A contagem não pode ser negativa."}, status=400)
            produto.refresh_from_db()
            diferenca = quantidade - produto.estoque_atual
            if not diferenca:
                return JsonResponse({"ok": True, "saldo": _numero(produto.estoque_atual), "igual": True})
            movimentar(produto, diferenca, Tipo.AJUSTE, usuario=usuario,
                       observacao=observacao or "Contagem de estoque")
        else:
            return JsonResponse({"ok": False, "erro": "Tipo de movimento inválido."}, status=400)

        produto.refresh_from_db()
        _limpar_cache_cardapio()
        return JsonResponse({"ok": True, "saldo": _numero(produto.estoque_atual)})


@login_required
@require_GET
def estoque_historico(request, produto_id):
    produto = get_object_or_404(Produtos, pk=produto_id)
    return JsonResponse({
        "produto": produto.nome_produto,
        "saldo": _numero(produto.estoque_atual),
        "movimentos": [
            {
                "tipo": m.tipo,
                "tipo_label": m.get_tipo_display(),
                "quantidade": ("+" if m.quantidade > 0 else "") + _numero(m.quantidade),
                "saldo": _numero(m.saldo_apos),
                "quando": timezone.localtime(m.criado_em).strftime("%d/%m %H:%M"),
                "observacao": m.observacao,
                "usuario": m.usuario,
            }
            for m in produto.movimentos.all()[:80]
        ],
    })


# ============================================================ CONFIGURAÇÕES
class ConfigLojaForm(forms.ModelForm):
    class Meta:
        model = ConfigLoja
        fields = [
            "nome", "slogan", "logo", "whatsapp", "telefone", "instagram", "endereco", "cidade",
            "taxa_entrega", "pedido_minimo", "tempo_preparo_min", "aceita_pedidos_online", "aceita_entrega",
        ]

    def clean_whatsapp(self):
        return re.sub(r"\D", "", self.cleaned_data.get("whatsapp") or "")

    def clean_instagram(self):
        return (self.cleaned_data.get("instagram") or "").strip().lstrip("@")


class ConfiguracoesView(LoginRequiredMixin, View):
    login_url = "login"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_staff:
            messages.error(request, "Só administradores mudam os dados da casa.")
            return redirect("pedidos")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, "configuracoes.html", {
            "form": ConfigLojaForm(instance=ConfigLoja.atual()),
            "horario": status_da_loja()["horario"],
        })

    def post(self, request):
        dados = request.POST.copy()
        for campo in ("taxa_entrega", "pedido_minimo"):
            dados[campo] = (dados.get(campo) or "0").replace(",", ".")
        form = ConfigLojaForm(dados, request.FILES, instance=ConfigLoja.atual())
        if form.is_valid():
            form.save()
            messages.success(request, "Dados da casa salvos.")
            return redirect("configuracoes")
        return render(request, "configuracoes.html", {"form": form, "horario": status_da_loja()["horario"]})
