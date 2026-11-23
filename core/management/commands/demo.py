"""Cardápio de exemplo de bar e restaurante (almoço, porções, doses, bebidas)
para conhecer o sistema: python manage.py demo

Não apaga nada: só cria o que ainda não existe (pelo nome)."""
from decimal import Decimal

from django.core.management.base import BaseCommand

from core.estoque import movimentar
from core.models import CategoriaProdutos, MovimentoEstoque, PratoDoDia, Produtos

# (nome, preço, custo, unidade, estoque inicial, mínimo, descrição)
CARDAPIO = {
    "Almoço": [
        ("Prato Feito - Bife Acebolado", "24.90", "9.50", "PR", None, 0, "Arroz, feijão, bife acebolado, fritas e salada"),
        ("Prato Feito - Frango Grelhado", "22.90", "8.00", "PR", None, 0, "Arroz, feijão, filé de frango, purê e salada"),
        ("Feijoada Completa", "34.90", "13.00", "PR", None, 0, "Com arroz, couve, torresmo e laranja"),
        ("Marmitex Tradicional", "18.00", "6.50", "UN", None, 0, "Arroz, feijão, uma carne e guarnição"),
    ],
    "Porções": [
        ("Batata Frita", "28.00", "8.00", "PC", None, 0, "Porção de 500 g"),
        ("Calabresa Acebolada", "36.00", "13.00", "PC", None, 0, "Com pão e vinagrete"),
        ("Torresmo", "32.00", "11.00", "PC", None, 0, "Crocante, com limão"),
        ("Frango a Passarinho", "39.00", "14.00", "PC", None, 0, "Com alho frito"),
    ],
    "Cervejas": [
        ("Cerveja Pilsen 600ml", "12.00", "6.20", "GF", 72, 24, "Garrafa bem gelada"),
        ("Cerveja Puro Malte 600ml", "15.00", "8.10", "GF", 48, 24, ""),
        ("Cerveja Long Neck", "9.00", "4.60", "GF", 36, 12, ""),
        ("Cerveja Lata 350ml", "6.00", "3.10", "LT", 60, 24, ""),
    ],
    "Bebidas": [
        ("Refrigerante Lata", "6.00", "2.80", "LT", 48, 12, "Coca, Guaraná ou Fanta"),
        ("Refrigerante 2L", "14.00", "7.50", "GF", 12, 4, ""),
        ("Água Mineral 500ml", "4.00", "1.20", "GF", 40, 12, "Com ou sem gás"),
        ("Suco Natural", "9.00", "3.00", "UN", None, 0, "Laranja, limão ou maracujá"),
        ("Energético Lata", "14.00", "7.90", "LT", 6, 8, ""),
    ],
    "Garrafas": [
        ("Whisky Red Label 1L", "189.00", "98.00", "GF", 4, 2, "Garrafa fechada"),
        ("Cachaça Artesanal 700ml", "79.00", "32.00", "GF", 5, 2, "Garrafa fechada"),
        ("Vodka 998ml", "69.00", "31.00", "GF", 3, 2, "Garrafa fechada"),
        ("Campari 900ml", "95.00", "52.00", "GF", 0, 1, "Garrafa fechada"),
    ],
}
# (nome da dose, preço, garrafa, doses por garrafa)
DOSES = [
    ("Dose Whisky Red Label", "16.00", "Whisky Red Label 1L", 20),
    ("Dose Cachaça Artesanal", "7.00", "Cachaça Artesanal 700ml", 14),
    ("Dose Vodka", "9.00", "Vodka 998ml", 20),
    ("Dose Campari", "12.00", "Campari 900ml", 18),
]
PRATOS_DO_DIA = {0: "Prato Feito - Bife Acebolado", 1: "Prato Feito - Frango Grelhado",
                 2: "Feijoada Completa", 3: "Prato Feito - Bife Acebolado",
                 4: "Prato Feito - Frango Grelhado", 5: "Feijoada Completa"}


class Command(BaseCommand):
    help = "Cria um cardápio de exemplo (bar e restaurante)."

    def handle(self, *args, **options):
        criados = 0
        codigo = Produtos.objects.count() + 1
        for ordem, (nome_categoria, itens) in enumerate(CARDAPIO.items(), start=1):
            categoria, _ = CategoriaProdutos.objects.get_or_create(
                nome_categoria=nome_categoria, defaults={"ordem": ordem}
            )
            for nome, preco, custo, unidade, inicial, minimo, descricao in itens:
                if Produtos.objects.filter(nome_produto=nome).exists():
                    continue
                produto = Produtos.objects.create(
                    nome_produto=nome, preco=Decimal(preco), custo=Decimal(custo), unidade=unidade,
                    categoria=categoria, descricao=descricao, codigo=codigo,
                    controla_estoque=inicial is not None, estoque_minimo=minimo,
                )
                codigo += 1
                criados += 1
                if inicial:
                    movimentar(produto, inicial, MovimentoEstoque.Tipo.ENTRADA, usuario="demo",
                               observacao="Estoque inicial (demonstração)", custo_unitario=Decimal(custo))

        doses, _ = CategoriaProdutos.objects.get_or_create(nome_categoria="Doses", defaults={"ordem": 6})
        for nome, preco, garrafa, fracoes in DOSES:
            base = Produtos.objects.filter(nome_produto=garrafa).first()
            if not base or Produtos.objects.filter(nome_produto=nome).exists():
                continue
            Produtos.objects.create(
                nome_produto=nome, preco=Decimal(preco), unidade="DS", categoria=doses, codigo=codigo,
                produto_base=base, fracoes_por_unidade=fracoes, descricao=f"Dose de {garrafa}",
            )
            codigo += 1
            criados += 1

        for dia, nome in PRATOS_DO_DIA.items():
            produto = Produtos.objects.filter(nome_produto=nome).first()
            if produto:
                PratoDoDia.objects.get_or_create(produto=produto, dia_semana=dia)

        self.stdout.write(self.style.SUCCESS(f"Pronto: {criados} produto(s) de exemplo criado(s)."))
