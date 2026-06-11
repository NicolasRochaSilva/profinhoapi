"""Palavras-chave para identificar a categoria/modelo de cada requisição.

Usado como atalho rápido (heurística) antes de acionar o modelo roteador
(llama3.2:3b). Se as palavras-chave não forem conclusivas, cai para o roteador.
"""

from __future__ import annotations

import re

# categoria -> lista de palavras-chave
KEYWORDS: dict[str, list[str]] = {
    "programacao": [
        "código", "codigo", "code", "programa", "programação", "programacao",
        "função", "funcao", "function", "classe", "class", "bug", "erro de código",
        "python", "javascript", "typescript", "java", "c#", "csharp", "asp.net",
        "aspnet", "dotnet", ".net", "blazor", "html", "css", "js", "sql", "query",
        "api", "endpoint", "backend", "frontend", "framework", "biblioteca",
        "library", "deploy", "docker", "git", "compilar", "compile", "debug",
        "refatorar", "refactor", "script", "regex", "json", "yaml", "terminal",
        "banco de dados", "database", "migração", "migration", "stacktrace",
    ],
    "educacao": [
        "aula", "plano de aula", "exercício", "exercicio", "exercícios", "prova",
        "simulado", "avaliação", "avaliacao", "questão", "questao", "questões",
        "apostila", "resumo", "resumir", "matéria", "materia", "conteúdo educacional",
        "conteudo educacional", "ensinar", "ensino", "aluno", "professor", "didático",
        "didatico", "lição", "licao", "atividade", "redação", "redacao",
        "enem", "vestibular", "currículo", "curriculo", "explicar conceito",
        "história", "geografia", "matemática", "matematica", "português", "portugues",
        "ciências", "ciencias", "física", "fisica", "química", "quimica", "biologia",
        "fotossíntese", "fotossintese", "explicar", "explica", "6º ano", "7º ano", "8º ano",
        "verbo", "verbos", "gramática", "gramatica", "inglês", "ingles", "o que é", "o que e",
        "definição", "definicao", "to be",
    ],
    "imagem": [
        "imagem", "image", "foto", "print", "screenshot", "captura de tela",
        "gráfico", "grafico", "diagrama", "layout", "ocr", "ler imagem",
        "analisar imagem", "descrever imagem", "veja a imagem", "nesta figura",
        "figura", "logo", "design da", "página baseada", "pagina baseada",
    ],
    "chat": [
        "olá", "ola", "oi", "bom dia", "boa tarde", "boa noite", "tudo bem",
        "como você", "como voce", "quem é você", "quem e voce", "ajuda", "dúvida",
        "duvida", "converse", "conversar", "obrigado", "valeu",
    ],
}


def detectar_categoria(texto: str, tem_imagem: bool = False) -> tuple[str | None, int]:
    """Detecta categoria por palavras-chave.

    Retorna (categoria, pontuacao). Se nenhuma palavra-chave bater, retorna (None, 0).
    Presença de imagem força 'imagem'.
    """
    if tem_imagem:
        return "imagem", 100

    texto_low = texto.lower()
    placar: dict[str, int] = {cat: 0 for cat in KEYWORDS}

    for categoria, palavras in KEYWORDS.items():
        for palavra in palavras:
            # \b não funciona bem com acentos; usamos busca de substring com bordas simples
            if re.search(r"(?<!\w)" + re.escape(palavra) + r"(?!\w)", texto_low):
                placar[categoria] += 1

    melhor = max(placar, key=lambda c: placar[c])
    if placar[melhor] == 0:
        return None, 0
    return melhor, placar[melhor]


# Heurística para decidir se precisa buscar na web (antes do roteador llama3.2:3b).
WEB_SIM: list[str] = [
    "hoje", "agora", "atual", "atualizado", "recente", "últimas", "ultimas", "notícia",
    "noticia", "notícias", "noticias", "2024", "2025", "2026", "preço", "preco",
    "cotação", "cotacao", "câmbio", "cambio", "quanto custa", "disponível", "disponivel",
    "lançamento", "lancamento", "versão mais recente", "versao mais recente",
    "documentação oficial", "documentacao oficial", "site oficial", "buscar na internet",
    "pesquisar na web", "o que aconteceu", "última atualização", "ultima atualizacao",
]

WEB_NAO: list[str] = [
    "olá", "ola", "oi", "plano de aula", "exercício", "exercicio", "prova", "gabarito",
    "resumo", "explique", "explica", "o que é", "o que e", "como funciona", "definição",
    "definicao", "conceito", "fotossíntese", "fotossintese", "para o 6º", "para o 7º",
    "para o 8º", "alunos do", "crie um", "monte um", "faça um", "faca um", "elabore",
]


def _contar_palavras(texto_low: str, palavras: list[str]) -> int:
    n = 0
    for palavra in palavras:
        if re.search(r"(?<!\w)" + re.escape(palavra) + r"(?!\w)", texto_low):
            n += 1
    return n


def detectar_precisa_web(texto: str) -> tuple[bool | None, str]:
    """Retorna (True/False, motivo) ou (None, '') se inconclusivo (usar llama3.2:3b)."""
    texto_low = texto.lower()
    sim = _contar_palavras(texto_low, WEB_SIM)
    nao = _contar_palavras(texto_low, WEB_NAO)

    if sim >= 1 and sim > nao:
        return True, f"Palavras-chave ({sim}) indicam informação atualizada na web."
    if nao >= 2 or (nao >= 1 and sim == 0):
        return False, f"Palavras-chave ({nao}) indicam conteúdo estável (sem web)."
    return None, ""
