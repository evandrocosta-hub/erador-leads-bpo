from datetime import date

import pandas as pd

from gerador_leads_bpo.config import PADRAO
from gerador_leads_bpo.score import calcular_score_linha


def _linha(**extras):
    base = {
        "data_inicio": "20260401",
        "cnae_principal": "6201501",
        "capital_social": 50000.0,
        "email": "contato@empresa.com.br",
        "telefone": "47 33334444",
        "opcao_simples": "S",
    }
    base.update(extras)
    return pd.Series(base)


HOJE = date(2026, 7, 1)


def test_lead_perfeito_score_100_classe_a():
    resultado = calcular_score_linha(_linha(), PADRAO["score"], PADRAO["icp"], HOJE)
    assert resultado["score"] == 100
    assert resultado["classe"] == "A"
    assert "Aberta há 3 meses" in resultado["motivo_score"]
    assert "CNAE prioritário" in resultado["motivo_score"]
    assert "Optante do Simples" in resultado["motivo_score"]


def test_lead_fraco_classe_c():
    linha = _linha(
        data_inicio="20200101",
        cnae_principal="9999999",
        capital_social=0.0,
        email="",
        telefone="",
        opcao_simples="",
    )
    resultado = calcular_score_linha(linha, PADRAO["score"], PADRAO["icp"], HOJE)
    assert resultado["classe"] == "C"
    assert resultado["score"] < 45
    assert resultado["regime_provavel"] == "Provável Simples (porte ME/EPP)"


def test_cnae_alvo_nao_prioritario_pontua_menos():
    prioritario = calcular_score_linha(_linha(), PADRAO["score"], PADRAO["icp"], HOJE)
    alvo = calcular_score_linha(
        _linha(cnae_principal="8511200"), PADRAO["score"], PADRAO["icp"], HOJE
    )
    diferenca = PADRAO["score"]["pesos"]["cnae_prioritario"] - PADRAO["score"]["pesos"]["cnae_alvo"]
    assert prioritario["score"] - alvo["score"] == diferenca


def test_capital_fora_da_faixa_nao_pontua():
    dentro = calcular_score_linha(_linha(), PADRAO["score"], PADRAO["icp"], HOJE)
    fora = calcular_score_linha(_linha(capital_social=100.0), PADRAO["score"], PADRAO["icp"], HOJE)
    assert dentro["score"] - fora["score"] == PADRAO["score"]["pesos"]["capital_faixa"]


def test_nao_optante_simples_marca_regime():
    resultado = calcular_score_linha(_linha(opcao_simples="N"), PADRAO["score"], PADRAO["icp"], HOJE)
    assert resultado["regime_provavel"] == "Fora do Simples"
