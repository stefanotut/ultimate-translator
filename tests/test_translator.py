"""Translation engine: request shape per model, batching, errors, EPUB/PDF output."""

import json
import os
import re
import time
import zipfile
from types import SimpleNamespace

import pytest

import translator
from samples import make_locked_pdf, make_rich_epub, make_scanned_pdf, make_text_pdf


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class StatusError(Exception):
    """Stands in for an SDK APIStatusError (the classifier only looks at these attributes)."""

    def __init__(self, status, message='error', code=None, param=None, headers=None):
        super().__init__(message)
        self.status_code = status
        self.message = message
        self.code = code
        self.param = param
        self.body = {'type': 'error', 'error': {'type': 'x', 'message': message}}
        self.response = SimpleNamespace(headers=headers or {})


def _segments(message):
    match = re.search(r'<segments>\n(.*)\n</segments>', message, re.S)
    return json.loads(match.group(1)) if match else None


def _single_text(message):
    marker = 'continuing naturally from the context above.\n\n'
    return message.split(marker, 1)[1] if marker in message else message


def fake_translate(text):
    return '[it] ' + text


def anthropic_response(text, stop_reason='end_turn', input_tokens=100, output_tokens=50, category=None):
    return SimpleNamespace(
        content=[SimpleNamespace(type='thinking', thinking=''), SimpleNamespace(type='text', text=text)],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if category else None,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeAnthropic:
    """Records every messages.create call; `handler(kwargs)` returns a response or raises."""

    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or self.translate_everything
        self.messages = self

    def with_options(self, **_kwargs):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.handler(kwargs)

    @staticmethod
    def translate_everything(kwargs):
        message = kwargs['messages'][0]['content']
        segments = _segments(message)
        if segments is not None:
            return anthropic_response(json.dumps({'translations': [fake_translate(s) for s in segments]}))
        return anthropic_response(fake_translate(_single_text(message)))


def openai_response(text, finish_reason='stop', refusal=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, refusal=refusal), finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=40),
    )


class FakeOpenAI:
    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or self.translate_everything
        self.chat = SimpleNamespace(completions=self)

    def with_options(self, **_kwargs):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.handler(kwargs)

    @staticmethod
    def translate_everything(kwargs):
        message = kwargs['messages'][1]['content']
        segments = _segments(message)
        if segments is not None:
            return openai_response(json.dumps({'translations': [fake_translate(s) for s in segments]}))
        return openai_response(fake_translate(_single_text(message)))


class Memory:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value


@pytest.fixture(autouse=True)
def clean_translator_state(monkeypatch):
    translator._dropped_features.clear()
    translator._no_batch_models.clear()
    translator._listings.clear()
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-key')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test-key')
    monkeypatch.setenv('LIBRARY_AI_TAGS', '0')
    monkeypatch.delenv('TRANSLATION_EFFORT', raising=False)
    yield
    translator._dropped_features.clear()
    translator._no_batch_models.clear()
    translator._listings.clear()


@pytest.fixture
def claude(monkeypatch):
    fake = FakeAnthropic()
    monkeypatch.setattr(translator, 'get_anthropic_client', lambda: fake)
    return fake


@pytest.fixture
def gpt(monkeypatch):
    fake = FakeOpenAI()
    monkeypatch.setattr(translator, 'get_openai_client', lambda: fake)
    return fake


def session(model='claude-sonnet-5', **kwargs):
    kwargs.setdefault('sleep', lambda seconds: None)
    return translator.TranslationSession('English', 'Italian', model, **kwargs)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def test_model_table_uses_current_models_only():
    retired = {'claude-haiku-3-5-20241022', 'claude-3-5-haiku-20241022', 'claude-sonnet-4-20250514',
               'claude-opus-4-20250514'}
    assert not retired & set(translator.MODEL_PRICING)
    anthropic_models = {m for m, info in translator.MODEL_PRICING.items() if info['provider'] == 'anthropic'}
    assert anthropic_models == {'claude-opus-5', 'claude-sonnet-5', 'claude-haiku-4-5'}
    for model_id, info in translator.MODEL_PRICING.items():
        assert {'provider', 'display_name', 'input_cost', 'output_cost', 'quality', 'speed'} <= set(info), model_id
        assert not ('temperature' in info and (info.get('effort') or info.get('reasoning'))), model_id
    for old, new in translator.MODEL_ALIASES.items():
        assert new in translator.MODEL_PRICING, old
    assert set(translator.DEFAULT_MODELS.values()) <= set(translator.MODEL_PRICING)
    assert set(translator.DETECTION_MODELS.values()) <= set(translator.MODEL_PRICING)


@pytest.mark.parametrize('requested,expected', [
    ('claude-sonnet-4-20250514', 'claude-sonnet-5'),
    ('claude-opus-4-20250514', 'claude-opus-5'),
    ('claude-haiku-3-5-20241022', 'claude-haiku-4-5'),
    ('claude-opus-5', 'claude-opus-5'),
    ('gpt-4.1', 'gpt-4.1'),
    ('  gpt-4o  ', 'gpt-4o'),
    ('claude-2.1', None),
    ('', None),
    (None, None),
])
def test_retired_model_ids_resolve_to_their_replacement(requested, expected):
    assert translator.resolve_model(requested) == expected


