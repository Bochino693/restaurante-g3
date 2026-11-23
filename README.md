# G3 Bar e Restaurante — sistema de gestão

Django 5.2 · Caixa (PDV), pedidos online com acompanhamento, estoque com
doses, cadastro de produtos com foto, impressão das notinhas e estatísticas.
Feito sobre o sistema do outro restaurante (mesma hospedagem: Vercel, banco no
Supabase e fotos na Cloudinary).

## O que tem

| Tela | Endereço | Para quê |
| --- | --- | --- |
| Cardápio do cliente | `/` | Fotos, busca, carrinho e **pedido online** (entrega ou retirada) |
| Acompanhamento | `/acompanhar/<código>/` | O cliente vê as etapas, a previsão e o mapa, atualizando sozinho |
| Caixa | `/caixa/` | Venda no balcão, com o saldo de estoque em cada produto |
| Pedidos | `/pedidos/` | Pedidos do dia, relatórios por pagamento, aceitar/avançar/cancelar |
| Produtos | `/produtos/` | Cadastro com foto, preço, custo, margem e tipo de estoque |
| Estoque | `/estoque/` | Saldos, alertas, compras, perdas, contagem, histórico e gráficos |
| Cardápio do dia | `/admin-cardapio/pratos-do-dia/` | Prato do dia de cada dia da semana |
| Estatísticas | `/dashboards/` | Faturamento, **lucro**, pagamentos, produtos, bebidas, horários |
| Dados da casa | `/configuracoes/` | Nome, logo, WhatsApp, endereço, taxa de entrega, pedido mínimo |

Adicionais dos produtos e usuários ficam no Painel Admin (`/admin/`).

## Começar (no computador)

```
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py demo          # opcional: cardápio de exemplo de bar e restaurante
python manage.py runserver
```

Depois: entre em `/login/`, abra **Dados da casa** e preencha nome, WhatsApp,
endereço, cidade e taxa de entrega. Em **Produtos**, cadastre os itens com foto.

## Pedido online, em fases

1. O cliente monta o carrinho no cardápio, informa nome, telefone, entrega ou
   retirada e a forma de pagamento (paga ao receber). O preço e o estoque são
   conferidos no servidor.
2. O pedido entra como **Recebido**. Em qualquer tela do painel toca um aviso
   e aparece o cartão "Novo pedido online"; a lista de Pedidos se atualiza.
3. **Aceitar** leva para *Em preparo* e já abre a impressão da notinha.
   Depois: *Em entrega* (ou pronto para retirar) e *Finalizado*.
4. O cliente acompanha em `/acompanhar/<código>/`: cada etapa com a hora, a
   previsão (tempo médio de Dados da casa), o mapa do endereço e o botão do
   WhatsApp da casa. A tela confere o andamento a cada 12 segundos.

No detalhe do pedido (painel) há o mapa da entrega, a rota no Google Maps, o
WhatsApp do cliente e a "tela do cliente". O endereço do acompanhamento usa um
código sorteado: quem não tem o link não vê o pedido. Há um limite de 8
pedidos a cada 10 minutos por aparelho.

O mapa é o do Google embutido por endereço (sem chave de API). Preencher a
**cidade** em Dados da casa faz o mapa achar a rua certa.

## Estoque e doses

Cada produto tem um de três tipos (escolhido no cadastro):

- **Sem controle**: pratos feitos na hora.
- **Estoque próprio**: garrafa, lata, porção. Tem saldo e um mínimo de aviso.
- **Dose**: não tem saldo; cada venda tira uma fração da garrafa. Ex.: "Dose
  Red Label" com 20 doses por garrafa baixa 0,05 da "Whisky Red Label 1L".
  O estoque mostra quantas doses a garrafa ainda rende.

A venda (Caixa ou online) dá baixa na mesma transação do pedido, e o Caixa
avisa quando um item chega ao mínimo ou zera. O Caixa **não bloqueia** a venda
sem saldo (o saldo fica negativo até a contagem); o cardápio online mostra
"Esgotado" e recusa o item. **Cancelar** um pedido devolve ao estoque o que
ele tirou. Em Estoque: *Compra* (soma e atualiza o custo), *Contagem*
(informa quanto tem; o sistema lança a diferença) e *Perda*. Todo movimento
fica no histórico, com usuário e saldo. Código: `core/estoque.py`.

