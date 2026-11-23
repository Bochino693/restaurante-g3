import secrets
from decimal import Decimal

from django.core.cache import cache
from django.db import models


class Prime(models.Model):
    ativo = models.BooleanField(default=True)
    criacao = models.DateTimeField(auto_now_add=True, null=True)
    atualizado = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        abstract = True


class ConfigLoja(models.Model):
    """Os dados da casa (uma linha só): aparecem no cardápio, na notinha e
    no acompanhamento do pedido. Editados em Configurações, sem mexer em código."""
    nome = models.CharField(max_length=80, default="G3 Bar e Restaurante")
    slogan = models.CharField(max_length=120, blank=True, default="Almoço, petiscos, doses e bebida gelada")
    logo = models.ImageField(upload_to="loja/", blank=True)
    whatsapp = models.CharField(
        max_length=20, blank=True,
        help_text="Só dígitos, com 55 + DDD. Recebe o aviso dos pedidos online.",
    )
    telefone = models.CharField(max_length=20, blank=True)
    instagram = models.CharField(max_length=60, blank=True, default="barrestauranteg3")
    endereco = models.CharField(max_length=180, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    taxa_entrega = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    pedido_minimo = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    tempo_preparo_min = models.PositiveIntegerField(default=40, verbose_name="Tempo médio (min)")
    aceita_pedidos_online = models.BooleanField(default=True)
    aceita_entrega = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Configuração da loja"
        verbose_name_plural = "Configuração da loja"

    def __str__(self):
        return self.nome

    CHAVE_CACHE = "config-loja"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(self.CHAVE_CACHE)

    @classmethod
    def atual(cls):
        """A configuração da casa (60 s em memória: é lida em toda página)."""
        loja = cache.get(cls.CHAVE_CACHE)
        if loja is None:
            loja, _ = cls.objects.get_or_create(pk=1)
            cache.set(cls.CHAVE_CACHE, loja, 60)
        return loja


class CategoriaProdutos(Prime):
    imagem_categoria = models.ImageField(upload_to='imagens_categorias/', blank=True)
    nome_categoria = models.CharField(max_length=150)
    ordem = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.nome_categoria

    class Meta:
        verbose_name = "Categoria de Produto"
        verbose_name_plural = "Categorias de Produto"
        ordering = ["ordem", "nome_categoria"]


class Produtos(Prime):
    class Unidade(models.TextChoices):
        UNIDADE = "UN", "Unidade"
        GARRAFA = "GF", "Garrafa"
        LATA = "LT", "Lata"
        DOSE = "DS", "Dose"
        PORCAO = "PC", "Porção"
        PRATO = "PR", "Prato"
        KG = "KG", "Kg"
        LITRO = "L", "Litro"

    nome_produto = models.CharField(max_length=180, null=False)
    descricao = models.CharField(max_length=240, blank=True)
    image_produto = models.ImageField(upload_to='produtos/', blank=True)
    codigo = models.IntegerField(default=0)
    preco = models.DecimalField(decimal_places=2, max_digits=9)
    # Quanto custa para a casa (uma unidade/garrafa). Dá o lucro nos relatórios.
    custo = models.DecimalField(decimal_places=2, max_digits=9, default=0)
    unidade = models.CharField(max_length=2, choices=Unidade.choices, default=Unidade.UNIDADE)

    categoria = models.ForeignKey(
        CategoriaProdutos,
        on_delete=models.CASCADE,
        related_name="produtos"
    )

    # ---- Estoque -----------------------------------------------------------
    # controla_estoque: este produto tem saldo próprio (garrafa, lata, porção).
    controla_estoque = models.BooleanField(default=False)
    estoque_atual = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    estoque_minimo = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    # DOSE: o produto não tem saldo próprio; cada venda tira uma fração da
    # garrafa (produto_base). Ex.: "Dose Red Label" baixa 1/20 da garrafa.
    produto_base = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="fracionados",
        verbose_name="Baixa do estoque de",
    )
    fracoes_por_unidade = models.PositiveIntegerField(
        default=1, verbose_name="Doses por garrafa",
        help_text="Quantas doses saem de uma garrafa/unidade do produto base.",
    )

    def __str__(self):
        return self.nome_produto

    class Meta:
        verbose_name = "Produto"
        verbose_name_plural = "Produtos"

    # Onde a venda dá baixa: (produto com saldo, quanto sai por unidade vendida).
    def alvo_estoque(self):
        if self.produto_base_id and self.produto_base and self.produto_base.controla_estoque:
            fracoes = max(int(self.fracoes_por_unidade or 1), 1)
            return self.produto_base, Decimal(1) / Decimal(fracoes)
        if self.controla_estoque:
            return self, Decimal(1)
        return None, Decimal(0)

    def custo_por_venda(self):
        """Custo de uma unidade vendida (a dose custa a fração da garrafa)."""
        if self.produto_base_id and self.produto_base:
            fracoes = max(int(self.fracoes_por_unidade or 1), 1)
            return (self.produto_base.custo or Decimal(0)) / Decimal(fracoes)
        return self.custo or Decimal(0)

    @property
    def situacao_estoque(self):
        if not self.controla_estoque:
            return ""
        if self.estoque_atual <= 0:
            return "zerado"
        if self.estoque_atual <= self.estoque_minimo:
            return "baixo"
        return "ok"


