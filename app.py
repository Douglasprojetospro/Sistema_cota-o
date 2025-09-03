from flask import Flask, render_template, request, send_file, jsonify, session
import pandas as pd
import re
import requests
import time
import os
from io import BytesIO
from werkzeug.utils import secure_filename
import threading
import atexit
import logging
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime

# Configure application
app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', 'chave-secreta-padrao-mude-isso-em-producao')  # Ajuste em produção
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Configuration
TOKEN_API = os.environ.get('TOKEN_API', "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpZCI6IjMzMSIsImNoYXZlIjoiOTI1MjRjNjI2Nzk3OTMyZWVkNTAxNjZlNjA2OGIxMTUiLCJ0aW1lc3RhbXAiOjE3NDQxOTc4MDZ9.kHED9W69zqHOH4NJ0rQh_LEmMhhWEuLlDCsVG_xe6kQ")
URL_API = "http://sistema.prolicitante.com.br/appapi/logistica/cotar_frete_externo"

# Configurações para cotação em massa
ALLOWED_EXTENSIONS = {'xlsx'}

# Variáveis globais
cotações_selecionadas = []
solicitacoes_coleta = []

# Variável global para progresso da cotação em massa
class ProgressState:
    def __init__(self):
        self.total = 0
        self.atual = 0
        self.processando = False
        self.arquivo = None
        self.erro = None
        self.nome_arquivo = None
        self.cancelar = False
        self.thread = None
        self.controle_requisicoes = [0, time.time()]
        self.tipo_retorno = 'mais_barata'

progresso = ProgressState()

# Funções auxiliares
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def limpar_cnpj(cnpj):
    return re.sub(r'\D', '', str(cnpj))

@app.template_filter('extract_number')
def extract_number_filter(text):
    import re
    if text:
        match = re.search(r'\d+', str(text))
        return int(match.group()) if match else 0
    return 0

@app.template_filter('format_date')
def format_date_filter(date_string):
    try:
        date_obj = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        return date_obj.strftime('%d/%m/%Y %H:%M')
    except:
        return date_string

# ========== ROTAS PRINCIPAIS ==========
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/cotacoes")
def cotacoes():
    return render_template("index.html")

@app.route("/massa")
def massa():
    return render_template("massa.html")

@app.route("/selecionadas")
def pagina_selecionadas():
    return render_template("selecionadas.html", cotações=cotações_selecionadas)

@app.route("/relatorios")
def relatorios():
    return render_template("relatorios.html", solicitacoes=solicitacoes_coleta)

# ========== HEALTH CHECK ==========
@app.route('/health')
def health_check():
    return jsonify({"status": "healthy"}), 200

