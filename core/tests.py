import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .horario import loja_aberta, status_da_loja
from .models import CategoriaProdutos, ItensPedido, Pedidos, Produtos, ProdutosMaisClick
from .views import _reais

SP = ZoneInfo("America/Sao_Paulo")


def quando(ano, mes, dia, hora, minuto=0):
    return datetime(ano, mes, dia, hora, minuto, tzinfo=SP)


@override_settings(LOJA_DIAS=(0, 1, 2, 3, 4), LOJA_ABRE=11, LOJA_FECHA=16)
class HorarioTests(TestCase):
    """Segunda a sexta, das 11h às 16h."""

    def test_aberta_em_dia_util_no_horario(self):
        self.assertTrue(loja_aberta(quando(2026, 9, 28, 12)))   # segunda
        self.assertTrue(loja_aberta(quando(2026, 10, 2, 15, 59)))  # sexta

    def test_fechada_no_sabado_e_domingo(self):
        self.assertFalse(loja_aberta(quando(2026, 10, 3, 12)))  # sábado
        self.assertFalse(loja_aberta(quando(2026, 10, 4, 12)))  # domingo

    def test_fechada_fora_do_horario(self):
        self.assertFalse(loja_aberta(quando(2026, 9, 28, 10, 59)))
        self.assertFalse(loja_aberta(quando(2026, 9, 28, 16)))

    def test_proxima_abertura_na_sexta_a_noite_e_segunda(self):
        status = status_da_loja(quando(2026, 10, 2, 18))
        self.assertFalse(status["aberta"])
        self.assertEqual(status["detalhe"], "abre segunda às 11h")

    def test_proxima_abertura_de_manha_e_hoje(self):
        self.assertEqual(status_da_loja(quando(2026, 9, 29, 9))["detalhe"], "abre hoje às 11h")


class TelasTests(TestCase):
    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        categoria = CategoriaProdutos(nome_categoria="Marmitas")
        categoria.imagem_categoria.name = "x.png"
        categoria.save()
        # Produto sem foto: antes derrubava o cardápio inteiro (erro 500).
        self.produto = Produtos.objects.create(
            nome_produto="Marmita d'Avó", categoria=categoria, preco=Decimal("22.90"), codigo=10
        )
        item = ItensPedido.objects.create(
            produto=self.produto, quantidade=2, preco_unitario=Decimal("22.90"), subtotal=Decimal("45.80")
        )
        self.pedido = Pedidos.objects.create(
            nome_cliente="Ana", total=Decimal("45.80"), forma_pagamento="DINHEIRO",
            status=Pedidos.StatusPedido.ENTREGA,
        )
        self.pedido.itens.add(item)

    def test_cardapio_publico_sem_login_e_com_produto_sem_foto(self):
        resposta = self.client.get(reverse("cardapio"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Marmita d&#x27;Avó")
        self.assertContains(resposta, "catalogo-json")

    def test_dashboard_renderiza_e_responde_json(self):
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("dashboards")).status_code, 200)
        dados = self.client.get(reverse("dashboards") + "?atalho=tudo&ajax=1").json()
        self.assertEqual(dados["total_pedidos"], 1)
        self.assertIn("grafico_dia_semana", dados)
        self.assertIn("grafico_categorias", dados)

    def test_avancar_status_exige_login(self):
        url = reverse("avancar_status", args=[self.pedido.id])
        self.client.post(url)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, Pedidos.StatusPedido.ENTREGA)

    def test_finalizar_em_dinheiro_exige_confirmacao(self):
        self.client.force_login(self.usuario)
        url = reverse("avancar_status", args=[self.pedido.id])
        cabecalho = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
        self.assertEqual(self.client.post(url, {}, **cabecalho).status_code, 400)
        self.assertEqual(self.client.post(url, {"confirmacao": "confirmar"}, **cabecalho).status_code, 200)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, Pedidos.StatusPedido.FINALIZADO)

    def test_clique_do_cardapio_soma_no_dia(self):
        url = reverse("registrar_clique", args=[self.produto.id])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(ProdutosMaisClick.objects.get(produto=self.produto).quantidade, 2)

    def test_vendas_formata_dinheiro(self):
        self.assertEqual(_reais(Decimal("53140.3000000000")), "53.140,30")
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("vendas")).status_code, 200)

    @override_settings(IMPRESSAO_API_KEY="chave-de-teste")
    def test_api_da_impressora_exige_a_chave(self):
        self.assertEqual(self.client.get("/api/pedidos-impressao/").status_code, 401)
        resposta = self.client.get("/api/pedidos-impressao/", HTTP_X_API_KEY="chave-de-teste")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["pedidos"][0]["id"], self.pedido.id)

    def test_telas_do_painel_abrem(self):
        self.client.force_login(self.usuario)
        for nome in ["pedidos", "caixa", "estoque", "gerenciar_pratos_dia"]:
            with self.subTest(tela=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)