O **custo** do produto é guardado em cada item vendido; as Estatísticas
mostram o lucro bruto estimado e a margem (a dose custa a fração da garrafa).

## Variáveis de ambiente

| Variável | Para quê | Padrão |
| --- | --- | --- |
| `DATABASE_URL` | banco (Supabase) | SQLite local |
| `SECRET_KEY` | segurança do Django | **defina em produção** |
| `DEBUG` | `False` em produção | `True` |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | fotos dos produtos | sem elas, as fotos ficam no disco (só para desenvolvimento: na Vercel o disco não guarda) |
| `LOJA_DIAS` | dias que abre (0 = segunda ... 6 = domingo) | `0,1,2,3,4,5,6` |
| `LOJA_ABRE` / `LOJA_FECHA` | horário (hora cheia). `LOJA_FECHA` menor que `LOJA_ABRE` = fecha depois da meia-noite | `10` / `23` |
| `ALLOWED_HOSTS_EXTRA`, `CSRF_TRUSTED_ORIGINS_EXTRA` | domínio próprio | vazias |
| `PALAVRAS_BEBIDAS` | palavras que fazem uma categoria contar como bebida nos relatórios | `bebida,refri,suco,cerveja,drink,dose,destilado,whisky,cachaça,vinho,chopp,água` |
| `WHATSAPP_LOJA` | WhatsApp da casa quando o campo de Dados da casa está vazio | vazio |
| `IMPRESSAO_API_KEY` | chave do agente de impressão externo (opcional) | vazia (desligado) |
| `QZ_CERTIFICADO`, `QZ_CHAVE_PRIVADA` | certificado do QZ Tray (o QZ lembra a permissão do site) | vazias |
| `MARMITA_PRECO_COM_REFRI`, `REFRI_MARMITA_PRECO_MAX` | oferecer o refri barato como adicional das marmitas desse preço | `0` (desligado) / `2.00` |
| `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_PASSWORD` | usados por `python manage.py createsu` | — |

## Publicar (igual ao outro restaurante)

1. Projeto novo na Vercel apontando para este repositório, com as variáveis
   acima (`DATABASE_URL` de um banco **novo** no Supabase, `SECRET_KEY`,
   `DEBUG=False` e as três da Cloudinary).
2. Uma vez, com o `DATABASE_URL` de produção: `python manage.py migrate` e
   `python manage.py createsu`.
3. Depois de mexer em CSS/JS: `python manage.py collectstatic --noinput` (a
   pasta `staticfiles/` vai versionada; é dela que o whitenoise serve).

## Impressão das notinhas (QZ Tray)

Um motor só para o Caixa e a tela de Pedidos: `core/static/core/js/impressao.js`.
A notinha sai com o nome da casa no topo. Se a impressora ou o QZ Tray
estiverem fora do ar, a notinha fica numa fila guardada no navegador e sai
sozinha quando voltarem. Na engrenagem da impressora: escolher a impressora,
papel de 80 mm ou 58 mm, vias, teste e diagnóstico.

Para o QZ não pedir permissão toda hora: `python qz/gerar_certificado.py`,
cole as duas linhas de `qz/COLAR-NA-VERCEL.txt` em `QZ_CERTIFICADO` e
`QZ_CHAVE_PRIVADA` na Vercel, faça o redeploy e rode `qz/configurar_caixa.bat`
no PC do caixa. Guarde `private-key.pem` fora do GitHub.

## Temas e CSS

Sol, Lua, Eclipse e Automático, no botão do topo. Cores da marca em
`core/static/core/css/tema.css` (`--cf-marca`). As telas de gestão usam
`core/static/core/css/gestao.css`. O Tailwind é compilado uma vez; depois de
mexer em classes dos templates:

```
npx tailwindcss@3.4.17 -c tailwind.config.js -i core/static_src/entrada.css -o core/static/core/css/app.css --minify
python manage.py collectstatic --noinput
```

## Testes

```
python manage.py test core
```
