import streamlit as st
import math
import pandas as pd
import numpy as np
import os
import sys

# --- FUNÇÃO DE UTILIDADE PARA FORMATAÇÃO PT-BR ---
def formatar_pt_br(valor, casas_decimais=2):
    """Formata um número float para o padrão string pt-BR (ponto milhar, vírgula decimal)."""
    
    # 1. Arredonda o valor (necessário antes de formatar grandes números)
    valor_arredondado = round(valor, casas_decimais)
    
    # 2. Formata para string, usando o padrão de milhares e decimais locais (pode variar)
    numero_str = f"{valor_arredondado:,.{casas_decimais}f}"
    
    # 3. Força a conversão para pt-BR: 
    #    Troca a vírgula (que pode ser usada como milhar) por 'X' temporariamente.
    #    Troca o ponto (que pode ser o decimal) por vírgula.
    #    Troca o 'X' por ponto.
    #    Exemplo: 10,000.50 (EUA) -> 10.000,50 (BR)
    return numero_str.replace('.', 'X').replace(',', '.').replace('X', ',')

# --- CONFIGURAÇÃO DE PATH E DADOS GLOBAIS ---

def resource_path(relative_path):
    # Lógica de path corrigida para Streamlit Cloud
    base_path = os.path.abspath(os.path.dirname(__file__)) 
    return os.path.join(base_path, relative_path)

# Fatores 'k' para cálculo de Icc admissível, baseado em NBR 5410 / IEC 60949-2
FATOR_K_ICC = {
    'Cobre': {
        'PVC (70°C)': 115,    # θi=70°C -> θf=160°C
        'XLPE (90°C)': 176,   # θi=90°C -> θf=250°C
        'EPR/HEPR (90°C)': 143, # θi=90°C -> θf=220°C
    },
    'Alumínio': {
        'PVC (70°C)': 74,     # θi=70°C -> θf=140°C
        'XLPE (90°C)': 145,    # θi=90°C -> θf=200°C
        'EPR/HEPR (90°C)': 112,  # θi=90°C -> θf=180°C
    }
}


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
                data['R_ohm_km'],       # 0: Resistência (R)
                data['X_ohm_km'],       # 1: Reatância (X)
                data['I_admissivel'],   # 2: Corrente Admissível (I)
                data['Custo_por_metro'] # 3: Custo por metro (C)
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
TABELA_CABOS_E_CUSTO = carregar_e_preparar_dados(resource_path('tabela_cabos_br.csv'))
TABELA_AREAS_CABOS = carregar_e_preparar_areas_cabos(resource_path('tabela_areas_cabos_br.csv'))
TABELA_ELETRODUTOS = carregar_e_preparar_eletrodutos(resource_path('tabela_eletrodutos_br.csv'))
OPCOES_BITOLA_NOMINAL = sorted(TABELA_AREAS_CABOS.keys())


# --- FUNÇÕES DE CÁLCULO DE ENGENHARIA ---

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
    """Valida as regras de agrupamento de circuitos."""
    
    bitolas_agrupadas = sorted(list(bitolas_agrupadas))
    num_bitolas = len(bitolas_agrupadas)
    
    if num_bitolas > 3:
        return False, "O agrupamento não deve exceder 3 bitolas nominais diferentes."

    if num_bitolas > 1:
        
        try:
            indices_selecionados = sorted([
                todas_opcoes_bitola.index(b) for b in bitolas_agrupadas
            ])
        except ValueError:
            return False, "Erro na validação: Uma bitola selecionada não foi encontrada na lista nominal."

        diferenca_indices = indices_selecionados[-1] - indices_selecionados[0]
        tamanho_agrupamento = len(indices_selecionados) - 1
        
        if diferenca_indices != tamanho_agrupamento:
            return False, "As bitolas agrupadas devem ser **consecutivas** na escala nominal (ex: 10, 16, 25). Não são permitidos 'saltos'."

    return True, "Validação OK."


def dimensionar_eletroduto(dados_circuitos, areas_cabos, eletrodutos, todas_opcoes_bitola):
    """Calcula a área e dimensiona o eletroduto."""
    
    bitolas_agrupadas = dados_circuitos.keys()
    valido, mensagem = validar_circuitos_agrupados(bitolas_agrupadas, todas_opcoes_bitola)
    
    if not valido:
        return None, mensagem 

    area_total_ocupada = 0
    
    for bitola_mm2, num_condutores in dados_circuitos.items():
        bitola_float = float(bitola_mm2)
        if bitola_float in areas_cabos:
            area_cabo = areas_cabos[bitola_float] 
            area_total_ocupada += area_cabo * num_condutores
        
    melhor_eletroduto = None
    
    bitolas_eletrodutos_ordenadas = sorted(eletrodutos.keys())
    
    for bitola_mm in bitolas_eletrodutos_ordenadas:
        dados_eletroduto = eletrodutos[bitola_mm]
        area_util_40_perc = dados_eletroduto['Area_40_perc_mm2']
        
        if area_total_ocupada <= area_util_40_perc:
            
            melhor_eletroduto = dados_eletroduto.copy()
            melhor_eletroduto['Bitola_mm'] = bitola_mm 
            
            melhor_eletroduto['Area_Ocupada_Cabos'] = area_total_ocupada
            melhor_eletroduto['Taxa_Ocupacao_Perc'] = (area_total_ocupada / dados_eletroduto['Area_Interna_mm2']) * 100
            break
            
    return melhor_eletroduto, mensagem


