from flask import Flask, render_template, request, jsonify, send_file, url_for, redirect
from dotenv import load_dotenv
import os
import re
import logging
import json
import uuid
import requests
from datetime import datetime
import xml.etree.ElementTree as ET
from io import BytesIO

# ---------------------------------------------------------------------
# Logging (logo após imports, para capturar tudo)
# ---------------------------------------------------------------------
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente do arquivo .env
load_dotenv()

# ---------------------------------------------------------------------
# Tentativa de importar o blueprint externo (opcional)
# ---------------------------------------------------------------------
try:
    from massa_blueprint import massa_bp  # precisa definir 'massa_bp' lá
    logger.info("Blueprint 'massa_bp' importado com sucesso")
except Exception as e:
    import traceback
    print("\n[ERRO] Falha ao importar massa_blueprint:\n", e)
    traceback.print_exc()
    massa_bp = None

# ---------------------------------------------------------------------
# Configuração do app
# ---------------------------------------------------------------------
app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', 'chave-secreta-padrao-mude-isso-em-producao')
app.config['UPLOAD_FOLDER'] = 'Uploads'            # Corresponde ao esperado pelo blueprint
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB

# API Configuration
app.config['TOKEN_API'] = os.environ.get('TOKEN_API')
app.config['URL_API'] = os.environ.get('URL_API')

# Validar configurações
if not app.config['TOKEN_API'] or not app.config['URL_API']:
    raise ValueError(
        "TOKEN_API e URL_API devem ser configurados como variáveis de ambiente. "
        "Crie um arquivo .env na raiz do projeto com TOKEN_API e URL_API, "
        "ou defina essas variáveis no ambiente do sistema."
    )

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)  # Garante que a pasta Uploads exista

# ---------------------------------------------------------------------
# Armazenamento em memória (demo)
# ---------------------------------------------------------------------
cotacoes_selecionadas = []
solicitacoes_coleta = []

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
ALLOWED_STATUSES = {"solicitacao", "transito", "pendencia", "entregue"}

def _normalize_status(s: str) -> str:
    s = (s or "").strip().lower()
    mapa = {
        "solicitação de coleta": "solicitacao",
        "solicitação": "solicitacao",
        "solicitacao de coleta": "solicitacao",
        "trânsito": "transito",
        "pendência": "pendencia",
        "pendente": "pendencia",
    }
    s = mapa.get(s, s)
    return s if s in ALLOWED_STATUSES else "solicitacao"

def limpar_cnpj(cnpj):
    """Remove todos os caracteres não numéricos."""
    return re.sub(r'\D', '', str(cnpj))

def _findtext_anyns(root: ET.Element, tag: str, default: str = "") -> str:
    """Procura texto de um tag (ignorando namespace) em qualquer profundidade."""
    if root is None:
        return default
    for elem in root.iter():
        t = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        if t == tag:
            return (elem.text or "").strip()
    return default

def parse_nfe_xml(xml_str: str) -> dict:
    """
    Parser leve para NFe, ignorando namespaces.
    Extrai campos comuns; se algo não existir, retorna vazio.
    (Lê blocos 'emit' e 'dest' separadamente)
    """
    try:
        root = ET.fromstring(xml_str)
    except Exception:
        return {
            "numero": "",
            "serie": "",
            "data_emissao": "",
            "valor_nf": "",
            "origem": {"nome": "", "cnpj": "", "cep": "", "cidade": "", "uf": ""},
            "destino": {"nome": "", "cnpj": "", "cep": "", "cidade": "", "uf": ""},
        }

    def _find_first(root_el: ET.Element, tagname: str):
        for el in root_el.iter():
            t = el.tag.split('}')[-1] if '}' in el.tag else el.tag
            if t == tagname:
                return el
        return None

    numero = _findtext_anyns(root, "nNF")
    serie = _findtext_anyns(root, "serie")
    data_emissao = _findtext_anyns(root, "dhEmi") or _findtext_anyns(root, "dEmi")
    valor_nf = _findtext_anyns(root, "vNF")

    emit = _find_first(root, "emit")
    dest = _find_first(root, "dest")

    def bloco(node: ET.Element):
        return {
            "nome": _findtext_anyns(node, "xNome") if node is not None else "",
            "cnpj": _findtext_anyns(node, "CNPJ") if node is not None else "",
            "cep": _findtext_anyns(node, "CEP") if node is not None else "",
            "cidade": _findtext_anyns(node, "xMun") if node is not None else "",
            "uf": _findtext_anyns(node, "UF") if node is not None else "",
        }

    origem = bloco(emit)
    destino = bloco(dest)

    return {
        "numero": numero,
        "serie": serie,
        "data_emissao": data_emissao,
        "valor_nf": valor_nf,
        "origem": origem,
        "destino": destino,
    }

