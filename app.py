python
from flask import Flask, render_template, request, jsonify
import re
import requests
import time
import os
import logging

# Configure application
app = Flask(__name__, static_folder='static')

# API Configuration
TOKEN_API = os.environ.get('TOKEN_API', "seu_token_aqui")
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

        # Validate required fields
        campos_obrigatorios = ['cnpj_origem', 'cep_origem', 'cnpj_destino', 'cep_destino']
        for campo in campos_obrigatorios:
            if not dados.get(campo):
                return jsonify({
                    "status": "erro", 
                    "mensagem": f"O campo {campo} é obrigatório"
                }), 400

        # Process multiple packages
        produtos = []
        i = 0
        while True:
            if not dados.get(f"quantidade_{i}"):
                if i == 0:
                    return jsonify({
                        "status": "erro",
                        "mensagem": "É necessário informar pelo menos um pacote"
                    }), 400
                break
                
            produtos.append({
                "descricao": f"Carga {i+1}",
                "quantidade": dados.get(f"quantidade_{i}"),
                "peso": dados.get(f"peso_{i}", "0"),
                "altura": dados.get(f"altura_{i}", "0"),
                "largura": dados.get(f"largura_{i}", "0"),
                "profundidade": dados.get(f"comprimento_{i}", "0"),
                "valor": str(float(dados.get(f"valor_unitario_{i}", "0")) * float(dados.get(f"quantidade_{i}", "1"))
            })
            i += 1

        # Prepare API payload
        payload = {
            "id_contrato_transportadora_segmento": "9",
            "cnpj_origem": limpar_cnpj(dados.get("cnpj_origem")),
            "cep_origem": dados.get("cep_origem"),
            "estado_origem": "SC",
            "cidade_origem": "Lages",
            "cnpj_destino": limpar_cnpj(dados.get("cnpj_destino")),
            "cep_destino": dados.get("cep_destino"),
            "estado_destino": "",
            "cidade_destino": "",
            "produtos": produtos
        }

        headers = {
            "Authorization": TOKEN_API,
            "Content-Type": "application/json"
        }

        # Make API request
        response = requests.post(
            URL_API,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        data = response.json()

        if isinstance(data, dict) and not data.get("erro") and data.get("resultado"):
            opcoes_ordenadas = sorted(
                data["resultado"],
                key=lambda x: float(x.get("total", 0))
            
            return jsonify({
                "status": "sucesso",
                "opcoes": opcoes_ordenadas,
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

    except Exception as e:
        return jsonify({
            "status": "erro",
            "mensagem": f"Erro interno: {str(e)}"
        }), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