def test_unknown_model_is_a_fatal_error():
    with pytest.raises(translator.FatalTranslationError):
        session('claude-2.1')


# ---------------------------------------------------------------------------
# Request shape per model
# ---------------------------------------------------------------------------

def test_opus_5_request_has_no_sampling_parameters_and_uses_effort_and_fallbacks(claude):
    assert session('claude-opus-5').translate('Hello world') == '[it] Hello world'
    call = claude.calls[0]
    assert call['model'] == 'claude-opus-5'
    assert 'temperature' not in call and 'temperature' not in call.get('extra_body', {})
    assert call['output_config'] == {'effort': 'low'}
    assert call['extra_body'] == {'fallbacks': 'default'}
    assert call['extra_headers'] == {'anthropic-beta': translator.REFUSAL_FALLBACK_BETA}
    assert call['max_tokens'] == translator.MAX_OUTPUT_TOKENS
    assert 'SOURCE LANGUAGE: English' in call['system'] and 'TARGET LANGUAGE: Italian' in call['system']


def test_sonnet_5_request(claude):
    session('claude-sonnet-5').translate('Hello world')
    call = claude.calls[0]
    assert call['output_config'] == {'effort': 'low'}
    assert 'extra_body' not in call and 'extra_headers' not in call


def test_haiku_keeps_its_temperature_outside_the_typed_sdk_arguments(claude):
    session('claude-haiku-4-5').translate('Hello world')
    call = claude.calls[0]
    assert 'temperature' not in call  # removed from the SDK 1.x signature
    assert call['extra_body'] == {'temperature': 0.3}
    assert 'output_config' not in call  # Haiku 4.5 does not take effort


def test_effort_can_be_changed_from_the_environment(claude, monkeypatch):
    monkeypatch.setenv('TRANSLATION_EFFORT', 'medium')
    session('claude-opus-5').translate('Hello world')
    monkeypatch.setenv('TRANSLATION_EFFORT', 'bogus')
    session('claude-opus-5').translate('Another sentence')
    assert [c['output_config']['effort'] for c in claude.calls] == ['medium', 'low']


def test_batched_request_asks_for_structured_output(claude):
    result = session('claude-sonnet-5').translate_batch(['One apple.', 'Two <em>pears</em>.', '42'])
    assert result == ['[it] One apple.', '[it] Two <em>pears</em>.', '42']
    assert len(claude.calls) == 1
    call = claude.calls[0]
    assert call['output_config']['format']['type'] == 'json_schema'
    assert call['output_config']['effort'] == 'low'
    assert _segments(call['messages'][0]['content']) == ['One apple.', 'Two <em>pears</em>.']


def test_openai_models_get_the_parameters_they_accept(gpt):
    session('gpt-4.1').translate('Hello world')
    session('o3').translate('Hello world')
    session('gpt-4.1').translate_batch(['A cat.', 'A dog.'])
    standard, reasoning, batch = gpt.calls
    assert standard['temperature'] == 0.3 and standard['max_completion_tokens'] == translator.MAX_OUTPUT_TOKENS
    assert 'max_tokens' not in standard and 'reasoning_effort' not in standard
    assert reasoning['reasoning_effort'] == 'low' and 'temperature' not in reasoning
    assert batch['response_format']['type'] == 'json_schema'
    assert batch['response_format']['json_schema']['strict'] is True


# ---------------------------------------------------------------------------
# Batching, memory, context
# ---------------------------------------------------------------------------

def test_batches_are_split_by_size_and_carry_context(claude, monkeypatch):
    monkeypatch.setattr(translator, 'BATCH_MAX_SEGMENTS', 2)
    texts = ['First passage.', 'Second passage.', 'Third passage.', 'Fourth passage.', 'Fifth passage.']
    progress = []
    result = session().translate_batch(texts, on_progress=lambda done, total: progress.append((done, total)))
    assert result == [fake_translate(t) for t in texts]
    assert len(claude.calls) == 3
    assert progress == [(2, 5), (4, 5), (5, 5)]
    second = claude.calls[1]['messages'][0]['content']
    assert '<previous_translation>' in second and '[it] Second passage.' in second


def test_memory_reuses_translations_without_calling_the_api(claude):
    memory = Memory()
    first = session(memory=memory)
    first.translate_batch(['Alpha text.', 'Beta text.'])
    assert first.summary()['api_calls'] == 1

    second = session(memory=memory)
    assert second.translate_batch(['Alpha text.', 'Beta text.', 'Gamma text.']) == [
        '[it] Alpha text.', '[it] Beta text.', '[it] Gamma text.']
    assert second.remembered == 2 and second.api_calls == 1
    assert _segments(claude.calls[-1]['messages'][0]['content']) is None  # only one passage left: single request

    # A different model or language pair does not reuse them
    other = translator.TranslationSession('English', 'French', 'claude-sonnet-5', memory=memory)
    other.translate('Alpha text.')
    assert other.remembered == 0


def test_usage_and_cost_are_counted(claude):
    s = session('claude-opus-5')
    s.translate('Hello world')
    summary = s.summary()
    assert summary['input_tokens'] == 100 and summary['output_tokens'] == 50
    assert summary['cost'] == pytest.approx((100 * 5 + 50 * 25) / 1_000_000)


