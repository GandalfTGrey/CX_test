"""Frontend regressions for paging, controls and result feedback."""
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from dashboard.context import Context, ResultsPending
from dashboard.index import Index, build
from dashboard import loading

APP = str(Path(__file__).parents[1] / 'app.py')


def ready(at):
    for _ in range(100):
        if not at.session_state['work_busy']:
            return at
        time.sleep(.02)
        at.run()
    raise AssertionError('background work did not settle')


def test_no_configuration_has_no_example_data_interface(monkeypatch, tmp_path):
    monkeypatch.delenv('DASHBOARD_DATA_ROOT', raising=False)
    monkeypatch.setenv('DASHBOARD_INDEX_DIR', str(tmp_path / 'index'))
    with patch('dashboard.workbench.Index', side_effect=AssertionError('must not start without a root')):
        at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert not any(widget.label == '数据来源' for widget in at.radio)
    assert not any('生成' in button.label for button in at.button)
    assert any('先设置评测数据目录' in text.value for text in at.markdown)
    assert not (tmp_path / 'index').exists()


def test_domain_metric_survives_view_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv('DASHBOARD_INDEX_DIR', str(tmp_path / 'index'))
    at = ready(AppTest.from_file(APP, default_timeout=30).run())
    at.radio(key='domain:pnc:pills:指标').set_value('ade').run()
    ready(at)
    at.radio(key='work-view').set_value('基线对比').run()
    ready(at)
    at.radio(key='work-view').set_value('任务指标').run()
    ready(at)
    assert at.radio(key='domain:pnc:pills:指标').value == 'ade'
    assert not at.exception and not at.error


def test_training_has_no_evaluation_success_or_pager(monkeypatch, tmp_path):
    monkeypatch.setenv('DASHBOARD_INDEX_DIR', str(tmp_path / 'index'))
    at = ready(AppTest.from_file(APP, default_timeout=30).run())
    at.radio(key='work-view').set_value('训练速度').run()
    assert not any('结果文件' in text.value for text in at.markdown)
    assert not any(button.label in ('上一页', '下一页') for button in at.button)
    assert not any(box.label == '每页数量' for box in at.selectbox)
    assert not at.exception


def test_page_size_has_separate_permanent_state(monkeypatch, tmp_path):
    monkeypatch.setenv('DASHBOARD_INDEX_DIR', str(tmp_path / 'index'))
    at = ready(AppTest.from_file(APP, default_timeout=30).run())
    at.selectbox(key='page-size').set_value(10).run()
    ready(at)
    assert at.session_state['pagination_size'] == 10
    at.radio(key='work-view').set_value('训练速度').run()
    at.radio(key='work-view').set_value('任务指标').run()
    ready(at)
    assert at.session_state['pagination_size'] == 10
    assert at.selectbox(key='page-size').value == 10


def test_file_change_and_shrunk_results_clamp_before_loading(tmp_path):
    root = tmp_path / 'source'
    for number in range(25):
        folder = root / f'exp{number:02}@10'
        folder.mkdir(parents=True)
        (folder / 'pnc_eval.json').write_text('{"v":1}')
        if number == 0:
            (folder / 'depth_eval.json').write_text('{"mae":1}')
    index = Index(str(root), str(tmp_path / 'index'))
    build(index)
    context = Context(str(root), 'pnc', index=index, batch=index.state()['active'], page=2, page_size=10)
    assert len(context.records('pnc_eval.json')) == 5
    smaller = Context(str(root), 'pnc', index=index, batch=index.state()['active'], page=2, page_size=10)
    assert len(smaller.records('depth_eval.json')) == 1
    assert smaller.page == 0


def test_reference_can_be_loaded_outside_current_page(tmp_path):
    root = tmp_path / 'source'
    for number in range(25):
        folder = root / f'exp{number:02}@10'
        folder.mkdir(parents=True)
        (folder / 'pnc_eval.json').write_text('{"v":1}')
    index = Index(str(root), str(tmp_path / 'index'))
    build(index)
    context = Context(str(root), 'pnc', index=index, batch=index.state()['active'], page=1, page_size=10)
    records = context.records('pnc_eval.json')
    assert len(records) == 10
    reference = next(entry for entry in context.selection('pnc_eval.json') if entry.experiment == 'exp09')
    assert all(record.entry.experiment != 'exp09' for record in records)
    assert context.reference(reference).entry.experiment == 'exp09'


def test_loading_does_not_report_first_request_as_empty_and_retains_true_description(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    for name in ('candidate', 'other'):
        folder = root / (name + '@10')
        folder.mkdir(parents=True)
        (folder / 'pnc_eval.json').write_text('{"v":1}')
    index = Index(str(root), str(tmp_path / 'index'))
    build(index)
    previous = {}
    def create(keywords=()):
        return Context(str(root), 'pnc', index=index, batch=index.state()['active'], asynchronous=True,
                       session='descriptor-test', previous=previous, keywords=keywords)
    for _ in range(100):
        initial = create()
        try:
            records = initial.records('pnc_eval.json')
        except ResultsPending:
            time.sleep(.01)
            continue
        break
    assert len(records) == 2
    event = threading.Event()
    actual = loading.payload
    def blocked(*args):
        event.wait(timeout=5)
        return actual(*args)
    monkeypatch.setattr(loading, 'payload', blocked)
    try:
        pending = create(('candidate',))
        assert pending.records('pnc_eval.json') == records
        assert pending.pending and pending.preserved
        assert pending.displayed[0][0]['keywords'] == ()
        assert pending.displayed[0][0]['page_size'] == 50
        separate = Context(str(root), 'pnc', index=index, batch=index.state()['active'], asynchronous=True,
                           session='first-request', previous={})
        try:
            separate.records('pnc_eval.json')
        except ResultsPending:
            pass
        else:
            raise AssertionError('first pending request must not look like an empty result')
        assert separate.pending
    finally:
        event.set()


def test_grid_has_only_server_pagination(monkeypatch):
    import pandas as pd
    options = []
    monkeypatch.setattr('dashboard.context.safe_aggrid', lambda data, **kwargs: options.append(kwargs['gridOptions']))
    monkeypatch.setattr('dashboard.context.st.download_button', lambda *args, **kwargs: None)
    monkeypatch.setattr('dashboard.context.st.caption', lambda *args, **kwargs: None)
    context = Context('/synthetic', 'pnc')
    context.grid(pd.DataFrame({'Exp':['run']}), gridOptions={'pagination':True})
    assert options[0]['pagination'] is False
    assert options[0]['suppressPaginationPanel'] is True
