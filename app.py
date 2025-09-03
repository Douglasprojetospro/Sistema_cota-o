from flask import Flask, render_template, request, jsonify, session, send_file
import re
import requests
import time
import os
import logging
import json
from datetime import datetime
import uuid
import xml.etree.ElementTree as ET
from io import BytesIO

# Configure application
app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', 'chave-secreta-padrao-mude-isso-em-producao')

# API Configuration
TOKEN_API = os.environ.get('TOKEN_API', "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpZCI6IjMzMSIsImNoYXZlIjoiOTI1MjRjNjI2Nzk3OTMyZWVkNTAxNjZlNjA2OGIxMTUiLCJ0aW1lc3RhbXAiOjE3NDQxOTc4MDZ9.kHED9W69zqHOH4NJ0rQh_LEmMhhWEuLlDCsVG_xe6kQ")
URL_API = "http://sistema.prolicitante.com.br/appapi/logistica/cotar_frete_externo"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory storage for selected quotes and XML files
cotações_selecionadas = []
solicitacoes_coleta = []

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

@app.template_filter('format_date')
def format_date_filter(date_string):
    """Formata a data para exibição"""
    try:
        date_obj = datetime.fromisoformat(date_string.replace('Z', ''))
        return date_obj.strftime('%d/%m/%Y %H:%M')
    except:
        return date_string

@app.route("/")
def index():
    """Main page route"""
    return render_template("index.html")

@app.route("/cotar", methods=["POST"])
def cotar():
    """Freight calculation endpoint"""
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
            
            # Adicionar ID único e timestamp
            dados_cotacao["id"] = str(uuid.uuid4())
            dados_cotacao["timestamp"] = datetime.now().isoformat()
            
            # Adicionar à lista de cotações selecionadas
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
        # Renderizar página de cotações selecionadas
        return render_template("selecionadas.html", cotações=cotações_selecionadas)

@app.route("/selecionadas/<cotacao_id>", methods=["DELETE"])
def remover_cotacao(cotacao_id):
    """Endpoint para remover uma cotação específica"""
    global cotações_selecionadas
    
    try:
        # Encontrar e remover a cotação
        cotações_selecionadas = [c for c in cotações_selecionadas if c.get('id') != cotacao_id]
        
        return jsonify({
            "status": "sucesso",
            "mensagem": "Cotação removida com sucesso",
            "total_selecionadas": len(cotações_selecionadas)
        })
        
    except Exception as e:
        logger.error(f"Erro ao remover cotação: {str(e)}")
        return jsonify({
            "status": "erro",
            "mensagem": f"Erro ao remover cotação: {str(e)}"
        }), 500

@app.route("/solicitar-coleta", methods=["POST"])
def solicitar_coleta():
    """Endpoint para solicitar coleta com XML"""
    global solicitacoes_coleta
    
    try:
        # Verificar se foi enviado um arquivo
        if 'xml_file' not in request.files:
            return jsonify({"status": "erro", "mensagem": "Nenhum arquivo enviado"}), 400
        
        file = request.files['xml_file']
        
        # Verificar se o arquivo tem um nome
        if file.filename == '':
            return jsonify({"status": "erro", "mensagem": "Nenhum arquivo selecionado"}), 400
        
        # Verificar se é um arquivo XML
        if not file.filename.lower().endswith('.xml'):
            return jsonify({"status": "erro", "mensagem": "O arquivo deve ser XML"}), 400
        
        # Ler e processar o XML
        xml_content = file.read()
        
        try:
            # Tentar parsear o XML para validar
            root = ET.fromstring(xml_content)
            
            # Extrair informações básicas do XML (exemplo para NFe)
            nfe_info = {}
            try:
                nfe_info['numero'] = root.find('.//{http://www.portalfiscal.inf.br/nfe}nNF').text
                nfe_info['serie'] = root.find('.//{http://www.portalfiscal.inf.br/nfe}serie').text
                nfe_info['data_emissao'] = root.find('.//{http://www.portalfiscal.inf.br/nfe}dhEmi').text
            except:
                # Se não for NFe padrão, usar informações básicas
                nfe_info['numero'] = "Não identificado"
                nfe_info['serie'] = "Não identificada"
                nfe_info['data_emissao'] = datetime.now().isoformat()
            
        except ET.ParseError:
            return jsonify({"status": "erro", "mensagem": "Arquivo XML inválido"}), 400
        
        # Obter dados do formulário
        cotacao_id = request.form.get('cotacao_id')
        observacoes = request.form.get('observacoes', '')
        
        # Encontrar a cotação correspondente
        cotacao = next((c for c in cotações_selecionadas if c.get('id') == cotacao_id), None)
        
        if not cotacao:
            return jsonify({"status": "erro", "mensagem": "Cotação não encontrada"}), 404
        
        # Criar solicitação de coleta
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
        
        # Adicionar à lista de solicitações
        solicitacoes_coleta.append(solicitacao)
        
        logger.info(f"Solicitação de coleta criada: {solicitacao['id']}")
        
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

@app.route("/relatorios")
def relatorios():
    """Página de relatórios com solicitações de coleta"""
    return render_template("relatorios.html", solicitacoes=solicitacoes_coleta)

@app.route("/relatorios/<solicitacao_id>/xml")
def visualizar_xml(solicitacao_id):
    """Endpoint para visualizar XML de uma solicitação"""
    try:
        # Encontrar a solicitação
        solicitacao = next((s for s in solicitacoes_coleta if s.get('id') == solicitacao_id), None)
        
        if not solicitacao:
            return "Solicitação não encontrada", 404
        
        # Retornar o XML formatado
        return f"<pre>{solicitacao['xml_content']}</pre>"
        
    except Exception as e:
        logger.error(f"Erro ao visualizar XML: {str(e)}")
        return f"Erro ao visualizar XML: {str(e)}", 500

@app.route("/relatorios/<solicitacao_id>/download")
def download_xml(solicitacao_id):
    """Endpoint para download do XML"""
    try:
        # Encontrar a solicitação
        solicitacao = next((s for s in solicitacoes_coleta if s.get('id') == solicitacao_id), None)
        
        if not solicitacao:
            return "Solicitação não encontrada", 404
        
        # Criar arquivo para download
        xml_bytes = BytesIO(solicitacao['xml_content'].encode('utf-8'))
        
        return send_file(
            xml_bytes,
            as_attachment=True,
            download_name=solicitacao['xml_filename'],
            mimetype='application/xml'
        )
        
    except Exception as e:
        logger.error(f"Erro ao fazer download do XML: {str(e)}")
        return f"Erro ao fazer download: {str(e)}", 500

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
