"""Score de propensão (0-100) e classificação A/B/C dos leads.

Componentes (pesos configuráveis no config.yaml):
  - idade: empresa recém-aberta pesa mais (contratação de BPO é decidida cedo)
  - cnae: CNAE de serviços prioritário > CNAE-alvo genérico
  - capital: capital social dentro da faixa típica de PME
  - contato: presença de e-mail e telefone no cadastro da RFB
  - simples: opção confirmada pelo Simples Nacional
Cada ponto do score vem acompanhado de uma justificativa em texto.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date

import pandas as pd

LOGGER = logging.getLogger(__name__)

_SQL_LEADS = """
SELECT
    e.cnpj, e.cnpj_basico, e.nome_fantasia, e.data_inicio, e.cnae_principal,
    e.uf, e.municipio_codigo, e.telefone, e.email,
    emp.razao_social, emp.capital_social, emp.porte,
    c.descricao   AS cnae_descricao,
    m.descricao   AS municipio,
    s.opcao_simples, s.opcao_mei
FROM estabelecimentos e
JOIN empresas   emp ON emp.cnpj_basico = e.cnpj_basico
LEFT JOIN cnaes      c ON c.codigo = e.cnae_principal
LEFT JOIN municipios m ON m.codigo = e.municipio_codigo
LEFT JOIN simples    s ON s.cnpj_basico = e.cnpj_basico
"""

PORTE_DESCRICAO = {"01": "ME", "03": "EPP", "05": "Demais", "00": "Não informado"}


def _idade_meses(data_inicio: str, hoje: date) -> int | None:
    try:
        abertura = date(int(data_inicio[:4]), int(data_inicio[4:6]), int(data_inicio[6:8]))
    except (ValueError, TypeError, IndexError):
        return None
    return max(0, (hoje.year - abertura.year) * 12 + hoje.month - abertura.month)


def _pontos_idade(idade_meses: int | None, peso_max: int) -> tuple[int, str]:
    if idade_meses is None:
        return 0, "Data de abertura inválida"
    if idade_meses <= 6:
        fator, faixa = 1.0, "até 6 meses"
    elif idade_meses <= 12:
        fator, faixa = 0.8, "até 12 meses"
    elif idade_meses <= 24:
        fator, faixa = 0.55, "até 24 meses"
    elif idade_meses <= 36:
        fator, faixa = 0.3, "até 36 meses"
    else:
        fator, faixa = 0.15, "mais de 36 meses"
    pontos = round(peso_max * fator)
    return pontos, f"Aberta há {idade_meses} meses ({faixa}) (+{pontos})"


def _formatar_cnpj(cnpj: str) -> str:
    cnpj = str(cnpj).zfill(14)
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def _formatar_cnae(codigo: str) -> str:
    codigo = str(codigo)
    if len(codigo) == 7:
        return f"{codigo[:4]}-{codigo[4]}/{codigo[5:]}"
    return codigo


def calcular_score_linha(linha: pd.Series, cfg_score: dict, icp: dict, hoje: date) -> pd.Series:
    pesos = cfg_score["pesos"]
    faixa = cfg_score["capital_pme"]
    motivos: list[str] = []
    score = 0

    # 1) Recência da abertura
    idade = _idade_meses(linha["data_inicio"], hoje)
    pontos, motivo = _pontos_idade(idade, int(pesos["idade"]))
    score += pontos
    motivos.append(motivo)

    # 2) CNAE
    cnae = str(linha["cnae_principal"])
    cnae_legivel = _formatar_cnae(cnae)
    if any(cnae.startswith(p) for p in icp.get("cnaes_prioritarios", [])):
        pontos = int(pesos["cnae_prioritario"])
        motivos.append(f"CNAE prioritário para BPO {cnae_legivel} (+{pontos})")
    elif any(cnae.startswith(p) for p in icp.get("cnaes_alvo", [])):
        pontos = int(pesos["cnae_alvo"])
        motivos.append(f"CNAE-alvo {cnae_legivel} (+{pontos})")
    else:
        pontos = 0
    score += pontos

    # 3) Capital social na faixa PME
    capital = float(linha.get("capital_social") or 0)
    if faixa["minimo"] <= capital <= faixa["maximo"]:
        pontos = int(pesos["capital_faixa"])
        motivos.append(f"Capital social na faixa PME (R$ {capital:,.0f}) (+{pontos})")
        score += pontos

    # 4) Contatos no cadastro
    if str(linha.get("email") or "").strip():
        pontos = int(pesos["email"])
        motivos.append(f"E-mail no cadastro (+{pontos})")
        score += pontos
    if str(linha.get("telefone") or "").strip():
        pontos = int(pesos["telefone"])
        motivos.append(f"Telefone no cadastro (+{pontos})")
        score += pontos

    # 5) Simples Nacional
    opcao = str(linha.get("opcao_simples") or "").upper()
    if opcao == "S":
        pontos = int(pesos["simples"])
        motivos.append(f"Optante do Simples Nacional (+{pontos})")
        score += pontos
        regime = "Simples (optante)"
    elif opcao == "N":
        regime = "Fora do Simples"
    else:
        regime = "Provável Simples (porte ME/EPP)"

    score = min(100, score)
    classes = cfg_score["classes"]
    if score >= classes["A"]:
        classe = "A"
    elif score >= classes["B"]:
        classe = "B"
    else:
        classe = "C"

    return pd.Series(
        {
            "score": score,
            "classe": classe,
            "motivo_score": "; ".join(motivos),
            "idade_meses": idade,
            "regime_provavel": regime,
        }
    )


def gerar_leads(con: sqlite3.Connection, config: dict, hoje: date | None = None) -> pd.DataFrame:
    """Monta o DataFrame final de leads com score, classe e justificativa."""
    hoje = hoje or date.today()
    icp = config["icp"]
    cfg_score = config["score"]

    df = pd.read_sql_query(_SQL_LEADS, con)
    LOGGER.info("Leads candidatos após junção estabelecimentos x empresas: %d", len(df))
    if df.empty:
        return df

    if icp.get("somente_simples_provavel") and "opcao_simples" in df.columns:
        antes = len(df)
        df = df[df["opcao_simples"].fillna("").str.upper() != "N"]
        LOGGER.info("Removidos %d leads com NÃO opção explícita pelo Simples", antes - len(df))

    extras = df.apply(calcular_score_linha, axis=1, args=(cfg_score, icp, hoje))
    df = pd.concat([df.reset_index(drop=True), extras.reset_index(drop=True)], axis=1)

    df["cnpj_formatado"] = df["cnpj"].map(_formatar_cnpj)
    df["cnae_formatado"] = df["cnae_principal"].map(_formatar_cnae)
    df["porte_descricao"] = df["porte"].map(PORTE_DESCRICAO).fillna(df["porte"])
    df["data_abertura"] = pd.to_datetime(df["data_inicio"], format="%Y%m%d", errors="coerce").dt.date

    df = df.sort_values(["score", "razao_social"], ascending=[False, True]).reset_index(drop=True)
    LOGGER.info(
        "Leads finais: %d (A: %d, B: %d, C: %d)",
        len(df),
        int((df["classe"] == "A").sum()),
        int((df["classe"] == "B").sum()),
        int((df["classe"] == "C").sum()),
    )
    return df
