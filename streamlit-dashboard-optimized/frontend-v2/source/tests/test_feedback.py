import threading
import time

from streamlit.testing.v1 import AppTest

from dashboard import loading
from dashboard.context import Context
from dashboard.index import Index, build


def ready(at):
    for _ in range(100):
        if not at.session_state['work_busy']:
            return at
        time.sleep(.02)
        at.run()
    raise AssertionError('background operation did not settle')


def test_cancel_preserves_applied_query_and_unapplied_input(monkeypatch, tmp_path):
    monkeypatch.setenv('DASHBOARD_INDEX_DIR', str(tmp_path / 'local'))
    at = ready(AppTest.from_file('app.py', default_timeout=30).run())
    before = at.session_state['applied_query'].copy()
    event = threading.Event()
    entered = threading.Event()
    actual = loading.payload
    def blocked(*args):
        entered.set()
        event.wait(timeout=10)
        return actual(*args)
    monkeypatch.setattr(loading, 'payload', blocked)
    try:
        at.text_input(key='draft-words').input('candidate')
        next(button for button in at.button if button.label == '应用筛选').click().run()
        assert entered.wait(timeout=2)
        assert at.session_state['applied_query'] == before
        assert any('保留旧结果' in message.value for message in at.markdown)
        next(button for button in at.button if button.label == '取消本次读取').click().run()
        assert at.session_state['applied_query'] == before
        assert at.text_input(key='draft-words').value == 'candidate'
        assert any('有未应用的修改' in caption.value for caption in at.caption)
        assert not at.exception and not at.error
    finally:
        event.set()


def test_marked_baseline_is_present_on_second_page(tmp_path):
    root = tmp_path / 'source'
    for number in range(26):
        folder = root / ('exp{:02d}@10'.format(number))
        folder.mkdir(parents=True)
        (folder / 'pnc_eval.json').write_text('{"score":1}')
        if number == 0:
            (folder / 'STAR').touch()
    index = Index(str(root), str(tmp_path / 'local'))
    build(index)
    context = Context(str(root), 'pnc', index=index, batch=index.state()['active'], page=1, page_size=10)
    records = context.records('pnc_eval.json')
    assert len(records) == 11
    assert any(record.entry.experiment == 'exp00' for record in records)
