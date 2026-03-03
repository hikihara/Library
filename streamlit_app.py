import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import plotly.express as px

# --- 1. ページ基本設定 ---
st.set_page_config(page_title="スマートコンソーシアム・シミュレーター", layout="wide")

# --- 2. セッション状態の初期化 ---
if 'master_db' not in st.session_state:
    st.session_state.master_db = pd.DataFrame()
if 'history_pts' not in st.session_state:
    st.session_state.history_pts = []

# --- 3. 補助関数（パレート最適・ジニ係数） ---
def find_pareto_front(costs, benefits):
    costs, benefits = np.array(costs), np.array(benefits)
    indices = np.arange(len(costs))
    pareto_front = []
    for i in indices:
        is_dominated = False
        for j in indices:
            if (costs[j] <= costs[i] and benefits[j] >= benefits[i]) and \
               (costs[j] < costs[i] or benefits[j] > benefits[i]):
                is_dominated = True
                break
        if not is_dominated:
            pareto_front.append(i)
    return sorted(pareto_front, key=lambda x: costs[x])

def calculate_gini(x):
    x = np.array(x, dtype=float)
    x = x[~np.isnan(x)]
    if x.size == 0 or np.sum(x) == 0: return 0
    n = len(x)
    diff_sum = np.sum(np.abs(x[:, None] - x))
    return diff_sum / (2 * n * np.sum(x))

# --- 4. 計算エンジン（全機能統合） ---
def run_strategic_simulation(params, base_df):
    np.random.seed(42)
    UNIT_APC_INDIV = params['list_apc_price'] / 10000 
    
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
    num_cols = ['Access', 'Total_Pubs', 'Base_Sub', 'Tokens']
    for col in num_cols:
        if col in working_df.columns:
            working_df[col] = pd.to_numeric(working_df[col], errors='coerce').fillna(0).astype(float)

    green_r = params['green_oa_rate'] / 100
    unbundle_r = params['unbundle_rate']
    
    # --- A. 現状支出の推計 ---
    working_df['Indiv_Cost'] = working_df['Base_Sub'] + (working_df['Total_Pubs'] * 0.4 * UNIT_APC_INDIV)
    
    # --- B. 法人化後支出の計算 ---
    working_df['Green_OA_Pubs'] = working_df['Total_Pubs'] * green_r
    working_df['Gold_OA_Pubs'] = (working_df['Total_Pubs'] * (0.6 - green_r)).clip(lower=0)
    working_df['Total_OA_Pubs'] = working_df['Green_OA_Pubs'] + working_df['Gold_OA_Pubs']
    
    negotiated_apc = UNIT_APC_INDIV * (params['target_apc_price'] / params['list_apc_price'])
    total_cons_sub = working_df['Base_Sub'].sum() * (1 - unbundle_r)
    total_cons_apc = working_df['Gold_OA_Pubs'].sum() * negotiated_apc
    
    # --- C. スマートILL & バックファイル & 基金 ---
    potential_reqs = working_df['Access'].sum() * unbundle_r * params['req_rate']
    actual_reqs = potential_reqs * (1 - params['backfile_rate'])
    
    ill_count = actual_reqs * params['ill_cover_rate']
    ppv_count = actual_reqs * (1 - params['ill_cover_rate'])
    
    total_ill_cost = (ill_count * params['smart_ill_unit_cost']) / 100000000
    total_ppv_cost = (ppv_count * params['ppv_unit_price']) / 100000000
    annual_fund_cost = params['fund_investment'] / 10 # 10年償却
    
    total_cons_cost = total_cons_sub + total_cons_apc + total_ill_cost + total_ppv_cost + annual_fund_cost
    
    # --- D. 分担金按分 (Ezproxyログ等の重み付け) ---
    w = params['read_weight']
    acc_sum = working_df['Access'].sum()
    pub_sum = working_df['Total_Pubs'].sum()
    acc_share = working_df['Access'] / acc_sum if acc_sum > 0 else 0
    pub_share = working_df['Total_Pubs'] / pub_sum if pub_sum > 0 else 0
    working_df['Cons_Cost'] = total_cons_cost * (w * acc_share + (1-w) * pub_share)
    
    # --- E. 指標計算 ---
    working_df['Win_Loss'] = working_df['Indiv_Cost'] - working_df['Cons_Cost']
    working_df['ROI'] = working_df['Total_OA_Pubs'] / working_df['Cons_Cost'].replace(0, np.nan)
    working_df['OA_Rate'] = (working_df['Total_OA_Pubs'] / working_df['Total_Pubs'] * 100).clip(upper=100)
    
    return total_cons_cost, working_df['Total_OA_Pubs'].sum(), total_cons_sub, total_cons_apc, total_ill_cost, total_ppv_cost, annual_fund_cost, ill_count, working_df

