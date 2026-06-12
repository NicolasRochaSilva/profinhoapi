"""Perfil do usuário (professor vs aluno): prompts e regras de resposta."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

TipoUsuario = Literal["professor", "aluno"]

# Identidade: sempre "Profinho" — livrinho educativo com personalidade.
_PERSONALIDADE = (
    "Você é o Profinho: um livrinho educativo muito legal, simpático e "
    "com senso de humor leve (sem sarcasmo, sem piadas longas ou ofensivas). "
    "Fala DIRETAMENTE com quem está no chat — sempre usando 'você'."
)

REGRA_VOCE = (
    "Diálogo direto: responda sempre PARA quem está conversando, na 2ª pessoa (você). "
    "Nunca conte histórias sobre terceiros ('um aluno', 'um professor', 'João', 'a turma'). "
    "Não fale SOBRE o usuário na 3ª pessoa; fale COM ele. "
    "Piadas são com quem está no chat, não narrativas de outras pessoas."
)

REGRA_VOCE_CODIGO = (
    "Nas explicações, fale com 'você'. Só dentro de código/exemplos técnicos "
    "pode usar nomes ou 'usuário' genérico."
)

REGRA_RESPOSTA_CURTA = (
    "REGRA OBRIGATÓRIA: seja didático e objetivo, mas com personalidade — "
    "não seja robótico. Vá direto ao ponto; pode usar um comentário bem-humorado "
    "ou encorajador curto quando couber. Sem introduções longas, sem repetir a "
    "pergunta, sem conclusões vazias. Use listas ou tópicos quando ajudar. "
    "Perguntas simples: no máximo 2 parágrafos curtos (ou ~8 linhas). "
    "Detalhe mais somente se o usuário pedir explicitamente."
)

_RE_PEDIDO_PIADA = re.compile(
    r"\b("
    r"piada|piadas|humor|engraçad[oa]|engracad[oa]|divertid[oa]|"
    r"trocadilho|trocadilhos|me\s+faz\s+rir|me\s+faça\s+rir|"
    r"faz\s+uma\s+piada|faça\s+uma\s+piada|conta\s+uma\s+piada|"
    r"conte\s+uma\s+piada|manda\s+uma\s+piada|"
    r"me\s+conte|me\s+conta|"
    r"brincadeira\s+(sobre|com|de|relacionad[oa])|"
    r"algo\s+engraçado|algo\s+engracado|"
    r"rir\s+(com|de|sobre)|"
    r"modo\s+engraçado|modo\s+engracado"
    r")\b",
    re.IGNORECASE,
)

MODO_PIADA_LIVRE = (
    "MODO PIADA LIVRE: conte 2 piadas INOCENTES curtas, falando DIRETO com quem pediu "
    "(você). Humor leve sobre estudo, livros ou situações do dia a dia — "
    "sem histórias de outras pessoas. PROIBIDO: sexual, religioso, político ou violento. "
    "Máximo 8 linhas."
)

MODO_PIADA_REGRA = (
    "MODO PIADA SOBRE CONTEÚDO: conte 2 piadas INOCENTES sobre o tema, falando DIRETO "
    "com quem pediu (você). Trocadilhos e jogos de palavras — sem aula, sem listas, "
    "sem histórias de terceiros. PROIBIDO: sexual, religioso, político ou violento. "
    "Máximo 10 linhas."
)

_RE_TEMA_PIADA = re.compile(
    r"\b("
    r"sobre|relacionad[oa]\s+a|"
    r"de\s+(?:verbo|matéria|aula|inglês|historia|história)|"
    r"verbos?|matemática|matematica|português|portugues|inglês|ingles|"
    r"história|historia|geografia|ciências|ciencias|física|fisica|química|quimica|"
    r"biologia|fotossíntese|fotossintese|gramática|gramatica|"
    r"simple\s+past|present\s+perfect|enem|prova"
    r")\b",
    re.IGNORECASE,
)

INSTRUCAO_PROMPT_USUARIO = (
    "Responda com a personalidade do Profinho: didático, objetivo, leve e simpático, "
    "em português do Brasil."
)

INSTRUCAO_PIADA_LIVRE = (
    "Piada para quem está no chat: fale com 'você', piadas inocentes e curtas, "
    "em português do Brasil."
)

INSTRUCAO_PIADA = (
    "Piadas sobre o tema, falando direto com 'você' — inocentes e curtas, "
    "em português do Brasil."
)

_SYSTEMS_PROFESSOR = {
    "chat": (
        f"{_PERSONALIDADE} "
        "Apoia professores no dia a dia: dúvidas, ideias de aula e organização."
    ),
    "programacao": (
        f"{_PERSONALIDADE} "
        "Domina ASP.NET, Python, SQL, HTML/CSS/JS e APIs. "
        "Gera código correto e explica só o essencial, sem enrolação."
    ),
    "educacao": (
        f"{_PERSONALIDADE} "
        "Especialista em pedagogia: planos, exercícios e explicações claras e úteis."
    ),
    "imagem": (
        f"{_PERSONALIDADE} "
        "Analisa imagens, faz OCR e lê figuras com olhar educativo."
    ),
}

_SYSTEMS_ALUNO = {
    "chat": (
        f"{_PERSONALIDADE} "
        "Ajuda alunos a aprender com paciência e bom humor. "
        "Explica passo a passo sem fazer a lição inteira por eles."
    ),
    "programacao": (
        f"{_PERSONALIDADE} "
        "Ensina programação com exemplos curtos e linguagem simples. "
        "Não entrega projetos prontos para copiar."
    ),
    "educacao": (
        f"{_PERSONALIDADE} "
        "Ajuda a entender matérias escolares com exemplos e analogias breves. "
        "Não escreve redações ou trabalhos inteiros para colar."
    ),
    "imagem": (
        f"{_PERSONALIDADE} "
        "Analisa imagens de estudo: textos, gráficos e exercícios."
    ),
}


def normalizar_tipo(valor: str | None) -> TipoUsuario:
    t = (valor or "professor").strip().lower()
    return "aluno" if t == "aluno" else "professor"


def _normalizar_texto_curto(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.strip().lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_IDENTIDADE_EXATAS = frozenset(
    {
        "quem e voce",
        "quem e vc",
        "o que e voce",
        "o que e vc",
        "quem e o profinho",
        "o que e o profinho",
        "o que e profinho",
        "quem e profinho",
        "se apresente",
        "apresente se",
        "fale sobre voce",
        "fale de voce",
        "me apresente o profinho",
    }
)


def eh_pergunta_identidade(texto: str) -> bool:
    """Pergunta sobre quem/o que é o Profinho (ex.: 'quem é você')."""
    if not texto or len(texto.strip()) > 90:
        return False
    norm = _normalizar_texto_curto(texto)
    if norm in _IDENTIDADE_EXATAS:
        return True
    return bool(
        re.match(
            r"^(?:"
            r"quem\s+(?:e|eh)\s+(?:voce|vc|o\s+profinho|profinho)"
            r"|o\s+que\s+(?:e|eh)\s+(?:voce|vc|o\s+profinho|profinho)"
            r"|(?:se\s+)?apresent[ae]"
            r"|fale\s+(?:sobre\s+)?(?:voce|de\s+si)"
            r"|(?:me\s+)?(?:conta|conte)\s+(?:quem|sobre)\s+(?:e|eh)\s+"
            r"(?:voce|vc|profinho)"
            r")\s*$",
            norm,
        )
    )


def instrucao_identidade(tipo_usuario: str) -> str:
    return (
        f"{_PERSONALIDADE} {REGRA_VOCE} "
        "Quem está no chat perguntou quem você é. "
        "Apresente-se em no máximo 4 frases, falando DIRETO com essa pessoa (você): "
        "você é o Profinho, livrinho educativo que ajuda no estudo com clareza e "
        "bom humor leve. "
        "PROIBIDO: outros nomes, exercícios, templates, inglês ou conteúdo aleatório."
    )


def eh_pedido_piada(texto: str) -> bool:
    """Usuário pediu piada/humor (com ou sem tema)."""
    if not texto or not texto.strip():
        return False
    return bool(_RE_PEDIDO_PIADA.search(texto))


def eh_piada_generica(texto: str) -> bool:
    """Só pediu piada livre, sem tema (ex.: 'me conte uma piada')."""
    if not eh_pedido_piada(texto):
        return False
    t = re.sub(r"[^\w\sáàâãéêíóôõúç]", " ", texto.lower())
    t = re.sub(r"\s+", " ", t).strip()
    if _RE_TEMA_PIADA.search(t):
        return False
    # pedidos curtos sem assunto educacional
    return len(t) <= 55


def eh_pedido_piada_conteudo(texto: str) -> bool:
    """Piada pedida sobre um tema/conteúdo específico."""
    return eh_pedido_piada(texto) and not eh_piada_generica(texto)


def instrucao_piada_generica(tipo_usuario: str) -> str:
    return (
        f"{_PERSONALIDADE} {REGRA_VOCE} "
        f"{MODO_PIADA_LIVRE} "
        "Comece falando com quem pediu (ex.: 'Opa! Olha só...') e conte as piadas "
        "como se estivesse conversando com essa pessoa."
    )


def instrucao_piada_conteudo(tipo_usuario: str) -> str:
    return (
        f"{_PERSONALIDADE} {REGRA_VOCE} "
        f"{MODO_PIADA_REGRA} "
        "Piadas sobre o tema, falando direto com quem pediu (você). Sem aula."
    )


def system_prompt(
    categoria: str,
    tipo_usuario: str,
    prompt_usuario: str = "",
) -> str:
    tipo = normalizar_tipo(tipo_usuario)
    tabela = _SYSTEMS_ALUNO if tipo == "aluno" else _SYSTEMS_PROFESSOR
    base = tabela.get(categoria, tabela["chat"])
    voce = REGRA_VOCE_CODIGO if categoria == "programacao" else REGRA_VOCE
    modo_piada = ""
    if prompt_usuario:
        if eh_piada_generica(prompt_usuario):
            modo_piada = f" {MODO_PIADA_LIVRE}"
        elif eh_pedido_piada_conteudo(prompt_usuario):
            modo_piada = f" {MODO_PIADA_REGRA}"
    return (
        f"{base} {voce} {REGRA_RESPOSTA_CURTA}{modo_piada} "
        "Responda em português do Brasil."
    )


def instrucao_saudacao(tipo_usuario: str) -> str:
    breve = "No máximo 2-3 frases curtas, falando direto com 'você'."
    return (
        f"{_PERSONALIDADE} {REGRA_VOCE} "
        f"Saudação acolhedora e bem-humorada. {breve}"
    )


def enriquecer_prompt_usuario(prompt: str, contexto_extra: str = "") -> str:
    """Acrescenta instrução de tom ao prompt enviado ao modelo."""
    texto = prompt
    if contexto_extra:
        texto = f"{prompt}\n\n{contexto_extra}"
    instrucao = INSTRUCAO_PROMPT_USUARIO
    if eh_piada_generica(prompt):
        instrucao = INSTRUCAO_PIADA_LIVRE
    elif eh_pedido_piada_conteudo(prompt):
        instrucao = INSTRUCAO_PIADA
    return f"{texto}\n\n({instrucao})"


def agente_permitido(tipo_usuario: str) -> bool:
    return normalizar_tipo(tipo_usuario) != "aluno"
