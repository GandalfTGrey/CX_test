"""Project-local workaround for streamlit-aggrid 1.0.5 on Streamlit 1.39/1.40.

The upstream renderer refers to components.components.MarshallComponentException.
Its alias points at streamlit.components.v1, where that path is unavailable.
Even StopException/RerunException can therefore turn into AttributeError.
Use a private function globals mapping with the correct exception namespace;
do not mutate Streamlit, st_aggrid, or any installed package file.
"""
from types import FunctionType

import st_aggrid
from streamlit.components.v1.custom_component import MarshallComponentException


class _ComponentNamespace:
    def __init__(self, original):
        self._original = original

    def __getattr__(self, name):
        if name == 'components':
            return self
        if name == 'MarshallComponentException':
            return MarshallComponentException
        return getattr(self._original, name)


def safe_aggrid(data, **kwargs):
    """Render using upstream conversions while preserving control exceptions."""
    original = st_aggrid.AgGrid
    if isinstance(original, FunctionType) and 'components' in original.__globals__:
        namespace = original.__globals__.copy()
        namespace['components'] = _ComponentNamespace(namespace['components'])
        renderer = FunctionType(original.__code__, namespace, original.__name__,
                                original.__defaults__, original.__closure__)
        renderer.__kwdefaults__ = original.__kwdefaults__
    else:
        renderer = original
    return renderer(data, **kwargs)
