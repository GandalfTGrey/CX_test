"""Regressions for upstream AgGrid's obsolete exception lookup on Streamlit 1.40."""
import pandas as pd
import pytest
import st_aggrid
from streamlit.components.v1.custom_component import MarshallComponentException
from streamlit.runtime.scriptrunner_utils.exceptions import RerunException, StopException
from streamlit.runtime.scriptrunner_utils.script_requests import RerunData

from dashboard.compat import safe_aggrid


@pytest.mark.parametrize('control', [StopException(), RerunException(RerunData())])
def test_streamlit_control_exceptions_propagate_unchanged(monkeypatch, control):
    def interrupt(**kwargs):
        raise control
    monkeypatch.setattr(st_aggrid, '_component_func', interrupt)
    with pytest.raises(type(control)) as caught:
        safe_aggrid(pd.DataFrame({'score': [1.0]}))
    assert caught.value is control


def test_real_marshalling_error_is_not_masked_by_attribute_error(monkeypatch):
    def fail(**kwargs):
        raise MarshallComponentException('cannot serialize custom renderer')
    monkeypatch.setattr(st_aggrid, '_component_func', fail)
    with pytest.raises(MarshallComponentException, match='cannot serialize custom renderer'):
        safe_aggrid(pd.DataFrame({'score': [1.0]}))


def test_safe_renderer_keeps_component_namespace_unchanged(monkeypatch):
    original = st_aggrid.AgGrid
    components = original.__globals__.get('components')
    namespace = dict(vars(components)) if components is not None else None
    calls = []
    monkeypatch.setattr(st_aggrid, '_component_func', lambda **kwargs: calls.append(kwargs))
    safe_aggrid(pd.DataFrame({'score': [2.5]}), key='compat-check', update_on=[])
    assert st_aggrid.AgGrid is original
    if components is not None:
        assert original.__globals__['components'] is components
        assert dict(vars(components)) == namespace
    assert len(calls) == 1
    assert calls[0]['key'] == 'compat-check'
    # Conversion and component configuration remain upstream AgGrid behavior.
    assert len(calls[0]['gridOptions']['rowData']) == 1
    assert calls[0]['gridOptions']['rowData'][0]['score'] == 2.5
