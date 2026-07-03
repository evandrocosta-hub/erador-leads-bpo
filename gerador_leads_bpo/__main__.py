"""CLI do pipeline: python -m gerador_leads_bpo --config config.yaml"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .carga import criar_banco, executar_carga
from .config import carregar_config
from .download import baixar_dados
from .exportar import exportar_excel
from .score import gerar_leads

LOGGER = logging.getLogger("gerador_leads_bpo")


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gerador-leads-bpo",
        description=(
            "Gera listas de prospecção de PMEs para BPO financeiro a partir dos "
            "Dados Abertos do CNPJ (Receita Federal)."
        ),
    )
    parser.add_argument("--config", default="config.yaml", help="caminho do config.yaml com o ICP")
    parser.add_argument("--saida", default=None, help="sobrescreve o caminho do Excel de saída")
    parser.add_argument(
        "--pular-download",
        action="store_true",
        help="não baixa nem recarrega dados; gera o Excel a partir do SQLite existente",
    )
    parser.add_argument(
        "--forcar-download", action="store_true", help="baixa novamente mesmo se o arquivo já existir"
    )
    parser.add_argument("--versao", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = montar_parser().parse_args(argv)

    config = carregar_config(args.config)
    if args.saida:
        config["saida"]["arquivo_excel"] = args.saida

    if args.pular_download:
        LOGGER.info("Pulando download/carga; usando banco existente %s", config["dados"]["banco_sqlite"])
        con = criar_banco(config["dados"]["banco_sqlite"], recriar=False)
    else:
        arquivos = baixar_dados(config, forcar=args.forcar_download)
        # a carga sempre recria o banco para refletir o ICP atual do config
        con = criar_banco(config["dados"]["banco_sqlite"], recriar=True)
        executar_carga(con, arquivos, config)

    try:
        leads = gerar_leads(con, config)
        if leads.empty:
            LOGGER.warning("Nenhum lead encontrado com o ICP atual — revise os filtros do config.yaml")
        caminho = exportar_excel(leads, config["saida"]["arquivo_excel"])
    finally:
        con.close()

    LOGGER.info("Pronto! Lista de prospecção em: %s", caminho)
    return 0


if __name__ == "__main__":
    sys.exit(main())
