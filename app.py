import streamlit as st
import math
import pandas as pd
import numpy as np


# Exemplo de como DEVE ESTAR DENTRO DO SEU CÓDIGO (app.py ou cabos.py)

import os
import sys

def resource_path(relative_path):
    # Lógica de path corrigida para PyInstaller e Streamlit Cloud
    # Em um ambiente Streamlit Cloud simples, o sys._MEIPASS pode não ser necessário,
    # mas o os.path.abspath(".") é essencial para encontrar arquivos na raiz.
    base_path = os.path.abspath(os.path.dirname(__file__)) 
    return os.path.join(base_path, relative_path)

# E o carregamento:
# df = pd.read_csv(resource_path('tabela_cabos_br.csv'))


# --- FUNÇÕES DE CARREGAMENTO E PREPARAÇÃO DE DADOS ---

@st.cache_data
def carregar_e_preparar_dados(caminho_csv):
    """Carrega o CSV de cabos (Pt-BR) e prepara a tabela."""
    try:
        df = pd.read_csv(caminho_csv, sep=';', decimal=',')
        tabela_cabos = df.set_index('Bitola').T.to_dict('series')
        
        final_table = {}
        for bitola, data in tabela_cabos.items():
             final_table[bitola] = [
                 data['R_ohm_km'],         # 0: Resistência (R)
                 data['X_ohm_km'],         # 1: Reatância (X)
                 data['I_admissivel'],     # 2: Corrente Admissível (I)
                 data['Custo_por_metro']   # 3: Custo por metro (C)
             ]
        return final_table
        
    except FileNotFoundError:
        st.error(f"Erro: O ficheiro {caminho_csv} não foi encontrado.")
        return {}
    except Exception as e:
        st.error(f"Erro ao processar o CSV principal: {e}")
        return {}

@st.cache_data
def carregar_e_preparar_areas_cabos(caminho_csv):
    """Carrega as ÁREAS REAIS da seção nominal dos cabos (mm²), incluindo isolação."""
    try:
        df = pd.read_csv(caminho_csv, sep=';', decimal=',')
        return df.set_index('Bitola').to_dict()['Area_mm2']
    except Exception as e:
        st.error(f"Erro ao carregar áreas dos cabos: {e}")
        return {}

@st.cache_data
def carregar_e_preparar_eletrodutos(caminho_csv):
    """Carrega as áreas dos eletrodutos e a conversão de bitola."""
    try:
        df = pd.read_csv(caminho_csv, sep=';', decimal=',')
        df['Bitola_Display'] = df['Bitola_mm'].astype(str) + "mm (" + df['Bitola_pol'] + ")"
        df = df.set_index('Bitola_mm')
        return df.to_dict('index')
    except Exception as e:
        st.error(f"Erro ao carregar tabela de eletrodutos: {e}")
        return {}


# --- CARREGAMENTO GLOBAL ---
TABELA_CABOS_E_CUSTO = carregar_e_preparar_dados('tabela_cabos_br.csv')
TABELA_AREAS_CABOS = carregar_e_preparar_areas_cabos('tabela_areas_cabos_br.csv')
TABELA_ELETRODUTOS = carregar_e_preparar_eletrodutos('tabela_eletrodutos_br.csv')
OPCOES_BITOLA_NOMINAL = sorted(TABELA_AREAS_CABOS.keys())


# --- FUNÇÕES DE CÁLCULO DE ENGENHARIA (MANTIDAS) ---

def calcular_queda_tensao_percentual(Ib, L_metros, CosPhi, V_LL, R_ohm_km, X_ohm_km, sistema):
    """Calcula a queda de tensão (DeltaV) percentual."""
    L_km = L_metros / 1000.0
    SinPhi = math.sqrt(1.0 - (CosPhi ** 2)) 
    
    if sistema == 'Trifásico':
        K = math.sqrt(3) 
    else: # Monofásico
        K = 2
        
    DeltaV = K * Ib * L_km * (R_ohm_km * CosPhi + X_ohm_km * SinPhi)
    DeltaV_percent = (DeltaV / V_LL) * 100.0
    
    return DeltaV_percent

