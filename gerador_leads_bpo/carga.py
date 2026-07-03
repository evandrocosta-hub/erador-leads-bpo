"""Carga em streaming dos CSVs da RFB para um banco SQLite local.

Os arquivos da RFB são CSVs sem cabeçalho, separados por ';', codificação
Latin-1, distribuídos em ZIPs de vários GB. Tudo é lido em chunks
(pandas.read_csv com chunksize) para manter o uso de memória estável,
filtrando cada chunk pelo ICP antes de gravar no banco.
"""

from __future__ import annotations

import logging
import sqlite3
import unicodedata
import zipfile
from pathlib import Path
from typing import Iterable

import pandas as pd

LOGGER = logging.getLogger(__name__)

TAMANHO_CHUNK = 100_000
SITUACAO_ATIVA = "02"

# Índices das colunas no leiaute oficial (Layout dos Dados Abertos do CNPJ)
EST = {
    "cnpj_basico": 0,
    "cnpj_ordem": 1,
    "cnpj_dv": 2,
    "matriz_filial": 3,
    "nome_fantasia": 4,
    "situacao": 5,
    "data_inicio": 10,
    "cnae_principal": 11,
    "uf": 19,
    "municipio_codigo": 20,
    "ddd1": 21,
    "telefone1": 22,
    "email": 27,
}
EMP = {
    "cnpj_basico": 0,
    "razao_social": 1,
    "natureza_juridica": 2,
    "capital_social": 4,
    "porte": 5,
}
SIM = {
    "cnpj_basico": 0,
    "opcao_simples": 1,
    "data_exclusao_simples": 3,
    "opcao_mei": 4,
}


def normalizar_nome(texto: str) -> str:
    """Remove acentos e caixa para casar nomes de municípios ('Florianópolis' -> 'FLORIANOPOLIS')."""
    sem_acento = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.upper().split())