def test_code_fences_around_the_answer_are_removed(claude):
    claude.handler = lambda kwargs: anthropic_response('```\nCiao mondo\n```')
    assert session().translate('Hello world') == 'Ciao mondo'


# ---------------------------------------------------------------------------
# Passage-level problems: the rest of the book goes on
# ---------------------------------------------------------------------------

def test_a_refused_passage_keeps_its_text_and_the_others_are_translated(claude):
    def handler(kwargs):
        message = kwargs['messages'][0]['content']
        segments = _segments(message)
        if (segments and any('forbidden' in s for s in segments)) or (segments is None and 'forbidden' in message):
            return anthropic_response('', stop_reason='refusal', category='cyber')
        return FakeAnthropic.translate_everything(kwargs)

    claude.handler = handler
    notes = []
    s = session('claude-opus-5', on_note=notes.append)
    texts = ['One.', 'Two.', 'The forbidden one.', 'Four.', 'Five.']
    assert s.translate_batch(texts) == ['[it] One.', '[it] Two.', 'The forbidden one.', '[it] Four.', '[it] Five.']
    assert s.untranslated == 1 and s.passages == 5
    assert notes and 'cyber' in notes[0]


def test_a_batch_with_the_wrong_number_of_translations_is_retried_in_halves(claude):
    def handler(kwargs):
        segments = _segments(kwargs['messages'][0]['content'])
        if segments and len(segments) > 2:
            return anthropic_response(json.dumps({'translations': ['merged']}))
        return FakeAnthropic.translate_everything(kwargs)

    claude.handler = handler
    texts = [f'Passage number {i}.' for i in range(6)]
    assert session().translate_batch(texts) == [fake_translate(t) for t in texts]


def test_invalid_json_is_retried_in_halves(claude):
    def handler(kwargs):
        segments = _segments(kwargs['messages'][0]['content'])
        if segments and len(segments) > 1:
            return anthropic_response('{"translations": ["broken"')
        return FakeAnthropic.translate_everything(kwargs)

    claude.handler = handler
    texts = ['Uno.', 'Due.', 'Tre.']
    assert session().translate_batch(texts) == [fake_translate(t) for t in texts]


def test_truncated_plain_text_is_split_and_translated_in_parts(claude):
    long_text = ('This is a sentence about sales. ' * 10 + '\n\n') * 4

    def handler(kwargs):
        text = _single_text(kwargs['messages'][0]['content'])
        if len(text) > 700:
            return anthropic_response('partial', stop_reason='max_tokens')
        return anthropic_response(fake_translate(text))

    claude.handler = handler
    result = session().translate(long_text)
    assert result.count('[it] ') >= 2
    assert 'partial' not in result


def test_empty_translations_inside_a_batch_are_redone_one_by_one(claude):
    def handler(kwargs):
        segments = _segments(kwargs['messages'][0]['content'])
        if segments:
            return anthropic_response(json.dumps({'translations': [fake_translate(segments[0]), '']}))
        return FakeAnthropic.translate_everything(kwargs)

    claude.handler = handler
    assert session().translate_batch(['Red.', 'Blue.']) == ['[it] Red.', '[it] Blue.']


# ---------------------------------------------------------------------------
# Errors that stop the job, errors that are retried
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('error,expected', [
    (StatusError(401, 'invalid x-api-key'), 'Chiave API Anthropic non valida'),
    (StatusError(404, 'model: claude-sonnet-5'), 'non e disponibile'),
    (StatusError(402, 'billing'), 'Credito Anthropic esaurito'),
    (StatusError(403, 'no access'), 'non ha accesso'),
    (TypeError("create() got an unexpected keyword argument 'x'"), 'pip install -U -r requirements.txt'),
])
def test_permanent_errors_stop_the_job_with_a_clear_message(claude, error, expected):
    def handler(kwargs):
        raise error

    claude.handler = handler
    with pytest.raises(translator.FatalTranslationError, match=expected):
        session().translate('Hello world')
    assert len(claude.calls) == 1  # no pointless retries


def test_openai_without_credit_stops_at_once(gpt):
    def handler(kwargs):
        raise StatusError(429, 'You exceeded your current quota', code='insufficient_quota')

    gpt.handler = handler
    with pytest.raises(translator.FatalTranslationError, match='Credito OpenAI esaurito'):
        session('gpt-4.1').translate('Hello world')
    assert len(gpt.calls) == 1


def test_temporary_errors_are_retried_with_pauses(claude):
    failures = [StatusError(529, 'Overloaded'), StatusError(429, 'rate limited', headers={'retry-after': '40'}),
                ConnectionError('reset')]

    def handler(kwargs):
        if failures:
            raise failures.pop(0)
        return FakeAnthropic.translate_everything(kwargs)

    claude.handler = handler
    waits, pauses = [], []
    s = session(on_wait=waits.append, sleep=pauses.append)
    assert s.translate('Hello world') == '[it] Hello world'
    assert len(pauses) == 3 and pauses[1] >= 40  # retry-after is honoured
    assert 'sovraccarico' in waits[0] and 'rate limit' in waits[1] and 'rete' in waits[2]


