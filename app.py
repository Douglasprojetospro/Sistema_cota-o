from flask import Flask, render_template, request, jsonify, session
import re
import requests
import time
import os
import logging
import json
from datetime import datetime

# Configure application
app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', 'chave-secreta-padrao-mude-isso-em-producao')

# API Configuration
TOKEN_API = os.environ.get('TOKEN_API', "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpZCI6IjMzMSIsImNoYXZlIjoiOTI1MjRjNjI2Nzk3OTMyZWVkNTAxNjZlNjA2OGIxMTUiLCJ0aW1lc3RhbXAiOjE3NDQxOTc4MDZ9.kHED9W69zqHOH4NJ0rQh_LEmMhhWEuLlDCsVG_xe6kQ")
URL_API = "http://sistema.prolicitante.com.br/appapi/logistica/cotar_frete_externo"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Rate limiting control
controle_requisicoes = [0, time.time()]

# In-memory storage for selected quotes (in production, use a database)
cotações_selecionadas = []

def limpar_cnpj(cnpj):
    """Remove all non-digit characters from CNPJ/CPF"""
    return re.sub(r'\D', '', str(cnpj))

@app.template_filter('extract_number')
def extract_number_filter(text):
    """Filtro para extrair números de strings"""
    import re
    if text:
        match = re.search(r'\d+', str(text))
        return int(match.group()) if match else 0
    return 0

@app.route("/")
def index():
    """Main page route"""
    return render_template("index.html")

@app.route("/cotar", methods=["POST"])
def cotar():
    """Freight calculation endpoint"""
    global controle_requisicoes
    
    try:
        # Get form data
        dados = request.form.to_dict()
        logger.info(f"Dados recebidos: {dados}")

        # Validate required fields
        campos_obrigatorios = ['cnpj_origem', 'cep_origem', 'cnpj_destino', 'cep_destino']
        for campo in campos_obrigatorios:
            if not dados.get(campo):
                error_msg = f"Campo obrigatório faltando: {campo}"
                logger.error(error_msg)
                return jsonify({"status": "erro", "mensagem": error_msg}), 400

        # Process multiple packages
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

        # Prepare API payload with fixed ID 9
        payload = {
            "id_contrato_transportadora_segmento": "1",  # ID fixo conforme solicitado
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

        # Make API request
        response = requests.post(URL_API, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Resposta completa da API: {data}")

        # Process all results without filtering
        if isinstance(data, dict) and "resultado" in data:
            todas_opcoes = data["resultado"]
            
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

@app.route("/selecionadas", methods=["POST", "GET"])
def selecionadas():
    """Endpoint para gerenciar cotações selecionadas"""
    global cotações_selecionadas
    
    if request.method == "POST":
        try:
            # Receber dados da cotação selecionada
            dados_cotacao = request.get_json()
            
            # Adicionar timestamp
            dados_cotacao["timestamp"] = datetime.now().isoformat()
            
            # Adicionar à lista de cotações selecionadas
            cotações_selecionadas.append(dados_cotacao)
            
            logger.info(f"Cotação selecionada: {dados_cotacao}")
            
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
        # Renderizar página de cotações selecionadas
        return render_template("selecionadas.html", cotações=cotações_selecionadas)

@app.route("/api/selecionadas", methods=["GET"])
def api_selecionadas():
    """API endpoint para obter cotações selecionadas (JSON)"""
    global cotações_selecionadas
    return jsonify({
        "status": "sucesso",
        "cotações_selecionadas": cotações_selecionadas,
        "total": len(cotações_selecionadas)
    })

@app.route("/selecionadas/limpar", methods=["POST"])
def limpar_selecionadas():
    """Endpoint para limpar cotações selecionadas"""
    global cotações_selecionadas
    cotações_selecionadas = []
    
    return jsonify({
        "status": "sucesso",
        "mensagem": "Cotações selecionadas foram limpas"
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