# --- 5. UI / サイドバー ---
st.sidebar.title("🛡️ スマートコンソーシアム戦略")
p_type = st.sidebar.selectbox("対象出版社", ["Elsevier", "Wiley/Springer"])
g_oa = st.sidebar.slider("グリーンOA率 (%)", 0, 50, 25)
unb = st.sidebar.slider("購読削減(Unbundle)率", 0.0, 1.0, 0.40)
w_read = st.sidebar.slider("按分重み (利用 1.0 ↔ 出版 0.0)", 0.0, 1.0, 0.5)

st.sidebar.divider()
st.sidebar.subheader("🏦 基金・バックファイル")
fund_inv = st.sidebar.number_input("基金投資額 (億円)", value=50)
bf_rate = st.sidebar.slider("バックファイル購入割合 (%)", 0, 100, 40)

st.sidebar.divider()
st.sidebar.subheader("⚡ ILL / PPV需要予測")
req_r = st.sidebar.slider("非保持論文への要求率 (%)", 1, 30, 5)
ill_r = st.sidebar.slider("スマートILLカバー率 (%)", 0, 100, 85)
ill_u = st.sidebar.number_input("スマートILL単価 (円)", value=500)

params = {
    'pub_type': p_type, 'green_oa_rate': g_oa, 'unbundle_rate': unb, 'read_weight': w_read,
    'fund_investment': fund_inv, 'backfile_rate': bf_rate / 100,
    'smart_ill_unit_cost': ill_u, 'ill_cover_rate': ill_r / 100, 'req_rate': req_r / 100,
    'ppv_unit_price': 4000, 'list_apc_price': 45, 'target_apc_price': 30
}

if st.sidebar.button("履歴と軸をリセット"):
    st.session_state.history_pts = []
    st.rerun()

# --- 6. 計算実行 ---
total_cost, total_oa, sub_c, apc_c, ill_c, ppv_c, fund_c, ill_n, df_final = run_strategic_simulation(params, st.session_state.master_db)
st.session_state.history_pts.append({'cost': total_cost, 'oa': total_oa})

# --- 7. メイン画面表示 ---
mode = st.sidebar.radio("メニュー", ["📈 戦略ダッシュボード", "⚖️ ティア別Win-Loss", "🔄 トークン融通(Sankey)", "💾 データ管理"])

if mode == "📈 戦略ダッシュボード":
    st.header(f"🚀 {p_type} 戦略意思決定ダッシュボード")
    
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("法人総コスト", f"{total_cost:.1f} 億円")
    m2.metric("総OA論文数", f"{total_oa:.0f} 本")
    m3.metric("年間想定ILL件数", f"{ill_n:,.0f} 件")
    m4.metric("ILL+PPVコスト", f"{(ill_c+ppv_c):.2f} 億円")

    st.divider()
    col_l, col_r = st.columns([2, 1])

    with col_l:
        st.subheader("🎯 パレート・フロンティアと履歴追跡")
        hist_df = pd.DataFrame(st.session_state.history_pts)
        x_range = [50, 250] if p_type == "Elsevier" else [20, 120]
        y_range = [3000, 12000] if p_type == "Elsevier" else [1000, 6000]
        
        c_theory = np.linspace(x_range[0], x_range[1], 100)
        oa_theory = total_oa * (c_theory / total_cost)**0.65
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=c_theory, y=oa_theory, mode='lines', name='理論限界', line=dict(color='red', dash='dot')))
        fig.add_trace(go.Scatter(x=hist_df['cost'], y=hist_df['oa'], mode='markers', name='履歴', marker=dict(color='gray', opacity=0.3)))
        fig.add_trace(go.Scatter(x=[total_cost], y=[total_oa], mode='markers', name='現在', marker=dict(color='blue', size=20, symbol='star')))
        fig.update_layout(xaxis=dict(range=x_range, title="コスト(億円)"), yaxis=dict(range=y_range, title="OA数"), height=500, template="plotly_white")
        st.plotly_chart(fig, use_container_width=True)
        

    with col_r:
        st.subheader("💰 コスト構造の内訳")
        fig_pie = go.Figure(data=[go.Pie(labels=['Sub', 'APC', 'ILL', 'PPV', 'Fund'], values=[sub_c, apc_c, ill_c, ppv_c, fund_c], hole=.4)])
        st.plotly_chart(fig_pie, use_container_width=True)
        st.info("バックファイル購入率やILLカバー率を上げると、高額な外貨流出（PPV）を抑制し、パレート曲線上の左上へシフトします。")
        

