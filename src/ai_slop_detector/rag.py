from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class RAGIndex:
    """ChromaDB-backed vector index for per-repo guideline context.

    Uses sentence-transformers all-MiniLM-L6-v2 (256-token context window,
    ~1000 chars) so chunks are targeted at 800-1000 chars.
    """

    _CHUNK_TARGET = 900
    _CHUNK_MIN = 200
    _CHUNK_HARD_CAP = 1500

    def __init__(self, persist_dir: Path | str = "data/chroma") -> None:
        import chromadb
        from sentence_transformers import SentenceTransformer

        self._chroma = chromadb.PersistentClient(path=str(persist_dir))
        self._model = SentenceTransformer("all-MiniLM-L6-v2")

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_repo(
        self,
        repo_name: str,
        contributing_md: str | None,
        agents_md: str | None,
        merged_prs: list[dict[str, Any]],
    ) -> int:
        """Index repo context into ChromaDB; returns number of chunks upserted."""
        collection = self._chroma.get_or_create_collection(
            name=_collection_name(repo_name)
        )
        # Wipe existing chunks so a re-index is idempotent.
        existing = collection.get()
        if existing["ids"]:
            collection.delete(ids=existing["ids"])

        chunks: list[str] = []
        chunk_ids: list[str] = []
        metadatas: list[dict[str, str]] = []

        for source_name, content in [
            ("CONTRIBUTING.md", contributing_md),
            ("AGENTS.md", agents_md),
        ]:
            if not content:
                continue
            for i, chunk in enumerate(_chunk_document(content, source_name)):
                cid = f"{source_name}:{i}"
                chunks.append(chunk)
                chunk_ids.append(cid)
                metadatas.append({"source": source_name, "chunk_index": str(i)})

        pr_offset = len(chunks)
        for j, pr in enumerate(merged_prs):
            title = pr.get("title", "")
            body = pr.get("body", "") or ""
            text = f"PR: {title}\n\n{body}".strip()
            if not text:
                continue
            # Each PR title+body is one chunk (truncated to hard cap if needed).
            text = text[: self._CHUNK_HARD_CAP]
            cid = f"merged-pr:{pr_offset + j}"
            chunks.append(text)
            chunk_ids.append(cid)
            metadatas.append({"source": "merged-pr", "chunk_index": str(j)})

        if not chunks:
            return 0

        embeddings = self._model.encode(chunks).tolist()
        collection.upsert(
            documents=chunks,
            ids=chunk_ids,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        return len(chunks)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(self, repo_name: str, query: str, top_k: int = 8) -> list[str]:
        """Return the top-k most relevant chunks for the query.

        Returns an empty list if the repo has not been indexed yet rather
        than raising, so the pipeline degrades gracefully.
        """
        try:
            collection = self._chroma.get_collection(name=_collection_name(repo_name))
        except Exception:
            return []

        n = min(top_k, collection.count())
        if n == 0:
            return []

        query_embedding = self._model.encode([query]).tolist()
        results = collection.query(query_embeddings=query_embedding, n_results=n)
        docs: list[str] = results["documents"][0]
        ids: list[str] = results["ids"][0]
        # Prefix each chunk with its ID so the critic can cite it.
        return [f"[{cid}] {doc}" for cid, doc in zip(ids, docs)]


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _collection_name(repo_name: str) -> str:
    """ChromaDB collection names must be alphanumeric + hyphens, 3-63 chars."""
    safe = re.sub(r"[^a-zA-Z0-9-]", "-", repo_name)
    # Ensure minimum length requirement
    if len(safe) < 3:
        safe = safe + "---"
    return safe[:63]


def _chunk_document(text: str, source: str) -> list[str]:
    """Split a markdown document into semantically coherent chunks.

    Strategy:
    - Primary split on blank lines (\\n\\n), keeping markdown headings.
    - Merge adjacent short paragraphs until chunk reaches _CHUNK_MIN chars.
    - Split overly long paragraphs further on sentence boundaries.
    - Prefix every chunk with its nearest #/## heading so embeddings can
      disambiguate e.g. '## Testing' vs '## Naming Conventions'.
    """
    paragraphs = re.split(r"\n{2,}", text.strip())
    current_heading = ""
    chunks: list[str] = []
    buffer = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Track the most recent heading.
        heading_match = re.match(r"^(#{1,3} .+)", para)
        if heading_match:
            current_heading = heading_match.group(1)

        # Attach the current heading as prefix so each chunk is self-contained.
        prefixed = f"{current_heading}\n{para}" if current_heading else para

        if len(prefixed) > RAGIndex._CHUNK_HARD_CAP:
            # Flush buffer first.
            if buffer:
                chunks.extend(_split_long(buffer, RAGIndex._CHUNK_TARGET))
                buffer = ""
            chunks.extend(_split_long(prefixed, RAGIndex._CHUNK_TARGET))
            continue

        candidate = (buffer + "\n\n" + prefixed).strip() if buffer else prefixed
        if len(candidate) >= RAGIndex._CHUNK_TARGET:
            chunks.append(candidate[: RAGIndex._CHUNK_HARD_CAP])
            buffer = ""
        elif len(candidate) >= RAGIndex._CHUNK_MIN and len(candidate) < RAGIndex._CHUNK_TARGET:
            # Good size — emit only once we'd overflow the next addition.
            buffer = candidate
        else:
            buffer = candidate

    if buffer:
        chunks.append(buffer[: RAGIndex._CHUNK_HARD_CAP])

    return chunks or [text[: RAGIndex._CHUNK_HARD_CAP]]


def _split_long(text: str, target: int) -> list[str]:
    """Split a long string on sentence boundaries, falling back to hard split."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    buf = ""
    for sentence in sentences:
        candidate = (buf + " " + sentence).strip()
        if len(candidate) > target and buf:
            chunks.append(buf[: RAGIndex._CHUNK_HARD_CAP])
            buf = sentence
        else:
            buf = candidate
    if buf:
        chunks.append(buf[: RAGIndex._CHUNK_HARD_CAP])
    return chunks or [text[: RAGIndex._CHUNK_HARD_CAP]]