class Adicional(models.Model):
    nome = models.CharField(max_length=120)
    preco = models.DecimalField(max_digits=9, decimal_places=2)

    produtos = models.ManyToManyField(
        Produtos,
        related_name="adicionais_disponiveis",
        blank=True,
    )

    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.nome

    class Meta:
        verbose_name = "Adicional"
        verbose_name_plural = "Adicionais"


class PratoDoDia(models.Model):
    DIAS_SEMANA = (
        (0, "Segunda-feira"),
        (1, "Terça-feira"),
        (2, "Quarta-feira"),
        (3, "Quinta-feira"),
        (4, "Sexta-feira"),
        (5, "Sábado"),
        (6, "Domingo"),
    )

    produto = models.ForeignKey(
        "Produtos",
        on_delete=models.CASCADE,
        related_name="agenda_prato_dia"
    )

    dia_semana = models.IntegerField(
        choices=DIAS_SEMANA
    )

    ativo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.get_dia_semana_display()} - {self.produto.nome_produto}"


class MovimentoEstoque(models.Model):
    """Cada entrada e saída do estoque: é o histórico que explica o saldo."""

    class Tipo(models.TextChoices):
        ENTRADA = "ENTRADA", "Entrada (compra)"
        VENDA = "VENDA", "Venda"
        ESTORNO = "ESTORNO", "Estorno de venda"
        AJUSTE = "AJUSTE", "Ajuste de contagem"
        PERDA = "PERDA", "Perda / quebra"

    produto = models.ForeignKey(Produtos, on_delete=models.CASCADE, related_name="movimentos")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    # Positivo entra, negativo sai.
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    saldo_apos = models.DecimalField(max_digits=12, decimal_places=3)
    custo_unitario = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    validade = models.DateField(null=True, blank=True)
    observacao = models.CharField(max_length=200, blank=True)
    pedido = models.ForeignKey(
        "Pedidos", on_delete=models.SET_NULL, null=True, blank=True, related_name="movimentos_estoque"
    )
    usuario = models.CharField(max_length=150, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Movimento de estoque"
        verbose_name_plural = "Movimentos de estoque"
        ordering = ["-criado_em", "-id"]
        indexes = [models.Index(fields=["produto", "-criado_em"], name="mov_produto_idx")]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.quantidade} · {self.produto}"


class ItensPedido(Prime):
    quantidade = models.IntegerField(default=1)
    preco_unitario = models.DecimalField(decimal_places=2, max_digits=9)
    subtotal = models.DecimalField(decimal_places=2, max_digits=9)
    # Custo de uma unidade no momento da venda (lucro dos relatórios).
    custo_unitario = models.DecimalField(decimal_places=2, max_digits=9, default=0)

    produto = models.ForeignKey(
        Produtos,
        on_delete=models.SET_NULL,
        null=True,
        related_name='itens_pedido'
    )

    # Ex: [{"nome": "Ovo Extra", "qtd": 1, "preco": "2.00"}]
    adicionais = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.produto.nome_produto if self.produto else "Produto removido"

    class Meta:
        verbose_name = "Item de Pedido"
        verbose_name_plural = "Itens de Pedido"


