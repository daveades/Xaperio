import json
import math
import os
import urllib.error
import urllib.request

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
VECTOR_DIMENSIONS = 384
MAX_BATCH_SIZE = 32
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RERANK_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANK_BATCH_SIZE = 16
QA_MODEL_NAME = "deepset/minilm-uncased-squad2"
QA_BATCH_SIZE = 8


class EmbeddingClientError(RuntimeError):
    pass


class AnswerClientError(RuntimeError):
    pass


def _post(path, payload, timeout, expected_model=MODEL_NAME, expected_dimensions=VECTOR_DIMENSIONS):
    endpoint = os.environ.get("EMBEDDING_URL", "http://127.0.0.1:8001").rstrip("/")
    try:
        request = urllib.request.Request(
            f"{endpoint}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_data = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, TimeoutError, ValueError, urllib.error.HTTPError, urllib.error.URLError) as error:
        raise EmbeddingClientError("The embedding service is unavailable.") from error
    if len(response_data) > MAX_RESPONSE_BYTES:
        raise EmbeddingClientError("The embedding service response is too large.")
    try:
        result = json.loads(response_data)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EmbeddingClientError("The embedding service returned invalid JSON.") from error
    if not isinstance(result, dict):
        raise EmbeddingClientError("The embedding service returned an invalid response.")
    if result.get("model") != expected_model:
        raise EmbeddingClientError("The embedding service returned an incompatible model.")
    if expected_dimensions is not None and result.get("dimensions") != expected_dimensions:
        raise EmbeddingClientError("The embedding service returned an incompatible model.")
    return result


def _validate_vector(vector):
    if not isinstance(vector, list) or len(vector) != VECTOR_DIMENSIONS:
        raise EmbeddingClientError("The embedding service returned an invalid vector.")
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        for value in vector
    ):
        raise EmbeddingClientError("The embedding service returned an invalid vector.")
    return vector


def embed_documents(texts):
    if not isinstance(texts, list) or not texts or len(texts) > MAX_BATCH_SIZE:
        raise ValueError(f"texts must contain between 1 and {MAX_BATCH_SIZE} items")
    result = _post("/embed/documents", {"texts": texts}, 120)
    vectors = result.get("vectors")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise EmbeddingClientError("The embedding service returned an unexpected number of vectors.")
    return [_validate_vector(vector) for vector in vectors]


def embed_query(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must not be empty")
    result = _post("/embed/query", {"text": text}, 5)
    return _validate_vector(result.get("vector"))


def rerank(question, passages):
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be empty")
    if (
        not isinstance(passages, list)
        or not passages
        or any(not isinstance(passage, str) or not passage.strip() for passage in passages)
    ):
        raise ValueError("passages must contain non-empty text")
    scores = []
    for start in range(0, len(passages), RERANK_BATCH_SIZE):
        batch = passages[start : start + RERANK_BATCH_SIZE]
        result = _post(
            "/rerank",
            {"question": question, "passages": batch},
            30,
            RERANK_MODEL_NAME,
            None,
        )
        batch_scores = result.get("scores")
        if not isinstance(batch_scores, list) or len(batch_scores) != len(batch):
            raise EmbeddingClientError("The embedding service returned an unexpected number of scores.")
        if any(
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(score)
            for score in batch_scores
        ):
            raise EmbeddingClientError("The embedding service returned an invalid score.")
        scores.extend(float(score) for score in batch_scores)
    return scores


def _validate_answer(passage, answer):
    if not isinstance(answer, dict):
        raise AnswerClientError("The answer service returned an invalid answer.")
    text = answer.get("answer")
    score = answer.get("score")
    start = answer.get("start")
    end = answer.get("end")
    if (
        not isinstance(text, str)
        or not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not math.isfinite(score)
    ):
        raise AnswerClientError("The answer service returned an invalid answer.")
    if not text:
        if start is not None or end is not None:
            raise AnswerClientError("The answer service returned invalid answer offsets.")
        return {"answer": "", "score": float(score), "start": None, "end": None}
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
        or end > len(passage)
        or passage[start:end] != text
    ):
        raise AnswerClientError("The answer service returned invalid answer offsets.")
    return {"answer": text, "score": float(score), "start": start, "end": end}


def extract_answers(question, passages):
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be empty")
    if (
        not isinstance(passages, list)
        or not passages
        or any(not isinstance(passage, str) or not passage.strip() for passage in passages)
    ):
        raise ValueError("passages must contain non-empty text")
    question = question.strip()
    passages = [passage.strip() for passage in passages]
    answers = []
    for start in range(0, len(passages), QA_BATCH_SIZE):
        batch = passages[start : start + QA_BATCH_SIZE]
        try:
            result = _post(
                "/answer",
                {"question": question, "passages": batch},
                30,
                QA_MODEL_NAME,
                None,
            )
        except EmbeddingClientError as error:
            raise AnswerClientError("The answer service is unavailable.") from error
        batch_answers = result.get("answers")
        if not isinstance(batch_answers, list) or len(batch_answers) != len(batch):
            raise AnswerClientError("The answer service returned an unexpected number of answers.")
        answers.extend(
            _validate_answer(passage, answer)
            for passage, answer in zip(batch, batch_answers)
        )
    return answers
