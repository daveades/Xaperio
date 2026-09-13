import math

from flask import Flask, jsonify, request
from sentence_transformers import CrossEncoder, SentenceTransformer
from transformers import pipeline

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
VECTOR_DIMENSIONS = 384
MAX_BATCH_SIZE = 32
MAX_TEXT_CHARACTERS = 5000
MAX_REQUEST_BYTES = 1_000_000
RERANK_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANK_BATCH_SIZE = 16
QA_MODEL_NAME = "deepset/minilm-uncased-squad2"
QA_BATCH_SIZE = 8

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
model = SentenceTransformer(MODEL_NAME, device="cpu")
reranker = CrossEncoder(RERANK_MODEL_NAME, device="cpu")
answerer = pipeline("question-answering", model=QA_MODEL_NAME, tokenizer=QA_MODEL_NAME, device=-1)



def validate_text(value):
    if not isinstance(value, str):
        raise ValueError("text must be a string")
    value = value.strip()
    if not value:
        raise ValueError("text must not be empty")
    if len(value) > MAX_TEXT_CHARACTERS:
        raise ValueError(f"text must not exceed {MAX_TEXT_CHARACTERS} characters")
    return value


def embed_texts(texts):
    vectors = model.encode(texts, normalize_embeddings=True).tolist()
    if any(len(vector) != VECTOR_DIMENSIONS for vector in vectors):
        raise RuntimeError("embedding model returned an unexpected vector size")
    return vectors


@app.get("/health")
def health():
    return {
        "ok": True,
        "model": MODEL_NAME,
        "dimensions": VECTOR_DIMENSIONS,
        "reranker": RERANK_MODEL_NAME,
        "answerer": QA_MODEL_NAME,
    }


@app.post("/embed/documents")
def embed_documents():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return {"error": "request body must be a JSON object"}, 400
    texts = data.get("texts")
    if not isinstance(texts, list):
        return {"error": "texts must be a list"}, 400
    if not texts:
        return {"error": "texts must not be empty"}, 400
    if len(texts) > MAX_BATCH_SIZE:
        return {"error": f"texts must contain at most {MAX_BATCH_SIZE} items"}, 400
    try:
        texts = [validate_text(text) for text in texts]
    except ValueError as error:
        return {"error": str(error)}, 400
    vectors = embed_texts(texts)
    return jsonify(
        {
            "model": MODEL_NAME,
            "dimensions": VECTOR_DIMENSIONS,
            "vectors": vectors,
        }
    )


@app.post("/embed/query")
def embed_query():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return {"error": "request body must be a JSON object"}, 400
    try:
        text = validate_text(data.get("text"))
    except ValueError as error:
        return {"error": str(error)}, 400
    vector = embed_texts([text])[0]
    return jsonify(
        {
            "model": MODEL_NAME,
            "dimensions": VECTOR_DIMENSIONS,
            "vector": vector,
        }
    )


@app.post("/rerank")
def rerank_passages():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return {"error": "reranking is only available locally"}, 403
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return {"error": "request body must be a JSON object"}, 400
    passages = data.get("passages")
    if not isinstance(passages, list):
        return {"error": "passages must be a list"}, 400
    if not passages:
        return {"error": "passages must not be empty"}, 400
    if len(passages) > RERANK_BATCH_SIZE:
        return {"error": f"passages must contain at most {RERANK_BATCH_SIZE} items"}, 400
    try:
        question = validate_text(data.get("question"))
        passages = [validate_text(passage) for passage in passages]
    except ValueError as error:
        return {"error": str(error)}, 400
    scores = [
        float(score)
        for score in reranker.predict(
            [(question, passage) for passage in passages],
            batch_size=RERANK_BATCH_SIZE,
            show_progress_bar=False,
        )
    ]
    if len(scores) != len(passages) or any(not math.isfinite(score) for score in scores):
        raise RuntimeError("reranker returned invalid scores")
    return jsonify({"model": RERANK_MODEL_NAME, "scores": scores})


def _validated_answer(passage, result):
    if not isinstance(result, dict):
        raise RuntimeError("answer model returned an invalid result")
    answer = result.get("answer")
    raw_score = result.get("score")
    if not isinstance(answer, str) or isinstance(raw_score, bool):
        raise RuntimeError("answer model returned an invalid result")
    try:
        score = float(raw_score)
    except (TypeError, ValueError) as error:
        raise RuntimeError("answer model returned an invalid score") from error
    if not math.isfinite(score):
        raise RuntimeError("answer model returned an invalid score")
    if not answer:
        return {"answer": "", "score": score, "start": None, "end": None}
    start = result.get("start")
    end = result.get("end")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
        or end > len(passage)
        or passage[start:end] != answer
    ):
        raise RuntimeError("answer model returned invalid offsets")
    return {"answer": answer, "score": score, "start": start, "end": end}


@app.post("/answer")
def answer_passages():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return {"error": "answer extraction is only available locally"}, 403
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return {"error": "request body must be a JSON object"}, 400
    passages = data.get("passages")
    if not isinstance(passages, list):
        return {"error": "passages must be a list"}, 400
    if not passages:
        return {"error": "passages must not be empty"}, 400
    if len(passages) > QA_BATCH_SIZE:
        return {"error": f"passages must contain at most {QA_BATCH_SIZE} items"}, 400
    try:
        question = validate_text(data.get("question"))
        passages = [validate_text(passage) for passage in passages]
    except ValueError as error:
        return {"error": str(error)}, 400
    results = answerer(
        [{"question": question, "context": passage} for passage in passages],
        batch_size=QA_BATCH_SIZE,
        handle_impossible_answer=True,
    )
    if not isinstance(results, list) or len(results) != len(passages):
        raise RuntimeError("answer model returned an unexpected number of results")
    answers = [_validated_answer(passage, result) for passage, result in zip(passages, results)]
    return jsonify({"model": QA_MODEL_NAME, "answers": answers})
