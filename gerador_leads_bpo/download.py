"""Download dos arquivos de Dados Abertos do CNPJ (Receita Federal).

Baixa apenas o necessário para o pipeline: Estabelecimentos*.zip,
Empresas*.zip, Cnaes.zip, Municipios.zip e, opcionalmente, Simples.zip.
Os arquivos de Sócios NÃO são baixados (dados de pessoa física ficam de
fora por decisão de projeto — ver aviso de LGPD no README).
"""

from __future__ import annotations

import logging
import re
import time
import zipfile
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger(__name__)

ARQUIVOS_AUXILIARES = ("Cnaes.zip", "Municipios.zip")
_BLOCO = 1024 * 1024  # 1 MiB

# O servidor da RFB é lento e instável: conexão pode demorar a ser aceita e
# quedas no meio do download são comuns. (connect, read) em segundos.
_TIMEOUT = (30, 300)
_MAX_TENTATIVAS = 5          # tentativas por operação de rede (loop externo)
_ESPERA_BASE = 10           # segundos; backoff exponencial entre tentativas


def _nova_sessao() -> requests.Session:
    sessao = requests.Session()
    sessao.headers["User-Agent"] = (
        "Mozilla/5.0 (compatible; gerador-leads-bpo/1.0; uso B2B; dados publicos RFB)"
    )
    # Adapter cuida apenas de respostas transitórias de status (5xx/429); os
    # erros de conexão ficam a cargo do loop externo _get_com_retry/baixar,
    # para não multiplicar os timeouts de connect num eventual bloqueio de IP.
    retry = Retry(
        total=2,
        connect=0,
        read=0,
        backoff_factor=3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adaptador = HTTPAdapter(max_retries=retry)
    sessao.mount("https://", adaptador)
    sessao.mount("http://", adaptador)
    return sessao


def _get_com_retry(sessao: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET com backoff exponencial por cima do retry do adapter.

    Cobre o caso do servidor da RFB que simplesmente não responde ao SYN
    (connect timeout), quando um novo pedido minutos depois costuma passar.
    """
    kwargs.setdefault("timeout", _TIMEOUT)
    ultimo_erro: Exception | None = None
    for tentativa in range(1, _MAX_TENTATIVAS + 1):
        try:
            resposta = sessao.get(url, **kwargs)
            resposta.raise_for_status()
            return resposta
        except requests.RequestException as erro:
            ultimo_erro = erro
            if tentativa == _MAX_TENTATIVAS:
                break
            espera = _ESPERA_BASE * (2 ** (tentativa - 1))
            LOGGER.warning(
                "Falha ao acessar %s (tentativa %d/%d): %s — aguardando %ds",
                url, tentativa, _MAX_TENTATIVAS, erro, espera,
            )
            time.sleep(espera)
    raise RuntimeError(
        f"Não foi possível acessar {url} após {_MAX_TENTATIVAS} tentativas. "
        f"O servidor da RFB pode estar fora do ar ou bloqueando este IP. "
        f"Último erro: {ultimo_erro}"
    ) from ultimo_erro


def resolver_referencia(url_base: str, referencia: str, sessao: requests.Session) -> str:
    """Resolve 'mais_recente' para a última pasta mensal (YYYY-MM) do índice."""
    if referencia and referencia != "mais_recente":
        return referencia
    resposta = _get_com_retry(sessao, url_base)
    pastas = sorted(set(re.findall(r"(\d{4}-\d{2})/", resposta.text)))
    if not pastas:
        raise RuntimeError(f"Nenhuma pasta mensal (YYYY-MM) encontrada em {url_base}")
    return pastas[-1]


def listar_arquivos_remotos(url_pasta: str, sessao: requests.Session) -> list[str]:
    resposta = _get_com_retry(sessao, url_pasta)
    nomes = set(re.findall(r'href="([A-Za-z0-9._-]+\.zip)"', resposta.text))
    return sorted(nomes)


def selecionar_arquivos(
    disponiveis: list[str], usar_simples: bool, limite_arquivos: int
) -> list[str]:
    """Escolhe, entre os arquivos do índice, apenas os que o pipeline usa."""
    empresas = sorted(n for n in disponiveis if n.startswith("Empresas"))
    estabelecimentos = sorted(n for n in disponiveis if n.startswith("Estabelecimentos"))
    if not empresas or not estabelecimentos:
        raise RuntimeError(
            "Índice remoto não contém arquivos Empresas*/Estabelecimentos* — "
            "verifique dados.url_base e dados.referencia"
        )
    if limite_arquivos and limite_arquivos > 0:
        LOGGER.warning(
            "limite_arquivos=%d ativo: processando %d/%d arquivos de cada tipo "
            "(a lista de leads ficará INCOMPLETA)",
            limite_arquivos, min(limite_arquivos, len(empresas)), len(empresas),
        )
        empresas = empresas[:limite_arquivos]
        estabelecimentos = estabelecimentos[:limite_arquivos]

    selecionados = list(ARQUIVOS_AUXILIARES) + estabelecimentos + empresas
    if usar_simples and "Simples.zip" in disponiveis:
        selecionados.append("Simples.zip")
    faltando = [n for n in ARQUIVOS_AUXILIARES if n not in disponiveis]
    if faltando:
        raise RuntimeError(f"Arquivos auxiliares ausentes no índice remoto: {faltando}")
    return selecionados


def _tamanho_remoto(url: str, sessao: requests.Session) -> int | None:
    try:
        resposta = sessao.head(url, timeout=60, allow_redirects=True)
        tamanho = resposta.headers.get("Content-Length")
        return int(tamanho) if tamanho else None
    except (requests.RequestException, ValueError):
        return None


def baixar_arquivo(url: str, destino: Path, sessao: requests.Session, forcar: bool = False) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() and not forcar:
        tamanho_remoto = _tamanho_remoto(url, sessao)
        if tamanho_remoto is None or destino.stat().st_size == tamanho_remoto:
            LOGGER.info("Já baixado, pulando: %s", destino.name)
            return destino
        LOGGER.info("Tamanho divergente, baixando novamente: %s", destino.name)

    parcial = destino.with_suffix(destino.suffix + ".parte")
    ultimo_erro: Exception | None = None
    for tentativa in range(1, _MAX_TENTATIVAS + 1):
        try:
            LOGGER.info("Baixando %s (tentativa %d/%d) ...", url, tentativa, _MAX_TENTATIVAS)
            with sessao.get(url, stream=True, timeout=_TIMEOUT) as resposta:
                resposta.raise_for_status()
                with open(parcial, "wb") as saida:
                    for bloco in resposta.iter_content(chunk_size=_BLOCO):
                        saida.write(bloco)
            if not zipfile.is_zipfile(parcial):
                parcial.unlink(missing_ok=True)
                raise RuntimeError(f"Download corrompido (não é um ZIP válido): {url}")
            parcial.replace(destino)
            LOGGER.info("Concluído: %s (%.1f MB)", destino.name, destino.stat().st_size / 1e6)
            return destino
        except (requests.RequestException, RuntimeError) as erro:
            ultimo_erro = erro
            parcial.unlink(missing_ok=True)
            if tentativa == _MAX_TENTATIVAS:
                break
            espera = _ESPERA_BASE * (2 ** (tentativa - 1))
            LOGGER.warning("Falha ao baixar %s: %s — aguardando %ds", destino.name, erro, espera)
            time.sleep(espera)
    raise RuntimeError(
        f"Não foi possível baixar {url} após {_MAX_TENTATIVAS} tentativas. "
        f"Último erro: {ultimo_erro}"
    ) from ultimo_erro


def baixar_dados(config: dict, forcar: bool = False) -> dict[str, list[Path]]:
    """Baixa todos os arquivos necessários e retorna os caminhos por tipo."""
    cfg = config["dados"]
    sessao = _nova_sessao()
    referencia = resolver_referencia(cfg["url_base"], cfg["referencia"], sessao)
    url_pasta = cfg["url_base"].rstrip("/") + f"/{referencia}/"
    LOGGER.info("Referência dos dados: %s", referencia)

    disponiveis = listar_arquivos_remotos(url_pasta, sessao)
    selecionados = selecionar_arquivos(
        disponiveis, cfg.get("usar_simples", True), int(cfg.get("limite_arquivos") or 0)
    )

    diretorio = Path(cfg["diretorio_download"]) / referencia
    caminhos: list[Path] = []
    for nome in selecionados:
        caminhos.append(baixar_arquivo(url_pasta + nome, diretorio / nome, sessao, forcar))

    return {
        "cnaes": [c for c in caminhos if c.name == "Cnaes.zip"],
        "municipios": [c for c in caminhos if c.name == "Municipios.zip"],
        "estabelecimentos": [c for c in caminhos if c.name.startswith("Estabelecimentos")],
        "empresas": [c for c in caminhos if c.name.startswith("Empresas")],
        "simples": [c for c in caminhos if c.name == "Simples.zip"],
    }
