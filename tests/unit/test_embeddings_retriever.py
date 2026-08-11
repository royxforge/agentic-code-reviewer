from agentic_code_reviewer.config.settings import Settings
from agentic_code_reviewer.retrieval.embeddings import LocalEmbedder
from agentic_code_reviewer.retrieval.retriever import Retriever


def test_local_embedder_deterministic_and_normalized():
    embedder = LocalEmbedder(dimension=256)
    texts = ["def search_users(query): sql injection", "class Order: totals"]
    a = embedder.embed_texts(texts)
    b = embedder.embed_texts(texts)
    assert a == b, "embedding must be deterministic"
    for vector in a:
        norm = sum(v * v for v in vector) ** 0.5
        assert abs(norm - 1.0) < 1e-6


def test_local_embedder_differentiates_semantics():
    embedder = LocalEmbedder(dimension=256)
    docs = [
        "function that builds sql queries from user input",
        "function that formats currency values",
    ]
    embedder.fit(docs)
    sql_q = embedder.embed_texts(["sql query injection from user input"])
    currency_q = embedder.embed_texts(["format currency dollar amount"])

    def sim(a, b):
        return sum(x * y for x, y in zip(a, b, strict=False))

    assert sim(sql_q[0], embedder.embed_texts([docs[0]])[0]) > sim(
        sql_q[0], embedder.embed_texts([docs[1]])[0]
    )
    assert sim(currency_q[0], embedder.embed_texts([docs[1]])[0]) > sim(
        currency_q[0], embedder.embed_texts([docs[0]])[0]
    )


def test_retriever_returns_relevant_chunk_first():
    settings = Settings(LLM_PROVIDER="mock", EMBEDDING_PROVIDER="local", LOCAL_EMBEDDING_DIM=256)
    retriever = Retriever(settings)
    files = {
        "db.py": (
            'def search_users(query):\n'
            '    sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n'
            "    return _db.execute(sql)\n"
        ),
        "cart.py": "def add_item(cart, item):\n    cart.append(item)\n    return len(cart)\n",
        "util.py": "def format_price(value):\n    return f'${value:.2f}'\n",
    }
    retriever.index_repository(files, repository="sample")
    assert retriever.is_ready
    results = retriever.retrieve("sql query built from user input", top_k=2)
    assert results
    assert results[0].file_path == "db.py"
    assert results[0].score > 0
    assert len(results) == 2


def test_retriever_empty_repo_not_ready():
    settings = Settings(LLM_PROVIDER="mock")
    retriever = Retriever(settings)
    retriever.index_repository({}, repository="empty")
    assert not retriever.is_ready
    assert retriever.retrieve("anything") == []
