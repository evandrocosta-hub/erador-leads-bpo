"""Exportação do resultado para Excel (abas Leads, Resumo e Como usar)."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

LOGGER = logging.getLogger(__name__)

COLUNAS_LEADS = {
    "razao_social": "Razão social",
    "nome_fantasia": "Nome fantasia",
    "cnpj_formatado": "CNPJ",
    "municipio": "Município",
    "uf": "UF",
    "cnae_formatado": "CNAE",
    "cnae_descricao": "CNAE (descrição)",
    "data_abertura": "Data de abertura",
    "idade_meses": "Idade (meses)",
    "porte_descricao": "Porte",
    "regime_provavel": "Regime provável",
    "capital_social": "Capital social (R$)",
    "telefone": "Telefone",
    "email": "E-mail",
    "score": "Score",
    "classe": "Classe",
    "motivo_score": "Motivo do score",
}

INSTRUCOES_COMO_USAR = [
    ("Como usar esta lista de prospecção", ""),
    ("", ""),
    ("Origem dos dados",
     "Dados públicos do CNPJ (Receita Federal). Uso B2B: o contato é com a "
     "empresa, não com pessoas físicas. Não há dados de sócios nesta lista."),
    ("", ""),
    ("1. Priorize a classe A",
     "São empresas recém-abertas, em CNAEs de serviços com alta aderência a "
     "BPO financeiro e com contato disponível. Comece por elas."),
    ("2. Cadência sugerida — classe A",
     "Dia 1: e-mail de apresentação curto (dor: rotina financeira consome tempo "
     "do dono). Dia 2: ligação. Dia 4: WhatsApp/e-mail de follow-up com material. "
     "Dia 7: ligação. Dia 12: e-mail de encerramento (breakup). 5 toques em 12 dias."),
    ("3. Cadência sugerida — classe B",
     "Dia 1: e-mail. Dia 4: ligação. Dia 10: e-mail de follow-up. 3 toques em 10 "
     "dias. Se houver resposta, migre para o fluxo da classe A."),
    ("4. Classe C",
     "Use em campanhas de e-mail em massa (nutrição) ou aguarde o próximo ciclo "
     "mensal de dados: a empresa pode amadurecer e subir de classe."),
    ("5. Mensagem que funciona para BPO",
     "Mencione o momento da empresa (recém-aberta), o segmento (CNAE) e uma dor "
     "concreta: emissão de notas, conciliação, fluxo de caixa, obrigações do "
     "Simples. Ofereça diagnóstico gratuito de 30 minutos."),
    ("6. Higiene da lista",
     "Antes de ligar, confira o telefone/e-mail (dados cadastrais podem estar "
     "desatualizados). Registre opt-out imediatamente e não recontate quem pediu "
     "para sair. Remova clientes atuais e concorrentes."),
    ("7. Atualização",
     "A RFB publica novos dados mensalmente. Rode o pipeline a cada mês para "
     "captar empresas recém-abertas — são os leads mais quentes."),
    ("", ""),
    ("Leitura do score",
     "0-100. Componentes: recência da abertura (mais novo = mais pontos), CNAE "
     "de serviços prioritário, capital social em faixa PME, presença de e-mail e "
     "telefone e opção pelo Simples. A coluna 'Motivo do score' detalha lead a lead."),
]


def _resumo(df: pd.DataFrame) -> pd.DataFrame:
    tabela = (
        df.pivot_table(index="municipio", columns="classe", values="cnpj", aggfunc="count", fill_value=0)
        .reindex(columns=["A", "B", "C"], fill_value=0)
    )
    tabela["Total"] = tabela.sum(axis=1)
    tabela = tabela.sort_values("Total", ascending=False)
    tabela.loc["TOTAL GERAL"] = tabela.sum()
    tabela.index.name = "Município"
    return tabela.reset_index()


def _ajustar_larguras(planilha, df: pd.DataFrame, minimo: int = 10, maximo: int = 60) -> None:
    from openpyxl.utils import get_column_letter

    for i, coluna in enumerate(df.columns, start=1):
        largura = max([len(str(coluna))] + [len(str(v)) for v in df[coluna].head(200)])
        planilha.column_dimensions[get_column_letter(i)].width = min(max(largura + 2, minimo), maximo)


def exportar_excel(df: pd.DataFrame, caminho: str | Path) -> Path:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)

    leads = df[list(COLUNAS_LEADS)].rename(columns=COLUNAS_LEADS)
    resumo = _resumo(df) if not df.empty else pd.DataFrame({"Município": [], "Total": []})
    como_usar = pd.DataFrame(INSTRUCOES_COMO_USAR, columns=["Tópico", "Orientação"])

    with pd.ExcelWriter(caminho, engine="openpyxl") as escritor:
        leads.to_excel(escritor, sheet_name="Leads", index=False)
        resumo.to_excel(escritor, sheet_name="Resumo", index=False)
        como_usar.to_excel(escritor, sheet_name="Como usar", index=False)
        _ajustar_larguras(escritor.sheets["Leads"], leads)
        _ajustar_larguras(escritor.sheets["Resumo"], resumo)
        _ajustar_larguras(escritor.sheets["Como usar"], como_usar, maximo=110)
        escritor.sheets["Leads"].freeze_panes = "A2"

    LOGGER.info("Excel gerado: %s (%d leads)", caminho, len(leads))
    return caminho
