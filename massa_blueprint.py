import os
import logging
from flask import Blueprint, render_template, request, jsonify, current_app

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------
logger = logging.getLogger(__name__)

# Carrega .env apenas em dev (opcional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---------------------------------------------------------------------
# Blueprint
#   IMPORTANTE: não importe este módulo dentro dele mesmo.
#   O app principal fará: from massa_blueprint import massa_bp
#   e depois app.register_blueprint(massa_bp, url_prefix="/massa")
# ---------------------------------------------------------------------
massa_bp = Blueprint("massa", __name__, template_folder="templates")

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _get_api_cfg():
    """
    Lê TOKEN_API e URL_API priorizando a config do app (produção),
    com fallback para variáveis de ambiente (dev).
    """
    token = (current_app.config.get("TOKEN_API")
             if current_app else None) or os.getenv("TOKEN_API")
    url = (current_app.config.get("URL_API")
           if current_app else None) or os.getenv("URL_API")
    return token, url

# ---------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------
@massa_bp.get("/")
def massa_home():
    """
    Tela inicial de cotação em massa.
    Use 'massa.html' como seu template principal para upload/ações em lote.
    """
    return render_template("massa.html", title="Cotação em Massa")

@massa_bp.post("/upload")
def upload():
    """
    Recebe um arquivo (CSV/Excel/JSON/ZIP/XML) para processar em lote.
    Nesta fase 1, apenas valida e retorna metadados básicos.
    A orquestração real (enviar cada item para /solicitar-coleta, etc.)
    pode ser feita no frontend, reutilizando seu endpoint único já existente.
    """
    # Verifica credenciais (não falhar aqui — o app já valida em startup)
    token, url = _get_api_cfg()
    if not token or not url:
        # mantemos 200 + msg clara para não bloquear UI; ajuste se quiser 500
        return jsonify({
            "status": "aviso",
            "mensagem": "TOKEN_API ou URL_API não configurado. Verifique as variáveis de ambiente."
        }), 200

    if "file" not in request.files or not request.files["file"].filename:
        # aceitamos também 'xml_file' para compatibilidade
        if "xml_file" in request.files and request.files["xml_file"].filename:
            f = request.files["xml_file"]
        else:
            return jsonify({"status": "erro", "mensagem": "Arquivo não enviado"}), 400
    else:
        f = request.files["file"]

    filename = f.filename
    size = 0
    try:
        # Não salvamos em disco nesta versão — apenas lemos para estatística
        content = f.read()
        size = len(content or b"")
    except Exception as e:
        logger.exception("Falha ao ler arquivo de upload")
        return jsonify({"status": "erro", "mensagem": f"Falha ao ler arquivo: {str(e)}"}), 400

    # Retorno simples; o frontend pode quebrar o lote e chamar /solicitar-coleta
    return jsonify({
        "status": "sucesso",
        "mensagem": "Arquivo recebido",
        "arquivo": {
            "nome": filename,
            "tamanho_bytes": size
        },
        "instrucoes": "Quebre o lote no frontend e envie cada item para /solicitar-coleta."
    }), 200

@massa_bp.get("/progresso")
def progresso():
    """
    Endpoint stub para progresso de processamento em massa.
    Em fases futuras, pode consultar uma fila/banco e retornar % concluído.
    """
    # Versão simples: retorna sempre vazio/0%. Ajuste para integrar com sua fila.
    return jsonify({
        "status": "ok",
        "itens_total": 0,
        "itens_processados": 0,
        "percentual": 0
    }), 200