def otimizar_bitola_por_custo(Ib, L_metros, CosPhi, V_LL, DeltaV_MAX, CA_agrupamento, tabela_cabos, sistema):
    """Otimiza a bitola pelo custo, respeitando I admissível (corrigida) e Queda de Tensão."""
    
    if not tabela_cabos:
        return {'bitola': None, 'atende_corrente': False}

    I_CORRIGIDA = Ib / CA_agrupamento
    bitolas_ordenadas = sorted(tabela_cabos.keys()) 
    
    melhor_solucao = {
        'bitola': None, 'queda_tensao_perc': float('inf'), 
        'custo_total': float('inf'), 'atende_corrente': False 
    }
    
    for bitola in bitolas_ordenadas:
        R, X, I_admissivel_sem_ca, Custo_metro = tabela_cabos[bitola]
        
        # 1. Critério de Corrente Admissível
        if I_admissivel_sem_ca < I_CORRIGIDA:
            continue
        
        melhor_solucao['atende_corrente'] = True

        # 2. Critério de Queda de Tensão
        dv_perc = calcular_queda_tensao_percentual(Ib, L_metros, CosPhi, V_LL, R, X, sistema)
        
        if dv_perc <= DeltaV_MAX:
            custo_atual = Custo_metro * L_metros
            
            melhor_solucao['bitola'] = f"{bitola} mm²"
            melhor_solucao['queda_tensao_perc'] = dv_perc
            melhor_solucao['custo_total'] = custo_atual
            melhor_solucao['I_admissivel_utilizada'] = I_admissivel_sem_ca
            
            return melhor_solucao

    return melhor_solucao


def validar_circuitos_agrupados(bitolas_agrupadas, todas_opcoes_bitola):
    """
    Valida se o agrupamento obedece às regras:
    1. Máximo de 3 bitolas diferentes.
    2. As bitolas diferentes devem ser consecutivas na escala nominal.
    """
    # Remove duplicatas e ordena as bitolas selecionadas
    bitolas_agrupadas = sorted(list(bitolas_agrupadas))
    num_bitolas = len(bitolas_agrupadas)
    
    # 1. Checa Máximo de 3 bitolas diferentes
    if num_bitolas > 3:
        return False, "O agrupamento não deve exceder 3 bitolas nominais diferentes."

    # 2. Checa se as bitolas são consecutivas (se houver mais de uma)
    if num_bitolas > 1:
        
        # Mapeia as bitolas selecionadas para o índice na lista nominal completa
        try:
            indices_selecionados = sorted([
                todas_opcoes_bitola.index(b) for b in bitolas_agrupadas
            ])
        except ValueError:
             return False, "Erro na validação: Uma bitola selecionada não foi encontrada na lista nominal."

        # Verifica consecutividade
        diferenca_indices = indices_selecionados[-1] - indices_selecionados[0]
        tamanho_agrupamento = len(indices_selecionados) - 1
        
        if diferenca_indices != tamanho_agrupamento:
             return False, "As bitolas agrupadas devem ser **consecutivas** na escala nominal (ex: 10, 16, 25). Não são permitidos 'saltos'."

    return True, "Validação OK."


def dimensionar_eletroduto(dados_circuitos, areas_cabos, eletrodutos, todas_opcoes_bitola):
    """
    Calcula a área total ocupada pelos cabos (usando a área real com isolação)
    e dimensiona o eletroduto pela taxa de ocupação de 40% (NBR 5410),
    após validação de agrupamento.
    """
    
    # 1. Validação da Nova Regra
    bitolas_agrupadas = dados_circuitos.keys()
    valido, mensagem = validar_circuitos_agrupados(bitolas_agrupadas, todas_opcoes_bitola)
    
    if not valido:
        return None, mensagem 

    area_total_ocupada = 0
    
    # 2. Calcular a área total ocupada
    for bitola_mm2, num_condutores in dados_circuitos.items():
        bitola_float = float(bitola_mm2)
        if bitola_float in areas_cabos:
            area_cabo = areas_cabos[bitola_float] 
            area_total_ocupada += area_cabo * num_condutores
        
    # 3. Encontrar o eletroduto
    melhor_eletroduto = None
    
    bitolas_eletrodutos_ordenadas = sorted(eletrodutos.keys())
    
    for bitola_mm in bitolas_eletrodutos_ordenadas:
        dados_eletroduto = eletrodutos[bitola_mm]
        area_util_40_perc = dados_eletroduto['Area_40_perc_mm2']
        
        if area_total_ocupada <= area_util_40_perc:
            # Encontrado o menor que atende à área (40% de ocupação)
            
            melhor_eletroduto = dados_eletroduto.copy()
            # Adiciona a chave 'Bitola_mm' novamente para evitar o KeyError no front-end
            melhor_eletroduto['Bitola_mm'] = bitola_mm 
            
            melhor_eletroduto['Area_Ocupada_Cabos'] = area_total_ocupada
            melhor_eletroduto['Taxa_Ocupacao_Perc'] = (area_total_ocupada / dados_eletroduto['Area_Interna_mm2']) * 100
            break
            
    return melhor_eletroduto, mensagem


