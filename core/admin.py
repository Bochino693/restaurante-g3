from django.contrib import admin
from django.utils.html import format_html
from .models import (CategoriaProdutos, Produtos, MovimentoEstoque, ItensPedido, Pedidos, Adicional, PratoDoDia,
                     ConfigLoja)


# Configurações de exibição de imagem miniatura
def exibir_miniatura(obj, campo_imagem):
    imagem = getattr(obj, campo_imagem)
    if imagem:
        return format_html('<img src="{}" style="width: 45px; height: 45px; border-radius: 8px; object-fit: cover;" />',
                           imagem.url)
    return "Sem imagem"


@admin.register(CategoriaProdutos)
class CategoriaProdutosAdmin(admin.ModelAdmin):
    list_display = ('get_image', 'nome_categoria', 'ativo', 'criacao')
    list_editable = ('ativo',)
    search_fields = ('nome_categoria',)

    def get_image(self, obj):
        return exibir_miniatura(obj, 'imagem_categoria')

    get_image.short_description = 'Imagem'


@admin.register(Produtos)
class ProdutosAdmin(admin.ModelAdmin):
    list_display = ('get_image', 'nome_produto', 'codigo', 'categoria', 'preco', 'custo', 'estoque_atual', 'ativo')
    list_filter = ('categoria', 'ativo')
    search_fields = ('nome_produto', 'codigo')
    list_editable = ('preco', 'ativo')
    list_per_page = 20

    def get_image(self, obj):
        return exibir_miniatura(obj, 'image_produto')

    get_image.short_description = 'Foto'


@admin.register(MovimentoEstoque)
class MovimentoEstoqueAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'produto', 'tipo', 'quantidade', 'saldo_apos', 'usuario', 'observacao')
    list_filter = ('tipo', 'criado_em')
    search_fields = ('produto__nome_produto', 'observacao')


@admin.register(ConfigLoja)
class ConfigLojaAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not ConfigLoja.objects.exists()


class ItensPedidoInline(admin.TabularInline):
    model = Pedidos.itens.through  # Como é ManyToMany
    extra = 0
    readonly_fields = ('get_subtotal',)

    def get_subtotal(self, obj):
        # Acesso ao item através da tabela intermediária
        item = obj.itenspedido
        return f"R$ {item.subtotal}"

    get_subtotal.short_description = "Subtotal"


@admin.register(ItensPedido)
class ItensPedidoAdmin(admin.ModelAdmin):
    list_display = ('produto', 'quantidade', 'preco_unitario', 'subtotal')
    readonly_fields = ('subtotal',)


@admin.register(Pedidos)
class PedidosAdmin(admin.ModelAdmin):
    list_display = ('id', 'nome_cliente', 'origem', 'status', 'total', 'criado_em', 'quantidade_itens')
    list_filter = ('status', 'origem', 'criado_em')
    date_hierarchy = 'criado_em'

    # inlines = [ItensPedidoInline] # Opcional: ver itens dentro do pedido

    def quantidade_itens(self, obj):
        return obj.itens.count()

    quantidade_itens.short_description = 'Qtd Itens'


@admin.register(Adicional)
class AdicionalAdmin(admin.ModelAdmin):

    list_display = (
        "nome",
        "preco",
        "ativo",
    )

    list_filter = (
        "ativo",
    )

    search_fields = (
        "nome",
    )

    filter_horizontal = (
        "produtos",
    )


@admin.register(PratoDoDia)
class PratoDoDiaAdmin(admin.ModelAdmin):

    list_display = (
        "produto",
        "dia_semana",
        "ativo",
    )

    list_filter = (
        "dia_semana",
        "ativo",
    )

    search_fields = (
        "produto__nome_produto",
    )

    ordering = (
        "dia_semana",
    )