"""Reserve each physical embedding attempt, including SDK-level retries."""
import json

import httpx

from app.infrastructure.external_ai_state import reserve_call
from app.infrastructure.external_ai_consent import mark_external_ai_used


def _validate(request, endpoint, model):
    if request.method != 'POST' or str(request.url) != endpoint:
        raise ValueError('Unexpected embedding endpoint')
    if len(request.content) > 65536:
        raise ValueError('Embedding request exceeds the byte limit')
    payload = json.loads(request.content)
    if not isinstance(payload, dict) or payload.get('model') != model:
        raise ValueError('Unexpected embedding payload')


class BudgetedEmbeddingTransport(httpx.HTTPTransport):
    def __init__(self, *, endpoint='https://api.openai.com/v1/embeddings',
                 model='text-embedding-3-small'):
        super().__init__(retries=0, trust_env=False)
        self.endpoint, self.model = endpoint, model

    def handle_request(self, request):
        _validate(request, self.endpoint, self.model)
        reserve_call('embedding')
        mark_external_ai_used('rag')
        return super().handle_request(request)


class BudgetedAsyncEmbeddingTransport(httpx.AsyncHTTPTransport):
    def __init__(self, *, endpoint, model):
        super().__init__(retries=0, trust_env=False)
        self.endpoint, self.model = endpoint, model

    async def handle_async_request(self, request):
        _validate(request, self.endpoint, self.model)
        # SQLite is bounded by a five-second timeout; avoid blocking the loop.
        import asyncio
        await asyncio.to_thread(reserve_call, 'embedding')
        mark_external_ai_used('rag')
        return await super().handle_async_request(request)
