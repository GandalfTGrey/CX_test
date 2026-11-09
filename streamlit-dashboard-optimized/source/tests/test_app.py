"""Exercise real Streamlit widgets, including state across view changes."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[1] / 'app.py')


@pytest.mark.parametrize('task', ['god', 'cog', 'bev', 'pnc', 'pvseg', 'depth', 'navi', 'bev-new'])
def test_task_and_analysis_views(task, monkeypatch):
    monkeypatch.delenv('DASHBOARD_DATA_ROOT', raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params['task'] = task
    at.run()
    assert not at.exception
    assert not at.error
    assert not at.warning
    assert next(metric.value for metric in at.metric if metric.label == '已载入实验') == '3'
    for page in ['基线对比', 'Checkpoint 趋势']:
        at.radio(key='work-view').set_value(page).run()
        assert not at.exception
        assert not at.error
        assert not at.warning
        assert len(at.dataframe) >= 1
        assert next(metric.value for metric in at.metric if metric.label == 'Checkpoint') == ('9' if '趋势' in page else '3')


def test_direction_resets_when_metric_changes(monkeypatch):
    monkeypatch.delenv('DASHBOARD_DATA_ROOT', raising=False)
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.radio(key='work-view').set_value('基线对比').run()
    next(widget for widget in at.selectbox if widget.label == '指标方向').set_value('越小越好').run()
    metric = next(widget for widget in at.selectbox if widget.label == '原始指标路径')
    next_path = next(value for value in metric.options if value != metric.value)
    metric.set_value(next_path).run()
    assert next(widget.value for widget in at.selectbox if widget.label == '指标方向') == '仅显示差值'
    assert not at.error


def test_task_radio_preserves_domain_controls_across_view_changes(monkeypatch):
    """The task picker must work on Streamlit 1.39 without segmented_control."""
    monkeypatch.delenv('DASHBOARD_DATA_ROOT', raising=False)
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.radio(key='active-task').set_value('bev-new').run()
    assert not at.exception
    assert not at.error
    assert at.radio(key='active-task').value == 'bev-new'
    at.radio(key='work-view').set_value('基线对比').run()
    assert len(at.dataframe) >= 1
    at.radio(key='work-view').set_value('任务指标').run()
    assert at.radio(key='active-task').value == 'bev-new'
    assert not at.exception
    assert not at.error


def test_training_form_reads_demo_log(monkeypatch):
    monkeypatch.delenv('DASHBOARD_DATA_ROOT', raising=False)
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.radio(key='work-view').set_value('训练速度').run()
    log = Path(APP).parent / 'demo_data' / 'baseline-v1' / 'training_logs' / 'Rank-0.log'
    next(widget for widget in at.text_area if widget.label.startswith('训练日志')).input(f'baseline | {log}')
    next(widget for widget in at.button if widget.label == '读取并对比').click().run()
    assert not at.exception
    assert not at.error
    assert not at.warning
    assert at.dataframe[0].value.iloc[0]['实验'] == 'baseline'
    assert at.dataframe[0].value.iloc[0]['有效区间'] > 0