def criar_banco(caminho: str | Path, recriar: bool = False) -> sqlite3.Connection:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if recriar and caminho.exists():
        caminho.unlink()
    con = sqlite3.connect(caminho)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS cnaes (codigo TEXT PRIMARY KEY, descricao TEXT);
        CREATE TABLE IF NOT EXISTS municipios (codigo TEXT PRIMARY KEY, descricao TEXT);
        CREATE TABLE IF NOT EXISTS estabelecimentos (
            cnpj_basico TEXT, cnpj TEXT PRIMARY KEY, nome_fantasia TEXT,
            data_inicio TEXT, cnae_principal TEXT, uf TEXT,
            municipio_codigo TEXT, telefone TEXT, email TEXT
        );
        CREATE TABLE IF NOT EXISTS empresas (
            cnpj_basico TEXT PRIMARY KEY, razao_social TEXT,
            natureza_juridica TEXT, capital_social REAL, porte TEXT
        );
        CREATE TABLE IF NOT EXISTS simples (
            cnpj_basico TEXT PRIMARY KEY, opcao_simples TEXT, opcao_mei TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_est_basico ON estabelecimentos (cnpj_basico);
        """
    )
    return con


def _chunks_zip(caminho_zip: Path, usecols: list[int] | None = None) -> Iterable[pd.DataFrame]:
    """Itera em chunks sobre o(s) CSV(s) dentro de um ZIP da RFB."""
    with zipfile.ZipFile(caminho_zip) as zf:
        for membro in zf.namelist():
            with zf.open(membro) as arquivo:
                leitor = pd.read_csv(
                    arquivo,
                    sep=";",
                    header=None,
                    dtype=str,
                    encoding="latin-1",
                    quotechar='"',
                    keep_default_na=False,
                    chunksize=TAMANHO_CHUNK,
                    usecols=usecols,
                    on_bad_lines="skip",
                )
                yield from leitor


def carregar_tabela_auxiliar(con: sqlite3.Connection, caminho_zip: Path, tabela: str) -> int:
    """Carrega Cnaes.zip / Municipios.zip (leiaute: codigo;descricao)."""
    total = 0
    for chunk in _chunks_zip(caminho_zip, usecols=[0, 1]):
        registros = [(str(c).strip(), str(d).strip()) for c, d in chunk.itertuples(index=False)]
        con.executemany(f"INSERT OR REPLACE INTO {tabela} (codigo, descricao) VALUES (?, ?)", registros)
        total += len(registros)
    con.commit()
    LOGGER.info("Tabela %s: %d registros", tabela, total)
    return total


def resolver_municipios(con: sqlite3.Connection, nomes: list[str]) -> set[str]:
    """Converte nomes de municípios do config nos códigos usados pela RFB."""
    if not nomes:
        return set()
    mapa: dict[str, str] = {}
    for codigo, descricao in con.execute("SELECT codigo, descricao FROM municipios"):
        mapa.setdefault(normalizar_nome(descricao), codigo)
    codigos: set[str] = set()
    for nome in nomes:
        chave = normalizar_nome(nome)
        if chave not in mapa:
            raise ValueError(
                f"Município '{nome}' não encontrado na tabela da RFB — confira a grafia no config.yaml"
            )
        codigos.add(mapa[chave])
    return codigos


def carregar_estabelecimentos(
    con: sqlite3.Connection,
    zips: list[Path],
    uf: str,
    municipio_codigos: set[str],
    cnae_prefixos: list[str],
    data_corte: str | None,
) -> int:
    """Filtra estabelecimentos pelo ICP durante o streaming e grava no banco.

    data_corte: data mínima de início de atividade no formato 'AAAAMMDD' (ou None).
    """
    usecols = sorted(set(EST.values()))
    prefixos = tuple(cnae_prefixos)
    total = 0
    for caminho in zips:
        LOGGER.info("Processando %s ...", caminho.name)
        for chunk in _chunks_zip(caminho, usecols=usecols):
            df = chunk[chunk[EST["situacao"]] == SITUACAO_ATIVA]
            df = df[df[EST["uf"]] == uf]
            if municipio_codigos:
                df = df[df[EST["municipio_codigo"]].isin(municipio_codigos)]
            if prefixos:
                df = df[df[EST["cnae_principal"]].str.startswith(prefixos)]
            if data_corte:
                df = df[df[EST["data_inicio"]] >= data_corte]
            if df.empty:
                continue

            cnpj = df[EST["cnpj_basico"]] + df[EST["cnpj_ordem"]] + df[EST["cnpj_dv"]]
            telefone = (df[EST["ddd1"]].str.strip() + " " + df[EST["telefone1"]].str.strip()).str.strip()
            registros = pd.DataFrame(
                {
                    "cnpj_basico": df[EST["cnpj_basico"]],
                    "cnpj": cnpj,
                    "nome_fantasia": df[EST["nome_fantasia"]].str.strip(),
                    "data_inicio": df[EST["data_inicio"]],
                    "cnae_principal": df[EST["cnae_principal"]],
                    "uf": df[EST["uf"]],
                    "municipio_codigo": df[EST["municipio_codigo"]],
                    "telefone": telefone,
                    "email": df[EST["email"]].str.strip().str.lower(),
                }
            )
            con.executemany(
                "INSERT OR REPLACE INTO estabelecimentos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                registros.itertuples(index=False, name=None),
            )
            total += len(registros)
        con.commit()
    LOGGER.info("Estabelecimentos dentro do ICP: %d", total)
    return total


def _cnpjs_filtrados(con: sqlite3.Connection) -> set[str]:
    return {linha[0] for linha in con.execute("SELECT DISTINCT cnpj_basico FROM estabelecimentos")}


def carregar_empresas(
    con: sqlite3.Connection, zips: list[Path], portes_rfb: set[str]
) -> int:
    """Carrega apenas empresas cujos estabelecimentos passaram no filtro do ICP."""
    interesse = _cnpjs_filtrados(con)
    usecols = sorted(set(EMP.values()))
    total = 0
    for caminho in zips:
        LOGGER.info("Processando %s ...", caminho.name)
        for chunk in _chunks_zip(caminho, usecols=usecols):
            df = chunk[chunk[EMP["cnpj_basico"]].isin(interesse)]
            if portes_rfb:
                df = df[df[EMP["porte"]].isin(portes_rfb)]
            if df.empty:
                continue
            capital = pd.to_numeric(
                df[EMP["capital_social"]].str.replace(",", ".", regex=False), errors="coerce"
            ).fillna(0.0)
            registros = pd.DataFrame(
                {
                    "cnpj_basico": df[EMP["cnpj_basico"]],
                    "razao_social": df[EMP["razao_social"]].str.strip(),
                    "natureza_juridica": df[EMP["natureza_juridica"]],
                    "capital_social": capital,
                    "porte": df[EMP["porte"]],
                }
            )
            con.executemany(
                "INSERT OR REPLACE INTO empresas VALUES (?, ?, ?, ?, ?)",
                registros.itertuples(index=False, name=None),
            )
            total += len(registros)
        con.commit()
    LOGGER.info("Empresas dentro do ICP (porte): %d", total)
    return total


def carregar_simples(con: sqlite3.Connection, zips: list[Path]) -> int:
    """Carrega a situação no Simples Nacional dos CNPJs de interesse."""
    if not zips:
        return 0
    interesse = _cnpjs_filtrados(con)
    usecols = sorted(set(SIM.values()))
    total = 0
    for caminho in zips:
        LOGGER.info("Processando %s ...", caminho.name)
        for chunk in _chunks_zip(caminho, usecols=usecols):
            df = chunk[chunk[SIM["cnpj_basico"]].isin(interesse)]
            if df.empty:
                continue
            registros = pd.DataFrame(
                {
                    "cnpj_basico": df[SIM["cnpj_basico"]],
                    "opcao_simples": df[SIM["opcao_simples"]].str.strip(),
                    "opcao_mei": df[SIM["opcao_mei"]].str.strip(),
                }
            )
            con.executemany(
                "INSERT OR REPLACE INTO simples VALUES (?, ?, ?)",
                registros.itertuples(index=False, name=None),
            )
            total += len(registros)
        con.commit()
    LOGGER.info("Registros do Simples carregados: %d", total)
    return total


def executar_carga(con: sqlite3.Connection, arquivos: dict[str, list[Path]], config: dict) -> None:
    """Orquestra a carga completa: auxiliares -> estabelecimentos -> empresas -> simples."""
    icp = config["icp"]

    carregar_tabela_auxiliar(con, arquivos["cnaes"][0], "cnaes")
    carregar_tabela_auxiliar(con, arquivos["municipios"][0], "municipios")

    municipio_codigos = resolver_municipios(con, icp.get("municipios", []))

    data_corte = None
    idade_max = int(icp.get("idade_maxima_meses") or 0)
    if idade_max > 0:
        corte = pd.Timestamp.today().normalize() - pd.DateOffset(months=idade_max)
        data_corte = corte.strftime("%Y%m%d")
        LOGGER.info("Filtrando empresas abertas a partir de %s", corte.date())

    carregar_estabelecimentos(
        con,
        arquivos["estabelecimentos"],
        uf=icp["uf"],
        municipio_codigos=municipio_codigos,
        cnae_prefixos=icp.get("cnaes_alvo", []),
        data_corte=data_corte,
    )

    from .config import PORTE_RFB

    portes_rfb = {PORTE_RFB[p] for p in icp.get("portes", [])}
    carregar_empresas(con, arquivos["empresas"], portes_rfb)
    carregar_simples(con, arquivos.get("simples", []))