def _novo_codigo():
    return secrets.token_urlsafe(9)


class Pedidos(Prime):
    class StatusPedido(models.TextChoices):
        # Pedido online que acabou de chegar e ainda não foi aceito pela casa.
        RECEBIDO = 'RECEBIDO', 'Recebido'
        PAGO = 'PAGO', 'Pago'
        PREPARO = 'PREPARO', 'Em Preparo'
        ENTREGA = 'ENTREGA', 'Em Entrega'
        FINALIZADO = 'FINALIZADO', 'Finalizado'
        CANCELADO = 'CANCELADO', 'Cancelado'

    class FormaPagamento(models.TextChoices):
        DINHEIRO = 'DINHEIRO', 'Dinheiro'
        PIX = 'PIX', 'Pix'
        CARTAO = 'CARTAO', 'Cartão'
        MISTO = 'MISTO', 'Misto'

    class Origem(models.TextChoices):
        BALCAO = 'BALCAO', 'Balcão'
        ONLINE = 'ONLINE', 'Online'

    forma_pagamento = models.CharField(
        max_length=20,
        choices=FormaPagamento.choices,
        default=FormaPagamento.PIX
    )
    origem = models.CharField(max_length=10, choices=Origem.choices, default=Origem.BALCAO)

    # Endereço público do acompanhamento: /acompanhar/<codigo>/
    codigo = models.CharField(max_length=20, unique=True, default=_novo_codigo, editable=False)
    # Quando o pedido entrou em cada etapa: {"PREPARO": "2026-01-01T12:00:00-03:00"}
    historico = models.JSONField(default=dict, blank=True)

    FLUXO = ['PAGO', 'PREPARO', 'ENTREGA', 'FINALIZADO']

    def proximo_status(self):
        if self.status == self.StatusPedido.RECEBIDO:
            return self.StatusPedido.PREPARO
        try:
            return self.StatusPedido(self.FLUXO[self.FLUXO.index(self.status) + 1])
        except (ValueError, IndexError):
            return None

    itens = models.ManyToManyField(ItensPedido)

    status = models.CharField(
        max_length=20,
        choices=StatusPedido.choices,
        default=StatusPedido.PAGO
    )

    nome_cliente = models.CharField(max_length=90, blank=True, null=True)
    impresso = models.BooleanField(default=False)
    descricao = models.TextField(blank=True, null=True)

    entrega = models.BooleanField(default=False)
    cpf = models.CharField(max_length=14, blank=True, null=True)

    rua = models.CharField(max_length=120, null=True, blank=True)
    numero = models.CharField(max_length=50, null=True, blank=True)
    complemento = models.CharField(max_length=120, null=True, blank=True)
    cep = models.CharField(max_length=9, blank=True, null=True)
    telefone = models.CharField(max_length=20, blank=True, null=True)
    troco_para = models.DecimalField(decimal_places=2, max_digits=9, null=True, blank=True)

    total = models.DecimalField(
        decimal_places=2,
        max_digits=9,
        default=0
    )

    taxa_motoca = models.DecimalField(
        decimal_places=2,
        max_digits=9,
        default=0,
        verbose_name="Taxa Motoca"
    )

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        indexes = [
            models.Index(fields=["criado_em"], name="pedido_criado_idx"),
            models.Index(fields=["status", "criado_em"], name="pedido_status_criado_idx"),
            models.Index(fields=["impresso"], name="pedido_impresso_idx"),
        ]

    def __str__(self):
        return f"Pedido #{self.id} - {self.status}"


class ProdutosMaisClick(Prime):
    produto = models.ForeignKey(Produtos, related_name='clicks_produtos', on_delete=models.CASCADE)
    quantidade = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Produto mais clicado"
        verbose_name_plural = "Produtos mais clicados"