elif mode == "⚖️ ティア別Win-Loss":
    st.header("⚖️ ティア別Win-Loss・公平性評価")
    gini = calculate_gini(df_final['ROI'])
    st.metric("ROI不平等度 (ジニ係数)", f"{gini:.3f}")

    cl1, cl2 = st.columns(2)
    with cl1:
        fig_wl = px.box(df_final, x='Tier', y='Win_Loss', color='Tier', points="all", title="現状比での削減額 (億円/校)")
        fig_wl.add_hline(y=0, line_dash="dash", line_color="red")
        st.plotly_chart(fig_wl, use_container_width=True)
    with cl2:
        fig_roi = px.violin(df_final, x='Tier', y='ROI', box=True, color='Tier', title="投資効率 (OA数/億円)")
        st.plotly_chart(fig_roi, use_container_width=True)

    st.subheader("詳細データリスト")
    f_dict = {c: "{:.4f}" for c in ['Indiv_Cost', 'Cons_Cost', 'Win_Loss', 'ROI', 'OA_Rate']}
    st.dataframe(df_final[['Entity', 'Tier', 'Indiv_Cost', 'Cons_Cost', 'Win_Loss', 'ROI', 'OA_Rate']].style.format(f_dict))
    

elif mode == "🔄 トークン融通(Sankey)":
    st.header("🔄 転換契約(TA)トークンの循環フロー")
    t1_excess = max(1, df_final[df_final['Tier']=='Tier1']['Tokens'].sum() - df_final[df_final['Tier']=='Tier1']['Gold_OA_Pubs'].sum())
    fig_s = go.Figure(data=[go.Sankey(node=dict(pad=15, thickness=20, label=["Tier1余剰", "法人Pool", "Tier2需要", "Tier3需要"]),
                                     link=dict(source=[0, 1, 1], target=[1, 2, 3], value=[t1_excess, t1_excess*0.8, t1_excess*0.2]))])
    st.plotly_chart(fig_s, use_container_width=True)
    

elif mode == "💾 データ管理":
    st.header("💾 外部データ連携 (Ezproxy & Master)")
    t1, t2 = st.tabs(["📥 インポート", "📤 エクスポート"])
    with t1:
        f_m = st.file_uploader("マスタCSV (Entity, Tier, Access, Total_Pubs, Base_Sub)", type="csv")
        if f_m: st.session_state.master_db = pd.read_csv(f_m); st.rerun()
        f_e = st.file_uploader("Ezproxyログ (Entity, Log_Count)", type="csv")
        if f_e and not df_final.empty:
            log = pd.read_csv(f_e)
            upd = df_final.merge(log[['Entity', 'Log_Count']], on='Entity', how='left')
            upd['Access'] = upd['Log_Count'].fillna(upd['Access'])
            st.session_state.master_db = upd; st.rerun()
    with t2:
        st.download_button("分析結果を保存", df_final.to_csv(index=False).encode('utf-8-sig'), "smart_simulation_result.csv")


# #財務基盤: 出版社別のスケール設定、APC価格交渉、購読削減（Unbundle）の影響計算。
# スマート戦略: 基金によるバックファイル購入のROI計算、**スマートILL（電子配信）**によるPPV代替ロジック。
# 視認性最適化: 履歴を残すパレート図、および出版社に応じた軸の範囲固定。
# 公平性評価: 各大学の個別得失を測るWin-Loss分析、投資効率のばらつきを測るジニ係数。
# リソース循環: 余剰トークンが大規模校から小規模校へ流れる様子を可視化するSankeyダイアグラム。
# 実データ連動: Ezproxyログをアップロードして按分計算を実測値に書き換える機能。