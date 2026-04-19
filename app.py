import io
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from pypdf import PdfReader

st.set_page_config(page_title="PDF para CSV - Autorizações ARTESP", page_icon="🚌", layout="wide")

PLATE_PATTERN = r"[A-Z]{3}(?:-?\d{4}|\d[A-Z]\d{2})"
AUTH_PATTERN = re.compile(r"AUTORIZA[ÇC][ÃA]O\s+(\d{10})\s*/?\s*(\d{4})", re.IGNORECASE)
DATE_PATTERN = re.compile(r"em\s+(\d{2}/\d{2}/\d{4})\s*,\s*[àa]s", re.IGNORECASE)
VALIDITY_PATTERN = re.compile(
    r"pelo\s+(?:prazo|per[ií]odo)\s+de\s*(\d+)\s*(?:\([^)]+\))?\s*dias?",
    re.IGNORECASE | re.DOTALL,
)
PERIOD_PATTERN = re.compile(
    r"per[ií]odo(?:\s+de)?\s*(\d{2}/\d{2}/\d{2,4})\s*[àa]\s*(\d{2}/\d{2}/\d{2,4})",
    re.IGNORECASE | re.DOTALL,
)
# Mais tolerante: em alguns PDFs a empresa vem depois da vírgula do fundamento legal, sem o artigo "a"
REQUESTER_PATTERN = re.compile(
    r"AUTORIZO.*?(?:com\s+base|nos?\s+termos).*?,\s*(?:a\s+|à\s+)?(?:empresa\s+)?(.+?)\s*,\s*CNPJ\s*N[º°\.]?\s*",
    re.IGNORECASE | re.DOTALL,
)
REQUESTED_PATTERN = re.compile(
    r"os ve[íi]culos relacionados\s+da\s+(.+?)\s*,\s+devidamente registrados",
    re.IGNORECASE | re.DOTALL,
)
OWN_VEHICLES_PATTERN = re.compile(
    r"os ve[íi]culos relacionados\s+devidamente registrados",
    re.IGNORECASE,
)
SECTION_PATTERN = re.compile(
    r"Prefixo\s+Placa(?P<section>.*?)(?:Para tanto|Os ve[íi]culos estejam|O n[ãa]o cumprimento)",
    re.IGNORECASE | re.DOTALL,
)
PAIR_PATTERN = re.compile(rf"(\d{{1,6}})\s+({PLATE_PATTERN})")
SPLIT_PREFIX = re.compile(r"(?<!\d)(\d{1,6})(?!\d)")
PLATE_ONLY_PATTERN = re.compile(rf"^{PLATE_PATTERN}$")
INTERESSADO_PATTERN = re.compile(r"Interessado:\s*(.+?)\s*(?:Assunto:|AUTORIZA[ÇC][ÃA]O)", re.IGNORECASE | re.DOTALL)

CSV_COLUMNS = [
    "numero_autorizacao",
    "data_aprovacao",
    "requerente",
    "requisitada",
    "dias_validade",
    "data_vencimento",
    "placa",
    "prefixo",
]


