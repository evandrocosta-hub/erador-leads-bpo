# Imagem para rodar o pipeline em qualquer máquina/VPS com IP brasileiro.
# Uso:
#   docker build -t gerador-leads-bpo .
#   docker run --rm -v "$PWD/saida:/app/saida" -v "$PWD/dados:/app/dados" gerador-leads-bpo
# A planilha final aparece em ./saida/leads_bpo.xlsx no host.
FROM python:3.12-slim

WORKDIR /app

# Dependências primeiro, para aproveitar o cache de camadas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# config.yaml e as pastas de dados/saída podem ser montados como volumes
VOLUME ["/app/dados", "/app/saida"]

ENTRYPOINT ["python", "-m", "gerador_leads_bpo"]
CMD ["--config", "config.yaml"]