class RelatorioBebidasTests(TestCase):
    """Bebidas = produtos de categoria com "bebida", "refri", "suco"... no nome."""

    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        marmitas = self._categoria("Marmitas")
        bebidas = self._categoria("Bebidas")
        sucos = self._categoria("SUCOS NATURAIS")
        marmita = Produtos.objects.create(nome_produto="Marmita", categoria=marmitas, preco=Decimal("20.00"))
        coca = Produtos.objects.create(nome_produto="Coca-Cola 2L", categoria=bebidas, preco=Decimal("6.00"))
        suco = Produtos.objects.create(nome_produto="Suco de laranja", categoria=sucos, preco=Decimal("5.00"))

        # Pix com entrega: marmita + 2 cocas + 1 suco (duas bebidas no mesmo pedido).
        self.pedido_pix = self._pedido(
            "PIX", [(marmita, 1), (coca, 2), (suco, 1)], taxa=Decimal("5.00"), entrega=True
        )
        Pedidos.objects.filter(pk=self.pedido_pix.pk).update(
            rua="Rua das Flores", numero="12", cep="01234-000", telefone="(11) 98765-4321"
        )
        # Dinheiro: só a marmita.
        self.pedido_dinheiro = self._pedido("DINHEIRO", [(marmita, 1)])
        # Cancelado: não entra em relatório nenhum.
        self._pedido("PIX", [(coca, 3)], status=Pedidos.StatusPedido.CANCELADO)

    def _categoria(self, nome):
        categoria = CategoriaProdutos(nome_categoria=nome)
        categoria.imagem_categoria.name = "x.png"
        categoria.save()
        return categoria

    def _pedido(self, pagamento, itens, taxa=Decimal("0.00"), entrega=False, status=Pedidos.StatusPedido.PAGO):
        criados = [
            ItensPedido.objects.create(
                produto=produto, quantidade=qtd, preco_unitario=produto.preco, subtotal=produto.preco * qtd
            )
            for produto, qtd in itens
        ]
        pedido = Pedidos.objects.create(
            nome_cliente="Cliente", forma_pagamento=pagamento, status=status, entrega=entrega,
            taxa_motoca=taxa, total=sum((i.subtotal for i in criados), Decimal("0.00")),
        )
        pedido.itens.add(*criados)
        return pedido

    def test_relatorio_da_tela_de_pedidos(self):
        self.client.force_login(self.usuario)
        resposta = self.client.get(reverse("pedidos") + "?periodo=hoje")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["total_bebidas"], "17,00")
        self.assertEqual(resposta.context["qtd_bebidas"], 3)
        self.assertEqual(
            resposta.context["bebidas_vendidas"],
            [
                {"nome": "Coca-Cola 2L", "unidades": 2, "receita": "12,00"},
                {"nome": "Suco de laranja", "unidades": 1, "receita": "5,00"},
            ],
        )
        # O resto do relatório continua igual.
        self.assertEqual(resposta.context["total_pix"], "37,00")
        self.assertEqual(resposta.context["total_taxa_motoca"], "5,00")
        self.assertContains(resposta, 'data-relatorio="bebidas"')

    def test_bebidas_obedecem_ao_filtro_de_pagamento(self):
        self.client.force_login(self.usuario)
        dados = self.client.get(reverse("resumo_pedidos") + "?periodo=hoje&pagamento=DINHEIRO").json()
        self.assertEqual(dados["total_bebidas"], "0,00")
        self.assertEqual(dados["qtd_bebidas"], 0)
        dados = self.client.get(reverse("resumo_pedidos") + "?periodo=hoje&pagamento=PIX").json()
        self.assertEqual(dados["total_bebidas"], "17,00")
        self.assertEqual(dados["total_pix"], "37,00")

    def test_relatorio_grafico_de_bebidas_nas_estatisticas(self):
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("dashboards") + "?atalho=hoje").status_code, 200)
        dados = self.client.get(reverse("dashboards") + "?atalho=hoje&ajax=1").json()

        bebidas = dados["bebidas"]
        self.assertEqual(bebidas["receita"], "17,00")
        self.assertEqual(bebidas["unidades"], 3)
        self.assertEqual(bebidas["lider"], "Coca-Cola 2L")
        self.assertEqual(bebidas["participacao"], 29.8)       # 17 de 57 em itens
        self.assertEqual(bebidas["pedidos_com"], 1)            # sem contar duas vezes
        self.assertEqual(bebidas["pedidos_pct"], 50.0)
        self.assertEqual(bebidas["ticket_com"], "37,00")
        self.assertEqual(bebidas["ticket_sem"], "20,00")
        self.assertEqual([b["nome"] for b in bebidas["ranking"]], ["Coca-Cola 2L", "Suco de laranja"])
        self.assertEqual(dados["grafico_top_bebidas"]["valores"], [1, 2])

        serie = dados["grafico_vendas_diarias"]
        self.assertEqual(sum(serie["bebidas"]), 17.0)
        self.assertEqual(sum(serie["bebidas_unidades"]), 3)
        self.assertEqual(sum(serie["por_pagamento"]["PIX"]), 37.0)
        self.assertEqual(sum(serie["por_pagamento"]["DINHEIRO"]), 20.0)
        self.assertEqual(sum(serie["taxas"]), 5.0)
        self.assertEqual(len(serie["bebidas"]), len(serie["labels"]))

    @override_settings(PALAVRAS_BEBIDAS=[])
    def test_sem_palavras_nada_e_bebida(self):
        self.client.force_login(self.usuario)
        dados = self.client.get(reverse("resumo_pedidos") + "?periodo=hoje").json()
        self.assertEqual(dados["total_bebidas"], "0,00")

    def test_estatisticas_e_pedidos_sem_nenhum_pedido(self):
        Pedidos.objects.all().delete()
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("dashboards")).status_code, 200)
        dados = self.client.get(reverse("dashboards") + "?ajax=1").json()
        self.assertEqual(dados["bebidas"]["receita"], "0,00")
        self.assertEqual(dados["bebidas"]["lider"], "Sem vendas")
        self.assertIsNone(dados["bebidas"]["ticket_diferenca"])
        resposta = self.client.get(reverse("pedidos"))
        self.assertEqual(resposta.context["total_bebidas"], "0,00")
        self.assertContains(resposta, "Nenhum pedido encontrado.")
        self.assertEqual(self.client.get(reverse("relatorio_pedidos", args=["bebidas"])).json()["itens"], [])


