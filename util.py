# -*- coding: utf-8 -*-

import os
import logging
import shutil
from functools import wraps
from datetime import datetime
import json
from typing import Union, Dict, Any, TypeVar

# Componentes originais do GIS4WRF
from osgeo import osr, ogr, gdal
import numpy as np

# Componentes para a nova lógica de download
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from gis4wrf.core.errors import UserError

LOG = logging.getLogger(__name__)
Number = TypeVar('Number', int, float)

# --- Funções Originais do util.py ---
def export(fn):
    mod = __import__(fn.__module__, fromlist=[fn.__name__])
    mod.__all__ = mod.__all__ if hasattr(mod, '__all__) else []
    if fn.__name__ not in mod.__all__:
        mod.__all__.append(fn.__name__)
    return fn

@export
def as_float(val: Any) -> float:
    return float(val)

@export
def osr_from_wkt(wkt: str) -> osr.SpatialReference:
    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    return srs

@export
def remove_dir(path: str) -> None:
    if os.path.exists(path):
        LOG.info(f'Removing directory: {path}')
        shutil.rmtree(path)

# --- Funções Adicionadas para Corrigir o Download ---

@export
def requests_retry_session(retries=3, backoff_factor=0.3, status_forcelist=(500, 502, 504), session=None):
    session = session or requests.Session()
    retry = Retry(
        total=retries, read=retries, connect=retries,
        backoff_factor=backoff_factor, status_forcelist=status_forcelist)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

@export
def get_result(response: requests.Response) -> Dict:
    try:
        response.raise_for_status()
        result = response.json()
        if 'error' in result:
            raise UserError(f"RDA API Error: {result['error'].get('message', 'Unknown error')}")
        if 'result' in result and result['result'] == 'ok':
            return result.get('data', {})
        if 'products' in result or 'variables' in result:
            return result
        raise UserError(f'Unexpected API response: {response.text[:200]}')
    except json.JSONDecodeError:
        raise UserError(f'Failed to decode JSON response from server: {response.text[:200]}')
    except requests.exceptions.RequestException as e:
        raise UserError(f'Connection error to RDA server: {e}')

@export
def parse_date(date_str: str) -> datetime:
    if not date_str: return datetime(1900, 1, 1)
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            pass
    return datetime(1900, 1, 1)
