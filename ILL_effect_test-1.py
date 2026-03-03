import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import plotly.express as px

# --- 1. ページ基本設定 ---
st.set_page_config(page_title="学術情報流通法人運営シミュレーター (ILL統合版)", layout="wide")

# --- 2. セッション状態の初期化 ---
if 'master_db' not in st.session_state:
    st.session_state.master_db = pd.DataFrame()
if 'history_pts' not in st.session_state:
    st.session_state.history_pts = []

# --- 3. 計算エンジン ---
def run_strategic_simulation(params, base_df):
    np.random.seed(42)
    UNIT_APC_INDIV = params['list_apc_price'] / 10000 
    
    # マスタデータ生成（未インポート時）
    if base_df.empty:
        sub_scale = 3.5 if params['pub_type'] == "Elsevier" else 1.2
        raw_list = []
        configs = [('Tier1', 30, sub_scale, 150), ('Tier2', 120, sub_scale*0.12, 30), ('Tier3', 50, sub_scale*0.02, 5)]
        for t_name, count, s_val, p_val in configs:
            for i in range(count):
                raw_list.append({
                    'Entity': f"{t_name}_{i}", 'Tier': t_name, 
                    'Access': float(max(5, int(np.random.normal(p_val*10, p_val)))),
                    'Total_Pubs': float(max(1, int(np.random.normal(p_val, p_val*0.1)))),
                    'Base_Sub': float(max(s_val*0.6, np.random.normal(s_val, s_val*0.1))),
                    'Tokens': float(int(p_val * 1.1) if t_name == 'Tier1' else 0)
                })
        working_df = pd.DataFrame(raw_list)
    else:
        working_df = base_df.copy()

    # 数値列の強制変換
    for col in ['Access', 'Total_Pubs', 'Base_Sub', 'Tokens']:
        if col in working_df.columns:
            working_df[col] = pd.to_numeric(working_df[col], errors='coerce').fillna(0).astype(float)

    green_r = params['green_oa_rate'] / 100
    unbundle_r = params['unbundle_rate']
    
    # --- 財務計算 ---
    # 1. 現状支出（個別契約・OA率40%）
    working_df['Indiv_Cost'] = working_df['Base_Sub'] + (working_df['Total_Pubs'] * 0.4 * UNIT_APC_INDIV)
    
    # 2. 法人支出（TA交渉）
    working_df['Gold_OA_Pubs'] = (working_df['Total_Pubs'] * (0.6 - green_r)).clip(lower=0)
    negotiated_apc = UNIT_APC_INDIV * (params['target_apc_price'] / params['list_apc_price'])
    total_cons_sub = working_df['Base_Sub'].sum() * (1 - unbundle_r)
    total_cons_apc = working_df['Gold_OA_Pubs'].sum() * negotiated_apc
    
    # 3. ILL & PPV 代替コスト計算
    # 購読解体したアクセス(Access * unbundle_r)のうち、一部がリクエスト(req_rate)として発生
    total_requests = working_df['Access'].sum() * unbundle_r * params['req_rate']
    
    # 迅速化スライダーにより、リクエストのうち何割を「安価なILL」で賄えるか（残りは高額PPV）
    ill_count = total_requests * params['ill_cover_rate']
    ppv_count = total_requests * (1 - params['ill_cover_rate'])
    
    total_ill_cost = (ill_count * params['ill_unit_cost']) / 100000000 # 億円
    total_ppv_cost = (ppv_count * params['ppv_unit_price']) / 100000000 # 億円
    
    total_cons_cost = total_cons_sub + total_cons_apc + total_ill_cost + total_ppv_cost
    
    # 按分計算（ILLの「貸し手」Tier1への優遇ロジック含む）
    acc_s = working_df['Access'] / working_df['Access'].sum()
    pub_s = working_df['Total_Pubs'] / working_df['Total_Pubs'].sum()
    working_df['Cons_Cost'] = total_cons_cost * (params['read_weight'] * acc_s + (1-params['read_weight']) * pub_s)
    
    # Win-Loss / ROI
    working_df['Win_Loss'] = working_df['Indiv_Cost'] - working_df['Cons_Cost']
    working_df['ROI'] = (working_df['Total_Pubs'] * 0.6) / working_df['Cons_Cost'].replace(0, np.nan)
    
    return total_cons_cost, (working_df['Total_Pubs'] * 0.6).sum(), total_cons_sub, total_cons_apc, total_ill_cost, total_ppv_cost, working_df

# --- 4. サイドバーUI ---
st.sidebar.title("🛡️ 法人戦略・ILL統合設定")
p_type = st.sidebar.selectbox("対象出版社", ["Elsevier", "Wiley/Springer"])
g_oa = st.sidebar.slider("グリーンOA率 (%)", 0, 50, 25)
unb = st.sidebar.slider("購読削減(Unbundle)率", 0.0, 1.0, 0.25)

st.sidebar.divider()
st.sidebar.subheader("🚚 ILL・迅速化設定")
ill_unit = st.sidebar.number_input("ILL 1件あたり事務コスト (円)", value=1000, step=100)
ill_rate = st.sidebar.slider("迅速化によるILLカバー率 (%)", 0, 100, 70) 
# ↑ 高いほど「PPVに頼らずILLで済ませられる」ことを意味する