class RelatoriosDosCartoesTests(RelatorioBebidasTests):
    """Cada cartão do topo de Pedidos abre um relatório com os pedidos dele."""

    # Os testes herdados já rodam na classe mãe.
    test_relatorio_da_tela_de_pedidos = None
    test_bebidas_obedecem_ao_filtro_de_pagamento = None
    test_relatorio_grafico_de_bebidas_nas_estatisticas = None
    test_sem_palavras_nada_e_bebida = None
    test_estatisticas_e_pedidos_sem_nenhum_pedido = None

    def _relatorio(self, tipo, filtros="?periodo=hoje"):
        self.client.force_login(self.usuario)
        resposta = self.client.get(reverse("relatorio_pedidos", args=[tipo]) + filtros)
        self.assertEqual(resposta.status_code, 200, tipo)
        return resposta.json()

    def test_valor_de_cada_cartao(self):
        esperado = {
            "total": "57,00", "dinheiro": "20,00", "pix": "37,00", "cartao": "0,00", "misto": "0,00",
            "taxa": "5,00", "bebidas": "17,00", "retirada_dinheiro": "20,00",
            "entrega_dinheiro": "0,00", "caixa_dinheiro": "20,00",
        }
        for tipo, valor in esperado.items():
            with self.subTest(tipo=tipo):
                self.assertEqual(self._relatorio(tipo)["valor"], valor)

    def test_total_divide_por_pagamento_e_avisa_cancelados(self):
        dados = self._relatorio("total")
        quebra = {parte["rotulo"]: parte for parte in dados["quebra"]}
        self.assertEqual(quebra["Pix"]["valor"], "37,00")
        self.assertEqual(quebra["Pix"]["pct"], 64.9)
        self.assertEqual(quebra["Dinheiro"]["pct"], 35.1)
        self.assertEqual(dados["pedidos_total"], 2)
        self.assertIn("1 pedido cancelado", dados["nota"])

    def test_lista_de_pedidos_de_cada_relatorio(self):
        pix = self._relatorio("pix")
        self.assertEqual([p["id"] for p in pix["pedidos"]], [self.pedido_pix.id])
        taxa = self._relatorio("taxa")
        self.assertEqual(taxa["pedidos"][0]["valor"], "5,00")
        self.assertEqual(taxa["pedidos"][0]["detalhe"], "Rua das Flores, 12")
        bebidas = self._relatorio("bebidas")
        self.assertEqual([(p["id"], p["valor"]) for p in bebidas["pedidos"]], [(self.pedido_pix.id, "17,00")])
        self.assertEqual([i["nome"] for i in bebidas["itens"]], ["Coca-Cola 2L", "Suco de laranja"])
        self.assertEqual(self._relatorio("cartao")["pedidos"], [])

    def test_relatorio_obedece_ao_filtro_de_pagamento(self):
        self.assertEqual(self._relatorio("total", "?periodo=hoje&pagamento=DINHEIRO")["valor"], "20,00")

    @override_settings(PALAVRAS_BEBIDAS=[])
    def test_relatorio_de_bebidas_sem_palavras(self):
        dados = self._relatorio("bebidas")
        self.assertEqual(dados["valor"], "0,00")
        self.assertEqual(dados["pedidos"], [])

    def test_relatorio_desconhecido_e_login(self):
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("relatorio_pedidos", args=["xyz"])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("relatorio_pedidos", args=["pix"])).status_code, 302)
        self.assertEqual(self.client.get(reverse("pedido_detalhes", args=[self.pedido_pix.id])).status_code, 302)

    def test_detalhe_do_pedido(self):
        self.client.force_login(self.usuario)
        dados = self.client.get(reverse("pedido_detalhes", args=[self.pedido_pix.id])).json()
        self.assertEqual([i["nome"] for i in dados["itens"]], ["Marmita", "Coca-Cola 2L", "Suco de laranja"])
        self.assertEqual([i["bebida"] for i in dados["itens"]], [False, True, True])
        self.assertEqual(dados["total"], "37,00")
        self.assertEqual(dados["taxa"], "5,00")
        self.assertEqual(dados["total_final"], "42,00")
        self.assertEqual(dados["desconto"], "")
        self.assertEqual(dados["unidades"], 4)
        self.assertEqual(dados["whatsapp"], "https://wa.me/5511987654321")
        self.assertIn("google.com/maps", dados["mapa"])
        self.assertEqual((dados["etapa"], dados["proximo"]), (0, "PREPARO"))
        self.assertEqual(self.client.get(reverse("pedido_detalhes", args=[999999])).status_code, 404)

    def test_detalhe_mostra_o_desconto(self):
        Pedidos.objects.filter(pk=self.pedido_dinheiro.pk).update(total=Decimal("18.00"))
        self.client.force_login(self.usuario)
        dados = self.client.get(reverse("pedido_detalhes", args=[self.pedido_dinheiro.id])).json()
        self.assertEqual((dados["bruto"], dados["desconto"], dados["total"]), ("20,00", "2,00", "18,00"))
        self.assertEqual(dados["whatsapp"], "")
        self.assertEqual(dados["mapa"], "")

    def test_pagina_de_pedidos_leva_os_detalhes_e_os_cartoes(self):
        self.client.force_login(self.usuario)
        resposta = self.client.get(reverse("pedidos") + "?periodo=hoje")
        self.assertIn(str(self.pedido_pix.id), resposta.context["pedidos_detalhes"])
        self.assertContains(resposta, 'id="pedidosPaginaJson"')
        for tipo in ["total", "dinheiro", "pix", "cartao", "misto", "taxa", "bebidas",
                     "retirada_dinheiro", "entrega_dinheiro", "caixa_dinheiro"]:
            self.assertContains(resposta, f'data-relatorio="{tipo}"')


class FotosDasBebidasTests(TestCase):
    """Bebida aparece inteira na foto (classe foto-bebida); comida continua cobrindo o quadro."""

    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        for nome_categoria, nome_produto in [("Bebidas", "Guaraná Lata"), ("Marmitas", "Marmita")]:
            categoria = CategoriaProdutos(nome_categoria=nome_categoria)
            categoria.imagem_categoria.name = "x.png"
            categoria.save()
            produto = Produtos(nome_produto=nome_produto, categoria=categoria, preco=Decimal("6.00"))
            produto.image_produto.name = f"produtos/{nome_produto}.png"
            produto.save()

    def _imagens(self, html):
        return {
            alt: "foto-bebida" in classes
            for alt, classes in re.findall(r'<img[^>]*alt="([^"]+)"[^>]*class="([^"]*)"', html, re.S)
            if alt in ("Guaraná Lata", "Marmita")
        }

    def test_cardapio(self):
        imagens = self._imagens(self.client.get(reverse("cardapio")).content.decode())
        self.assertEqual(imagens, {"Guaraná Lata": True, "Marmita": False})

    def test_caixa(self):
        self.client.force_login(self.usuario)
        imagens = self._imagens(self.client.get(reverse("caixa")).content.decode())
        self.assertEqual(imagens, {"Guaraná Lata": True, "Marmita": False})