# ---------------------------------------------------------------------
# Filtros/Helpers Jinja
# ---------------------------------------------------------------------
@app.template_filter('extract_number')
def extract_number_filter(text):
    """Extrai o primeiro número de uma string (ou 0)."""
    if text:
        m = re.search(r'\d+', str(text))
        return int(m.group()) if m else 0
    return 0

@app.template_filter('format_date')
def format_date_filter(date_string):
    """Formata ISO/Z → dd/mm/aaaa HH:MM; se falhar, devolve original."""
    try:
        ds = (date_string or "").replace('Z', '')
        dt = datetime.fromisoformat(ds)
        return dt.strftime('%d/%m/%Y %H:%M')
    except Exception:
        return date_string

@app.context_processor
def inject_url_helpers():
    """Helpers para usar nos templates sem quebrar, com ou sem blueprint `massa`."""
    def massa_url():
        try:
            return url_for('massa.massa_home')
        except Exception:
            return url_for('massa_home_alias')  # alias simples para /massa/
    def massa_url_for(endpoint_suffix: str, **kwargs):
        if 'massa' in app.blueprints:
            return url_for(f'massa.{endpoint_suffix}', **kwargs)
        if endpoint_suffix == 'massa_home':
            return url_for('massa_home_alias')
        # para endpoints como upload/progresso, sem blueprint não há fallback seguro:
        raise RuntimeError(f"Endpoint massa.{endpoint_suffix} indisponível (blueprint não carregado).")
    return dict(massa_url=massa_url, massa_url_for=massa_url_for)

# ---------------------------------------------------------------------
# Rotas básicas
# ---------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    # endpoint ASCII para health check no Render
    return "ok", 200

@app.get("/health")
def health():
    return "OK - Flask respondeu"

@app.get("/")
def home():
    return render_template("index.html", title="Meu App")

@app.get("/cotacoes")
def cotacoes_alias():
    return render_template("index.html", title="Meu App")