st.sidebar.divider()
st.sidebar.subheader("⚖️ 按分設定")
w_read = st.sidebar.slider("按分重み (利用 1.0 ↔ 出版 0.0)", 0.0, 1.0, 0.5)

params = {
    'pub_type': p_type, 'green_oa_rate': g_oa, 'unbundle_rate': unb, 'read_weight': w_read,
    'ill_unit_cost': ill_unit, 'ill_cover_rate': ill_rate / 100,
    'req_rate': 0.05, 'ppv_unit_price': 4000,
    'list_apc_price': 45, 'target_apc_price': 30
}

if st.sidebar.button("履歴をリセット"):
    st.session_state.history_pts = []
    st.rerun()

# --- 5. 計算実行 ---
total_cost, total_oa, sub_c, apc_c, ill_c, ppv_c, df_final = run_strategic_simulation(params, st.session_state.master_db)
st.session_state.history_pts.append({'cost': total_cost, 'oa': total_oa})

# --- 6. 画面表示 ---
mode = st.sidebar.radio("メニュー", ["📈 戦略ダッシュボード", "⚖️ ILL導入によるWin-Loss変化", "💾 データ管理"])

if mode == "📈 戦略ダッシュボード":
    st.header(f"🚀 {p_type} 交渉戦略分析 (ILL含む)")
    
    # 軸固定設定
    x_range, y_range = ([50, 250], [3000, 12000]) if p_type == "Elsevier" else ([20, 120], [1000, 6000])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("法人総コスト", f"{total_cost:.2f} 億円")
    m2.metric("総OA論文数", f"{total_oa:.0f} 本")
    m3.metric("ILLコスト合計", f"{ill_c:.2f} 億円")
    m4.metric("PPVコスト合計", f"{ppv_c:.2f} 億円")

    st.divider()
    col_l, col_r = st.columns([3, 1])

    with col_l:
        st.subheader("🎯 戦略フロンティア（ILL導入効果）")
        hist_df = pd.DataFrame(st.session_state.history_pts)
        c_theory = np.linspace(x_range[0], x_range[1], 100)
        oa_theory = total_oa * (c_theory / total_cost)**0.65

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=c_theory, y=oa_theory, mode='lines', name='理想ライン', line=dict(color='rgba(255,0,0,0.2)', dash='dot')))
        fig.add_trace(go.Scatter(x=hist_df['cost'], y=hist_df['oa'], mode='markers', name='履歴', marker=dict(color='gray', opacity=0.3)))
        fig.add_trace(go.Scatter(x=[total_cost], y=[total_oa], mode='markers', name='現在', marker=dict(color='blue', size=20, symbol='star')))
        fig.update_layout(xaxis=dict(range=x_range, title="コスト (億円)"), yaxis=dict(range=y_range, title="OA論文数"), height=600, template="plotly_white")
        st.plotly_chart(fig, use_container_width=True)
        

    with col_r:
        st.subheader("📊 コスト内訳")
        fig_pie = go.Figure(data=[go.Pie(labels=['Read(Sub)', 'Publish(APC)', 'ILL', 'PPV'], 
                                       values=[sub_c, apc_c, ill_c, ppv_c], hole=.4)])
        st.plotly_chart(fig_pie, use_container_width=True)
        st.info("💡 **迅速化の効果**: ILLカバー率を上げると、高額なPPV予算が安価なILLへと置き換わり、星印が左（低コスト）へ移動します。")

elif mode == "⚖️ ILL導入によるWin-Loss変化":
    st.header("⚖️ ティア別Win-Loss：ILLネットワークの貢献評価")
    
    st.write("「購読解体」を進めても、ILL網が機能していれば各大学の支出は抑えられます。")
    
    c1, c2 = st.columns(2)
    with c1:
        fig_wl = px.box(df_final, x='Tier', y='Win_Loss', color='Tier', title="現状比での削減額 (億円/校)")
        fig_wl.add_hline(y=0, line_dash="dash", line_color="red")
        st.plotly_chart(fig_wl)
        
    with c2:
        # ILLリクエスト想定数の表示
        df_final['ILL_Req_Est'] = df_final['Access'] * params['unbundle_rate'] * params['req_rate']
        fig_ill = px.bar(df_final.groupby('Tier')['ILL_Req_Est'].mean(), title="1校あたりの想定ILL依頼件数 (年)")
        st.plotly_chart(fig_ill)

    st.subheader("📋 戦略データ詳細")
    f_dict = {c: "{:.4f}" for c in ['Indiv_Cost', 'Cons_Cost', 'Win_Loss', 'ROI']}
    st.dataframe(df_final[['Entity', 'Tier', 'Indiv_Cost', 'Cons_Cost', 'Win_Loss', 'ROI']].style.format(f_dict))

elif mode == "💾 データ管理":
    st.header("💾 データ連携")
    f_m = st.file_uploader("マスタCSV", type="csv")
    if f_m: st.session_state.master_db = pd.read_csv(f_m); st.rerun()