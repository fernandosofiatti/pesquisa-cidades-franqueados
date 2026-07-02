"""
Le a planilha Cidades_Franqueados.xlsx, casa cada cidade citada com a base
oficial de municipios do IBGE (lat/lon), deduplica os registros por
franqueado (Codigo da plataforma) unindo todas as cidades que ele atende,
e grava tudo em data/franqueados.db (SQLite) para o app consumir.

Uso:
    python prepare_data.py
"""
import difflib
import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd

# Correcoes manuais para nomes que nao passam nem no fuzzy match (erros de
# digitacao na planilha original que fogem muito do nome oficial do IBGE).
ALIASES = {
    ("barra do sul", "SC"): "Balneário Barra do Sul",
    ("assu", "RN"): "Açu",
}

BASE_DIR = Path(__file__).parent
XLSX_PATH = BASE_DIR / "Cidades_Franqueados.xlsx"
CLIENTES_XLSX_PATH = BASE_DIR / "nome_clientes.xlsx"
IBGE_CSV = BASE_DIR / "data" / "municipios_ibge.csv"
DB_PATH = BASE_DIR / "data" / "franqueados.db"

UF_POR_CODIGO = {
    11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO",
    21: "MA", 22: "PI", 23: "CE", 24: "RN", 25: "PB", 26: "PE", 27: "AL",
    28: "SE", 29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP", 41: "PR",
    42: "SC", 43: "RS", 50: "MS", 51: "MT", 52: "GO", 53: "DF",
}

ESTADO_NOME_PARA_UF = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM",
    "bahia": "BA", "ceara": "CE", "distrito federal": "DF",
    "espirito santo": "ES", "goias": "GO", "maranhao": "MA",
    "mato grosso": "MT", "mato grosso do sul": "MS", "minas gerais": "MG",
    "para": "PA", "paraiba": "PB", "parana": "PR", "pernambuco": "PE",
    "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN",
    "rio grande do sul": "RS", "rondonia": "RO", "roraima": "RR",
    "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE",
    "tocantins": "TO",
}


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def normalize(text: str) -> str:
    text = strip_accents(str(text)).lower().strip()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_ibge():
    df = pd.read_csv(IBGE_CSV)
    df["uf"] = df["codigo_uf"].map(UF_POR_CODIGO)
    df["norm"] = df["nome"].map(normalize)
    by_uf_name = {(r.uf, r.norm): (r.nome, r.uf, r.latitude, r.longitude) for r in df.itertuples()}
    by_name = {}
    norms_by_uf = {}
    for r in df.itertuples():
        by_name.setdefault(r.norm, []).append((r.nome, r.uf, r.latitude, r.longitude))
        norms_by_uf.setdefault(r.uf, []).append(r.norm)
    return by_uf_name, by_name, norms_by_uf


VALID_UFS = set(UF_POR_CODIGO.values())


def lookup_city(name: str, uf_hint: str | None, by_uf_name: dict, by_name: dict, norms_by_uf: dict):
    norm = normalize(name)
    if not norm:
        return None
    if uf_hint:
        hit = by_uf_name.get((uf_hint, norm))
        if hit:
            return hit
    candidates = by_name.get(norm)
    if candidates:
        if uf_hint:
            for c in candidates:
                if c[1] == uf_hint:
                    return c
        if len(candidates) == 1:
            return candidates[0]
        return candidates[0]
    # fuzzy match como ultimo recurso (corrige pequenos erros de digitacao)
    alias = ALIASES.get((norm, uf_hint))
    if alias:
        return lookup_city(alias, uf_hint, by_uf_name, by_name, norms_by_uf)
    pool = norms_by_uf.get(uf_hint) if uf_hint else [n for norms in norms_by_uf.values() for n in norms]
    close = difflib.get_close_matches(norm, pool or [], n=1, cutoff=0.82)
    if close:
        candidates = by_name.get(close[0])
        if uf_hint:
            for c in candidates:
                if c[1] == uf_hint:
                    return c
        return candidates[0]
    return None


UF_RE = re.compile(r"\b([A-Z]{2})\b$")


def strip_trailing_uf(part: str):
    m = UF_RE.search(part)
    if m and m.group(1) in VALID_UFS:
        return part[: m.start()].strip(), m.group(1)
    return part, None


def split_city_cell(cell: str, default_uf: str | None):
    """Retorna lista de (nome_cidade, uf_hint) a partir da celula bruta da planilha."""
    cell = re.sub(r"\.\s+(?=[A-ZÀ-Ú])", ", ", cell)  # "Pitanga. Manoel Ribas" -> vira separador
    entries = []
    for part in cell.split(","):
        part = part.strip(" .")
        if not part:
            continue
        if "/" in part:
            # "Santa Cruz/RN" (uma cidade) vs "Vitoria ES / Montes Claros MG" (varias)
            sub_parts = [p.strip() for p in part.split("/") if p.strip()]
            if len(sub_parts) == 2 and UF_RE.search(sub_parts[1]) and len(sub_parts[1]) <= 3:
                entries.append((sub_parts[0], sub_parts[1]))
                continue
            for sp in sub_parts:
                name, uf = strip_trailing_uf(sp)
                entries.append((name, uf or default_uf))
            continue
        name, uf = strip_trailing_uf(part)
        entries.append((name, uf or default_uf))
    return entries


def resolve_entry(name: str, uf_hint, by_uf_name, by_name, norms_by_uf):
    """Tenta casar 'name' com o IBGE; se falhar, tenta separar por ' e ' (uma ou duas cidades)."""
    hit = lookup_city(name, uf_hint, by_uf_name, by_name, norms_by_uf)
    if hit:
        return [hit]
    parts = re.split(r"\s+e\s+", name, flags=re.IGNORECASE)
    if len(parts) == 2:
        hits = [h for h in (lookup_city(p, uf_hint, by_uf_name, by_name, norms_by_uf) for p in parts) if h]
        if hits:
            return hits
    return []


