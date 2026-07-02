"""
App de busca de franqueados mais proximos por cidade.

Rodar com:
    streamlit run app.py
"""
import sqlite3
import unicodedata
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "franqueados.db"
IBGE_CSV = BASE_DIR / "data" / "municipios_ibge.csv"

UF_POR_CODIGO = {
    11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO",
    21: "MA", 22: "PI", 23: "CE", 24: "RN", 25: "PB", 26: "PE", 27: "AL",
    28: "SE", 29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP", 41: "PR",
    42: "SC", 43: "RS", 50: "MS", 51: "MT", 52: "GO", 53: "DF",
}

st.set_page_config(page_title="Franqueados mais próximos", layout="wide")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


@st.cache_data
def load_cidades():
    df = pd.read_csv(IBGE_CSV)
    df["uf"] = df["codigo_uf"].map(UF_POR_CODIGO)
    df["label"] = df["nome"] + " - " + df["uf"]
    return df[["nome", "uf", "latitude", "longitude", "label"]]


@st.cache_data
def load_franqueados():
    conn = sqlite3.connect(DB_PATH)
    franqueados = pd.read_sql("SELECT * FROM franqueados", conn)
    cidades = pd.read_sql("SELECT * FROM franqueado_cidades", conn)
    conn.close()
    return franqueados, cidades


cidades_ibge = load_cidades()
franqueados, franqueado_cidades = load_franqueados()

st.title("Mapa de Franqueados")
st.caption(f"{len(franqueados)} franqueados cadastrados · {len(franqueado_cidades)} vínculos de cidade atendida")

busca = st.selectbox(
    "Digite/selecione a cidade que deseja pesquisar",
    options=[""] + cidades_ibge["label"].tolist(),
    index=0,
    placeholder="Ex: Cascavel - PR",
)

top_n = st.slider("Quantidade de franqueados mais próximos a exibir", 3, 30, 10)

col_map, col_result = st.columns([3, 2])

fig = go.Figure()
fig.add_trace(go.Scattergeo(
    lon=franqueado_cidades["longitude"],
    lat=franqueado_cidades["latitude"],
    mode="markers",
    marker=dict(size=5, color="#1f77b4"),
    text=franqueado_cidades["cidade"] + "/" + franqueado_cidades["uf"],
    name="Franqueados",
    hoverinfo="text",
))

resultado = None

if busca:
    nome_cidade, uf_busca = busca.rsplit(" - ", 1)
    alvo = cidades_ibge[(cidades_ibge["nome"] == nome_cidade) & (cidades_ibge["uf"] == uf_busca)].iloc[0]
    lat0, lon0 = alvo["latitude"], alvo["longitude"]

    fc = franqueado_cidades.copy()
    fc["distancia_km"] = fc.apply(lambda r: haversine_km(lat0, lon0, r.latitude, r.longitude), axis=1)
    mais_proxima = fc.loc[fc.groupby("codigo_plataforma")["distancia_km"].idxmin()]
    resultado = mais_proxima.merge(franqueados, on="codigo_plataforma").sort_values("distancia_km").head(top_n)

    fig.add_trace(go.Scattergeo(
        lon=[lon0], lat=[lat0], mode="markers",
        marker=dict(size=12, color="red", symbol="star"),
        name=f"Pesquisa: {busca}", hoverinfo="text", text=[busca],
    ))
    for _, r in resultado.iterrows():
        fig.add_trace(go.Scattergeo(
            lon=[lon0, r.longitude], lat=[lat0, r.latitude],
            mode="lines", line=dict(width=1, color="rgba(220,20,60,0.5)"),
            showlegend=False, hoverinfo="skip",
        ))

fig.update_geos(
    scope="south america", center=dict(lat=-14, lon=-52),
    projection_scale=3.2, showcountries=True, showland=True,
    landcolor="rgb(235,235,235)", countrycolor="rgb(180,180,180)",
)
fig.update_layout(height=650, margin=dict(l=0, r=0, t=0, b=0), legend=dict(orientation="h"))

with col_map:
    st.plotly_chart(fig, use_container_width=True)

with col_result:
    if resultado is None:
        st.info("Selecione uma cidade acima para ver os franqueados mais próximos.")
    else:
        st.subheader(f"Franqueados mais próximos de {busca}")
        tabela = resultado[[
            "codigo_plataforma", "nome_franqueado", "cidade", "uf", "distancia_km",
        ]].rename(columns={
            "codigo_plataforma": "Código da plataforma",
            "nome_franqueado": "Nome do Franqueado",
            "cidade": "Cidade mais próxima",
            "uf": "UF",
            "distancia_km": "Distância (km)",
        })
        tabela["Distância (km)"] = tabela["Distância (km)"].round(1)
        tabela["Nome do Franqueado"] = tabela["Nome do Franqueado"].fillna("(nome não informado)")
        st.dataframe(tabela, hide_index=True, use_container_width=True)
