"""Widget preferences survive Streamlit's cleanup when a view is hidden."""
import streamlit as st


def control(kind, label, *args, key, reset_page=False, **kwargs):
    preferences = st.session_state.setdefault('control_preferences', {})
    stored = preferences.get(key)
    if key in preferences:
        if kind in ('radio', 'selectbox'):
            options = list(args[0])
            if stored in options:
                kwargs['index'] = options.index(stored)
        elif kind == 'multiselect':
            kwargs['default'] = [value for value in stored if value in args[0]]
        elif kind in ('toggle', 'checkbox', 'text_area', 'text_input'):
            kwargs['value'] = stored
        elif kind == 'slider':
            low, high = kwargs.get('min_value'), kwargs.get('max_value')
            if low is not None and high is not None and low <= stored <= high:
                kwargs['value'] = stored
    old_callback = kwargs.pop('on_change', None)

    def changed():
        preferences[key] = st.session_state[key]
        if reset_page:
            st.session_state['result_page'] = 0
        if old_callback:
            old_callback()

    kwargs['on_change'] = changed
    value = getattr(st, kind)(label, *args, key=key, **kwargs)
    preferences[key] = value
    return value


def reset_page():
    st.session_state['result_page'] = 0