def test_a_provider_down_for_minutes_stops_the_job(claude):
    def handler(kwargs):
        raise StatusError(503, 'unavailable')

    claude.handler = handler
    pauses = []
    with pytest.raises(translator.FatalTranslationError, match='non risponde'):
        session(sleep=pauses.append).translate('Hello world')
    assert len(pauses) == len(translator.TRANSIENT_DELAYS)


def test_a_refused_beta_feature_is_dropped_only_after_a_request_works_without_it(claude):
    def handler(kwargs):
        if 'fallbacks' in kwargs.get('extra_body', {}):
            raise StatusError(400, 'Unexpected value(s) for the anthropic-beta header')
        return FakeAnthropic.translate_everything(kwargs)

    claude.handler = handler
    s = session('claude-opus-5')
    assert s.translate('Hello world') == '[it] Hello world'
    assert s.translate('Second text') == '[it] Second text'
    # 1st passage: refused, then fine without fallbacks; 2nd passage: sent without them at once
    assert len(claude.calls) == 3
    assert 'extra_body' not in claude.calls[2] and claude.calls[2]['output_config'] == {'effort': 'low'}


def test_a_request_the_api_always_refuses_stops_the_job_without_dropping_features(claude):
    def handler(kwargs):
        raise StatusError(400, 'Your credit balance is too low to access the Anthropic API')

    claude.handler = handler
    with pytest.raises(translator.FatalTranslationError, match='credit balance is too low'):
        session('claude-opus-5').translate_batch(['One.', 'Two.'])
    assert translator._dropped('claude-opus-5') == set()
    assert 'claude-opus-5' not in translator._no_batch_models


def test_models_that_refuse_batched_requests_translate_one_passage_at_a_time(claude):
    def handler(kwargs):
        if 'format' in kwargs.get('output_config', {}):
            raise StatusError(400, 'output_config.format: not supported')
        return FakeAnthropic.translate_everything(kwargs)

    claude.handler = handler
    s = session('claude-sonnet-5')
    assert s.translate_batch(['One.', 'Two.', 'Three.']) == ['[it] One.', '[it] Two.', '[it] Three.']
    assert 'claude-sonnet-5' in translator._no_batch_models
    calls = len(claude.calls)
    s.translate_batch(['Four.', 'Five.'])
    assert len(claude.calls) == calls + 2
    assert all('format' not in c.get('output_config', {}) for c in claude.calls[calls:])


def test_openai_parameters_refused_by_a_model_are_dropped(gpt):
    def handler(kwargs):
        if 'temperature' in kwargs:
            raise StatusError(400, "Unsupported value: 'temperature'", code='unsupported_value', param='temperature')
        return FakeOpenAI.translate_everything(kwargs)

    gpt.handler = handler
    assert session('gpt-4o').translate('Hello world') == '[it] Hello world'
    assert session('gpt-4o').translate('Again') == '[it] Again'
    assert 'temperature' not in gpt.calls[-1]
    assert len(gpt.calls) == 3


# ---------------------------------------------------------------------------
# The real SDKs accept the requests we build (catches signature changes)
# ---------------------------------------------------------------------------

def _mock_transport(module, handler):
    """MockTransport of the HTTP library the SDK was built on (httpx2 for 1.x, httpx before)."""
    try:
        import httpx2 as http
        if module.__version__.startswith('0.') and module.__name__ == 'anthropic':
            raise ImportError
        if module.__name__ == 'openai' and int(module.__version__.split('.')[0]) < 3:
            raise ImportError
    except ImportError:
        import httpx as http
    return http, http.MockTransport(handler)


def test_anthropic_sdk_accepts_and_sends_the_requests(monkeypatch):
    import anthropic

    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((body, request.headers.get('anthropic-beta')))
        text = body['messages'][0]['content']
        segments = _segments(text)
        answer = json.dumps({'translations': [fake_translate(s) for s in segments]}) if segments \
            else fake_translate(_single_text(text))
        return http.Response(200, json={
            'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': body['model'],
            'content': [{'type': 'thinking', 'thinking': '', 'signature': 'x'}, {'type': 'text', 'text': answer}],
            'stop_reason': 'end_turn', 'stop_sequence': None,
            'usage': {'input_tokens': 12, 'output_tokens': 7},
        })

    http, transport = _mock_transport(anthropic, handler)
    client = anthropic.Anthropic(api_key='sk-ant-test', http_client=anthropic.DefaultHttpxClient(transport=transport))
    monkeypatch.setattr(translator, 'get_anthropic_client', lambda: client)

    assert session('claude-opus-5').translate('Hello world') == '[it] Hello world'
    assert session('claude-haiku-4-5').translate_batch(['A cat.', 'A dog.']) == ['[it] A cat.', '[it] A dog.']

    opus, haiku = seen
    assert opus[0]['output_config'] == {'effort': 'low'} and opus[0]['fallbacks'] == 'default'
    assert opus[1] == translator.REFUSAL_FALLBACK_BETA
    assert 'temperature' not in opus[0]
    assert haiku[0]['temperature'] == 0.3 and haiku[0]['output_config']['format']['type'] == 'json_schema'
    assert haiku[1] is None