def normalize_spaces(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def fix_broken_plates(text: str) -> str:
    text = re.sub(r"([A-Z]{3})-\s*\n\s*(\d{4})", r"\1-\2", text)
    text = re.sub(r"([A-Z]{3})\s*\n\s*(\d{4})", r"\1\2", text)
    text = re.sub(r"([A-Z]{3}\d[A-Z])\s*\n\s*(\d{2})", r"\1\2", text)
    text = re.sub(r"([A-Z]{3})\s*-\s*(\d{4})", r"\1-\2", text)
    return text


def clean_company_name(name: str) -> str:
    name = normalize_spaces(name)
    name = re.sub(r"\s+", " ", name).strip(" .,;:\n\t")
    name = re.sub(r"\s+\.$", "", name)
    # Remove apenas "empresa"/"Empresa" do texto corrido.
    # Mantém "EMPRESA" quando estiver em maiúsculas, pois pode fazer parte da razão social.
    name = re.sub(r"^(?:empresa|Empresa)\s+", "", name)
    return name.strip(" .,;:\n\t")


def normalize_plate(plate: str) -> str:
    return plate.replace("-", "").strip().upper()


def parse_date_ddmmyyyy(date_str: str) -> datetime:
    date_str = date_str.strip()
    fmt = "%d/%m/%Y" if len(date_str.split("/")[-1]) == 4 else "%d/%m/%y"
    return datetime.strptime(date_str, fmt)


def fmt_date(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y")


def extract_pages(file_bytes: bytes) -> List[str]:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages: List[str] = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        txt = normalize_spaces(txt)
        txt = fix_broken_plates(txt)
        pages.append(txt)
    return pages


def is_auth_start_page(page_text: str) -> bool:
    return bool(
        re.search(
            r"AUTORIZA[ÇC][ÃA]O\s+\d{10}\s*/?\s*\d{4}\s*-\s*ARTESP",
            page_text,
            re.IGNORECASE,
        )
    )


def group_authorization_chunks(pages: List[str]) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []

    for page in pages:
        if is_auth_start_page(page):
            if current:
                chunks.append("\n".join(current))
            current = [page]
        elif current:
            current.append(page)

    if current:
        chunks.append("\n".join(current))

    return chunks


def extract_authorization_number(chunk: str) -> str:
    match = AUTH_PATTERN.search(chunk)
    return f"{match.group(1)}/{match.group(2)}" if match else ""


def extract_approval_date(chunk: str) -> str:
    dates = DATE_PATTERN.findall(chunk)
    return dates[-1] if dates else ""


def extract_validity_days(chunk: str) -> Optional[int]:
    match = VALIDITY_PATTERN.search(chunk)
    if match:
        return int(match.group(1))

    period_match = PERIOD_PATTERN.search(chunk)
    if period_match:
        start = parse_date_ddmmyyyy(period_match.group(1))
        end = parse_date_ddmmyyyy(period_match.group(2))
        return (end - start).days

    return None


def extract_requester(chunk: str) -> str:
    # caminho principal: pega o trecho imediatamente antes de CNPJ e usa o segmento após a última vírgula
    cnpj_match = re.search(r"CNPJ\s*N[º°\.]?", chunk, re.IGNORECASE)
    if cnpj_match:
        before = normalize_spaces(chunk[max(0, cnpj_match.start() - 260):cnpj_match.start()])
        parts = [p.strip() for p in before.split(",") if p.strip()]
        if parts:
            candidate = parts[-1]
            candidate = re.sub(r"^(?:a|à)\s+", "", candidate, flags=re.IGNORECASE).strip()
            if candidate and len(candidate) > 3:
                return clean_company_name(candidate)

    # fallback regex
    match = REQUESTER_PATTERN.search(chunk)
    if match:
        candidate = re.sub(r"^(?:a|à)\s+", "", match.group(1), flags=re.IGNORECASE).strip()
        if candidate:
            return clean_company_name(candidate)

    # fallback: usa o primeiro interessado do cabeçalho, antes da vírgula com pessoa física
    m2 = INTERESSADO_PATTERN.search(chunk)
    if m2:
        header = clean_company_name(m2.group(1))
        parts = [p.strip() for p in header.split(",") if p.strip()]
        if parts:
            return parts[0]

    return ""


def extract_requested_company(chunk: str, requester: str) -> str:
    match = REQUESTED_PATTERN.search(chunk)
    if match:
        company = clean_company_name(match.group(1))
        return company if company else ("PRÓPRIA" if requester else "")
    if OWN_VEHICLES_PATTERN.search(chunk):
        return "PRÓPRIA"
    return "PRÓPRIA" if requester else ""


def _extract_section(chunk: str) -> str:
    section_match = SECTION_PATTERN.search(chunk)
    section = section_match.group("section") if section_match else chunk
    section = fix_broken_plates(section)
    section = re.sub(r"\bPrefixo\s+Placa\b", " ", section, flags=re.IGNORECASE)
    return normalize_spaces(section)


def _tokenize_section(section: str) -> List[str]:
    raw_lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
    tokens: List[str] = []
    for line in raw_lines:
        parts = re.findall(rf"\d{{1,6}}|{PLATE_PATTERN}", line)
        if parts:
            tokens.extend(parts)
    return tokens


def extract_prefix_and_plates(chunk: str) -> Tuple[List[str], List[str]]:
    section = _extract_section(chunk)

    prefixes: List[str] = []
    plates: List[str] = []
    seen = set()

    for prefix, plate in PAIR_PATTERN.findall(section):
        normalized = normalize_plate(plate)
        key = (prefix, normalized)
        if key not in seen:
            seen.add(key)
            prefixes.append(prefix)
            plates.append(normalized)

    tokens = _tokenize_section(section)
    pending_prefix: Optional[str] = None
    for token in tokens:
        if SPLIT_PREFIX.fullmatch(token):
            pending_prefix = token
            continue
        if pending_prefix and PLATE_ONLY_PATTERN.fullmatch(token):
            normalized = normalize_plate(token)
            key = (pending_prefix, normalized)
            if key not in seen:
                seen.add(key)
                prefixes.append(pending_prefix)
                plates.append(normalized)
            pending_prefix = None

    return prefixes, plates


def build_record(chunk: str) -> Dict[str, str]:
    auth_number = extract_authorization_number(chunk)
    approval_date = extract_approval_date(chunk)
    requester = extract_requester(chunk)
    requested = extract_requested_company(chunk, requester)
    validity_days = extract_validity_days(chunk)
    prefixes, plates = extract_prefix_and_plates(chunk)

    due_date = ""
    if approval_date and validity_days is not None:
        due_date = fmt_date(parse_date_ddmmyyyy(approval_date) + timedelta(days=validity_days))

    return {
        "numero_autorizacao": auth_number,
        "data_aprovacao": approval_date,
        "requerente": requester,
        "requisitada": requested,
        "dias_validade": "" if validity_days is None else str(validity_days),
        "data_vencimento": due_date,
        "placa": "/".join(plates),
        "prefixo": "/".join(prefixes),
    }


def parse_pdf_to_dataframe(file_bytes: bytes) -> Tuple[pd.DataFrame, int, int]:
    pages = extract_pages(file_bytes)
    chunks = group_authorization_chunks(pages)
    records: List[Dict[str, str]] = []

    for chunk in chunks:
        record = build_record(chunk)
        if record["numero_autorizacao"]:
            records.append(record)

    df = pd.DataFrame(records, columns=CSV_COLUMNS)
    return df, len(pages), len(chunks)


st.title("🚌 Conversor de PDF ARTESP para CSV")
st.caption(
    "Versão final revisada: junta placas partidas, preserva o requerente corretamente, remove apenas 'empresa/Empresa' do texto corrido e calcula dias também quando a autorização vier por período."
)

with st.expander("Melhorias desta revisão", expanded=False):
    st.markdown(
        """
- preserva o nome social completo do **requerente**
- remove "empresa" ou "Empresa" do início apenas quando vier como texto corrido; mantém **EMPRESA** quando faz parte da razão social
- corrige casos em que o PDF omite o artigo antes da empresa no trecho do **AUTORIZO**
- usa fallback pelo campo **Interessado** quando o requerente não for capturado no corpo
- calcula **dias de validade** também quando o PDF trouxer um período com data inicial e final
- junta placas quebradas em duas linhas
- mantém a saída sem hífen e datas em `dd/mm/aaaa`
        """
    )

uploaded = st.file_uploader("Envie o PDF de autorizações", type=["pdf"])

if uploaded:
    file_bytes = uploaded.read()
    with st.spinner("Lendo PDF e extraindo autorizações..."):
        df, total_pages, total_auths = parse_pdf_to_dataframe(file_bytes)

    st.success(f"PDF processado: {total_pages} páginas, {total_auths} autorizações encontradas.")
    st.dataframe(df, use_container_width=True)

    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")

    output_xlsx = io.BytesIO()
    with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Autorizacoes", index=False)
    output_xlsx.seek(0)

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "Baixar CSV",
            data=csv_bytes,
            file_name="autorizacoes_extraidas.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            "Baixar Excel",
            data=output_xlsx.getvalue(),
            file_name="autorizacoes_extraidas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
else:
    st.info("Envie um PDF para gerar CSV e Excel das autorizações.")
