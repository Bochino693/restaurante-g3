from django.urls import path
from .qz_seguranca import qz_assinar, qz_certificado, qz_diagnostico
from .gestao import (EstoqueView, ProdutosView, ConfiguracoesView, pedido_online, acompanhar_pedido,
                     acompanhar_dados, pedidos_novos, cancelar_pedido, produto_alternar, categoria_salvar,
                     estoque_historico)
from .views import (CaixaView,
                    PedidosView, CardapioClienteView,
                    adicionais_produto, VendasView, DashboardAnalyticsView,
                    LoginView, LogoutView, GerenciarPratosDiaView, RemoverPratoDiaView, avancar_status,
                    confirmar_impressao, pedidos_pendentes_impressao, ResumoPedidosView, PedidoReimprimirView,
                    registrar_clique, RelatorioPedidosView, PedidoDetalheView)

from .instalar import instalar

urlpatterns = [
    path('instalar/', instalar, name='instalar'),
    path('', CardapioClienteView.as_view(), name='cardapio'),
    path('caixa/', CaixaView.as_view(), name='caixa'),
    path('estoque/', EstoqueView.as_view(), name='estoque'),
    path('estoque/historico/<int:produto_id>/', estoque_historico, name='estoque_historico'),
    path('produtos/', ProdutosView.as_view(), name='produtos'),
    path('produtos/<int:produto_id>/alternar/', produto_alternar, name='produto_alternar'),
    path('categorias/salvar/', categoria_salvar, name='categoria_salvar'),
    path('configuracoes/', ConfiguracoesView.as_view(), name='configuracoes'),
    path('api/pedido-online/', pedido_online, name='pedido_online'),
    path('api/pedidos-novos/', pedidos_novos, name='pedidos_novos'),
    path('acompanhar/<str:codigo>/', acompanhar_pedido, name='acompanhar'),
    path('acompanhar/<str:codigo>/dados/', acompanhar_dados, name='acompanhar_dados'),
    path('pedido/<int:pedido_id>/cancelar/', cancelar_pedido, name='cancelar_pedido'),
    path('pedidos/', PedidosView.as_view(), name='pedidos'),
    path(
        "produto/<int:produto_id>/adicionais/",
        adicionais_produto,
    ),

    path('vendas/', VendasView.as_view(), name='vendas'),
    path('dashboards/', DashboardAnalyticsView.as_view(), name='dashboards'),
    path('admin-cardapio/pratos-do-dia/', GerenciarPratosDiaView.as_view(), name='gerenciar_pratos_dia'),
    path('pedido/<int:pedido_id>/avancar-status/', avancar_status, name='avancar_status'),
    path('admin-cardapio/pratos-do-dia/remover/<int:pk>/', RemoverPratoDiaView.as_view(), name='remover_prato_dia'),


    path('api/qz/certificado/', qz_certificado, name='qz_certificado'),
    path('api/qz/assinar/', qz_assinar, name='qz_assinar'),
    path('api/qz/diagnostico/', qz_diagnostico, name='qz_diagnostico'),
    path('api/pedidos-impressao/', pedidos_pendentes_impressao),
    path('api/clique/<int:produto_id>/', registrar_clique, name='registrar_clique'),
    path('api/confirmar-impressao/<int:pedido_id>/', confirmar_impressao),
    path('pedidos/resumo/', ResumoPedidosView.as_view(), name='resumo_pedidos'),
    path('pedido/<int:pedido_id>/reimprimir/', PedidoReimprimirView.as_view(), name='pedido_reimprimir'),
    path('pedidos/relatorio/<slug:tipo>/', RelatorioPedidosView.as_view(), name='relatorio_pedidos'),
    path('pedido/<int:pedido_id>/detalhes/', PedidoDetalheView.as_view(), name='pedido_detalhes'),


    path("logout/", LogoutView.as_view(), name="logout"),
    path('login/', LoginView.as_view(), name='login'),
]