def test_anthropic_sdk_errors_are_classified(monkeypatch):
    import anthropic

    overloaded = (529, {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Overloaded'}})
    bad_key = (401, {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}})
    responses = [overloaded] * 3 + [bad_key]  # the SDK itself retries twice before giving up

    def handler(request):
        status, body = responses.pop(0) if len(responses) > 1 else responses[0]
        return http.Response(status, json=body, headers={'retry-after-ms': '1'})

    http, transport = _mock_transport(anthropic, handler)
    client = anthropic.Anthropic(api_key='sk-ant-test', http_client=anthropic.DefaultHttpxClient(transport=transport))
    monkeypatch.setattr(translator, 'get_anthropic_client', lambda: client)
    waits = []
    with pytest.raises(translator.FatalTranslationError, match='Chiave API Anthropic non valida'):
        session(on_wait=waits.append).translate('Hello world')
    assert len(waits) == 1  # the overload was retried, the bad key was not


def test_openai_sdk_accepts_and_sends_the_requests(monkeypatch):
    import openai

    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        text = body['messages'][1]['content']
        segments = _segments(text)
        answer = json.dumps({'translations': [fake_translate(s) for s in segments]}) if segments \
            else fake_translate(_single_text(text))
        return http.Response(200, json={
            'id': 'chatcmpl-1', 'object': 'chat.completion', 'created': 0, 'model': body['model'],
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': answer, 'refusal': None},
                         'finish_reason': 'stop', 'logprobs': None}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
        })

    http, transport = _mock_transport(openai, handler)
    client = openai.OpenAI(api_key='sk-test', http_client=openai.DefaultHttpxClient(transport=transport))
    monkeypatch.setattr(translator, 'get_openai_client', lambda: client)

    assert session('gpt-4.1').translate_batch(['A cat.', 'A dog.']) == ['[it] A cat.', '[it] A dog.']
    assert session('o4-mini').translate('Hello world') == '[it] Hello world'
    assert seen[0]['response_format']['type'] == 'json_schema' and seen[0]['temperature'] == 0.3
    assert seen[1]['reasoning_effort'] == 'low' and 'temperature' not in seen[1]


# ---------------------------------------------------------------------------
# Which models the keys can use
# ---------------------------------------------------------------------------

def test_model_listing_hides_models_the_key_cannot_use(monkeypatch):
    monkeypatch.setenv('MODEL_CHECK', '1')
    listed = {
        'anthropic': ['claude-opus-5', 'claude-haiku-4-5-20251001', 'claude-3-haiku-20240307'],
        'openai': ['gpt-4.1-2025-04-14', 'gpt-4o-mini', 'o3-mini'],
    }

    class Listing:
        def __init__(self, provider):
            self.provider = provider
            self.models = self

        def with_options(self, **_kwargs):
            return self

        def list(self):
            return [SimpleNamespace(id=i) for i in listed[self.provider]]

    monkeypatch.setattr(translator, '_client', Listing)
    anthropic_status = translator.provider_status('anthropic')
    assert anthropic_status['models'] == {'claude-opus-5', 'claude-haiku-4-5'}
    assert translator.provider_status('openai')['models'] == {'gpt-4.1', 'gpt-4o-mini', 'o3-mini'}
    assert translator.model_available('claude-opus-5') == (True, None)
    usable, reason = translator.model_available('claude-sonnet-5')
    assert not usable and 'Claude Sonnet 5' in reason


def test_model_listing_reports_an_invalid_key(monkeypatch):
    monkeypatch.setenv('MODEL_CHECK', '1')

    class Rejecting:
        def __init__(self, provider):
            self.models = self

        def with_options(self, **_kwargs):
            return self

        def list(self):
            raise StatusError(401, 'invalid x-api-key')

    monkeypatch.setattr(translator, '_client', Rejecting)
    status = translator.provider_status('anthropic')
    assert status['valid'] is False and status['error'] == 'Chiave API non valida'
    assert translator.model_available('claude-sonnet-5')[0] is False


def test_unreachable_provider_does_not_hide_anything(monkeypatch):
    monkeypatch.setenv('MODEL_CHECK', '1')

    class Offline:
        def __init__(self, provider):
            self.models = self

        def with_options(self, **_kwargs):
            return self

        def list(self):
            raise ConnectionError('offline')

    monkeypatch.setattr(translator, '_client', Offline)
    status = translator.provider_status('openai')
    assert status['valid'] is None and status['models'] is None
    assert translator.model_available('gpt-4.1') == (True, None)


# ---------------------------------------------------------------------------
# EPUB: only the text changes
# ---------------------------------------------------------------------------