def get_fator_k(isolamento, material_condutor):
    """Obtém o fator 'k' com base no isolamento e material do condutor."""
    
    material_key = material_condutor if material_condutor in FATOR_K_ICC else 'Cobre'
    isolamento_key = isolamento if isolamento in FATOR_K_ICC[material_key] else 'PVC (70°C)' 
    
    return FATOR_K_ICC[material_key][isolamento_key]

def calcular_corrente_cc_admissivel(Area_nominal_mm2, tempo_cc_seg, k_fator):
    """
    Calcula a corrente de curto-circuito admissível (Icc_adm) de um cabo.
    Fórmula: Icc_adm = (A * k) / sqrt(t)
    """
    if tempo_cc_seg <= 0 or Area_nominal_mm2 <= 0:
        return 0.0
    
    Icc_adm = (Area_nominal_mm2 * k_fator) / math.sqrt(tempo_cc_seg)
    return Icc_adm


# --- INTERFACE DO USUÁRIO (STREAMLIT) ---

st.set_page_config(page_title="Dimensionamento de Cabos e Eletrodutos | SaaS Eng.", layout="wide")

st.title("⚡ Dimensionamento de Cabos e Eletrodutos")
st.caption("Sampaio, Manoel Camargo - Engenheiro Eletricista - CREA-SP: 068.503.146-7 - www.sampaio-eng-eletrica.com.br - projetos@sampaio-eng-eletrica.com.br")
st.caption("Cálculos baseados em critérios da NBR 5410. Verifique seus CSVs.")

# Usar st.session_state para armazenar o resultado da otimização de forma persistente
if 'resultado_otimizacao' not in st.session_state:
    st.session_state.resultado_otimizacao = {'bitola': None, 'atende_corrente': False}

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
        st.session_state.resultado_otimizacao = resultado # Armazena na sessão
        
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
                # Aplica formatar_pt_br ao custo total
                st.info(f"O custo estimado para o cabo é de **R$ {formatar_pt_br(resultado['custo_total'])}**.")
        elif not resultado['atende_corrente']:
            st.error(f"❌ **Falha no Critério de Corrente:** A corrente corrigida é maior que a máxima admissível de todas as bitolas listadas na tabela.")
        else:
            st.warning(f"⚠️ **Falha no Critério de Queda de Tensão:** Nenhuma bitola atende à restrição de queda de tensão ({dv_max}%).")

# --- NOVO BLOCO (Antiga Secção 3: Verificação de Curto-Circuito -> Nova Secção 2) ---

st.divider()
st.header("2. Verificação de Curto-Circuito (Critério Térmico)")
st.caption("Verifica a capacidade do cabo de suportar a Icc máxima esperada pelo tempo de atuação da proteção. (Icc_adm = A * k / sqrt(t))")

col_cc_1, col_cc_2, col_cc_3 = st.columns(3)

# 1. Bitola a ser verificada (usa a otimizada por padrão)
bitola_otimizada_str = st.session_state.resultado_otimizacao['bitola'].replace(" mm²", "") if st.session_state.resultado_otimizacao['bitola'] else OPCOES_BITOLA_NOMINAL[0]

with col_cc_1:
    bitola_a_verificar_str = st.selectbox(
        "Bitola do Cabo para Verificação (mm²)", 
        options=OPCOES_BITOLA_NOMINAL, 
        index=OPCOES_BITOLA_NOMINAL.index(float(bitola_otimizada_str)) if float(bitola_otimizada_str) in OPCOES_BITOLA_NOMINAL else 0,
        key="cc_bitola_verificar",
        help="Selecione a bitola nominal para o cálculo de Icc. Usa a bitola da Secção 1 por padrão, se calculada."
    )
    
with col_cc_2:
    # 2. Material do Condutor
    material_selecionado = st.selectbox(
        "Material do Condutor",
        options=['Cobre', 'Alumínio'],
        key="cc_material",
        help="O material do condutor altera o fator 'k' térmico."
    )

    # 3. Seleção do Isolamento
    isolamento_selecionado = st.selectbox(
        "Isolamento do Cabo (Fator 'k' depende desta escolha)",
        options=list(FATOR_K_ICC['Cobre'].keys()), 
        key="cc_isolamento",
        help="Isolamento que define a temperatura máxima de curto-circuito (θf)."
    )
    
