from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from pr_triage.rag import RAGIndex, _chunk_document, _collection_name, _split_long


# ------------------------------------------------------------------
# RAGIndex — constructor and methods (chromadb + sentence_transformers mocked)
# ------------------------------------------------------------------

@pytest.fixture
def rag(tmp_path):
    """RAGIndex with chromadb and SentenceTransformer fully mocked."""
    mock_chroma = MagicMock()
    mock_st = MagicMock()

    with patch.dict(sys.modules, {
        "chromadb": mock_chroma,
        "sentence_transformers": MagicMock(SentenceTransformer=mock_st),
    }):
        import importlib
        import pr_triage.rag as rag_mod
        importlib.reload(rag_mod)
        instance = rag_mod.RAGIndex(persist_dir=tmp_path)
        instance._chroma = mock_chroma.PersistentClient.return_value
        instance._model = mock_st.return_value
        yield instance

    # Reload to restore real module state after test
    importlib.reload(rag_mod)


def test_rag_retrieve_missing_collection_returns_empty(tmp_path):
    """retrieve() returns [] gracefully when the collection does not exist."""
    mock_chroma_mod = MagicMock()
    mock_client = MagicMock()
    mock_chroma_mod.PersistentClient.return_value = mock_client
    mock_client.get_collection.side_effect = Exception("no such collection")

    with patch.dict(sys.modules, {
        "chromadb": mock_chroma_mod,
        "sentence_transformers": MagicMock(SentenceTransformer=MagicMock()),
    }):
        import importlib
        import pr_triage.rag as rag_mod
        importlib.reload(rag_mod)
        rag = rag_mod.RAGIndex(persist_dir=tmp_path)
        result = rag.retrieve("owner/repo", "query string")
    assert result == []
    importlib.reload(rag_mod)


def test_rag_index_repo_returns_zero_on_empty_content(tmp_path):
    """index_repo() returns 0 when all sources are None/empty."""
    mock_chroma_mod = MagicMock()
    mock_collection = MagicMock()
    mock_collection.get.return_value = {"ids": []}
    mock_chroma_mod.PersistentClient.return_value.get_or_create_collection.return_value = mock_collection

    with patch.dict(sys.modules, {
        "chromadb": mock_chroma_mod,
        "sentence_transformers": MagicMock(SentenceTransformer=MagicMock()),
    }):
        import importlib
        import pr_triage.rag as rag_mod
        importlib.reload(rag_mod)
        rag = rag_mod.RAGIndex(persist_dir=tmp_path)
        count = rag.index_repo("owner/repo", contributing_md=None, agents_md=None, merged_prs=[])
    assert count == 0
    importlib.reload(rag_mod)


def test_rag_retrieve_returns_prefixed_chunks(tmp_path):
    """retrieve() prefixes each chunk with its chunk ID."""
    mock_chroma_mod = MagicMock()
    mock_client = MagicMock()
    mock_chroma_mod.PersistentClient.return_value = mock_client
    mock_collection = MagicMock()
    mock_collection.count.return_value = 2
    mock_collection.query.return_value = {
        "documents": [["Use conventional commits.", "All PRs need tests."]],
        "ids": [["CONTRIBUTING.md:0", "CONTRIBUTING.md:1"]],
    }
    mock_client.get_collection.return_value = mock_collection
    mock_st = MagicMock()
    mock_st.return_value.encode.return_value = MagicMock(tolist=lambda: [[0.1, 0.2]])

    with patch.dict(sys.modules, {
        "chromadb": mock_chroma_mod,
        "sentence_transformers": MagicMock(SentenceTransformer=mock_st),
    }):
        import importlib
        import pr_triage.rag as rag_mod
        importlib.reload(rag_mod)
        rag = rag_mod.RAGIndex(persist_dir=tmp_path)
        results = rag.retrieve("owner/repo", "query")

    assert len(results) == 2
    assert results[0].startswith("[CONTRIBUTING.md:0]")
    assert results[1].startswith("[CONTRIBUTING.md:1]")
    importlib.reload(rag_mod)


# ------------------------------------------------------------------
# _collection_name
# ------------------------------------------------------------------

def test_collection_name_replaces_slash():
    assert _collection_name("owner/repo") == "owner-repo"


def test_collection_name_truncates_to_63_chars():
    long_name = "a" * 70
    result = _collection_name(long_name)
    assert len(result) <= 63


def test_collection_name_pads_short_names():
    result = _collection_name("ab")
    assert len(result) >= 3


def test_collection_name_strips_special_chars():
    result = _collection_name("owner/my.repo_name")
    assert "/" not in result
    assert "." not in result
    assert "_" not in result


# ------------------------------------------------------------------
# _split_long
# ------------------------------------------------------------------

def test_split_long_short_text_returns_single_chunk():
    text = "Short text that fits in target."
    result = _split_long(text, target=200)
    assert result == [text]


def test_split_long_splits_on_sentence_boundary():
    text = "First sentence. Second sentence. Third sentence."
    result = _split_long(text, target=20)
    assert len(result) > 1
    assert all(len(c) <= 1500 for c in result)


def test_split_long_handles_no_sentence_boundaries():
    text = "word " * 200  # no punctuation
    result = _split_long(text, target=100)
    assert len(result) >= 1
    assert all(len(c) <= 1500 for c in result)


# ------------------------------------------------------------------
# _chunk_document
# ------------------------------------------------------------------

def test_chunk_document_returns_at_least_one_chunk():
    text = "Some content here."
    result = _chunk_document(text, "CONTRIBUTING.md")
    assert len(result) >= 1


def test_chunk_document_short_doc_is_single_chunk():
    text = "Short contributing guide.\n\nJust a paragraph."
    result = _chunk_document(text, "CONTRIBUTING.md")
    assert len(result) == 1


def test_chunk_document_respects_hard_cap():
    long_para = "word " * 400  # ~2000 chars
    text = long_para
    result = _chunk_document(text, "CONTRIBUTING.md")
    assert all(len(c) <= 1500 for c in result)


def test_chunk_document_preserves_heading_prefix():
    text = "## Code Style\n\nUse PEP 8 and conventional commits.\n\nAlways write tests."
    result = _chunk_document(text, "CONTRIBUTING.md")
    # At least one chunk should contain the heading as prefix
    assert any("## Code Style" in chunk for chunk in result)


def test_chunk_document_merges_tiny_paragraphs():
    # Three tiny paragraphs that should be merged into fewer chunks than paragraphs
    text = "Para one.\n\nPara two.\n\nPara three.\n\nPara four.\n\nPara five."
    result = _chunk_document(text, "CONTRIBUTING.md")
    assert len(result) < 5  # should be merged


def test_chunk_document_long_doc_produces_multiple_chunks():
    # Build a doc that is clearly over the target chunk size
    section = "## Guidelines\n\n" + ("Follow all conventions carefully. " * 30) + "\n\n"
    text = section * 3
    result = _chunk_document(text, "CONTRIBUTING.md")
    assert len(result) > 1


def test_chunk_document_empty_paragraphs_skipped():
    text = "\n\n\n\nActual content here.\n\n\n\n"
    result = _chunk_document(text, "CONTRIBUTING.md")
    assert all(c.strip() for c in result)
