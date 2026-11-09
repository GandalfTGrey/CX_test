"""Two bounded caches: cheap directory snapshots and signature-keyed JSON."""
import time

import streamlit as st

from .data import scan, read_payload


@st.cache_data(ttl=10, max_entries=128, show_spinner=False)
def cached_scan(root, pattern, refresh_token=''):
    return scan(root, pattern), time.time()


@st.cache_data(ttl=300, max_entries=2048, show_spinner=False)
def cached_payload(path, mtime_ns, size, refresh_token=''):
    # refresh_token is explicit, so one user's refresh does not clear other sessions.
    return read_payload(path, mtime_ns, size)