with col_cc_3:
    # 4. Tempo de Curto-Circuito
    tempo_cc = st.number_input(
        "Tempo de Atuação da Proteção (t) [s]",
        min_value=0.01, value=0.1, max_value=5.0, step=0.01,
        key="cc_tempo",
        help="Tempo máximo que o curto-circuito deve durar, definido pelo dispositivo de proteção."
    )

# 5. Icc Máxima Esperada
icc_max_esperada = st.number_input(
    "Corrente Máxima de Curto-Circuito Esperada (Icc_max) [A]",
    min_value=0.0, value=10000.0, step=100.0,
    key="icc_max_esperada",
    help="Valor da Icc esperada no ponto de instalação do cabo (deve ser menor que a Icc Admissível)."
)


if st.button("🔍 Calcular e Verificar Curto-Circuito", key="btn_cc_check"):
    
    try:
        # Conversão e obtenção dos fatores (protegido contra erros de valor)
        bitola_float = float(bitola_a_verificar_str)
        Area_nominal_mm2 = bitola_float
        
        fator_k_usado = get_fator_k(isolamento_selecionado, material_selecionado)
        
        # Cálculo da Icc Admissível
        Icc_admissivel = calcular_corrente_cc_admissivel(Area_nominal_mm2, tempo_cc, fator_k_usado)

        st.subheader("Resultado da Verificação Térmica")
        
        col_res_icc_1, col_res_icc_2 = st.columns(2)
        
        # Aplicação da formatação pt-BR
        with col_res_icc_1:
            st.metric("Icc Admissível do Cabo", 
                      f"{formatar_pt_br(Icc_admissivel)} A", 
                      help=f"Calculada para A={Area_nominal_mm2} mm², k={fator_k_usado}, t={tempo_cc} s.")
            
            st.metric("Icc Máx. Esperada (Projeto)", 
                      f"{formatar_pt_br(icc_max_esperada)} A")
            
        with col_res_icc_2:
            st.metric("Fator 'k' Utilizado", f"{fator_k_usado}", 
                      help=f"Baseado em Condutor de {material_selecionado} e isolamento {isolamento_selecionado}.")
            st.metric("Tempo de Proteção (t)", f"{tempo_cc} s")
            
        st.write("---")
        
        # Critério de Conformidade
        if icc_max_esperada > Icc_admissivel:
            st.error(f"❌ **FALHA NO CRITÉRIO TÉRMICO!** A Icc máx. esperada é **MAIOR** que a Icc admissível. A bitola ({Area_nominal_mm2} mm²) não suporta termicamente o curto-circuito.")
        else:
            # Aplicação da formatação pt-BR na mensagem final
            st.success(f"✅ **CONFORME!** A Icc máx. esperada ({formatar_pt_br(icc_max_esperada, 0)} A) é **MENOR** que a Icc admissível do cabo ({formatar_pt_br(Icc_admissivel, 0)} A). O critério térmico é atendido.")
            
    except Exception as e:
        st.error(f"Erro no cálculo de curto-circuito: Verifique se a Bitola e o Tempo de Proteção são valores válidos. Detalhe: {e}")

# --- NOVO BLOCO (Antiga Secção 2: Dimensionamento de Eletroduto -> Nova Secção 3) ---

st.divider()
st.header("3. Dimensionamento de Eletroduto (Agrupamento)")
st.caption("Critérios: Área Real de Seção Isolada (40% máx.) **E** no máximo 3 bitolas consecutivas.")

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
        
        bitola_selecionada = col_bitola.selectbox(
            f"Bitola (mm²)", 
            options=OPCOES_BITOLA_NOMINAL, 
            key=f"bitola_{i}",
            help="Selecione a bitola nominal do condutor."
        )
        
        qnt_condutores = col_qnt.number_input(
            f"Qtd. Condutores", 
            min_value=1, 
            value=3, 
            step=1, 
            key=f"qnt_{i}",
            help="Número total de condutores dessa bitola (ex: 3 para trifásico + neutro, se o neutro for da mesma bitola)."
        )

        if bitola_selecionada not in dados_para_calculo:
            dados_para_calculo[bitola_selecionada] = 0
        dados_para_calculo[bitola_selecionada] += qnt_condutores

    st.write("---")
    if st.button("🔍 Dimensionar Eletroduto", key="btn_eletroduto"):
        
        total_cabos = sum(dados_para_calculo.values())
        
        if total_cabos == 0:
            st.warning("Adicione pelo menos um circuito para dimensionar o eletroduto.")
        else:
            resultado_eletroduto, mensagem = dimensionar_eletroduto(
                dados_para_calculo, TABELA_AREAS_CABOS, TABELA_ELETRODUTOS, OPCOES_BITOLA_NOMINAL
            )

            st.subheader("Resultado do Dimensionamento do Eletroduto")
            if resultado_eletroduto:
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
                st.error(f"❌ **Falha na Regra de Agrupamento:** {mensagem}")
            else:
                st.error("Nenhum eletroduto na tabela de dados é grande o suficiente para acomodar a área total dos cabos.")
                
