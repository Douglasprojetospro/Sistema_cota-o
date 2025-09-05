from flask import Blueprint, render_template, request, jsonify, current_app, send_file
import pandas as pd
import requests
import re
import time
import os
from io import BytesIO
from werkzeug.utils import secure_filename
import threading
import atexit
import logging
import json
import uuid
from datetime import datetime

# Configuração do Blueprint
massa_bp = Blueprint("massa", __name__, template_folder="templates")
log = logging.getLogger(__name__)

# Configurações para cotação em massa
ALLOWED_EXTENSIONS = {'xlsx'}

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
        self.tipo_retorno = 'todas_opcoes'  # padrão (lista todas as opções)

progresso = ProgressState()

# ------------------------------------------------------------
# Funções auxiliares
# ------------------------------------------------------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def _num(s, default="0"):
    try:
        return float(str(s if s is not None else default).replace(",", "."))
    except Exception:
        return float(default)

def _int(s, default="0"):
    try:
        return int(str(s if s is not None else default))
    except Exception:
        return int(default)

def _limpar_cnpj(cnpj):
    return re.sub(r"\D", "", str(cnpj or ""))

def _normalizar_opcao(o):
    """Garante todas as chaves com valores padrão e total numérico."""
    if not isinstance(o, dict):
        log.error(f"Dado inválido para normalização (não é dicionário): {o}")
        return {
            "transportadora": "N/D",
            "integrador": "",
            "total": 0.0,
            "prazo": "N/A",
            "servico": "Padrão",
            "imagem": "",
            "observacao": "Dado inválido: não é dicionário"
        }
    try:
        servico = o.get("servico", "Padrão")
        if servico is None or not isinstance(servico, (str, int, float)):
            servico = "Padrão"
        else:
            servico = str(servico).strip() or "Padrão"
        return {
            "transportadora": str(o.get("transportadora", "N/D") or "N/D"),
            "integrador": str(o.get("integrador", "") or ""),
            "total": _num(o.get("total", 0)),
            "prazo": str(o.get("prazo", "N/A") or "N/A"),
            "servico": servico,
            "imagem": str(o.get("imagem", "") or ""),
            "observacao": str(o.get("observacao", "") or "")
        }
    except Exception as e:
        log.error(f"Erro ao normalizar opção: {o}, erro: {str(e)}")
        return {
            "transportadora": "N/D",
            "integrador": "",
            "total": 0.0,
            "prazo": "N/A",
            "servico": "Padrão",
            "imagem": "",
            "observacao": f"Erro na normalização: {str(e)}"
        }

