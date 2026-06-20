# GIS4WRF (https://doi.org/10.5281/zenodo.1288569)
# Copyright (c) 2018 D. Meyer and M. Riechert. Licensed under MIT.

from typing import Set, Dict, Tuple, List, Optional
import os
import json
import hashlib
from datetime import datetime, timedelta

from gis4wrf.core.util import gdal, export

# Cache directory next to this file
_CACHE_DIR = os.path.join(os.path.dirname(__file__), '__grib_cache__')

class GribMetadata(object):
    def __init__(self, variables: Dict[str,str], times: List[datetime], path: Optional[str]=None) -> None:
        self.path = path # path to file
        self.variables = variables # maps variable names to labels
        self.times = times # ordered list of datetime objects

    @property
    def time_range(self) -> Tuple[datetime,datetime]:
        assert self.times
        return min(self.times), max(self.times)

    @property
    def interval_seconds(self) -> int:
        assert len(self.times) >= 2
        first, second = self.times[:2]
        return int((second - first).total_seconds())

def is_grib_file(path: str) -> bool:
    with open(path, 'rb') as f:
        return f.read(4) == b'GRIB'

def _file_fingerprint(path: str) -> str:
    """Fast fingerprint: path + size + mtime. No need to hash content."""
    stat = os.stat(path)
    key = f"{path}|{stat.st_size}|{stat.st_mtime}"
    return hashlib.md5(key.encode()).hexdigest()

def _cache_path(fingerprint: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, fingerprint + '.json')

def _load_from_cache(fingerprint: str) -> Optional['GribMetadata']:
    cp = _cache_path(fingerprint)
    if not os.path.exists(cp):
        return None
    try:
        with open(cp, 'r') as f:
            data = json.load(f)
        variables = data['variables']
        times = [datetime(1970, 1, 1) + timedelta(seconds=s) for s in data['unix_times']]
        path = data.get('path')
        return GribMetadata(variables, sorted(times), path)
    except Exception:
        return None

def _save_to_cache(fingerprint: str, meta: 'GribMetadata') -> None:
    cp = _cache_path(fingerprint)
    try:
        data = {
            'variables': meta.variables,
            'unix_times': [int((t - datetime(1970, 1, 1)).total_seconds()) for t in meta.times],
            'path': meta.path
        }
        with open(cp, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass  # Cache write failure is non-fatal

@export
def read_grib_folder_metadata(folder: str) -> Tuple[GribMetadata, List[GribMetadata]]:
    paths = [os.path.join(folder, filename)
             for filename in os.listdir(folder)]
    paths = [path for path in paths if is_grib_file(path)]
    return read_grib_files_metadata(paths)

@export
def read_grib_files_metadata(paths: List[str]) -> Tuple[GribMetadata, List[GribMetadata]]:
    ''' Reads metadata of multiple GRIB files which may have overlapping time steps 
        if they contain different variables (e.g. pressure vs surface levels).
        Returns aggregated and per-file metadata, where the latter are ordered by time.
    '''
    variables = {} # type: Dict[str,str]
    times = [] # type: List[datetime]

    metas = [] # type: List[GribMetadata]
    for path in paths:
        meta = read_grib_file_metadata(path)
        metas.append(meta)
        if not variables:
            variables = meta.variables.copy()
        else:
            variables.update(meta.variables)
        times.extend(meta.times)

    times = sorted(list(set(times)))
    metas.sort(key=lambda meta: meta.times)

    return GribMetadata(variables, times), metas

@export
def read_grib_file_metadata(path: str) -> GribMetadata:
    # Try cache first
    fingerprint = _file_fingerprint(path)
    cached = _load_from_cache(fingerprint)
    if cached is not None:
        return cached

    ds = gdal.Open(path, gdal.GA_ReadOnly)

    # ds.GetMetadata() returns nothing in gdal < 2.3, but with 2.3 it contains the GRIB_IDS
    # item which contains things like the center (e.g. NCEP). See http://www.gdal.org/frmt_grib.html.

    # TODO read bbox

    variables = dict()
    times = set()

    for i in range(1, ds.RasterCount + 1):
        band = ds.GetRasterBand(i)
        meta = band.GetMetadata()
        var_unit = meta['GRIB_UNIT'] # "[m/s]"
        var_name = meta['GRIB_ELEMENT'] # "VGRD"
        var_label = meta['GRIB_COMMENT'] # "v-component of wind [m/s]"
        valid_time = meta['GRIB_VALID_TIME'] # "  1438754400 sec UTC"

        var_label_without_unit = var_label.replace(var_unit, '').strip()
        variables[var_name] = var_label_without_unit
        
        unix = int(''.join(c for c in valid_time if c.isdigit() or c == '-'))
        time = datetime(1970, 1, 1) + timedelta(seconds=unix)
        times.add(time)

    result = GribMetadata(variables, sorted(times), path)
    # Save to cache for next time
    _save_to_cache(fingerprint, result)
    return result