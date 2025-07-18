from flask import Flask, render_template, request, jsonify
import re
import requests
import time
import logging

app = Flask(__name__, static_folder='static')

# Configurações
TOKEN_API = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpZCI6IjMzMSIsImNoYXZlIjoiOTI1MjRjNjI2Nzk3OTMyZWVkNTAxNjZlNjA2OGIxMTUiLCJ0aW1lc3RhbXAiOjE3NDQxOTc4MDZ9.kHED9W69zqHOH4NJ0rQh_LEmMhhWEuLlDCsVG_xe6kQ"
URL_API = "http://sistema.prolicitante.com.br/appapi/logistica/cotar_frete_externo"

# Configurar logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Variável global para controle de rate limiting
controle_requisicoes = [0, time.time()]

def limpar_cnpj(cnpj):
    return re.sub(r'\D', '', str(cnpj))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/cotar", methods=["POST"])
def cotar():
    global controle_requisicoes
    
    try:
        # Obter dados do formulário
        if request.content_type == 'application/json':
            dados = request.get_json()
        else:
            dados = request.form.to_dict()

        logger.debug(f"Dados recebidos: {dados}")

        # Validar campos obrigatórios
        campos_obrigatorios = ['cnpj_origem', 'cep_origem', 'cnpj_destino', 'cep_destino']
        for campo in campos_obrigatorios:
            if not dados.get(campo):
                logger.error(f"Campo obrigatório faltando: {campo}")
                return jsonify({
                    "status": "erro", 
                    "mensagem": f"O campo {campo} é obrigatório"
                }), 400

        # Controle de rate limiting
        if controle_requisicoes[0] >= 15:
            tempo_passado = time.time() - controle_requisicoes[1]
            if tempo_passado < 10:
                tempo_espera = 10 - tempo_passado
                time.sleep(tempo_espera)
            controle_requisicoes[0] = 0
            controle_requisicoes[1] = time.time()

        # Processar múltiplos pacotes
        produtos = []
        i = 0
        while True:
            quantidade = dados.get(f"quantidade_{i}")
            
            # Se for o primeiro pacote e não tiver quantidade, erro
            if i == 0 and not quantidade:
                logger.error("Nenhum pacote informado")
                return jsonify({
                    "status": "erro",
                    "mensagem": "É necessário informar pelo menos um pacote"
                }), 400
                
            # Se não tiver mais pacotes, sai do loop
            if not quantidade:
                break
                
            produtos.append({
                "descricao": f"Carga {i+1}",
                "quantidade": str(quantidade),
                "peso": str(dados.get(f"peso_{i}", 0)),
                "altura": str(dados.get(f"altura_{i}", 0)),
                "largura": str(dados.get(f"largura_{i}", 0)),
                "profundidade": str(dados.get(f"comprimento_{i}", 0)),
                "valor": str(float(dados.get(f"valor_unitario_{i}", 0)) * float(quantidade))
            })
            i += 1

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

        logger.debug(f"Payload enviado: {payload}")

        # Fazer requisição para a API
        response = requests.post(
            URL_API,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        response.raise_for_status()
        data = response.json()
        logger.debug(f"Resposta da API: {data}")

        # Processar resposta
        if isinstance(data, dict):
            if data.get("erro") in [False, "False"] and "resultado" in data:
                if data["resultado"]:
                    opcoes_ordenadas = sorted(
                        data["resultado"],
                        key=lambda x: float(x.get("total", 0))
                    )
                    
                    # Formatando os resultados
                    for opcao in opcoes_ordenadas:
                        opcao['total'] = float(opcao.get('total', 0))
                        opcao['prazo'] = opcao.get('prazo', 'N/A')
                    
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
            "mensagem": "Nenhuma transportadora disponível para esta rota"
        })

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na requisição: {str(e)}")
        return jsonify({
            "status": "erro",
            "mensagem": f"Erro na comunicação com a API: {str(e)}"
        }), 500
        
    except Exception as e:
        logger.error(f"Erro interno: {str(e)}")
        return jsonify({
            "status": "erro",
            "mensagem": f"Erro interno: {str(e)}"
        }), 500

if __name__ == "__main__":
    app.run(debug=True)
