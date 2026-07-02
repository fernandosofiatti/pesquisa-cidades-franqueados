"""
App de busca de franqueados mais proximos por cidade.

Rodar com:
    streamlit run app.py
"""
import json
import sqlite3
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "franqueados.db"
IBGE_CSV = BASE_DIR / "data" / "municipios_ibge.csv"
GEOJSON_PATH = BASE_DIR / "data" / "brasil_uf.geojson"

UF_POR_CODIGO = {
    11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO",
    21: "MA", 22: "PI", 23: "CE", 24: "RN", 25: "PB", 26: "PE", 27: "AL",
    28: "SE", 29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP", 41: "PR",
    42: "SC", 43: "RS", 50: "MS", 51: "MT", 52: "GO", 53: "DF",
}

st.set_page_config(page_title="Franqueados mais próximos", layout="wide")


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
    clientes = pd.read_sql("SELECT * FROM clientes", conn)
    conn.close()
    return franqueados, cidades, clientes


@st.cache_resource
def load_estados_geojson():
    with open(GEOJSON_PATH, encoding="utf-8") as f:
        return json.load(f)


cidades_ibge = load_cidades()
franqueados, franqueado_cidades, clientes = load_franqueados()
estados_geojson = load_estados_geojson()
CODIGOS_UF = [str(c) for c in UF_POR_CODIGO]

st.title("Mapa de Franqueados")
st.caption(
    f"{len(franqueados)} franqueados cadastrados · {len(franqueado_cidades)} vínculos de cidade atendida · "
    f"{len(clientes)} clientes (whitelabel) mapeados"
)

busca = st.selectbox(
    "Digite/selecione a cidade que deseja pesquisar",
    options=[""] + cidades_ibge["label"].tolist(),
    index=0,
    placeholder="Ex: Cascavel - PR",
)

top_n = st.slider("Quantidade de franqueados mais próximos a exibir", 3, 30, 10)

col_map, col_result = st.columns([3, 2])

fig = go.Figure()

fig.add_trace(go.Choroplethmapbox(
    geojson=estados_geojson,
    locations=CODIGOS_UF,
    featureidkey="properties.codarea",
    z=[1] * len(CODIGOS_UF),
    colorscale=[[0, "rgb(230,236,242)"], [1, "rgb(230,236,242)"]],
    showscale=False,
    marker=dict(opacity=0.55, line=dict(color="rgb(90,105,130)", width=1.2)),
    hoverinfo="skip",
    showlegend=False,
))

fig.add_trace(go.Scattermapbox(
    lon=franqueado_cidades["longitude"],
    lat=franqueado_cidades["latitude"],
    mode="markers",
    marker=dict(size=7, color="#1f77b4"),
    text=franqueado_cidades["cidade"] + "/" + franqueado_cidades["uf"],
    name="Franqueados",
    hoverinfo="text",
))

if len(clientes):
    fig.add_trace(go.Scattermapbox(
        lon=clientes["longitude"],
        lat=clientes["latitude"],
        mode="markers",
        marker=dict(size=12, color="#e67e22"),
        text=(
            "Cliente: " + clientes["franqueado_cliente"].fillna("") +
            "<br>ID_Whitelabel: " + clientes["id_whitelabel"].astype(str) +
            "<br>" + clientes["cidade"] + "/" + clientes["uf"]
        ),
        name="Clientes (whitelabel)",
        hoverinfo="text",
    ))

resultado = None
center = dict(lat=-14, lon=-52)
zoom = 3.2

if busca:
    nome_cidade, uf_busca = busca.rsplit(" - ", 1)
    alvo = cidades_ibge[(cidades_ibge["nome"] == nome_cidade) & (cidades_ibge["uf"] == uf_busca)].iloc[0]
    lat0, lon0 = alvo["latitude"], alvo["longitude"]
    center, zoom = dict(lat=lat0, lon=lon0), 5.5

    fc = franqueado_cidades.copy()
    fc["distancia_km"] = fc.apply(lambda r: haversine_km(lat0, lon0, r.latitude, r.longitude), axis=1)
    mais_proxima = fc.loc[fc.groupby("codigo_plataforma")["distancia_km"].idxmin()]
    resultado = mais_proxima.merge(franqueados, on="codigo_plataforma").sort_values("distancia_km").head(top_n)

    fig.add_trace(go.Scattermapbox(
        lon=[lon0], lat=[lat0], mode="markers",
        marker=dict(size=16, color="red"),
        name=f"Pesquisa: {busca}", hoverinfo="text", text=[busca],
    ))
    for _, r in resultado.iterrows():
        fig.add_trace(go.Scattermapbox(
            lon=[lon0, r.longitude], lat=[lat0, r.latitude],
            mode="lines", line=dict(width=1.5, color="rgba(220,20,60,0.6)"),
            showlegend=False, hoverinfo="skip",
        ))

fig.update_layout(
    mapbox=dict(style="carto-positron", center=center, zoom=zoom),
    height=650, margin=dict(l=0, r=0, t=0, b=0), legend=dict(orientation="h"),
)

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

if len(clientes):
    with st.expander(f"Clientes (whitelabel) mapeados — {len(clientes)} cidades destacadas em laranja no mapa"):
        tabela_clientes = clientes[[
            "id_whitelabel", "franqueado_cliente", "cidade", "uf", "codigo_encontrado",
        ]].rename(columns={
            "id_whitelabel": "ID_Whitelabel",
            "franqueado_cliente": "Franqueado",
            "cidade": "Cidade",
            "uf": "UF",
            "codigo_encontrado": "Encontrado em Código da plataforma?",
        })
        tabela_clientes["Encontrado em Código da plataforma?"] = tabela_clientes[
            "Encontrado em Código da plataforma?"
        ].map({1: "Sim", 0: "Não"})
        st.dataframe(tabela_clientes, hide_index=True, use_container_width=True)
