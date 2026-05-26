import os
import logging
from pathlib import Path
from typing import List, Optional

try:
    from langchain_chroma import Chroma
    from langchain_community.document_loaders import DirectoryLoader, TextLoader
    from langchain_openai import OpenAIEmbeddings
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    _RAG_IMPORT_ERROR: Optional[Exception] = None
except ModuleNotFoundError as exc:
    Chroma = None
    DirectoryLoader = None
    TextLoader = None
    OpenAIEmbeddings = None
    RecursiveCharacterTextSplitter = None
    _RAG_IMPORT_ERROR = exc

from app.core.models import Product, Profile


logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_KNOWLEDGE_DIR = APP_DIR / "knowledge"
DEFAULT_CHROMA_DIR = APP_DIR / "chroma_db"

_vectorstore: Optional[Chroma] = None


def _rag_dependencies_available() -> bool:
    return all([
        Chroma is not None,
        DirectoryLoader is not None,
        TextLoader is not None,
        OpenAIEmbeddings is not None,
        RecursiveCharacterTextSplitter is not None,
    ])


def get_knowledge_dir() -> Path:
    configured = os.getenv("RAG_DOCS_DIR")
    if configured:
        return Path(configured)
    return DEFAULT_KNOWLEDGE_DIR


def get_chroma_dir() -> Path:
    configured = os.getenv("CHROMA_DB_DIR")
    if configured:
        return Path(configured)
    return DEFAULT_CHROMA_DIR


def _get_embeddings() -> OpenAIEmbeddings:
    if not _rag_dependencies_available():
        raise RuntimeError(f"RAG dependencies unavailable: {_RAG_IMPORT_ERROR}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 없습니다. OpenAI 임베딩을 사용하려면 .env에 OPENAI_API_KEY를 설정해야 합니다.")

    return OpenAIEmbeddings(
        model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        api_key=api_key,
    )


def _load_documents():
    if not _rag_dependencies_available():
        raise RuntimeError(f"RAG dependencies unavailable: {_RAG_IMPORT_ERROR}")

    knowledge_dir = get_knowledge_dir()

    if not knowledge_dir.exists():
        return []

    loader = DirectoryLoader(
        str(knowledge_dir),
        glob="**/*.md",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
        show_progress=False,
    )

    docs = loader.load()
    return docs


def build_vectorstore(force_rebuild: bool = False) -> Chroma:
    global _vectorstore

    if not _rag_dependencies_available():
        raise RuntimeError(f"RAG dependencies unavailable: {_RAG_IMPORT_ERROR}")

    chroma_dir = get_chroma_dir()

    if _vectorstore is not None and not force_rebuild:
        return _vectorstore

    if chroma_dir.exists() and not force_rebuild:
        _vectorstore = Chroma(
            persist_directory=str(chroma_dir),
            embedding_function=_get_embeddings(),
        )
        return _vectorstore

    documents = _load_documents()

    if not documents:
        raise RuntimeError("RAG 문서가 없습니다. app/knowledge 폴더에 .md 파일을 넣어야 합니다.")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=int(os.getenv("RAG_CHUNK_SIZE", "500")),
        chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", "80")),
        separators=["\n\n", "\n", ".", " ", ""],
    )

    chunks = splitter.split_documents(documents)

    if not chunks:
        raise RuntimeError("RAG chunk 생성 실패")

    _vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=_get_embeddings(),
        persist_directory=str(chroma_dir),
    )

    return _vectorstore


def reset_rag_cache() -> None:
    global _vectorstore
    _vectorstore = None


def rebuild_rag_index() -> None:
    build_vectorstore(force_rebuild=True)


def _build_query(
    message: str,
    profile: Optional[Profile],
    product: Optional[Product],
) -> str:
    parts = [message]

    if profile:
        allergies = profile.resolved_allergies()
        diets = profile.resolved_special_diets()
        conditions = profile.conditions

        if allergies:
            parts.append("사용자 알레르기: " + ", ".join(allergies))
        if diets:
            parts.append("사용자 식이습관: " + ", ".join(diets))
        if conditions:
            parts.append("사용자 질환/건강상태: " + ", ".join(conditions))

    if product:
        if product.product_name:
            parts.append("제품명: " + product.product_name)

        ingredients = product.rawmtrl_nm or ", ".join(product.ingredients)
        if ingredients:
            parts.append("원재료명: " + ingredients)

        allergies = product.resolved_allergies()
        if allergies:
            parts.append("제품 알레르기 성분: " + ", ".join(allergies))

        if product.nutrition:
            nutrition_text = ", ".join(
                f"{key}: {value}" for key, value in product.nutrition.items()
            )
            parts.append("영양정보: " + nutrition_text)

    return "\n".join(parts)


def get_rag_context(
    message: str,
    profile: Optional[Profile] = None,
    product: Optional[Product] = None,
    top_k: int = 3,
) -> List[str]:
    try:
        vectorstore = build_vectorstore()
        query = _build_query(message=message, profile=profile, product=product)

        docs = vectorstore.similarity_search(query, k=top_k)

        results = []
        for doc in docs:
            source_path = doc.metadata.get("source", "unknown")
            source_name = Path(source_path).name
            text = doc.page_content.strip().replace("\n", " ")
            results.append(f"[{source_name}] {text}")

        return results

    except Exception as exc:
        logger.warning("RAG context unavailable: %s", exc)
        return []