class QuantidadeDasBebidasTests(TestCase):
    """Bebida abre o modal de quantidade (Caixa e cardápio); comida continua indo direto."""

    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        self.produtos = {}
        for nome_categoria, nome_produto in [("Bebidas", "Guaraná Lata"), ("Marmitas", "Marmita")]:
            categoria = CategoriaProdutos(nome_categoria=nome_categoria)
            categoria.imagem_categoria.name = "x.png"
            categoria.save()
            self.produtos[nome_produto] = Produtos.objects.create(
                nome_produto=nome_produto, categoria=categoria, preco=Decimal("6.00")
            )

    def test_caixa(self):
        self.client.force_login(self.usuario)
        html = self.client.get(reverse("caixa")).content.decode()
        guarana, marmita = self.produtos["Guaraná Lata"].id, self.produtos["Marmita"].id
        self.assertEqual(html.count(f"abrirQtdBebida(this, '{guarana}'"), 2)   # foto e nome do card
        self.assertNotIn(f"addProduto('{guarana}'", html)
        # comida sem adicionais: um clique, com o efeito de entrar no carrinho
        self.assertEqual(html.count(f"adicionarComEfeito(this, '{marmita}'"), 2)
        self.assertIn("core/js/quantidade.js", html)

    def test_bebida_com_adicionais_continua_no_modal_de_adicionais(self):
        from .models import Adicional
        Adicional.objects.create(nome="Gelo e limão", preco=Decimal("1.00")).produtos.add(self.produtos["Guaraná Lata"])
        self.client.force_login(self.usuario)
        html = self.client.get(reverse("caixa")).content.decode()
        guarana = self.produtos["Guaraná Lata"].id
        self.assertNotIn(f"abrirQtdBebida(this, '{guarana}'", html)
        # o modal de adicionais recebe o card (foto e cor do efeito)
        self.assertEqual(html.count(f"abrirModalAdicionais('{guarana}', 'Guaraná Lata', '6.00', this)"), 3)

    def test_cardapio(self):
        html = self.client.get(reverse("cardapio")).content.decode()
        cards = dict(re.findall(r'data-id="(\d+)"[^>]*?(data-bebida="1")?>', html))
        self.assertEqual(cards[str(self.produtos["Guaraná Lata"].id)], 'data-bebida="1"')
        self.assertEqual(cards[str(self.produtos["Marmita"].id)], "")
        self.assertIn("core/js/quantidade.js", html)


class CoresDasCategoriasTests(TestCase):
    """Cada categoria tem a sua cor no efeito de entrar no carrinho; bebida é sempre azul."""

    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        self.produtos = {}
        for nome_categoria, nome_produto in [("Marmitas", "Marmita"), ("Bebidas", "Guaraná Lata"), ("Pratos", "Feijoada")]:
            categoria = CategoriaProdutos(nome_categoria=nome_categoria)
            categoria.imagem_categoria.name = "x.png"
            categoria.save()
            self.produtos[nome_produto] = Produtos.objects.create(
                nome_produto=nome_produto, categoria=categoria, preco=Decimal("10.00")
            )

    def test_cores(self):
        from .views import COR_BEBIDA, PALETA_CATEGORIAS, _cores_das_categorias
        cores = _cores_das_categorias()
        p = self.produtos
        self.assertEqual(cores[p["Marmita"].categoria_id], PALETA_CATEGORIAS[0])
        self.assertEqual(cores[p["Guaraná Lata"].categoria_id], COR_BEBIDA)
        self.assertEqual(cores[p["Feijoada"].categoria_id], PALETA_CATEGORIAS[1])

    def test_caixa_e_cardapio_levam_a_cor(self):
        from .views import COR_BEBIDA, PALETA_CATEGORIAS
        self.client.force_login(self.usuario)
        html = self.client.get(reverse("caixa")).content.decode()
        self.assertEqual(html.count(f'data-cor="{COR_BEBIDA}"'), 1)
        self.assertEqual(html.count(f'data-cor="{PALETA_CATEGORIAS[0]}"'), 1)
        catalogo = self.client.get(reverse("cardapio")).context["catalogo"]
        self.assertEqual(catalogo[str(self.produtos["Guaraná Lata"].id)]["cor"], COR_BEBIDA)
        self.assertEqual(catalogo[str(self.produtos["Feijoada"].id)]["cor"], PALETA_CATEGORIAS[1])


class RefriNosAdicionaisTests(TestCase):
    """O refri de R$ 2 abre a lista de adicionais da marmita, com foto e em azul."""

    def setUp(self):
        from .models import Adicional
        self.categorias = {}
        for nome_categoria in ("Marmitas", "Bebidas"):
            categoria = CategoriaProdutos(nome_categoria=nome_categoria)
            categoria.imagem_categoria.name = "x.png"
            categoria.save()
            self.categorias[nome_categoria] = categoria
        self.marmita = Produtos.objects.create(
            nome_produto="Marmitex Frango", categoria=self.categorias["Marmitas"], preco=Decimal("10.00")
        )
        # o refri foi cadastrado por último, depois dos outros adicionais
        for nome, preco, ativo in [("Ovo", "3.00", True), ("Bacon", "4.00", False),
                                   ("Mistura Extra", "5.00", True), ("Refri", "2.00", True)]:
            Adicional.objects.create(nome=nome, preco=Decimal(preco), ativo=ativo).produtos.add(self.marmita)

    def _lista(self):
        resposta = self.client.get(f"/produto/{self.marmita.id}/adicionais/")
        self.assertEqual(resposta.status_code, 200)
        return resposta.json()

    def test_refri_vem_primeiro_com_foto_e_em_azul(self):
        from .views import COR_BEBIDA
        refri, ovo, mistura = self._lista()
        self.assertEqual((refri["nome"], refri["preco"], refri["bebida"], refri["cor"]), ("Refri", "2.00", True, COR_BEBIDA))
        self.assertIn("core/img/refri.svg?v=", refri["imagem"])
        # os outros continuam na ordem do cadastro, sem foto e sem destaque
        self.assertEqual([ovo["nome"], mistura["nome"]], ["Ovo", "Mistura Extra"])
        self.assertEqual((ovo["bebida"], ovo["imagem"], ovo["cor"]), (False, "", ""))

    def test_foto_do_produto_de_mesmo_nome(self):
        from .models import Adicional
        Adicional.objects.filter(nome="Refri").update(nome="Coca-Cola Lata")
        coca = Produtos(nome_produto="coca-cola lata", categoria=self.categorias["Bebidas"], preco=Decimal("6.00"), ativo=False)
        coca.image_produto.name = "produtos/coca.png"
        coca.save()
        primeiro = self._lista()[0]
        self.assertEqual(primeiro["nome"], "Coca-Cola Lata")
        self.assertTrue(primeiro["imagem"].endswith("produtos/coca.png"))

    def test_nomes_de_refri_contam_como_bebida(self):
        from .views import _adicional_eh_bebida
        for nome in ("Refri", "Refrigerante lata", "GUARANÁ", "Coca 350ml", "Suco de laranja"):
            self.assertTrue(_adicional_eh_bebida(nome), nome)
        for nome in ("Ovo", "Mistura Extra", "Alface c/ Tomate", "Pipoca", "Mandioca"):
            self.assertFalse(_adicional_eh_bebida(nome), nome)


