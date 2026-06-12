"""Moderação de conteúdo: bloqueio de temas sensíveis para todos os usuários."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

_EXCECOES_GENERO_EDUCACIONAL = (
    "genero textual",
    "gênero textual",
    "genero gramatical",
    "gênero gramatical",
    "concordancia de genero",
    "concordância de gênero",
    "flexao de genero",
    "flexão de gênero",
    "substantivo masculino",
    "substantivo feminino",
)

_TEMAS: dict[str, tuple[str, ...]] = {
    "politica": (
        "politica",
        "política",
        "politico",
        "político",
        "eleicao",
        "eleição",
        "presidente",
        "congresso nacional",
        "senado",
        "camara dos deputados",
        "câmara dos deputados",
        "partido politico",
        "partido político",
        "esquerda",
        "direita",
        "comunismo",
        "socialismo",
        "fascismo",
        "impeachment",
        "votacao",
        "votação",
        "candidato",
        "candidata",
        "bolsonaro",
        "lula",
        "manifestacao politica",
        "manifestação política",
    ),
    "religiao": (
        "religiao",
        "religião",
        "religioso",
        "deus",
        "jesus",
        "cristo",
        "biblia",
        "bíblia",
        "igreja",
        "pastor",
        "padre",
        "islam",
        "islamismo",
        "muçulmano",
        "musulmano",
        "judeu",
        "judaismo",
        "ateismo",
        "ateísmo",
        "oracao",
        "oração",
        "evangelico",
        "evangélico",
        "catolico",
        "católico",
        "protestante",
        "umbanda",
        "candomble",
        "candomblé",
    ),
    "sexo": (
        "sexo oral",
        "sexo anal",
        "relacao sexual",
        "relação sexual",
        "pornografia",
        "pornografico",
        "pornográfico",
        "porno",
        "masturbacao",
        "masturbação",
        "orgasmo",
        "prostituicao",
        "prostituição",
        "conteudo adulto",
        "conteúdo adulto",
        "onlyfans",
        "fetiche",
        "erotico",
        "erótico",
        "nudez",
        "sexualidade",
    ),
    "genero": (
        "identidade de genero",
        "identidade de gênero",
        "orientacao sexual",
        "orientação sexual",
        "lgbt",
        "lgbtq",
        "transgenero",
        "transgênero",
        "transexual",
        "nao binario",
        "não binario",
        "não-binário",
        "homossexual",
        "heterossexual",
        "bissexual",
        "gay",
        "lesbica",
        "lésbica",
        "queer",
        "pronome neutro",
        "ideologia de genero",
        "ideologia de gênero",
    ),
    "futebol": (
        "futebol",
        "soccer",
        "flamengo",
        "corinthians",
        "palmeiras",
        "gremio",
        "grêmio",
        "cruzeiro",
        "botafogo",
        "vasco",
        "fluminense",
        "copa do mundo",
        "libertadores",
        "brasileirao",
        "brasileirão",
        "champions league",
        "messi",
        "neymar",
        "selecao brasileira",
        "seleção brasileira",
        "campeonato brasileiro",
    ),
}

_ROTULOS = {
    "politica": "política",
    "religiao": "religião",
    "sexo": "sexualidade/conteúdo adulto",
    "genero": "identidade de gênero/orientação sexual",
    "futebol": "futebol",
}


@dataclass
class BloqueioConteudo:
    tema: str
    motivo: str


def _normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _contem_palavra(norm: str, palavra: str) -> bool:
    p = _normalizar(palavra)
    if " " in p:
        return p in norm
    return bool(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", norm))


def _tem_excecao_genero_educacional(norm: str) -> bool:
    return any(exc in norm for exc in _EXCECOES_GENERO_EDUCACIONAL)


def detectar_tema_bloqueado(texto: str) -> Optional[BloqueioConteudo]:
    if not (texto or "").strip():
        return None

    norm = _normalizar(texto)

    for tema, palavras in _TEMAS.items():
        if tema == "genero" and _tem_excecao_genero_educacional(norm):
            continue
        for palavra in palavras:
            if _contem_palavra(norm, palavra):
                rotulo = _ROTULOS.get(tema, tema)
                return BloqueioConteudo(
                    tema=tema,
                    motivo=f"Tema não permitido: {rotulo}.",
                )

    if _contem_palavra(norm, "sexo") or _contem_palavra(norm, "sexual"):
        if not _tem_excecao_genero_educacional(norm):
            return BloqueioConteudo(
                tema="sexo",
                motivo="Tema não permitido: sexualidade/conteúdo adulto.",
            )

    if _contem_palavra(norm, "genero") and not _tem_excecao_genero_educacional(norm):
        return BloqueioConteudo(
            tema="genero",
            motivo="Tema não permitido: identidade de gênero/orientação sexual.",
        )

    return None


def resposta_bloqueio(bloqueio: BloqueioConteudo, tipo_usuario: str) -> str:
    rotulo = bloqueio.motivo.lower().replace("tema não permitido: ", "").rstrip(".")
    if tipo_usuario == "aluno":
        return (
            "Oi! Eu sou o Profinho — aquele livrinho que adora ajudar nos estudos! "
            "Posso te ajudar com matérias, dúvidas e exercícios. "
            f"Sobre {rotulo}, prefiro não entrar nesse assunto aqui. "
            "Bora voltar pro conteúdo da aula? Me conta o que você está estudando!"
        )

    return (
        "Opa! Sou o Profinho, seu parceiro pedagógico de bolso. "
        f"{bloqueio.motivo} Esse tipo de assunto não rola por aqui — "
        "quero manter um ambiente seguro e focado na escola. "
        "Mas planos de aula, exercícios, explicações de conteúdo? "
        "Nisso eu sou expert — manda ver!"
    )