def processar_cotacao_massa(row, url_api, token, progresso):
    if not url_api or not token:
        raise ValueError("URL_API ou TOKEN_API não configurados")

    payload = {
        "id_contrato_transportadora_segmento": str(row.get("id_contrato_transportadora_segmento", "")),
        "cnpj_origem": _limpar_cnpj(row.get("cnpj_origem")),
        "cep_origem": row.get("cep_origem"),
        "estado_origem": row.get("estado_origem"),
        "cidade_origem": row.get("cidade_origem"),
        "cnpj_destino": _limpar_cnpj(row.get("cnpj_destino")),
        "cep_destino": row.get("cep_destino"),
        "estado_destino": row.get("estado_destino"),
        "cidade_destino": row.get("cidade_destino"),
        "produtos": [{
            "descricao": row.get("descricao", "Carga"),
            "quantidade": str(row.get("quantidade", "")),
            "peso": str(row.get("peso", "")),
            "altura": str(row.get("altura", "")),
            "largura": str(row.get("largura", "")),
            "profundidade": str(row.get("profundidade", "")),
            "valor": str(row.get("valor", ""))
        }]
    }
    headers = {"Authorization": token, "Content-Type": "application/json"}

    try:
        # Rate limit simples
        if progresso.controle_requisicoes[0] >= 15:
            tempo_passado = time.time() - progresso.controle_requisicoes[1]
            if tempo_passado < 10:
                time.sleep(10 - tempo_passado)
            progresso.controle_requisicoes[0] = 0
            progresso.controle_requisicoes[1] = time.time()

        log.debug(f"Enviando requisição para {url_api} com payload: {json.dumps(payload, ensure_ascii=False)}")
        resp = requests.post(url_api, headers=headers, json=payload, timeout=30)
        progresso.controle_requisicoes[0] += 1
        resp.raise_for_status()
        data = resp.json()
        log.debug(f"Resposta da API (linha {progresso.atual}): {json.dumps(data, ensure_ascii=False)}")

        if not isinstance(data, dict) or data.get("erro"):
            return {
                "status": "sem_resultado",
                "mensagem": data.get("mensagem", "Nenhuma cotação disponível") if isinstance(data, dict) else "Resposta inválida"
            }

        brutas = data.get("resultado") or []
        opcoes = [_normalizar_opcao(o) for o in brutas if isinstance(o, dict)]
        opcoes_validas = [o for o in opcoes if isinstance(o.get("total"), (int, float)) and o.get("total") >= 0]

        if not opcoes_validas:
            return {"status": "sem_resultado", "mensagem": "Nenhuma cotação com total válido"}

        mais_barata = min(opcoes_validas, key=lambda x: x.get("total", 0))
        return {"status": "sucesso", "mais_barata": mais_barata, "todas_opcoes": opcoes}

    except requests.exceptions.RequestException as e:
        log.error(f"Erro na requisição HTTP: {str(e)}")
        return {"status": "erro", "mensagem": str(e)}
    except ValueError as e:
        log.error(f"Erro ao parsear resposta JSON: {str(e)}")
        return {"status": "erro", "mensagem": "Resposta não-JSON da API"}

def processar_arquivo_background(filepath, app, logger):
    with app.app_context():
        try:
            # Config
            url_api = app.config.get("URL_API")
            token = app.config.get("TOKEN_API")
            if not url_api or not token:
                raise RuntimeError("URL_API ou TOKEN_API não configurados")

            # Planilha
            try:
                df = pd.read_excel(filepath, engine="openpyxl")
            except ImportError:
                progresso.erro = "Dependência 'openpyxl' não instalada. Adicione 'openpyxl' ao requirements.txt."
                logger.error(progresso.erro)
                return
            except Exception as e:
                progresso.erro = f"Falha ao ler o Excel: {str(e)}"
                logger.exception(progresso.erro)
                return

            resultados = []

            progresso.total = len(df)
            progresso.atual = 0
            progresso.processando = True
            progresso.erro = None

            # Loop principal (blindado)
            for _, row in df.iterrows():
                if progresso.cancelar:
                    break

                try:
                    cotacao = processar_cotacao_massa(row, url_api=url_api, token=token, progresso=progresso)
                    logger.debug(f"Cotação (linha {progresso.atual}): {cotacao}")
                except Exception:
                    logger.exception(f"Erro ao processar cotação (linha {progresso.atual})")
                    resultados.append({
                        **row.to_dict(),
                        "status": "erro",
                        "mensagem": "Erro na cotação (ver logs)"
                    })
                    progresso.atual += 1
                    continue

                status = (cotacao or {}).get("status")
                if status == "sucesso":
                    if progresso.tipo_retorno == 'mais_barata':
                        mb = _normalizar_opcao((cotacao or {}).get("mais_barata") or {})
                        resultados.append({
                            **row.to_dict(),
                            "transportadora_mais_barata": mb.get("transportadora", "N/D"),
                            "integrador_mais_barato": mb.get("integrador", ""),
                            "valor_frete_mais_barato": mb.get("total", 0),
                            "prazo_mais_barato": mb.get("prazo", "N/A"),
                            "servico_mais_barato": mb.get("servico", "Padrão"),
                            "imagem_mais_barata": mb.get("imagem", ""),
                            "observacao_mais_barata": mb.get("observacao", "")
                        })
                    else:
                        todas = (cotacao or {}).get("todas_opcoes") or []
                        mb = _normalizar_opcao((cotacao or {}).get("mais_barata") or {})
                        total_mb = mb.get("total", None)
                        if not isinstance(todas, list):
                            logger.error(f"'todas_opcoes' não é lista (linha {progresso.atual}): {todas}")
                            resultados.append({
                                **row.to_dict(),
                                "status": "erro",
                                "mensagem": "Formato inválido de todas_opcoes"
                            })
                        else:
                            for opcao in todas:
                                opcao = _normalizar_opcao(opcao)
                                resultados.append({
                                    **row.to_dict(),
                                    "transportadora": opcao.get("transportadora", "N/D"),
                                    "integrador": opcao.get("integrador", ""),
                                    "valor_frete": opcao.get("total", 0),
                                    "prazo": opcao.get("prazo", "N/A"),
                                    "servico": opcao.get("servico", "Padrão"),
                                    "imagem": opcao.get("imagem", ""),
                                    "observacao": opcao.get("observacao", ""),
                                    "melhor_opcao": "Sim" if (total_mb is not None and opcao.get("total") == total_mb) else "Não"
                                })
                else:
                    resultados.append({
                        **row.to_dict(),
                        "status": (cotacao or {}).get("status", "erro"),
                        "mensagem": (cotacao or {}).get("mensagem", "Erro na cotação")
                    })

                progresso.atual += 1

            # Saída
            if not progresso.cancelar:
                df_resultado = pd.DataFrame(resultados)
                output = BytesIO()
                try:
                    # Escreve XLSX com openpyxl
                    with pd.ExcelWriter(output, engine="openpyxl") as writer:
                        df_resultado.to_excel(writer, index=False)
                except ImportError:
                    progresso.erro = "Dependência 'openpyxl' não instalada para gerar o XLSX. Adicione ao requirements.txt."
                    logger.error(progresso.erro)
                    return
                output.seek(0)
                progresso.arquivo = output
                progresso.nome_arquivo = f"resultado_{secure_filename(os.path.basename(filepath))}"

        except Exception:
            progresso.erro = "Erro geral no processamento"
            logger.exception("Erro geral no processamento")
        finally:
            progresso.processando = False

    # Fora do app_context: I/O
    try:
        os.remove(filepath)
    except Exception:
        pass

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
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return output

