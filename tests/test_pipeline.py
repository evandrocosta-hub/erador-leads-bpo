"""Teste ponta a ponta: ZIPs sintéticos no leiaute da RFB -> SQLite -> Excel."""

import zipfile
from pathlib import Path

import pandas as pd
import pytest

from gerador_leads_bpo.carga import criar_banco, executar_carga
from gerador_leads_bpo.config import PADRAO, _mesclar
from gerador_leads_bpo.exportar import exportar_excel
from gerador_leads_bpo.score import gerar_leads


def _zip_csv(caminho: Path, linhas: list[list[str]]) -> Path:
    conteudo = "\n".join(";".join(f'"{campo}"' for campo in linha) for linha in linhas)
    with zipfile.ZipFile(caminho, "w") as zf:
        zf.writestr(caminho.stem + ".CSV", conteudo.encode("latin-1"))
    return caminho


def _estabelecimento(cnpj_basico, fantasia, situacao, data_inicio, cnae, uf, municipio,
                     ddd="47", fone="33334444", email="contato@x.com.br"):
    linha = [""] * 30
    linha[0], linha[1], linha[2], linha[3] = cnpj_basico, "0001", "99", "1"
    linha[4], linha[5], linha[10], linha[11] = fantasia, situacao, data_inicio, cnae
    linha[19], linha[20], linha[21], linha[22], linha[27] = uf, municipio, ddd, fone, email
    return linha


@pytest.fixture
def ambiente(tmp_path):
    hoje = pd.Timestamp.today()
    recente = (hoje - pd.DateOffset(months=3)).strftime("%Y%m%d")
    antiga = (hoje - pd.DateOffset(months=60)).strftime("%Y%m%d")

    estabelecimentos = [
        # dentro do ICP: ativa, Joinville/SC, CNAE 62, recém-aberta
        _estabelecimento("11111111", "TECH BOA", "02", recente, "6201501", "SC", "8179"),
        # fora: situação baixada
        _estabelecimento("22222222", "BAIXADA", "08", recente, "6201501", "SC", "8179"),
        # fora: outra UF
        _estabelecimento("33333333", "OUTRA UF", "02", recente, "6201501", "PR", "7535"),
        # fora: CNAE não-alvo
        _estabelecimento("44444444", "PADARIA", "02", recente, "1091101", "SC", "8179"),
        # fora: empresa antiga (idade > 24 meses)
        _estabelecimento("55555555", "ANTIGA", "02", antiga, "6201501", "SC", "8179"),
        # dentro do ICP, mas porte grande (removida na carga de empresas)
        _estabelecimento("66666666", "GRANDONA", "02", recente, "6201501", "SC", "8179"),
    ]
    empresas = [
        ["11111111", "TECH BOA LTDA", "2062", "0", "50000,00", "01", ""],
        ["22222222", "BAIXADA LTDA", "2062", "0", "10000,00", "01", ""],
        ["66666666", "GRANDONA SA", "2046", "0", "9000000,00", "05", ""],
    ]
    simples = [["11111111", "S", "20240101", "00000000", "N", "00000000", "00000000"]]
    cnaes = [["6201501", "Desenvolvimento de programas de computador sob encomenda"]]
    municipios = [["8179", "JOINVILLE"], ["7535", "CURITIBA"]]

    arquivos = {
        "estabelecimentos": [_zip_csv(tmp_path / "Estabelecimentos0.zip", estabelecimentos)],
        "empresas": [_zip_csv(tmp_path / "Empresas0.zip", empresas)],
        "simples": [_zip_csv(tmp_path / "Simples.zip", simples)],
        "cnaes": [_zip_csv(tmp_path / "Cnaes.zip", cnaes)],
        "municipios": [_zip_csv(tmp_path / "Municipios.zip", municipios)],
    }
    config = _mesclar(PADRAO, {
        "icp": {"uf": "SC", "municipios": ["Joinville"], "idade_maxima_meses": 24},
        "dados": {"banco_sqlite": str(tmp_path / "leads.db")},
        "saida": {"arquivo_excel": str(tmp_path / "saida" / "leads.xlsx")},
    })
    return arquivos, config


def test_pipeline_completo(ambiente):
    arquivos, config = ambiente
    con = criar_banco(config["dados"]["banco_sqlite"], recriar=True)
    executar_carga(con, arquivos, config)

    leads = gerar_leads(con, config)
    con.close()

    # só TECH BOA sobrevive a todos os filtros do ICP
    assert list(leads["razao_social"]) == ["TECH BOA LTDA"]
    lead = leads.iloc[0]
    assert lead["cnpj_formatado"] == "11.111.111/0001-99"
    assert lead["municipio"] == "JOINVILLE"
    assert lead["cnae_descricao"].startswith("Desenvolvimento")
    assert lead["classe"] == "A"
    assert lead["score"] == 100
    assert lead["regime_provavel"] == "Simples (optante)"
    assert "+25" in lead["motivo_score"]

    caminho = exportar_excel(leads, config["saida"]["arquivo_excel"])
    abas = pd.read_excel(caminho, sheet_name=None)
    assert set(abas) == {"Leads", "Resumo", "Como usar"}
    assert abas["Leads"].iloc[0]["Razão social"] == "TECH BOA LTDA"
    assert "Motivo do score" in abas["Leads"].columns
    resumo = abas["Resumo"]
    assert resumo[resumo["Município"] == "TOTAL GERAL"]["Total"].iloc[0] == 1
    assert not abas["Como usar"].empty