# --- INTERFACE DO USUÁRIO (STREAMLIT) ---

st.set_page_config(page_title="Dimensionamento de Cabos e Eletrodutos | SaaS Eng.", layout="wide")

st.title("⚡ Dimensionamento de Cabos e Eletrodutos")
st.caption("Sampaio, Manoel Camargo - Engenheiro Eletricista - CREA-SP: 068.503.146-7 - www.sampaio-eng-eletrica.com.br - projetos@sampaio-eng-eletrica.com.br")
# st.caption("(Manoel Camargo Sampaio")
st.caption("Cálculos baseados em critérios da NBR 5410. Verifique seus CSVs.")

# --- Secção 1: Otimização de Cabos ---
st.header("1. Dimensionamento do Circuito Individual")

col_sistema, col_norma = st.columns(2)

with col_sistema:
    st.subheader("Dados do Circuito (Cálculo do Cabo)")
    
    sistema_selecionado = st.selectbox("Sistema", options=['Trifásico', 'Monofásico'])
    
    corrente_ib = st.number_input("Corrente de Projeto (Ib) [A]", min_value=1.0, value=95.0, step=1.0)
    comprimento_l = st.number_input("Comprimento do Circuito [m]", min_value=1.0, value=150.0, step=1.0)
    fator_potencia = st.slider("Fator de Potência (cos φ)", min_value=0.5, max_value=1.0, value=0.85, step=0.01)

with col_norma:
    st.subheader("Restrições e Fatores de Correção")
    
    if sistema_selecionado == 'Trifásico':
        tensoes = [220.0, 380.0, 440.0]
        indice_tensao = tensoes.index(380.0) if 380.0 in tensoes else 0
        tensao_ll = st.selectbox("Tensão de Linha (V_LL) [V]", options=tensoes, index=indice_tensao)
    else: 
        tensoes = [127.0, 220.0]
        indice_tensao = tensoes.index(220.0) if 220.0 in tensoes else 0
        tensao_ll = st.selectbox("Tensão (F-N ou F-F) [V]", options=tensoes, index=indice_tensao)

    dv_max = st.number_input("Queda de Tensão Máxima Permitida [%]", 
                             min_value=1.0, max_value=5.0, value=4.0, step=0.1)
    
    fator_agrupamento = st.number_input("Fator de Agrupamento (Ca)", 
                                        min_value=0.2, max_value=1.0, value=1.0, step=0.05)


if st.button("🚀 Otimizar Bitola de Cabo"):
    if not TABELA_CABOS_E_CUSTO:
        st.error("Não foi possível executar a otimização de cabos. Verifique o ficheiro 'tabela_cabos_br.csv'.")
    else:
        resultado = otimizar_bitola_por_custo(
            corrente_ib, comprimento_l, fator_potencia, tensao_ll, dv_max, 
            fator_agrupamento, TABELA_CABOS_E_CUSTO, sistema_selecionado
        )
        
        st.subheader("Resultado Otimizado do Cabo")
        if resultado['bitola']:
            st.success(f"✅ **SOLUÇÃO ECONÔMICA E CONFORME ENCONTRADA!**")
            col_res_opt, col_res_tec = st.columns(2)
            
            with col_res_opt:
                st.metric("Bitola Otimizada (Menor Custo)", resultado['bitola'])
                st.metric("Queda de Tensão Calculada", f"{resultado['queda_tensao_perc']:.2f} %", delta=f"Máximo: {dv_max}%")
                
            with col_res_tec:
                st.metric("I Adm. Mínima Necessária (Corrigida)", f"{corrente_ib / fator_agrupamento:.2f} A")
                st.metric("I Adm. da Bitola Selecionada (sem Ca)", f"{resultado['I_admissivel_utilizada']:.2f} A")
                st.info(f"O custo estimado para o cabo é de **R$ {resultado['custo_total']:.2f}**.")
        elif not resultado['atende_corrente']:
            st.error(f"❌ **Falha no Critério de Corrente:** A corrente corrigida é maior que a máxima admissível de todas as bitolas listadas na tabela.")
        else:
            st.warning(f"⚠️ **Falha no Critério de Queda de Tensão:** Nenhuma bitola atende à restrição de queda de tensão ({dv_max}%).")