@override_settings(
    MARMITA_PRECO_COM_REFRI=Decimal("10.00"),
    PALAVRAS_BEBIDAS=["bebida", "refri", "suco", "cerveja", "drink"],
)
class RefriDaMarmitaTests(TestCase):
    """O REFRI-MINI 200ml (produto de R$ 2 das bebidas) é o primeiro adicional
    de toda marmita de R$ 10 e vai para o carrinho como o próprio produto."""

    def setUp(self):
        from .models import Adicional
        cache.clear()
        self.usuario = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        categorias = {}
        for nome_categoria in ("Marmitas", "Bebidas"):
            categoria = CategoriaProdutos(nome_categoria=nome_categoria)
            categoria.imagem_categoria.name = "x.png"
            categoria.save()
            categorias[nome_categoria] = categoria
        self.refri = Produtos(nome_produto="REFRI-MINI 200ml", categoria=categorias["Bebidas"], preco=Decimal("2.00"))
        self.refri.image_produto.name = "produtos/refri-mini.png"
        self.refri.save()
        # bebidas que não são o refri da marmita: não é refri / cara demais
        Produtos.objects.create(nome_produto="Água mineral", categoria=categorias["Bebidas"], preco=Decimal("2.00"))
        Produtos.objects.create(nome_produto="Coca-Cola 2L", categoria=categorias["Bebidas"], preco=Decimal("18.00"))

        def marmita(nome, preco):
            return Produtos.objects.create(nome_produto=nome, categoria=categorias["Marmitas"], preco=Decimal(preco))
        self.frango = marmita("MARMITEX FILE DE FRANGO", "10.00")
        self.carne = marmita("Marmitex Carne", "10.00")      # sem nenhum adicional cadastrado
        self.grande = marmita("Marmitex Grande", "15.00")
        Adicional.objects.create(nome="Ovo", preco=Decimal("3.00")).produtos.add(self.frango)

    def _lista(self, produto):
        resposta = self.client.get(f"/produto/{produto.id}/adicionais/")
        self.assertEqual(resposta.status_code, 200)
        return resposta.json()

    def test_refri_abre_a_lista_das_marmitas_de_10(self):
        from .views import COR_BEBIDA
        refri, ovo = self._lista(self.frango)
        self.assertEqual(
            (refri["nome"], refri["preco"], refri["produto_id"], refri["bebida"], refri["cor"]),
            ("REFRI-MINI 200ml", "2.00", self.refri.id, True, COR_BEBIDA),
        )
        self.assertTrue(refri["imagem"].endswith("produtos/refri-mini.png"))
        self.assertEqual(ovo["nome"], "Ovo")
        self.assertNotIn("produto_id", ovo)
        self.assertEqual([a["nome"] for a in self._lista(self.carne)], ["REFRI-MINI 200ml"])
        self.assertEqual(self._lista(self.grande), [])
        self.assertEqual(self._lista(self.refri), [])

    def test_marmita_de_10_sem_adicionais_abre_o_modal(self):
        self.client.force_login(self.usuario)
        html = self.client.get(reverse("caixa")).content.decode()
        self.assertEqual(html.count(f"abrirModalAdicionais('{self.carne.id}'"), 3)
        self.assertEqual(html.count(f"adicionarComEfeito(this, '{self.grande.id}'"), 2)
        catalogo = self.client.get(reverse("cardapio")).context["catalogo"]
        self.assertIs(catalogo[str(self.carne.id)]["tem_adicionais"], True)
        self.assertIs(catalogo[str(self.grande.id)]["tem_adicionais"], False)
        self.assertIs(catalogo[str(self.refri.id)]["tem_adicionais"], False)

    def test_refri_desativado_nada_muda(self):
        self.refri.ativo = False
        self.refri.save()
        self.assertEqual([a["nome"] for a in self._lista(self.frango)], ["Ovo"])
        self.assertEqual(self._lista(self.carne), [])
        self.client.force_login(self.usuario)
        html = self.client.get(reverse("caixa")).content.decode()
        self.assertEqual(html.count(f"adicionarComEfeito(this, '{self.carne.id}'"), 2)

    def test_venda_leva_o_refri_na_propria_linha(self):
        self.client.force_login(self.usuario)
        venda = {
            "carrinho": [
                {"id": str(self.frango.id), "qtd": 2, "adicionais": [{"nome": "Ovo", "preco": 3, "qtd": 1}]},
                {"id": str(self.refri.id), "qtd": 2, "adicionais": []},
            ],
            "nome_cliente": "Maria",
            "metodo_pagamento": "PIX",
            "tipo_entrega": "retirada",
            "endereco": {},
        }
        resposta = self.client.post(reverse("caixa"), venda, content_type="application/json",
                                    HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        pedido = Pedidos.objects.get()
        self.assertEqual(pedido.total, Decimal("30.00"))   # 2 × (10 + 3) + 2 × 2
        refri = pedido.itens.get(produto=self.refri)
        self.assertEqual((refri.quantidade, refri.subtotal, refri.adicionais), (2, Decimal("4.00"), []))


class QZAssinaturaTests(TestCase):
    """Certificado e assinatura do QZ Tray (precisam de login e conferem)."""

    def test_certificado_e_assinatura(self):
        import base64
        import tempfile
        from pathlib import Path
        from unittest import mock

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from django.contrib.auth.models import User
        from django.test import Client, override_settings

        chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        import datetime
        nome = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "teste")])
        agora = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome)
                .public_key(chave.public_key()).serial_number(1)
                .not_valid_before(agora - datetime.timedelta(days=1))
                .not_valid_after(agora + datetime.timedelta(days=30))
                .sign(chave, hashes.SHA512()))
        cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        chave_pem = chave.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()

        with override_settings(QZ_CERTIFICADO=cert_pem, QZ_CHAVE_PRIVADA=chave_pem):
            c = Client(enforce_csrf_checks=True)
            # o certificado é público (o PC do caixa baixa para o QZ); assinar exige login
            self.assertEqual(c.get("/api/qz/certificado/").status_code, 200)
            self.assertEqual(c.post("/api/qz/assinar/", data=b"x", content_type="text/plain",
                                    HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value).status_code, 401)
            User.objects.create_user("caixa", password="x")
            c.login(username="caixa", password="x")
            r = c.get("/api/qz/certificado/")
            self.assertEqual(r.status_code, 200)
            texto = b'{"call":"printers.find","timestamp":1}'
            r = c.post("/api/qz/assinar/", data=texto, content_type="text/plain",
                       HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)
            self.assertEqual(r.status_code, 200)
            cert.public_key().verify(base64.b64decode(r.content), texto,
                                     padding.PKCS1v15(), hashes.SHA512())