def test_epub_translation_keeps_styles_markup_and_structure(tmp_path, claude):
    import epub_handler

    source = make_rich_epub(str(tmp_path / 'rich.epub'))
    output = str(tmp_path / 'rich-it.epub')
    s = session(memory=Memory())
    epub_handler.translate_epub(source, output, 'English', 'Italian', model='claude-sonnet-5', session=s)

    with zipfile.ZipFile(source) as original, zipfile.ZipFile(output) as translated:
        infos = translated.infolist()
        assert infos[0].filename == 'mimetype' and infos[0].compress_type == zipfile.ZIP_STORED
        assert sorted(original.namelist()) == sorted(translated.namelist())
        assert original.read('OEBPS/Styles/style.css') == translated.read('OEBPS/Styles/style.css')
        chapter = translated.read('OEBPS/Text/chapter 1.xhtml').decode('utf-8')
        opf = translated.read('OEBPS/content.opf').decode('utf-8')
        ncx = translated.read('OEBPS/toc.ncx').decode('utf-8')
        nav = translated.read('OEBPS/nav.xhtml').decode('utf-8')

    # Head, stylesheet, classes and SVG are untouched
    assert '<link href="../Styles/style.css" rel="stylesheet" type="text/css"/>' in chapter
    assert '<style>p { color: #333; }</style>' in chapter
    assert 'class="chapter"' in chapter and 'id="top"' in chapter and 'epub:type="chapter"' in chapter
    assert 'viewBox="0 0 10 10"' in chapter and 'preserveAspectRatio="none"' in chapter and '<linearGradient' in chapter
    # Text is translated, inline markup kept, entities still valid XML
    assert '[it] Hello\xa0<em>world</em>, sales &amp; marketing.' in chapter
    assert '<title>[it] Chapter One</title>' in chapter and '[it] Chapter One</h1>' in chapter
    assert '[it] Main point<ul>' in chapter and '[it] Detail point</li>' in chapter  # text next to a nested list
    assert '<p>12</p>' in chapter  # nothing to translate
    assert 'lang="it"' in chapter and 'xml:lang="it"' in chapter
    assert '<dc:language>it</dc:language>' in opf
    assert '<text>[it] Chapter One</text>' in ncx and '<text>[it] The Rich Book</text>' in ncx
    # Only the link text is sent to the model: the link itself cannot be lost
    assert '<li><a href="Text/chapter%201.xhtml">[it] Chapter One</a></li>' in nav
    from lxml import etree
    etree.fromstring(chapter.encode('utf-8'))  # still well-formed XHTML


def test_an_element_holding_all_the_text_is_not_sent_to_the_model(claude):
    import epub_handler

    chapter = ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>'
               '<p><em class="x">All in italics.</em></p><p> <a href="n.xhtml#1">Note one</a> </p>'
               '<p>Mixed <b>bold</b> text.</p></body></html>')
    translated = epub_handler.translate_html_content(chapter, 'English', 'Italian', session=session())
    sent = _segments(claude.calls[0]['messages'][0]['content'])
    assert sent == ['T', 'All in italics.', 'Note one', 'Mixed <b>bold</b> text.']
    assert '<em class="x">[it] All in italics.</em>' in translated
    assert '<a href="n.xhtml#1">[it] Note one</a>' in translated


def test_epub_with_broken_markup_is_still_translated(tmp_path, claude):
    import epub_handler

    broken = ('<html><head><title>T</title></head><body><p>Unclosed paragraph &nbsp; here'
              '<p>Second <b>bold</p><svg viewBox="0 0 1 1"><rect/></svg></body></html>')
    source = make_rich_epub(str(tmp_path / 'broken.epub'), chapter=broken)
    output = str(tmp_path / 'broken-it.epub')
    epub_handler.translate_epub(source, output, 'English', 'Italian', model='claude-sonnet-5', session=session())
    with zipfile.ZipFile(output) as translated:
        chapter = translated.read('OEBPS/Text/chapter 1.xhtml').decode('utf-8')
    assert '[it] Unclosed paragraph' in chapter and '[it] Second' in chapter
    assert 'viewBox="0 0 1 1"' in chapter


def test_epub_where_every_passage_is_refused_fails_instead_of_returning_the_original(tmp_path, claude):
    import epub_handler

    claude.handler = lambda kwargs: anthropic_response('', stop_reason='refusal')
    source = make_rich_epub(str(tmp_path / 'rich.epub'))
    output = str(tmp_path / 'out.epub')
    with pytest.raises(translator.TranslationError, match='Nessuna parte del libro'):
        epub_handler.translate_epub(source, output, 'English', 'Italian', session=session())
    assert not os.path.exists(output)


def test_epub_fatal_error_stops_at_once(tmp_path, claude):
    import epub_handler

    def handler(kwargs):
        raise StatusError(401, 'invalid x-api-key')

    claude.handler = handler
    with pytest.raises(translator.FatalTranslationError):
        epub_handler.translate_epub(make_rich_epub(str(tmp_path / 'r.epub')), str(tmp_path / 'o.epub'),
                                    session=session())
    assert len(claude.calls) == 1


def test_drm_and_damaged_epubs_are_explained(tmp_path, claude):
    import epub_handler

    damaged = tmp_path / 'damaged.epub'
    damaged.write_bytes(b'not a zip at all')
    with pytest.raises(translator.TranslationError, match='EPUB non leggibile'):
        epub_handler.translate_epub(str(damaged), str(tmp_path / 'o.epub'), session=session())

    locked = make_rich_epub(str(tmp_path / 'drm.epub'))
    with zipfile.ZipFile(locked, 'a') as archive:
        archive.writestr('META-INF/encryption.xml',
                         '<encryption><EncryptedData><CipherData><CipherReference URI="OEBPS/Text/chapter 1.xhtml"/>'
                         '</CipherData></EncryptedData></encryption>')
    with pytest.raises(translator.TranslationError, match='DRM'):
        epub_handler.translate_epub(locked, str(tmp_path / 'o.epub'), session=session())