# ========== COTAÇÃO INDIVIDUAL ==========
@app.route("/cotar", methods=["POST"])
def cotar():
    try:
        dados = request.form.to_dict()
        logger.info(f"Dados recebidos: {dados}")
        campos_obrigatorios = ['cnpj_origem', 'cep_origem', 'cnpj_destino', 'cep_destino']
        for campo in campos_obrigatorios:
            if not dados.get(campo):
                error_msg = f"Campo obrigatório faltando: {campo}"
                logger.error(error_msg)
                return jsonify({"status": "erro", "mensagem": error_msg}), 400
        produtos = []
        i = 0
        while True:
            qtd_key = f"quantidade_{i}"
            if not dados.get(qtd_key):
                if i == 0:
                    error_msg = "É necessário informar pelo menos um pacote"
                    logger.error(error_msg)
                    return jsonify({"status": "erro", "mensagem": error_msg}), 400
                break
               
            produtos.append({
                "descricao": f"Carga {i+1}",
                "quantidade": dados[qtd_key],
                "peso": dados.get(f"peso_{i}", "0"),
                "altura": dados.get(f"altura_{i}", "0"),
                "largura": dados.get(f"largura_{i}", "0"),
                "profundidade": dados.get(f"comprimento_{i}", "0"),
                "valor": str(float(dados.get(f"valor_unitario_{i}", "0")) * float(dados[qtd_key]))
            })
            i += 1
        payload = {
            "id_contrato_transportadora_segmento": "1",
            "cnpj_origem": limpar_cnpj(dados["cnpj_origem"]),
            "cep_origem": dados["cep_origem"],
            "estado_origem": "SC",
            "cidade_origem": "Lages",
            "cnpj_destino": limpar_cnpj(dados["cnpj_destino"]),
            "cep_destino": dados["cep_destino"],
            "estado_destino": "",
            "cidade_destino": "",
            "produtos": produtos
        }
        headers = {
            "Authorization": TOKEN_API,
            "Content-Type": "application/json"
        }
        logger.info(f"Payload enviado: {json.dumps(payload, indent=2)}")  # Depuração
        response = requests.post(URL_API, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Resposta completa da API: {json.dumps(data, indent=2)}")  # Depuração detalhada
        if isinstance(data, dict) and "resultado" in data:
            todas_opcoes = data["resultado"]
            logger.info(f"Opções retornadas: {todas_opcoes}")  # Depuração
            if todas_opcoes:
                return jsonify({
                    "status": "sucesso",
                    "opcoes": [{
                        "transportadora": opcao.get("transportadora", "Transportadora não especificada"),
                        "total": float(opcao.get("total", 0)),
                        "prazo": opcao.get("prazo", "N/A"),
                        "servico": opcao.get("servico", "Padrão"),
                        "imagem": opcao.get("imagem", ""),
                        "integrador": opcao.get("integrador", ""),
                        "observacao": opcao.get("observacao", "")
                    } for opcao in todas_opcoes],
                    "dados_entrega": {
                        "valor_total_carga": sum(float(p["valor"]) for p in produtos),
                        "peso_total": sum(float(p["peso"]) for p in produtos),
                        "quantidade_total": sum(int(p["quantidade"]) for p in produtos)
                    }
                })
        return jsonify({
            "status": "sem_resultado",
            "mensagem": data.get("mensagem", "Nenhuma transportadora disponível para esta rota")
        })
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na comunicação com a API: {str(e)}")
        return jsonify({
            "status": "erro",
            "mensagem": f"Erro na comunicação com o sistema de fretes: {str(e)}"
        }), 500
       
    except Exception as e:
        logger.error(f"Erro interno: {str(e)}")
        return jsonify({
            "status": "erro",
            "mensagem": f"Erro interno no servidor: {str(e)}"
        }), 500

# ========== COTAÇÕES SELECIONADAS ==========
@app.route("/selecionadas", methods=["POST", "GET"])
def selecionadas():
    global cotações_selecionadas
   
    if request.method == "POST":
        try:
            dados_cotacao = request.get_json()
            if not dados_cotacao or "transportadora" not in dados_cotacao or "total" not in dados_cotacao or "prazo" not in dados_cotacao:
                return jsonify({"status": "erro", "mensagem": "Dados da cotação inválidos"}), 400
            dados_cotacao["id"] = str(uuid.uuid4())
            dados_cotacao["timestamp"] = datetime.now().isoformat()
            cotações_selecionadas.append(dados_cotacao)
            logger.info(f"Cotação selecionada: {dados_cotacao['transportadora']}")
            return jsonify({
                "status": "sucesso",
                "mensagem": "Cotação selecionada com sucesso",
                "total_selecionadas": len(cotações_selecionadas)
            })
        except Exception as e:
            logger.error(f"Erro ao processar cotação selecionada: {str(e)}")
            return jsonify({
                "status": "erro",
                "mensagem": f"Erro ao processar cotação: {str(e)}"
            }), 500
           
    elif request.method == "GET":
        return jsonify({
            "status": "sucesso",
            "cotações_selecionadas": cotações_selecionadas,
            "total": len(cotações_selecionadas)
        })

@app.route("/selecionadas/<cotacao_id>", methods=["DELETE"])
def remover_cotacao(cotacao_id):
    global cotações_selecionadas
    cotações_selecionadas = [c for c in cotações_selecionadas if c.get('id') != cotacao_id]
    return jsonify({
        "status": "sucesso",
        "mensagem": "Cotação removida com sucesso",
        "total_selecionadas": len(cotações_selecionadas)
    })

@app.route("/selecionadas/limpar", methods=["POST"])
def limpar_selecionadas():
    global cotações_selecionadas
    cotações_selecionadas = []
    return jsonify({
        "status": "sucesso",
        "mensagem": "Cotações selecionadas foram limpas"
    })

# ========== SOLICITAÇÃO DE COLETA ==========
@app.route("/solicitar-coleta", methods=["POST"])
def solicitar_coleta():
    global solicitacoes_coleta
   
    try:
        if 'xml_file' not in request.files:
            return jsonify({"status": "erro", "mensagem": "Nenhum arquivo enviado"}), 400
       
        file = request.files['xml_file']
        if file.filename == '':
            return jsonify({"status": "erro", "mensagem": "Nenhum arquivo selecionado"}), 400
       
        if not file.filename.lower().endswith('.xml'):
            return jsonify({"status": "erro", "mensagem": "O arquivo deve ser XML"}), 400
       
        xml_content = file.read()
       
        try:
            root = ET.fromstring(xml_content)
            nfe_info = {}
            try:
                nfe_info['numero'] = root.find('.//{http://www.portalfiscal.inf.br/nfe}nNF').text
                nfe_info['serie'] = root.find('.//{http://www.portalfiscal.inf.br/nfe}serie').text
                nfe_info['data_emissao'] = root.find('.//{http://www.portalfiscal.inf.br/nfe}dhEmi').text
            except:
                nfe_info['numero'] = "Não identificado"
                nfe_info['serie'] = "Não identificada"
                nfe_info['data_emissao'] = datetime.now().isoformat()
           
        except ET.ParseError:
            return jsonify({"status": "erro", "mensagem": "Arquivo XML inválido"}), 400
       
        cotacao_id = request.form.get('cotacao_id')
        observacoes = request.form.get('observacoes', '')
       
        cotacao = next((c for c in cotações_selecionadas if c.get('id') == cotacao_id), None)
       
        if not cotacao:
            return jsonify({"status": "erro", "mensagem": "Cotação não encontrada"}), 404
       
        solicitacao = {
            "id": str(uuid.uuid4()),
            "cotacao_id": cotacao_id,
            "cotacao": cotacao,
            "xml_filename": file.filename,
            "xml_content": xml_content.decode('utf-8'),
            "nfe_info": nfe_info,
            "observacoes": observacoes,
            "status": "pendente",
            "timestamp": datetime.now().isoformat()
        }
       
        solicitacoes_coleta.append(solicitacao)
       
        return jsonify({
            "status": "sucesso",
            "mensagem": "Solicitação de coleta criada com sucesso",
            "solicitacao_id": solicitacao['id']
        })
       
    except Exception as e:
        logger.error(f"Erro ao processar solicitação de coleta: {str(e)}")
        return jsonify({
            "status": "erro",
            "mensagem": f"Erro ao processar solicitação: {str(e)}"
        }), 500

@app.route("/relatorios/<solicitacao_id>/xml")
def visualizar_xml(solicitacao_id):
    solicitacao = next((s for s in solicitacoes_coleta if s.get('id') == solicitacao_id), None)
    if not solicitacao:
        return "Solicitação não encontrada", 404
    return f"<pre>{solicitacao['xml_content']}</pre>"

@app.route("/relatorios/<solicitacao_id>/download")
def download_xml(solicitacao_id):
    solicitacao = next((s for s in solicitacoes_coleta if s.get('id') == solicitacao_id), None)
    if not solicitacao:
        return "Solicitação não encontrada", 404
   
    xml_bytes = BytesIO(solicitacao['xml_content'].encode('utf-8'))
    return send_file(
        xml_bytes,
        as_attachment=True,
        download_name=solicitacao['xml_filename'],
        mimetype='application/xml'
    )

# ========== COTAÇÃO EM MASSA ==========
def gerar_modelo():
    dados_modelo = [{
        "id_contrato_transportadora_segmento": 1,
        "cnpj_origem": "48.566.347/0001-22",
        "cep_origem": "88504-357",
        "estado_origem": "SC",
        "cidade_origem": "Lages",
        "cnpj_destino": "11.417.744/0001-22",
        "cep_destino": "89660-000",
        "estado_destino": "SC",
        "cidade_destino": "LACERDOPOLIS",
        "descricao": "Produto 1",
        "quantidade": 1,
        "peso": 20.5,
        "altura": 0.3,
        "largura": 0.4,
        "profundidade": 0.5,
        "valor": 500,
        "observacao": "Embalagem frágil"
    }]
   
    df = pd.DataFrame(dados_modelo)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return output

def processar_cotacao_massa(row):
    payload = {
        "id_contrato_transportadora_segmento": str(row["id_contrato_transportadora_segmento"]),
        "cnpj_origem": limpar_cnpj(row["cnpj_origem"]),
        "cep_origem": row["cep_origem"],
        "estado_origem": row["estado_origem"],
        "cidade_origem": row["cidade_origem"],
        "cnpj_destino": limpar_cnpj(row["cnpj_destino"]),
        "cep_destino": row["cep_destino"],
        "estado_destino": row["estado_destino"],
        "cidade_destino": row["cidade_destino"],
        "produtos": [{
            "descricao": row["descricao"],
            "quantidade": str(row["quantidade"]),
            "peso": str(row["peso"]),
            "altura": str(row["altura"]),
            "largura": str(row["largura"]),
            "profundidade": str(row["profundidade"]),
            "valor": str(row["valor"])
        }]
    }
    headers = {
        "Authorization": TOKEN_API,
        "Content-Type": "application/json"
    }
    try:
        if progresso.controle_requisicoes[0] >= 15:
            tempo_passado = time.time() - progresso.controle_requisicoes[1]
            if tempo_passado < 10:
                time.sleep(10 - tempo_passado)
            progresso.controle_requisicoes[0] = 0
            progresso.controle_requisicoes[1] = time.time()
        response = requests.post(URL_API, headers=headers, json=payload, timeout=30)
        progresso.controle_requisicoes[0] += 1
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and not data.get("erro") and "resultado" in data:
            if data["resultado"]:
                mais_barata = min(data["resultado"], key=lambda x: float(x["total"]))
                return {
                    "status": "sucesso",
                    "mais_barata": mais_barata,
                    "todas_opcoes": data["resultado"]
                }
        return {"status": "sem_resultado", "mensagem": data.get("mensagem", "Nenhuma cotação disponível")}
    except requests.exceptions.RequestException as e:
        return {"status": "erro", "mensagem": str(e)}

def processar_arquivo_background(filepath):
    try:
        df = pd.read_excel(filepath)
        resultados = []
        progresso.total = len(df)
        progresso.atual = 0
        progresso.processando = True
        progresso.erro = None
       
        for _, row in df.iterrows():
            if progresso.cancelar:
                break
               
            cotacao = processar_cotacao_massa(row)
            if cotacao["status"] == "sucesso":
                if progresso.tipo_retorno == 'mais_barata':
                    resultados.append({
                        **row.to_dict(),
                        "transportadora_mais_barata": cotacao["mais_barata"]["transportadora"],
                        "integrador_mais_barato": cotacao["mais_barata"]["integrador"],
                        "valor_frete_mais_barato": cotacao["mais_barata"]["total"],
                        "prazo_mais_barato": cotacao["mais_barata"]["prazo"],
                        "servico_mais_barato": cotacao["mais_barata"]["servico"],
                        "imagem_mais_barata": cotacao["mais_barata"]["imagem"],
                        "observacao_mais_barata": cotacao["mais_barata"].get("observacao", "")
                    })
                else:
                    for opcao in cotacao["todas_opcoes"]:
                        resultados.append({
                            **row.to_dict(),
                            "transportadora": opcao["transportadora"],
                            "integrador": opcao["integrador"],
                            "valor_frete": opcao["total"],
                            "prazo": opcao["prazo"],
                            "servico": opcao["servico"],
                            "imagem": opcao["imagem"],
                            "observacao": opcao.get("observacao", ""),
                            "melhor_opcao": "Sim" if opcao == cotacao["mais_barata"] else "Não"
                        })
            else:
                resultados.append({
                    **row.to_dict(),
                    "status": cotacao["status"],
                    "mensagem": cotacao.get("mensagem", "Erro na cotação")
                })
           
            progresso.atual += 1
       
        if not progresso.cancelar:
            df_resultado = pd.DataFrame(resultados)
           
            if progresso.tipo_retorno == 'mais_barata':
                df_final = df_resultado.drop(columns=['todas_opcoes'] if 'todas_opcoes' in df_resultado.columns else [])
            else:
                df_final = df_resultado
           
            output = BytesIO()
            df_final.to_excel(output, index=False)
            output.seek(0)
           
            progresso.arquivo = output
            progresso.nome_arquivo = f"resultado_{secure_filename(os.path.basename(filepath))}"
       
    except Exception as e:
        progresso.erro = str(e)
        logger.error(f"Erro no processamento: {str(e)}")
    finally:
        progresso.processando = False
        try:
            os.remove(filepath)
        except:
            pass

@app.route("/baixar_modelo")
def baixar_modelo():
    modelo = gerar_modelo()
    return send_file(
        modelo,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        download_name="modelo_cotacao_massa.xlsx",
        as_attachment=True
    )

@app.route("/upload", methods=["POST"])
def upload():
    if 'arquivo' not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
   
    file = request.files['arquivo']
    if file.filename == '':
        return jsonify({"erro": "Nenhum arquivo selecionado"}), 400
   
    if not allowed_file(file.filename):
        return jsonify({"erro": "Tipo de arquivo não permitido"}), 400
   
    tipo_retorno = request.form.get('tipo_retorno', 'mais_barata')
    progresso.tipo_retorno = tipo_retorno
   
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
   
    progresso.total = 0
    progresso.atual = 0
    progresso.processando = True
    progresso.arquivo = None
    progresso.erro = None
    progresso.nome_arquivo = None
    progresso.cancelar = False
    progresso.controle_requisicoes = [0, time.time()]
   
    if progresso.thread is not None and progresso.thread.is_alive():
        progresso.thread.join()
   
    progresso.thread = threading.Thread(target=processar_arquivo_background, args=(filepath,))
    progresso.thread.start()
   
    return jsonify({"mensagem": "Processamento iniciado"})

@app.route("/progresso")
def obter_progresso():
    if progresso.thread is not None and not progresso.thread.is_alive():
        progresso.processando = False
   
    response_data = {
        "processando": progresso.processando,
        "progresso": int((progresso.atual / progresso.total) * 100) if progresso.total > 0 else 0,
        "atual": progresso.atual,
        "total": progresso.total
    }
    if progresso.erro:
        response_data.update({
            "erro": progresso.erro,
            "processando": False
        })
    elif not progresso.processando and progresso.arquivo is not None:
        response_data.update({
            "completo": True,
            "nome_arquivo": progresso.nome_arquivo
        })
    elif not progresso.processando:
        response_data.update({
            "erro": "Processamento não iniciado ou cancelado",
            "processando": False
        })
   
    return jsonify(response_data)

@app.route("/baixar_resultado")
def baixar_resultado():
    if progresso.arquivo is None:
        return jsonify({"erro": "Nenhum arquivo disponível"}), 404
   
    file_obj = progresso.arquivo
    filename = progresso.nome_arquivo or "resultado_cotacoes.xlsx"
    file_obj.seek(0)
   
    return send_file(
        file_obj,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        download_name=filename,
        as_attachment=True
    )

@app.route("/cancelar", methods=["POST"])
def cancelar():
    progresso.cancelar = True
    return jsonify({"mensagem": "Processamento cancelado"})

def cleanup():
    if progresso.thread is not None and progresso.thread.is_alive():
        progresso.cancelar = True
        progresso.thread.join()

atexit.register(cleanup)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