# ---------------------------------------------------------------------
# Cotação individual
# ---------------------------------------------------------------------
@app.post("/cotar")
def cotar():
    """Endpoint de cotação de frete (individual)."""
    try:
        TOKEN_API = app.config.get('TOKEN_API')
        URL_API = app.config.get('URL_API')

        if not TOKEN_API or not URL_API:
            return jsonify({"status": "erro", "mensagem": "TOKEN_API ou URL_API não configurado no ambiente."}), 500

        dados = request.form.to_dict()
        logger.info(f"Dados recebidos: {dados}")

        # Validação mínima
        campos_obrigatorios = ['cnpj_origem', 'cep_origem', 'cnpj_destino', 'cep_destino']
        faltando = [c for c in campos_obrigatorios if not dados.get(c)]
        if faltando:
            msg = f"Campo obrigatório faltando: {', '.join(faltando)}"
            logger.error(msg)
            return jsonify({"status": "erro", "mensagem": msg}), 400

        # Processa pacotes
        produtos = []
        i = 0
        while True:
            qtd_key = f"quantidade_{i}"
            if not dados.get(qtd_key):
                if i == 0:
                    msg = "É necessário informar pelo menos um pacote"
                    logger.error(msg)
                    return jsonify({"status": "erro", "mensagem": msg}), 400
                break

            def f(name, default="0"):
                try:
                    return float(str(dados.get(name, default)).replace(',', '.'))
                except Exception:
                    return float(default)

            qtd = int(str(dados[qtd_key]).strip() or "0")
            produtos.append({
                "descricao": f"Carga {i+1}",
                "quantidade": str(qtd),
                "peso": str(f(f"peso_{i}")),
                "altura": str(f(f"altura_{i}")),
                "largura": str(f(f"largura_{i}")),
                "profundidade": str(f(f"comprimento_{i}")),
                "valor": str(f(f"valor_unitario_{i}") * qtd)
            })
            i += 1

        payload = {
            "id_contrato_transportadora_segmento": "1",
            "cnpj_origem": limpar_cnpj(dados["cnpj_origem"]),
            "cep_origem": dados["cep_origem"],
            "estado_origem": dados.get("estado_origem", "SC"),
            "cidade_origem": dados.get("cidade_origem", "Lages"),
            "cnpj_destino": limpar_cnpj(dados["cnpj_destino"]),
            "cep_destino": dados["cep_destino"],
            "estado_destino": dados.get("estado_destino", ""),
            "cidade_destino": dados.get("cidade_destino", ""),
            "produtos": produtos
        }

        # >>> ENVIA EXATAMENTE O QUE ESTÁ NO .ENV (sem forçar 'Bearer ')
        headers = {"Authorization": TOKEN_API, "Content-Type": "application/json"}

        try:
            logger.info(f"POST {URL_API} headers={{'Authorization': '***masked***', 'Content-Type': 'application/json'}} payload={payload}")
            resp = requests.post(URL_API, headers=headers, json=payload, timeout=30)
            logger.info(f"API status={resp.status_code}")
            logger.info(f"API raw text (primeiros 500): {resp.text[:500]}")
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.exception("Falha HTTP ao chamar API de frete")
            return jsonify({"status": "erro", "mensagem": f"Erro na comunicação com o sistema de fretes: {str(e)}"}), 502

        try:
            data = resp.json()
        except ValueError:
            logger.error("Resposta não é JSON. Veja 'API raw text' acima.")
            return jsonify({
                "status": "erro",
                "mensagem": "A API retornou um conteúdo não-JSON. Verifique TOKEN_API/URL_API."
            }), 502

        logger.info(f"Resposta JSON da API: {data}")

        if isinstance(data, dict) and "resultado" in data:
            todas = data.get("resultado") or []
            if todas:
                return jsonify({
                    "status": "sucesso",
                    "opcoes": [{
                        "transportadora": o.get("transportadora", "Transportadora não especificada"),
                        "total": float(o.get("total", 0) or 0),
                        "prazo": o.get("prazo", "N/A"),
                        "servico": o.get("servico", "Padrão"),
                        "imagem": o.get("imagem", ""),
                        "integrador": o.get("integrador", ""),
                        "observacao": o.get("observacao", "")
                    } for o in todas],
                    "dados_entrega": {
                        "valor_total_carga": sum(float(p["valor"]) for p in produtos),
                        "peso_total": sum(float(p["peso"]) for p in produtos),
                        "quantidade_total": sum(int(p["quantidade"]) for p in produtos)
                    }
                })

        return jsonify({
            "status": "sem_resultado",
            "mensagem": (data.get("mensagem") if isinstance(data, dict) else None) or
                        "Nenhuma transportadora disponível para esta rota"
        })

    except Exception as e:
        logger.exception("Erro interno no /cotar")
        return jsonify({"status": "erro", "mensagem": f"Erro interno no servidor: {str(e)}"}), 500

# ---------------------------------------------------------------------
# Selecionadas
# ---------------------------------------------------------------------
@app.route("/selecionadas", methods=["POST", "GET"])
def selecionadas():
    """Gerencia cotações selecionadas."""
    global cotacoes_selecionadas

    if request.method == "POST":
        try:
            dados_cotacao = request.get_json(silent=True) or {}
            dados_cotacao["id"] = str(uuid.uuid4())
            dados_cotacao["timestamp"] = datetime.now().isoformat()
            cotacoes_selecionadas.append(dados_cotacao)

            logger.info(f"Cotação selecionada: {dados_cotacao.get('transportadora')}")
            return jsonify({
                "status": "sucesso",
                "mensagem": "Cotação selecionada com sucesso",
                "total_selecionadas": len(cotacoes_selecionadas)
            })
        except Exception as e:
            logger.exception("Erro ao processar cotação selecionada")
            return jsonify({"status": "erro", "mensagem": f"Erro ao processar cotação: {str(e)}"}), 500

    return render_template("selecionadas.html", cotacoes=cotacoes_selecionadas)