# --- Secção 2: Dimensionamento de Eletroduto ---

st.divider()
st.header("2. Dimensionamento de Eletroduto (Agrupamento)")
st.caption("Critérios: Área Real de Seção Isolada (40% máx.) **E** no máximo 3 bitolas consecutivas.")

# Novo Fluxo: Define o número de circuitos
num_circuitos = st.number_input(
    "Quantos circuitos diferentes (bitolas diferentes) serão agrupados?",
    min_value=0, max_value=3, value=1, step=1,
    help="Defina o número de diferentes bitolas que serão inseridas. Máximo de 3 para seguir o critério de agrupamento."
)

dados_para_calculo = {}

if num_circuitos > 0:
    st.subheader(f"Configuração de {num_circuitos} Circuitos:")
    
    col_index, col_bitola, col_qnt = st.columns([0.5, 3, 2])
    col_bitola.write("**Bitola (mm²)**")
    col_qnt.write("**Qtd. Condutores**")

    for i in range(num_circuitos):
        
        col_index.write(f"**Circ. {i+1}**")
        
        # Seleção de Bitola (usa o estado da sessão para manter o valor)
        bitola_selecionada = col_bitola.selectbox(
            f"Bitola (mm²)", 
            options=OPCOES_BITOLA_NOMINAL, 
            key=f"bitola_{i}",
            help="Selecione a bitola nominal do condutor."
        )
        
        # Quantidade de Condutores
        qnt_condutores = col_qnt.number_input(
            f"Qtd. Condutores", 
            min_value=1, 
            value=3, 
            step=1, 
            key=f"qnt_{i}",
            help="Número total de condutores dessa bitola (ex: 3 para trifásico + neutro, se o neutro for da mesma bitola)."
        )

        # Agrega os dados para o cálculo (soma as quantidades por bitola)
        if bitola_selecionada not in dados_para_calculo:
            dados_para_calculo[bitola_selecionada] = 0
        dados_para_calculo[bitola_selecionada] += qnt_condutores

    # --- Botão de Cálculo de Eletroduto ---
    st.write("---")
    if st.button("🔍 Dimensionar Eletroduto", key="btn_eletroduto"):
        
        total_cabos = sum(dados_para_calculo.values())
        
        if total_cabos == 0:
            st.warning("Adicione pelo menos um circuito para dimensionar o eletroduto.")
        else:
            # Chama a função de dimensionamento, que agora inclui a validação
            resultado_eletroduto, mensagem = dimensionar_eletroduto(
                dados_para_calculo, TABELA_AREAS_CABOS, TABELA_ELETRODUTOS, OPCOES_BITOLA_NOMINAL
            )

            st.subheader("Resultado do Dimensionamento do Eletroduto")
            if resultado_eletroduto:
                # Se passou pela validação E encontrou um eletroduto
                if total_cabos < 3:
                     st.warning("Aviso: O cálculo usa a taxa de **40%**. Para 1 cabo, a NBR 5410 permite 53%; para 2 cabos, 31%.")
                     
                st.success(f"✅ Eletroduto Mínimo Selecionado: **{resultado_eletroduto['Bitola_Display']}**")
                
                col_res_area, col_res_taxa = st.columns(2)

                with col_res_area:
                    st.metric("Área Total Ocupada pelos Cabos (Real)", f"{resultado_eletroduto['Area_Ocupada_Cabos']:.2f} mm²")
                    st.metric("Área Útil de 40% (Eletroduto Selecionado)", f"{resultado_eletroduto['Area_40_perc_mm2']:.2f} mm²")
                
                with col_res_taxa:
                    st.metric("Diâmetro Nominal do Eletroduto", f"{resultado_eletroduto['Bitola_mm']} mm ou {resultado_eletroduto['Bitola_pol']}")
                    st.metric("Taxa de Ocupação Real", f"{resultado_eletroduto['Taxa_Ocupacao_Perc']:.2f} %", delta=f"Limite: 40%")
            elif resultado_eletroduto is None and mensagem != "Validação OK.":
                # Se falhou na validação de agrupamento
                st.error(f"❌ **Falha na Regra de Agrupamento:** {mensagem}")
            else:
                # Se passou na validação, mas não encontrou eletroduto grande o suficiente
                st.error("Nenhum eletroduto na tabela de dados é grande o suficiente para acomodar a área total dos cabos.")
                