def _par_qz():
    """Um certificado e a chave dele, no formato do qz/gerar_certificado.py."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "G3 Bar e Restaurante - Impressao")])
    agora = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome)
            .public_key(chave.public_key()).serial_number(7)
            .not_valid_before(agora - datetime.timedelta(days=1))
            .not_valid_after(agora + datetime.timedelta(days=365))
            .sign(chave, hashes.SHA512()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode())


class QZDiagnosticoTests(TestCase):
    """O diagnóstico diz exatamente o que está errado nas variáveis do QZ."""

    def setUp(self):
        self.usuario = User.objects.create_user("caixa", password="x")
        self.cert, self.chave = _par_qz()

    def _diagnostico(self):
        self.client.force_login(self.usuario)
        return self.client.get(reverse("qz_diagnostico")).json()

    def test_exige_login(self):
        self.assertEqual(self.client.get(reverse("qz_diagnostico")).status_code, 401)

    def test_tudo_certo(self):
        with override_settings(QZ_CERTIFICADO=self.cert, QZ_CHAVE_PRIVADA=self.chave):
            d = self._diagnostico()
        self.assertEqual((d["certificado"], d["chave"], d["par_confere"], d["vencido"]), ("ok", "ok", True, False))
        self.assertEqual(d["titular"], "G3 Bar e Restaurante - Impressao")
        self.assertRegex(d["sha1"], r"^([0-9A-F]{2}:){19}[0-9A-F]{2}$")
        self.assertEqual(d["problemas"], [])

    def test_chave_de_outro_par(self):
        _, outra_chave = _par_qz()
        with override_settings(QZ_CERTIFICADO=self.cert, QZ_CHAVE_PRIVADA=outra_chave):
            d = self._diagnostico()
        self.assertIs(d["par_confere"], False)
        self.assertIn("NÃO é a do certificado", " ".join(d["problemas"]))

    def test_sem_variaveis(self):
        with override_settings(QZ_CERTIFICADO="", QZ_CHAVE_PRIVADA=""), \
                mock.patch("core.qz_seguranca._PASTA_LOCAL", Path("/nao/existe")):
            d = self._diagnostico()
            self.assertEqual(self.client.get(reverse("qz_certificado")).status_code, 503)
        self.assertEqual((d["certificado"], d["chave"], d["par_confere"]), ("ausente", "ausente", None))
        self.assertEqual(len(d["problemas"]), 2)

    def test_valor_colado_com_aspas_e_barra_n(self):
        """Como costuma ser colado na Vercel: entre aspas e com \n literal."""
        uma_linha = lambda pem: '"' + pem.strip().replace("\n", "\\n") + '"'
        with override_settings(QZ_CERTIFICADO=uma_linha(self.cert), QZ_CHAVE_PRIVADA="QZ_CHAVE_PRIVADA=" + uma_linha(self.chave)):
            d = self._diagnostico()
            resposta = self.client.get(reverse("qz_certificado") + "?baixar=1")
        self.assertIs(d["par_confere"], True)
        self.assertEqual(resposta.content.decode(), self.cert.strip() + "\n")
        self.assertIn('filename="override.crt"', resposta["Content-Disposition"])

    def test_certificado_invalido(self):
        with override_settings(QZ_CERTIFICADO="-----BEGIN CERTIFICATE-----\nlixo\n-----END CERTIFICATE-----",
                               QZ_CHAVE_PRIVADA=self.chave):
            d = self._diagnostico()
        self.assertEqual(d["certificado"], "invalido")
        self.assertIn("não é um certificado válido", " ".join(d["problemas"]))



# ===================================================================== G3
import json

from .estoque import movimentar
from .models import ConfigLoja, MovimentoEstoque


class ViraANoiteTests(TestCase):
    """Bar que fecha depois da meia-noite: a madrugada é do dia anterior."""

    @override_settings(LOJA_DIAS=(3, 4, 5), LOJA_ABRE=18, LOJA_FECHA=2)
    def test_madrugada_pertence_ao_dia_anterior(self):
        self.assertTrue(loja_aberta(quando(2026, 10, 1, 23)))      # quinta 23h
        self.assertTrue(loja_aberta(quando(2026, 10, 2, 1)))       # sexta 1h (noite de quinta)
        self.assertFalse(loja_aberta(quando(2026, 10, 2, 3)))      # sexta 3h
        self.assertFalse(loja_aberta(quando(2026, 10, 1, 1)))      # quinta 1h (noite de quarta: fechado)
        self.assertFalse(loja_aberta(quando(2026, 9, 30, 20)))     # quarta 20h


class BaseG3(TestCase):
    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_user("caixa", password="senha-forte-123")
        self.bebidas = CategoriaProdutos.objects.create(nome_categoria="Garrafas")
        self.doses = CategoriaProdutos.objects.create(nome_categoria="Doses")
        self.almoco = CategoriaProdutos.objects.create(nome_categoria="Almoço")
        self.garrafa = Produtos.objects.create(
            nome_produto="Whisky 1L", categoria=self.bebidas, preco=Decimal("180.00"), custo=Decimal("100.00"),
            controla_estoque=True, estoque_minimo=Decimal("2"),
        )
        movimentar(self.garrafa, 3, MovimentoEstoque.Tipo.ENTRADA)
        self.dose = Produtos.objects.create(
            nome_produto="Dose Whisky", categoria=self.doses, preco=Decimal("16.00"),
            produto_base=self.garrafa, fracoes_por_unidade=20,
        )
        self.cerveja = Produtos.objects.create(
            nome_produto="Cerveja 600ml", categoria=self.bebidas, preco=Decimal("12.00"), custo=Decimal("6.00"),
            controla_estoque=True, estoque_minimo=Decimal("12"),
        )
        movimentar(self.cerveja, 24, MovimentoEstoque.Tipo.ENTRADA)
        self.prato = Produtos.objects.create(nome_produto="Prato Feito", categoria=self.almoco, preco=Decimal("25.00"))

    def vender(self, carrinho, **extra):
        self.client.force_login(self.usuario)
        dados = {"carrinho": carrinho, "nome_cliente": "Balcão", "metodo_pagamento": "PIX",
                 "tipo_entrega": "retirada", **extra}
        return self.client.post(reverse("caixa"), data=json.dumps(dados), content_type="application/json")

    def saldo(self, produto):
        produto.refresh_from_db()
        return produto.estoque_atual


class EstoqueNaVendaTests(BaseG3):
    def test_venda_da_baixa_e_a_dose_tira_a_fracao_da_garrafa(self):
        resposta = self.vender([
            {"id": self.dose.id, "qtd": 4}, {"id": self.cerveja.id, "qtd": 2}, {"id": self.prato.id, "qtd": 1},
        ])
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(self.saldo(self.garrafa), Decimal("2.800"))    # 4 doses de 1/20
        self.assertEqual(self.saldo(self.cerveja), Decimal("22.000"))
        pedido = Pedidos.objects.get()
        self.assertEqual(pedido.total, Decimal("113.00"))
        self.assertEqual(pedido.movimentos_estoque.count(), 2)          # prato não tem estoque
        # custo guardado na venda: a dose custa 1/20 da garrafa
        custos = {i.produto_id: i.custo_unitario for i in pedido.itens.all()}
        self.assertEqual(custos[self.dose.id], Decimal("5.00"))
        self.assertEqual(custos[self.cerveja.id], Decimal("6.00"))

    def test_venda_avisa_quando_chega_no_minimo(self):
        resposta = self.vender([{"id": self.cerveja.id, "qtd": 12}])
        avisos = resposta.json()["estoque_baixo"]
        self.assertEqual([(a["nome"], a["situacao"]) for a in avisos], [("Cerveja 600ml", "baixo")])

    def test_preco_vem_do_banco_e_nao_do_navegador(self):
        self.vender([{"id": self.prato.id, "qtd": 1, "preco": "0.01"}])
        self.assertEqual(Pedidos.objects.get().total, Decimal("25.00"))

    def test_cancelar_devolve_o_estoque_uma_vez_so(self):
        self.vender([{"id": self.dose.id, "qtd": 10}, {"id": self.cerveja.id, "qtd": 5}])
        pedido = Pedidos.objects.get()
        for _ in range(2):
            resposta = self.client.post(reverse("cancelar_pedido", args=[pedido.id]))
            self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.saldo(self.garrafa), Decimal("3.000"))
        self.assertEqual(self.saldo(self.cerveja), Decimal("24.000"))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "CANCELADO")

    def test_cancelar_exige_login(self):
        self.vender([{"id": self.prato.id, "qtd": 1}])
        self.client.logout()
        resposta = self.client.post(reverse("cancelar_pedido", args=[Pedidos.objects.get().id]))
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Pedidos.objects.get().status, "PAGO")


class TelaDeEstoqueTests(BaseG3):
    def lancar(self, **dados):
        self.client.force_login(self.usuario)
        return self.client.post(reverse("estoque"), data=dados)

    def test_compra_soma_e_atualiza_o_custo(self):
        self.lancar(produto=self.cerveja.id, tipo="ENTRADA", quantidade="12", custo="6,50")
        self.assertEqual(self.saldo(self.cerveja), Decimal("36.000"))
        self.assertEqual(self.cerveja.custo, Decimal("6.50"))

    def test_contagem_acerta_o_saldo_pela_diferenca(self):
        self.lancar(produto=self.cerveja.id, tipo="AJUSTE", quantidade="20")
        self.assertEqual(self.saldo(self.cerveja), Decimal("20.000"))
        self.assertEqual(self.cerveja.movimentos.first().quantidade, Decimal("-4.000"))

    def test_perda_tira_do_saldo(self):
        self.lancar(produto=self.garrafa.id, tipo="PERDA", quantidade="0,5", observacao="quebrou")
        self.assertEqual(self.saldo(self.garrafa), Decimal("2.500"))

    def test_quantidade_invalida_nao_mexe(self):
        for quantidade in ("", "abc", "-3", "NaN"):
            resposta = self.lancar(produto=self.cerveja.id, tipo="ENTRADA", quantidade=quantidade)
            self.assertEqual(resposta.status_code, 400, quantidade)
        self.assertEqual(self.saldo(self.cerveja), Decimal("24.000"))

    def test_telas_de_gestao_abrem(self):
        self.client.force_login(self.usuario)
        for nome in ("estoque", "produtos"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200, nome)
        self.assertEqual(self.client.get(reverse("estoque_historico", args=[self.cerveja.id])).status_code, 200)
        # Dados da casa: só administrador.
        self.assertEqual(self.client.get(reverse("configuracoes")).status_code, 302)
        self.usuario.is_staff = True
        self.usuario.save()
        self.assertEqual(self.client.get(reverse("configuracoes")).status_code, 200)

    def test_telas_de_gestao_exigem_login(self):
        for nome in ("estoque", "produtos", "configuracoes", "pedidos_novos"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 302, nome)


class CadastroDeProdutoTests(BaseG3):
    def salvar(self, **dados):
        self.client.force_login(self.usuario)
        base = {"nome_produto": "Novo", "categoria": self.bebidas.id, "preco": "10,00", "unidade": "UN", "ativo": "on"}
        return self.client.post(reverse("produtos"), data={**base, **dados})

    def test_cria_com_estoque_inicial(self):
        resposta = self.salvar(nome_produto="Vodka 1L", controla_estoque="on", estoque_minimo="2",
                               estoque_inicial="6", custo="30,00")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        vodka = Produtos.objects.get(nome_produto="Vodka 1L")
        self.assertEqual(vodka.estoque_atual, Decimal("6.000"))
        self.assertEqual(vodka.custo, Decimal("30.00"))

    def test_edita_sem_perder_o_saldo(self):
        self.salvar(id=self.cerveja.id, nome_produto="Cerveja Pilsen 600ml", preco="13,50",
                    controla_estoque="on", estoque_minimo="12")
        self.cerveja.refresh_from_db()
        self.assertEqual((self.cerveja.nome_produto, self.cerveja.preco), ("Cerveja Pilsen 600ml", Decimal("13.50")))
        self.assertEqual(self.cerveja.estoque_atual, Decimal("24.000"))

    def test_dose_precisa_de_garrafa_com_estoque(self):
        resposta = self.salvar(nome_produto="Dose errada", produto_base=self.prato.id, fracoes_por_unidade="20")
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Produtos.objects.filter(nome_produto="Dose errada").exists())


@override_settings(LOJA_DIAS=(0, 1, 2, 3, 4, 5, 6), LOJA_ABRE=0, LOJA_FECHA=24)
class PedidoOnlineTests(BaseG3):
    def pedir(self, **extra):
        dados = {
            "nome": "Maria", "telefone": "(11) 98888-7777", "tipo": "entrega", "rua": "Rua A", "numero": "10",
            "pagamento": "Dinheiro", "troco": "100", "carrinho": [{"id": self.prato.id, "qtd": 2}], **extra,
        }
        return self.client.post(reverse("pedido_online"), data=json.dumps(dados), content_type="application/json")

    def test_pedido_entra_como_recebido_com_a_taxa_da_casa(self):
        ConfigLoja.objects.update_or_create(pk=1, defaults={"taxa_entrega": Decimal("7.00")})
        cache.clear()
        resposta = self.pedir()
        self.assertEqual(resposta.status_code, 200, resposta.content)
        pedido = Pedidos.objects.get()
        self.assertEqual((pedido.status, pedido.origem), ("RECEBIDO", "ONLINE"))
        self.assertEqual((pedido.total, pedido.taxa_motoca, pedido.troco_para),
                         (Decimal("50.00"), Decimal("7.00"), Decimal("100.00")))
        self.assertEqual(resposta.json()["acompanhar"], f"/acompanhar/{pedido.codigo}/")

    def test_cliente_acompanha_cada_etapa(self):
        self.pedir()
        pedido = Pedidos.objects.get()
        url = reverse("acompanhar_dados", args=[pedido.codigo])
        self.assertEqual(self.client.get(reverse("acompanhar", args=[pedido.codigo])).status_code, 200)
        self.assertEqual(self.client.get(url).json()["etapa"], 0)

        self.client.force_login(self.usuario)
        for etapa, status in [(1, "PREPARO"), (2, "ENTREGA")]:
            self.client.post(reverse("avancar_status", args=[pedido.id]), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            dados = self.client.get(url).json()
            self.assertEqual((dados["etapa"], dados["status"]), (etapa, status))
            self.assertTrue(dados["etapas"][etapa]["hora"])
        # dinheiro: finalizar pede a confirmação
        self.client.post(reverse("avancar_status", args=[pedido.id]), {"confirmacao": "confirmar"},
                         HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTrue(self.client.get(url).json()["finalizado"])

    def test_codigo_errado_nao_mostra_pedido(self):
        self.pedir()
        self.assertEqual(self.client.get("/acompanhar/nao-existe/").status_code, 404)

    def test_recusa_dados_incompletos_e_produto_esgotado(self):
        self.assertEqual(self.pedir(nome="").status_code, 400)
        self.assertEqual(self.pedir(telefone="123").status_code, 400)
        self.assertEqual(self.pedir(rua="").status_code, 400)
        self.assertEqual(self.pedir(pagamento="fiado").status_code, 400)
        self.assertEqual(self.pedir(carrinho=[]).status_code, 400)
        movimentar(self.garrafa, -3, MovimentoEstoque.Tipo.PERDA)
        self.assertEqual(self.pedir(carrinho=[{"id": self.dose.id, "qtd": 1}]).status_code, 400)
        self.assertEqual(Pedidos.objects.count(), 0)

    def test_fechado_ou_pausado_nao_aceita(self):
        with override_settings(LOJA_DIAS=()):
            self.assertEqual(self.pedir().status_code, 403)
        ConfigLoja.objects.update_or_create(pk=1, defaults={"aceita_pedidos_online": False})
        cache.clear()
        self.assertEqual(self.pedir().status_code, 403)
        self.assertEqual(Pedidos.objects.count(), 0)

    def test_limite_de_pedidos_por_aparelho(self):
        from .gestao import LIMITE_PEDIDOS_ONLINE
        for _ in range(LIMITE_PEDIDOS_ONLINE):
            self.assertEqual(self.pedir().status_code, 200)
        self.assertEqual(self.pedir().status_code, 429)

    def test_painel_avisa_pedido_novo(self):
        self.pedir()
        self.client.force_login(self.usuario)
        dados = self.client.get(reverse("pedidos_novos")).json()
        self.assertEqual((dados["quantidade"], dados["pedidos"][0]["cliente"]), (1, "Maria"))
        self.assertContains(self.client.get(reverse("pedidos")), "Pedido online")

    def test_cardapio_marca_o_esgotado(self):
        movimentar(self.garrafa, -3, MovimentoEstoque.Tipo.PERDA)
        cache.clear()
        resposta = self.client.get(reverse("cardapio"))
        self.assertTrue(resposta.context["catalogo"][str(self.dose.id)]["esgotado"])
        self.assertFalse(resposta.context["catalogo"][str(self.cerveja.id)]["esgotado"])