@app.route("/selecionadas/<cotacao_id>", methods=["DELETE"])
def remover_cotacao(cotacao_id):
    """Remove uma cotação específica."""
    global cotacoes_selecionadas
    try:
        cotacoes_selecionadas = [c for c in cotacoes_selecionadas if c.get('id') != cotacao_id]
        return jsonify({
            "status": "sucesso",
            "mensagem": "Cotação removida com sucesso",
            "total_selecionadas": len(cotacoes_selecionadas)
        })
    except Exception as e:
        logger.exception("Erro ao remover cotação")
        return jsonify({"status": "erro", "mensagem": f"Erro ao remover cotação: {str(e)}"}), 500

@app.post("/selecionadas/limpar")
def limpar_selecionadas():
    """Limpa todas as cotações selecionadas."""
    global cotacoes_selecionadas
    cotacoes_selecionadas = []
    return jsonify({"status": "sucesso", "mensagem": "Cotações selecionadas foram limpas"})

# ---------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------
@app.get("/relatorios")
def relatorios():
    return render_template("relatorios.html", solicitacoes=solicitacoes_coleta)

@app.post("/relatorios/<solicitacao_id>/status")
def atualizar_status(solicitacao_id):
    """Atualiza o status da solicitação (solicitacao, transito, pendencia, entregue)."""
    try:
        payload = request.get_json(silent=True) or {}
        novo_status = _normalize_status(payload.get("status"))
        s = next((x for x in solicitacoes_coleta if x.get('id') == solicitacao_id), None)
        if not s:
            return jsonify({"status": "erro", "mensagem": "Solicitação não encontrada"}), 404

        s["status"] = novo_status
        return jsonify({"status": "sucesso", "novo_status": novo_status})
    except Exception as e:
        logger.exception("Erro ao atualizar status")
        return jsonify({"status": "erro", "mensagem": f"Erro ao atualizar status: {str(e)}"}), 500

@app.get("/relatorios/<solicitacao_id>/xml")
def visualizar_xml(solicitacao_id):
    try:
        s = next((x for x in solicitacoes_coleta if x.get('id') == solicitacao_id), None)
        if not s:
            return "Solicitação não encontrada", 404
        return f"<pre>{s.get('xml_content','')}</pre>"
    except Exception as e:
        logger.exception("Erro ao visualizar XML")
        return f"Erro ao visualizar XML: {str(e)}", 500

@app.get("/relatorios/<solicitacao_id>/download")
def download_xml(solicitacao_id):
    try:
        s = next((x for x in solicitacoes_coleta if x.get('id') == solicitacao_id), None)
        if not s:
            return "Solicitação não encontrada", 404

        xml_bytes = BytesIO(s.get('xml_content','').encode('utf-8'))
        return send_file(
            xml_bytes,
            as_attachment=True,
            download_name=s.get('xml_filename', 'documento.xml'),
            mimetype='application/xml'
        )
    except Exception as e:
        logger.exception("Erro ao fazer download do XML")
        return f"Erro ao fazer download: {str(e)}", 500

