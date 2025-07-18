from flask import Flask, render_template, request, jsonify
import re
import requests
import time
import os
import logging

# Configure application
app = Flask(__name__, static_folder='static')

# API Configuration
TOKEN_API = os.environ.get('TOKEN_API', "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpZCI6IjMzMSIsImNoYXZlIjoiOTI1MjRjNjI2Nzk3OTMyZWVkNTAxNjZlNjA2OGIxMTUiLCJ0aW1lc3RhbXAiOjE3NDQxOTc4MDZ9.kHED9W69zqHOH4NJ0rQh_LEmMhhWEuLlDCsVG_xe6kQ")
URL_API = "http://sistema.prolicitante.com.br/appapi/logistica/cotar_frete_externo"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Rate limiting control
controle_requisicoes = [0, time.time()]

def limpar_cnpj(cnpj):
    """Remove all non-digit characters from CNPJ/CPF"""
    return re.sub(r'\D', '', str(cnpj))

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

        # Rate limiting
        if controle_requisicoes[0] >= 15:
            tempo_passado = time.time() - controle_requisicoes[1]
            if tempo_passado < 10:
                time.sleep(10 - tempo_passado)
            controle_requisicoes[0] = 0
            controle_requisicoes[1] = time.time()

        # Process packages
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

        # Prepare API request
        payload = {
            "id_contrato_transportadora_segmento": "9",
            "cnpj_origem": limpar_cnpj(dados["cnpj_origem"]),
            "cep_origem": dados["cep_origem"],
            "estado_origem": "SC",
            "cidade_origem": "Lages",
            "cnpj_destino": limpar_cnpj(dados["cnpj_destino"]),
            "cep_destino": dados["cep_destino"],
            "produtos": produtos
        }

        headers = {"Authorization": TOKEN_API, "Content-Type": "application/json"}
        logger.info(f"Enviando para API: {payload}")

        # Make API call
        response = requests.post(URL_API, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Resposta da API: {data}")

        # Process response
        if data.get("erro") in [False, "False", 0, "0"] and data.get("resultado"):
            resultados = sorted(
                data["resultado"],
                key=lambda x: float(x.get("total", 0))
            )
            
            return jsonify({
                "status": "sucesso",
                "opcoes": [{
                    "transportadora": r.get("transportadora"),
                    "total": float(r.get("total", 0)),
                    "prazo": r.get("prazo", "N/A"),
                    "servico": r.get("servico", "Padrão"),
                    "imagem": r.get("imagem")
                } for r in resultados],
                "dados_entrega": {
                    "valor_total": sum(float(p["valor"]) for p in produtos),
                    "peso_total": sum(float(p["peso"]) for p in produtos),
                    "qtd_total": sum(int(p["quantidade"]) for p in produtos)
                }
            })

        return jsonify({
            "status": "sem_resultado",
            "mensagem": data.get("mensagem", "Nenhuma transportadora disponível")
        })

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro API: {str(e)}")
        return jsonify({
            "status": "erro",
            "mensagem": f"Falha na comunicação com a transportadora: {str(e)}"
        }), 502
        
    except Exception as e:
        logger.error(f"Erro inesperado: {str(e)}")
        return jsonify({
            "status": "erro",
            "mensagem": "Ocorreu um erro interno"
        }), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