def test_analyze_epub_reads_hand_made_packages(tmp_path):
    import epub_handler

    analysis = epub_handler.analyze_epub(make_rich_epub(str(tmp_path / 'rich.epub')))
    assert analysis['num_chapters'] == 2  # the chapter and the navigation document
    assert analysis['total_words'] > 5 and analysis['estimated_tokens'] > 0


# ---------------------------------------------------------------------------
# PDF: text of every script, original look
# ---------------------------------------------------------------------------

def test_pdf_translation_writes_text_in_any_script(tmp_path, claude):
    import pdf_handler

    translations = {
        'The man walks home.': 'L’uomo torna a casa… “sempre” — 5 €',
        'Sales grow every year.': 'Продажи растут каждый год.',
    }

    def handler(kwargs):
        segments = _segments(kwargs['messages'][0]['content'])
        return anthropic_response(json.dumps({'translations': [translations[s] for s in segments]}))

    claude.handler = handler
    source = make_text_pdf(str(tmp_path / 'text.pdf'), list(translations))
    output = str(tmp_path / 'text-it.pdf')
    pdf_handler.translate_pdf(source, output, 'English', 'Italian', session=session())

    doc = pdf_handler.fitz.open(output)
    text = ' '.join(doc[0].get_text('text').split())
    doc.close()
    assert 'L’uomo torna a casa… “sempre” — 5 €' in text
    assert 'Продажи растут каждый год.' in text
    assert 'The man walks home' not in text


def test_pdf_lines_of_a_paragraph_are_sent_as_one_sentence(tmp_path, claude):
    import pdf_handler

    paragraph = ('Great sales teams do not push products, they solve problems for their customers '
                 'and they follow up with care after every single meeting.')
    source = make_text_pdf(str(tmp_path / 'wrap.pdf'), [paragraph])
    pdf_handler.translate_pdf(source, str(tmp_path / 'wrap-it.pdf'), session=session())
    sent = claude.calls[0]['messages'][0]['content']
    assert paragraph in sent


def test_scanned_and_locked_pdfs_are_explained(tmp_path, claude):
    import pdf_handler

    with pytest.raises(translator.TranslationError, match='OCR'):
        pdf_handler.translate_pdf(make_scanned_pdf(str(tmp_path / 'scan.pdf')), str(tmp_path / 'o.pdf'),
                                  session=session())
    with pytest.raises(translator.TranslationError, match='password'):
        pdf_handler.translate_pdf(make_locked_pdf(str(tmp_path / 'lock.pdf')), str(tmp_path / 'o.pdf'),
                                  session=session())
    assert claude.calls == []
    assert not os.path.exists(str(tmp_path / 'o.pdf'))


def test_pdf_passages_left_untranslated_keep_the_original_text(tmp_path, claude):
    import pdf_handler

    def handler(kwargs):
        segments = _segments(kwargs['messages'][0]['content'])
        if segments is None:
            text = _single_text(kwargs['messages'][0]['content'])
            if 'secret' in text:
                return anthropic_response('', stop_reason='refusal')
            return anthropic_response(fake_translate(text))
        if any('secret' in s for s in segments):
            return anthropic_response('', stop_reason='refusal')
        return anthropic_response(json.dumps({'translations': [fake_translate(s) for s in segments]}))

    claude.handler = handler
    source = make_text_pdf(str(tmp_path / 'mixed.pdf'), ['A normal paragraph.', 'The secret paragraph.'])
    output = str(tmp_path / 'mixed-it.pdf')
    s = session()
    pdf_handler.translate_pdf(source, output, session=s)
    doc = pdf_handler.fitz.open(output)
    text = doc[0].get_text('text')
    doc.close()
    assert '[it] A normal paragraph.' in text and 'The secret paragraph.' in text
    assert s.untranslated == 1


# ---------------------------------------------------------------------------
# App: jobs report problems instead of "completing" an untranslated book
# ---------------------------------------------------------------------------

def _wait(client, task_id):
    import library_db

    for _ in range(300):
        record = library_db.get_translation(task_id)
        if record and record['status'] in ('completed', 'error'):
            return client.get(f'/api/status/{task_id}').get_json()
        time.sleep(0.02)
    raise AssertionError('translation did not finish')


def _translate(client, path, model='claude-sonnet-5', **fields):
    with open(path, 'rb') as fh:
        response = client.post('/api/translate', data={
            'file': (fh, os.path.basename(path)), 'source_lang': 'English', 'target_lang': 'Italian',
            'provider': 'anthropic', 'model': model, **fields,
        }, content_type='multipart/form-data')
    return response