# ------------------------------------------------------------
# Rotas do Blueprint
# ------------------------------------------------------------
@massa_bp.get("/", endpoint="massa_home")
def massa_home():
    return render_template("massa.html", title="Cotação em Massa")

@massa_bp.post("/cotar-em-massa", endpoint="cotar_em_massa")
def cotar_em_massa():
    try:
        body = request.get_json(silent=True) or {}
        itens = body.get("itens") or []
        if not itens:
            return jsonify({"status": "erro", "mensagem": "Envie ao menos 1 item em 'itens'."}), 400

        token = current_app.config.get("TOKEN_API")
        url_api = current_app.config.get("URL_API")
        if not token or not url_api:
            return jsonify({"status": "erro", "mensagem": "TOKEN_API/URL_API não configurados."}), 500

        headers = {"Authorization": token, "Content-Type": "application/json"}
        resultados = []

        for idx, it in enumerate(itens):
            ref = it.get("ref") or f"linha-{idx+1}"
            faltando = [k for k in ["cnpj_origem", "cep_origem", "cnpj_destino", "cep_destino"] if not it.get(k)]
            if faltando:
                resultados.append({"ref": ref, "ok": False, "erro": f"Faltando: {', '.join(faltando)}"})
                continue

            pacotes = it.get("pacotes") or []
            if not pacotes:
                resultados.append({"ref": ref, "ok": False, "erro": "Informe ao menos 1 pacote"})
                continue

            produtos = []
            for p in pacotes:
                qtd = _int(p.get("quantidade", 0))
                produtos.append({
                    "descricao": "Carga",
                    "quantidade": str(qtd),
                    "peso": str(_num(p.get("peso", 0))),
                    "altura": str(_num(p.get("altura", 0))),
                    "largura": str(_num(p.get("largura", 0))),
                    "profundidade": str(_num(p.get("comprimento", 0))),
                    "valor": str(_num(p.get("valor_unitario", 0)) * max(qtd, 1)),
                })

            payload = {
                "id_contrato_transportadora_segmento": "1",
                "cnpj_origem": _limpar_cnpj(it.get("cnpj_origem")),
                "cep_origem": it.get("cep_origem"),
                "estado_origem": it.get("estado_origem", "SC"),
                "cidade_origem": it.get("cidade_origem", "Lages"),
                "cnpj_destino": _limpar_cnpj(it.get("cnpj_destino")),
                "cep_destino": it.get("cep_destino"),
                "estado_destino": it.get("estado_destino", ""),
                "cidade_destino": it.get("cidade_destino", ""),
                "produtos": produtos,
            }

            try:
                log.debug(f"Enviando requisição para {url_api} com payload: {json.dumps(payload, ensure_ascii=False)}")
                r = requests.post(url_api, headers=headers, json=payload, timeout=30)
                r.raise_for_status()
                data = r.json()
                log.debug(f"Resposta da API para {ref}: {json.dumps(data, ensure_ascii=False)}")
            except requests.exceptions.RequestException as e:
                resultados.append({"ref": ref, "ok": False, "erro": f"Falha HTTP: {e}"})
                continue
            except ValueError:
                resultados.append({"ref": ref, "ok": False, "erro": "Resposta não-JSON da API externa"})
                continue

            opcoes = []
            for o in (data.get("resultado") or []):
                opcoes.append(_normalizar_opcao(o))

            resultados.append({"ref": ref, "ok": True, "opcoes": opcoes, "mensagem": data.get("mensagem")})

        return jsonify({"status": "sucesso", "processados": len(resultados), "resultados": resultados, "timestamp": datetime.now().isoformat()})
    except Exception:
        current_app.logger.exception("Erro em /massa/cotar-em-massa")
        return jsonify({"status": "erro", "mensagem": "Erro interno"}), 500

