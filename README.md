# gerador-leads-bpo

Pipeline em Python que gera **listas de prospecção de PMEs para empresas de
BPO financeiro** a partir dos
[Dados Abertos do CNPJ da Receita Federal](https://arquivos.receitafederal.gov.br/dados/cnpj/).

O pipeline:

1. **Baixa** apenas os arquivos necessários (`Estabelecimentos*.zip`,
   `Empresas*.zip`, `Cnaes.zip`, `Municipios.zip` e, opcionalmente,
   `Simples.zip`) da pasta mensal mais recente da RFB;
2. **Processa em streaming** (pandas com `chunksize`) para não estourar
   memória — os CSVs somam vários GB — filtrando cada chunk pelo ICP antes de
   gravar num **SQLite local**;
3. **Pontua** cada empresa com um score de propensão 0-100 e classifica em
   **A/B/C**, com justificativa lead a lead;
4. **Exporta um Excel** pronto para o time comercial, com abas **Leads**,
   **Resumo** e **Como usar** (cadência de prospecção em português).

## Passo a passo de execução

Requisitos: Python 3.10+ e ~20 GB livres em disco (os ZIPs da RFB são grandes).

```bash
git clone https://github.com/evandrocosta-hub/erador-leads-bpo.git
cd erador-leads-bpo

# 1. Crie um ambiente virtual e instale as dependências
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Edite o config.yaml com o ICP do parceiro
#    (UF, municípios, CNAEs-alvo, porte, idade máxima etc.)

# 3. Rode o pipeline completo (download + carga + score + Excel)
python -m gerador_leads_bpo --config config.yaml
```

A lista final fica em `saida/leads_bpo.xlsx` (configurável em
`saida.arquivo_excel` ou via `--saida`).

### Opções úteis

| Opção | Efeito |
|---|---|
| `--pular-download` | Reaproveita o SQLite já carregado e só regera score + Excel (útil para ajustar pesos do score sem baixar tudo de novo) |
| `--forcar-download` | Baixa novamente mesmo que os ZIPs já existam localmente |
| `--saida caminho.xlsx` | Sobrescreve o caminho do Excel |

Para um teste rápido antes da rodada completa, use `dados.limite_arquivos: 1`
no `config.yaml` — o pipeline processa só o primeiro shard de cada tipo
(a lista sai **incompleta**, serve apenas para validar filtros).

## Rodando sem máquina própria (GitHub Actions)

Não precisa de computador nem de infraestrutura: o workflow
[`gerar-leads.yml`](.github/workflows/gerar-leads.yml) roda o pipeline
inteiro nos servidores do GitHub.

1. Acesse a aba **Actions** do repositório → workflow **"Gerar lista de leads"**
   → **Run workflow** (para um teste rápido, informe `1` em *limite_arquivos*);
2. Aguarde a execução terminar (a rodada completa baixa ~6 GB da RFB e pode
   levar de 1 a 3 horas; o teste com 1 shard leva ~10-20 minutos);
3. Baixe a planilha em **dois lugares**: na página da execução (artifact
   `leads-bpo`) ou na aba **Releases** do repositório (um release novo é
   criado a cada rodada, com a data no nome).

O workflow também roda sozinho **todo dia 15** (a RFB publica dados novos no
início de cada mês). Para mudar o ICP, edite o `config.yaml` direto na
interface do GitHub (ícone de lápis) e dispare o workflow de novo.

## Configuração do ICP (`config.yaml`)

| Chave | Descrição |
|---|---|
| `icp.uf` | UF do parceiro (obrigatória) |
| `icp.municipios` | Nomes dos municípios (vazio = UF inteira; acentos são normalizados) |
| `icp.cnaes_alvo` | Prefixos de CNAE. Default: serviços, saúde, tecnologia, agências, clínicas e e-commerce |
| `icp.cnaes_prioritarios` | Subconjunto que pontua mais no score |
| `icp.portes` | `ME` e/ou `EPP` (códigos 01/03 da RFB) |
| `icp.somente_simples_provavel` | Descarta empresas com NÃO opção explícita pelo Simples |
| `icp.idade_maxima_meses` | Ex.: 24 = abertas nos últimos 24 meses (0 = sem filtro) |
| `icp.situacao_cadastral` | Sempre `ATIVA` |

## Score de propensão (0-100)

| Componente | Peso default | Racional |
|---|---|---|
| Recência da abertura | até 30 | Empresa recém-aberta decide cedo quem cuida do financeiro |
| CNAE prioritário / alvo | 25 / 15 | Serviços recorrentes, agências, clínicas e e-commerce têm alta aderência a BPO |
| Capital social em faixa PME | 15 | Entre R$ 5 mil e R$ 1 mi (configurável) |
| E-mail no cadastro | 15 | Canal de contato imediato |
| Telefone no cadastro | 10 | Canal de contato imediato |
| Optante do Simples | 5 | Perfil fiscal típico do cliente de BPO |

Classes: **A** ≥ 70, **B** ≥ 45, **C** abaixo (cortes configuráveis). A coluna
**Motivo do score** explica cada pontuação, ex.:
`Aberta há 3 meses (até 6 meses) (+30); CNAE prioritário para BPO 6201-5/01 (+25); ...`

## Saída (Excel)

- **Leads** — razão social, nome fantasia, CNPJ, município, CNAE e descrição,
  data de abertura, porte, regime provável, capital social, telefone, e-mail,
  score, classe e motivo do score;
- **Resumo** — totais por classe e por município;
- **Como usar** — cadência de prospecção sugerida (em português) por classe.

## Testes

```bash
pip install pytest
python -m pytest tests/ -v
```

Os testes usam ZIPs sintéticos no leiaute da RFB — não dependem de rede.

## ⚠️ Aviso legal — dados públicos e LGPD

- Os dados utilizados são **públicos**, publicados pela Receita Federal do
  Brasil no programa de Dados Abertos do CNPJ, e referem-se a **pessoas
  jurídicas** (empresas).
- A lista gerada destina-se a **prospecção B2B**: o contato é feito com a
  empresa, em contexto profissional.
- Em conformidade com a **LGPD (Lei 13.709/2018)**, este pipeline **não baixa
  nem processa os arquivos de Sócios** — nenhum dado de pessoa física
  (nome de sócio, CPF etc.) é coletado, armazenado ou exportado.
- Boas práticas ao prospectar: identifique sua empresa, ofereça opt-out em
  todo contato, respeite pedidos de não recontato e mantenha os arquivos
  gerados sob acesso controlado.
- O uso da lista é de responsabilidade de quem prospecta; consulte seu
  jurídico para políticas internas de tratamento de dados.
