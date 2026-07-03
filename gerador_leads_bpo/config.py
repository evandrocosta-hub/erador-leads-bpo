"""Leitura e validação do config.yaml com o ICP do parceiro."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

PADRAO: dict[str, Any] = {
    "dados": {
        "url_base": "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/",
        "referencia": "mais_recente",
        "diretorio_download": "dados/download",
        "banco_sqlite": "dados/leads.db",
        "usar_simples": True,
        "limite_arquivos": 0,
    },
    "icp": {
        "uf": "",
        "municipios": [],
        "cnaes_alvo": [
            "62", "63", "70", "71", "73", "74", "82", "85", "86", "4791",
        ],
        "cnaes_prioritarios": ["62", "70", "73", "86", "4791"],
        "portes": ["ME", "EPP"],
        "somente_simples_provavel": True,
        "idade_maxima_meses": 24,
        "situacao_cadastral": "ATIVA",
    },
    "score": {
        "pesos": {
            "idade": 30,
            "cnae_prioritario": 25,
            "cnae_alvo": 15,
            "capital_faixa": 15,
            "email": 15,
            "telefone": 10,
            "simples": 5,
        },
        "capital_pme": {"minimo": 5000, "maximo": 1000000},
        "classes": {"A": 70, "B": 45},
    },
    "saida": {"arquivo_excel": "saida/leads_bpo.xlsx"},
}

# Códigos de porte no leiaute da RFB (arquivo Empresas)
PORTE_RFB = {"ME": "01", "EPP": "03", "DEMAIS": "05"}


def _mesclar(base: dict, extra: dict) -> dict:
    resultado = copy.deepcopy(base)
    for chave, valor in (extra or {}).items():
        if isinstance(valor, dict) and isinstance(resultado.get(chave), dict):
            resultado[chave] = _mesclar(resultado[chave], valor)
        else:
            resultado[chave] = valor
    return resultado


def carregar_config(caminho: str | Path) -> dict[str, Any]:
    caminho = Path(caminho)
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {caminho}")
    with open(caminho, encoding="utf-8") as arquivo:
        bruto = yaml.safe_load(arquivo) or {}

    config = _mesclar(PADRAO, bruto)
    validar_config(config)
    return config


def validar_config(config: dict[str, Any]) -> None:
    icp = config["icp"]
    if not icp.get("uf"):
        raise ValueError("config.yaml: informe icp.uf (sigla da UF, ex.: 'SC')")
    icp["uf"] = str(icp["uf"]).strip().upper()
    if len(icp["uf"]) != 2:
        raise ValueError(f"config.yaml: UF inválida: {icp['uf']!r}")

    portes_invalidos = [p for p in icp.get("portes", []) if p.upper() not in PORTE_RFB]
    if portes_invalidos:
        raise ValueError(
            f"config.yaml: portes inválidos {portes_invalidos}; use {sorted(PORTE_RFB)}"
        )
    icp["portes"] = [p.upper() for p in icp.get("portes", [])]

    situacao = str(icp.get("situacao_cadastral", "ATIVA")).upper()
    if situacao != "ATIVA":
        raise ValueError(
            "config.yaml: apenas situacao_cadastral 'ATIVA' é suportada — "
            "prospectar empresas baixadas/inaptas não faz sentido para BPO"
        )
    icp["situacao_cadastral"] = situacao

    icp["cnaes_alvo"] = [str(c).replace(".", "").replace("-", "").replace("/", "") for c in icp.get("cnaes_alvo", [])]
    icp["cnaes_prioritarios"] = [
        str(c).replace(".", "").replace("-", "").replace("/", "") for c in icp.get("cnaes_prioritarios", [])
    ]

    classes = config["score"]["classes"]
    if not (0 < classes["B"] < classes["A"] <= 100):
        raise ValueError("config.yaml: cortes de classe devem satisfazer 0 < B < A <= 100")