@massa_bp.post("/solicitar-coleta", endpoint="solicitar_coleta")
def solicitar_coleta():
    return jsonify({"status": "sucesso", "mensagem": "OK (massa.solicitar_coleta)"}), 200

@massa_bp.get("/baixar_modelo", endpoint="baixar_modelo")
def baixar_modelo():
    modelo = gerar_modelo()
    return send_file(
        modelo,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        download_name="modelo_cotacao_massa.xlsx",
        as_attachment=True
    )

@massa_bp.route("/upload", methods=["POST"], endpoint="upload")
def upload():
    if 'arquivo' not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400

    file = request.files['arquivo']
    if file.filename == '':
        return jsonify({"erro": "Nenhum arquivo selecionado"}), 400

    if not allowed_file(file.filename):
        return jsonify({"erro": "Tipo de arquivo não permitido"}), 400

    progresso.tipo_retorno = request.form.get('tipo_retorno', 'todas_opcoes')

    filename = secure_filename(file.filename)
    upload_dir = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)
    file.save(filepath)

    # Reset
    progresso.total = progresso.atual = 0
    progresso.processando = True
    progresso.arquivo = None
    progresso.erro = None
    progresso.nome_arquivo = None
    progresso.cancelar = False
    progresso.controle_requisicoes = [0, time.time()]

    app = current_app._get_current_object()
    logger = current_app.logger

    if progresso.thread is not None and progresso.thread.is_alive():
        progresso.cancelar = True
        progresso.thread.join()

    progresso.thread = threading.Thread(
        target=processar_arquivo_background,
        args=(filepath, app, logger),
        daemon=True
    )
    progresso.thread.start()

    return jsonify({"mensagem": "Processamento iniciado"})

@massa_bp.get("/progresso", endpoint="progresso")
@massa_bp.get("/obter_progresso", endpoint="obter_progresso")  # alias p/ templates antigos
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

@massa_bp.get("/baixar_resultado", endpoint="baixar_resultado")
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

@massa_bp.post("/cancelar", endpoint="cancelar")
def cancelar():
    progresso.cancelar = True
    return jsonify({"mensagem": "Processamento cancelado"})

# Cleanup ao encerrar a aplicação
def cleanup():
    if progresso.thread is not None and progresso.thread.is_alive():
        progresso.cancelar = True
        progresso.thread.join()

atexit.register(cleanup)