def test_translation_job_reports_real_cost_and_warnings(client, tmp_path, claude, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, 'UPLOAD_DIR', str(tmp_path))
    monkeypatch.setattr(app_module, 'OUTPUT_DIR', str(tmp_path))
    response = _translate(client, make_rich_epub(str(tmp_path / 'rich.epub')), model='claude-sonnet-4-20250514')
    assert response.status_code == 200, response.get_json()
    status = _wait(client, response.get_json()['task_id'])
    assert status['status'] == 'completed', status
    messages = [entry['message'] for entry in status['logs']]
    assert any(m.startswith('Modello: Claude Sonnet 5') for m in messages)
    assert any(m.startswith('Costo API effettivo: $') for m in messages)
    assert any(m.startswith('Sezioni tradotte:') for m in messages)
    assert not os.path.exists(str(tmp_path / f"{response.get_json()['task_id']}_rich.epub"))  # upload removed
    download = client.get(f"/api/download/{response.get_json()['task_id']}")
    assert download.status_code == 200 and download.data[:2] == b'PK'


def test_translation_job_with_a_bad_key_ends_in_error(client, tmp_path, claude, monkeypatch):
    import app as app_module

    def handler(kwargs):
        raise StatusError(401, 'invalid x-api-key')

    claude.handler = handler
    monkeypatch.setattr(app_module, 'UPLOAD_DIR', str(tmp_path))
    monkeypatch.setattr(app_module, 'OUTPUT_DIR', str(tmp_path))
    response = _translate(client, make_rich_epub(str(tmp_path / 'rich.epub')))
    status = _wait(client, response.get_json()['task_id'])
    assert status['status'] == 'error'
    assert 'Chiave API Anthropic non valida' in status['error']
    assert client.get(f"/api/download/{response.get_json()['task_id']}").status_code == 400


def test_retrying_a_failed_job_reuses_what_was_translated(client, tmp_path, claude, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, 'UPLOAD_DIR', str(tmp_path))
    monkeypatch.setattr(app_module, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(translator, 'BATCH_MAX_SEGMENTS', 1)
    calls = {'n': 0}

    def failing_after_two(kwargs):
        calls['n'] += 1
        if calls['n'] > 2:
            raise StatusError(402, 'credit exhausted')
        return FakeAnthropic.translate_everything(kwargs)

    claude.handler = failing_after_two
    epub = make_rich_epub(str(tmp_path / 'rich.epub'))
    first = _wait(client, _translate(client, epub).get_json()['task_id'])
    assert first['status'] == 'error' and 'Credito' in first['error']

    claude.handler = FakeAnthropic.translate_everything
    before = len(claude.calls)
    second = _wait(client, _translate(client, epub).get_json()['task_id'])
    assert second['status'] == 'completed'
    summary = next(e['message'] for e in second['logs'] if e['message'].startswith('Sezioni tradotte:'))
    translated, total, reused = map(int, re.findall(r'\d+', summary))
    assert reused >= 2 and translated == total  # identical passages (the chapter title) count once each
    assert len(claude.calls) - before == total - reused  # the two passages done before were not paid again


def test_models_endpoint_lists_current_models_and_hides_unavailable_ones(client, monkeypatch):
    statuses = {
        'anthropic': {'configured': True, 'valid': True, 'models': {'claude-opus-5', 'claude-haiku-4-5'}, 'error': None},
        'openai': {'configured': True, 'valid': False, 'models': None, 'error': 'Chiave API non valida'},
    }
    monkeypatch.setattr(translator, 'provider_status', lambda provider, refresh=False: statuses[provider])
    data = client.get('/api/models').get_json()
    assert [m['id'] for m in data['models']] == ['claude-opus-5', 'claude-haiku-4-5']
    assert data['default_model'] == 'claude-opus-5'  # Sonnet 5 is not available to this key
    assert data['api_status'] == {'anthropic': True, 'openai': False}
    assert data['api_errors'] == {'openai': 'Chiave API non valida'}
    opus = data['models'][0]
    assert opus['token_factor'] == 1.3 and opus['output_factor'] == 1.3

    response = client.post('/api/translate', data={'book_id': 'x', 'model': 'claude-sonnet-5'})
    assert response.status_code == 400 and 'Claude Sonnet 5' in response.get_json()['error']


def test_models_endpoint_default_when_every_key_works(client):
    data = client.get('/api/models').get_json()
    ids = [m['id'] for m in data['models']]
    assert 'claude-sonnet-5' in ids and 'gpt-4.1' in ids
    assert data['default_model'] == 'claude-sonnet-5'


def test_detect_language_falls_back_to_the_offline_detector(client, tmp_path, monkeypatch):
    monkeypatch.setattr(translator, 'detect_language', lambda *args, **kwargs: 'Unknown')
    pdf = make_text_pdf(str(tmp_path / 'it.pdf'), [
        'Il libro che hai scritto è molto bello e non vedo l\'ora di leggerlo con gli amici per la prima volta. ' * 3])
    with open(pdf, 'rb') as fh:
        response = client.post('/api/detect-language', data={'file': (fh, 'it.pdf')},
                               content_type='multipart/form-data')
    assert response.get_json() == {'language': 'Italian', 'method': 'offline'}


def test_detect_language_normalizes_the_ai_answer(client, tmp_path, monkeypatch):
    monkeypatch.setattr(translator, 'detect_language', lambda *args, **kwargs: 'italiano')
    epub = make_rich_epub(str(tmp_path / 'rich.epub'))
    with open(epub, 'rb') as fh:
        response = client.post('/api/detect-language', data={'file': (fh, 'rich.epub')},
                               content_type='multipart/form-data')
    assert response.get_json() == {'language': 'Italian', 'method': 'ai'}