def process_clientes(conn, codigos_franqueados, by_uf_name, by_name, norms_by_uf):
    """Le nome_clientes.xlsx, casa ID_Whitelabel com Codigo da plataforma e geocodifica a cidade."""
    conn.execute("""
        CREATE TABLE clientes (
            id_whitelabel INTEGER,
            franqueado_cliente TEXT,
            cidade TEXT,
            uf TEXT,
            latitude REAL,
            longitude REAL,
            codigo_encontrado INTEGER
        )
    """)
    if not CLIENTES_XLSX_PATH.exists():
        conn.commit()
        return 0, 0, []

    df = pd.read_excel(CLIENTES_XLSX_PATH)
    df.columns = ["id_whitelabel", "cidade", "uf", "franqueado_cliente"]
    df = df.dropna(subset=["id_whitelabel"])
    df["id_whitelabel"] = df["id_whitelabel"].astype(int)

    nao_casadas = []
    encontrados = 0
    for row in df.itertuples(index=False):
        hit = lookup_city(row.cidade, row.uf, by_uf_name, by_name, norms_by_uf)
        if not hit:
            nao_casadas.append((row.id_whitelabel, row.franqueado_cliente, row.cidade, row.uf))
            continue
        nome_oficial, uf, lat, lon = hit
        codigo_encontrado = row.id_whitelabel in codigos_franqueados
        if codigo_encontrado:
            encontrados += 1
        conn.execute(
            "INSERT INTO clientes VALUES (?,?,?,?,?,?,?)",
            (row.id_whitelabel, row.franqueado_cliente, nome_oficial, uf, lat, lon, int(codigo_encontrado)),
        )
    conn.commit()
    return len(df), encontrados, nao_casadas


def main():
    by_uf_name, by_name, norms_by_uf = load_ibge()

    df = pd.read_excel(XLSX_PATH)
    df.columns = [
        "codigo_plataforma", "nome_franqueado", "nr_territorios",
        "regiao", "estado", "cidades_raw",
    ]
    df = df.dropna(subset=["codigo_plataforma"])
    df["codigo_plataforma"] = df["codigo_plataforma"].astype(int)

    franqueados = {}  # codigo -> dict(nome, regiao, estados:set, cidades:dict[(nome,uf)] = (lat,lon))
    nao_casadas = []

    for row in df.itertuples(index=False):
        codigo = row.codigo_plataforma
        f = franqueados.setdefault(codigo, {
            "nome": row.nome_franqueado,
            "regiao": row.regiao,
            "estados": set(),
            "cidades": {},
        })
        if isinstance(row.nome_franqueado, str) and len(row.nome_franqueado) > len(str(f["nome"])):
            f["nome"] = row.nome_franqueado
        if isinstance(row.regiao, str):
            f["regiao"] = row.regiao

        default_uf = ESTADO_NOME_PARA_UF.get(normalize(row.estado)) if isinstance(row.estado, str) else None
        if default_uf:
            f["estados"].add(default_uf)

        if not isinstance(row.cidades_raw, str):
            continue

        for raw_name, uf_hint in split_city_cell(row.cidades_raw, default_uf):
            hits = resolve_entry(raw_name, uf_hint or default_uf, by_uf_name, by_name, norms_by_uf)
            if not hits:
                nao_casadas.append((codigo, row.nome_franqueado, raw_name, uf_hint or default_uf))
                continue
            for nome_oficial, uf, lat, lon in hits:
                f["cidades"][(nome_oficial, uf)] = (lat, lon)

    DB_PATH.parent.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE franqueados (
            codigo_plataforma INTEGER PRIMARY KEY,
            nome_franqueado TEXT,
            regiao TEXT,
            estados TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE franqueado_cidades (
            codigo_plataforma INTEGER,
            cidade TEXT,
            uf TEXT,
            latitude REAL,
            longitude REAL
        )
    """)

    total_cidades = 0
    for codigo, f in franqueados.items():
        conn.execute(
            "INSERT INTO franqueados VALUES (?,?,?,?)",
            (codigo, f["nome"], f["regiao"], ",".join(sorted(f["estados"]))),
        )
        for (cidade, uf), (lat, lon) in f["cidades"].items():
            conn.execute(
                "INSERT INTO franqueado_cidades VALUES (?,?,?,?,?)",
                (codigo, cidade, uf, lat, lon),
            )
            total_cidades += 1
    total_clientes, clientes_com_codigo, clientes_nao_casados = process_clientes(
        conn, set(franqueados.keys()), by_uf_name, by_name, norms_by_uf,
    )
    conn.close()

    print(f"Franqueados unicos: {len(franqueados)}")
    print(f"Vinculos franqueado-cidade gravados: {total_cidades}")
    print(f"Cidades NAO casadas com o IBGE: {len(nao_casadas)}")
    if nao_casadas:
        print("\n--- Revisar manualmente (codigo, franqueado, cidade, uf_hint) ---")
        for item in nao_casadas:
            print(item)

    if total_clientes:
        print(f"\nClientes (whitelabel) processados: {total_clientes}")
        print(f"Clientes com ID_Whitelabel encontrado em Codigo da plataforma: {clientes_com_codigo}")
        print(f"Clientes SEM Codigo da plataforma correspondente: {total_clientes - clientes_com_codigo}")
        if clientes_nao_casados:
            print(f"Cidades de clientes NAO casadas com o IBGE: {len(clientes_nao_casados)}")
            for item in clientes_nao_casados:
                print(item)


if __name__ == "__main__":
    main()
