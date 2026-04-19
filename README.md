# Conversor PDF ARTESP para CSV

App Streamlit para extrair autorizações da ARTESP a partir de PDFs e gerar CSV/Excel.

## Arquivos principais
- `app.py`
- `requirements.txt`

## Publicação no Streamlit
1. Suba estes arquivos no GitHub.
2. No Streamlit Community Cloud, crie um novo app.
3. Escolha o repositório, branch e o arquivo `app.py`.
4. Publique.

## O que esta versão faz melhor
- separa o PDF por autorização
- usa a data da assinatura digital
- calcula a data de vencimento
- concatena placas e prefixos com `/`
- remove hífen das placas na saída
- reconhece placas quebradas em duas linhas, como `ABC-` em uma linha e `1234` na seguinte
- trata `PRÓPRIA` quando não houver empresa cedente distinta

- diferencia **EMPRESA** (mantém) de **empresa/Empresa** no início do requerente (remove)