# ---------------------------------------------------------------------
# Alias/compat: endpoint esperado pelo template: url_for("solicitar_coleta")
# ---------------------------------------------------------------------
@app.route("/solicitar-coleta", methods=["POST"], endpoint="solicitar_coleta")
def solicitar_coleta_alias():
    """
    Alias para manter compatibilidade com templates que chamam url_for("solicitar_coleta").
    - Aceita JSON ou form-data/multipart.
    - Pode receber um arquivo XML (campo 'xml_file') OU texto XML (campo 'xml_content').
    - Pode receber 'cotacao_id' para vincular à cotação selecionada.
    """
    try:
        payload = request.get_json(silent=True) or {}
        form = request.form.to_dict() if not payload else {}
        files = request.files if not payload else {}

        cotacao_id = payload.get("cotacao_id") if payload else form.get("cotacao_id")
        observacoes = payload.get("observacoes") if payload else form.get("observacoes")

        xml_filename = ""
        xml_content = ""

        if files and "xml_file" in files and files["xml_file"]:
            f = files["xml_file"]
            xml_filename = f.filename or f"nfe_{uuid.uuid4().hex}.xml"
            xml_content = f.read().decode("utf-8", errors="ignore")
        else:
            xml_content = (payload.get("xml_content") if payload else form.get("xml_content")) or ""
            xml_filename = (payload.get("xml_filename") if payload else form.get("xml_filename")) or f"nfe_{uuid.uuid4().hex}.xml"

        nfe_info = parse_nfe_xml(xml_content) if xml_content else {}

        cotacao = next((c for c in cotacoes_selecionadas if c.get("id") == cotacao_id), None)

        solicitacao = {
            "id": str(uuid.uuid4()),
            "cotacao_id": cotacao_id,
            "cotacao": cotacao,
            "xml_filename": xml_filename,
            "xml_content": xml_content,
            "nfe_info": nfe_info,
            "observacoes": observacoes,
            "status": "solicitacao",
            "timestamp": datetime.now().isoformat()
        }
        solicitacoes_coleta.append(solicitacao)

        return jsonify({
            "status": "sucesso",
            "mensagem": "Solicitação de coleta registrada",
            "solicitacao_id": solicitacao["id"]
        })

    except Exception as e:
        logger.exception("Erro ao registrar solicitação de coleta")
        return jsonify({"status": "erro", "mensagem": f"Erro ao registrar solicitação: {str(e)}"}), 500

# ---------------------------------------------------------------------
# Seed (teste rápido)
# ---------------------------------------------------------------------
@app.get("/_seed")
def _seed():
    global cotacoes_selecionadas, solicitacoes_coleta

    cot = {
        "id": "seed-cot-1",
        "transportadora": "Rapidão LTDA",
        "servico": "Expresso",
        "prazo": "2 dias",
        "total": 150.75,
        "timestamp": datetime.now().isoformat()
    }
    cotacoes_selecionadas.append(cot)

    solic = {
        "id": "seed-sol-1",
        "cotacao_id": cot["id"],
        "cotacao": cot,
        "xml_filename": "teste.xml",
        "xml_content": "<xml>...</xml>",
        "nfe_info": {
            "numero": "123",
            "serie": "1",
            "data_emissao": datetime.now().isoformat(),
            "valor_nf": "1000.00",
            "origem": {"nome": "Empresa Origem", "cnpj": "00.000.000/0001-00", "cep": "00000-000", "cidade": "Lages", "uf": "SC"},
            "destino": {"nome": "Cliente Destino", "cnpj": "11.111.111/0001-11", "cep": "01001-000", "cidade": "São Paulo", "uf": "SP"},
        },
        "observacoes": "seed",
        "status": "pendencia",
        "timestamp": datetime.now().isoformat()
    }
    solicitacoes_coleta.append(solic)

    return jsonify({
        "ok": True,
        "selecionadas_url": url_for("selecionadas"),
        "relatorios_url": url_for("relatorios"),
        "massa_url": (url_for("massa.massa_home") if "massa" in app.blueprints else url_for("massa_home_alias"))
    })

# ---------------------------------------------------------------------
# Registro do blueprint (quando disponível) + fallback sem blueprint
# ---------------------------------------------------------------------
if massa_bp is not None:
    if "massa" not in app.blueprints:
        app.register_blueprint(massa_bp, url_prefix="/massa")
        app.logger.info("Blueprint 'massa' registrado em /massa")
    else:
        app.logger.info("Blueprint 'massa' já estava registrado; pulando registro.")
else:
    @app.get("/massa/")
    def massa_home_alias():
        return render_template("massa.html", title="Cotação em Massa (alias)")

# ---------------------------------------------------------------------
# Rota de diagnóstico (listar rotas)
# ---------------------------------------------------------------------
@app.get("/_routes")
def _routes():
    """Lista de rotas para conferência rápida no navegador."""
    return jsonify(sorted([{
        "endpoint": r.endpoint,
        "methods": sorted(list(r.methods - {'HEAD', 'OPTIONS'})),
        "rule": str(r)
    } for r in app.url_map.iter_rules()], key=lambda x: x["rule"]))

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

