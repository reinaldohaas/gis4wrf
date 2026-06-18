# GIS4WRF (https://doi.org/10.5281/zenodo.1288569)
# Copyright (c) 2018 D. Meyer and M. Riechert. Licensed under MIT.

from typing import List, Iterable, Tuple, Union
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Disable tqdm progress bars to avoid AttributeError in QGIS GUI/non-tty environments
os.environ['TQDM_DISABLE'] = '1'

# Fallback dummy stream to prevent tqdm from crashing if stdout/stderr are None
class DummyStream:
    def write(self, x): pass
    def flush(self): pass
    def isatty(self): return False

if sys.stderr is None:
    sys.stderr = DummyStream()
if sys.stdout is None:
    sys.stdout = DummyStream()

import cdsapi

from gis4wrf.core.util import export, remove_dir
from gis4wrf.core.errors import UserError

DATE_FORMAT = '%Y%m%d%H%M'

@export
def get_met_dataset_path(base_dir: Union[str,Path], dataset_name: str, product_name: str,
                         start_date: datetime, end_date: datetime) -> Path:
    datetime_range = '{}-{}'.format(start_date.strftime(DATE_FORMAT), end_date.strftime(DATE_FORMAT))
    base_dir = Path(base_dir)
    product_dir = base_dir / dataset_name / product_name
    path = product_dir / datetime_range
    return path

@export
def is_met_dataset_downloaded(base_dir: Union[str,Path], dataset_name: str, product_name: str,
                               start_date: datetime, end_date: datetime) -> bool:
    path = get_met_dataset_path(base_dir, dataset_name, product_name, start_date, end_date)
    return path.exists()

@export
def get_met_products(dataset_name: str, cds_key: str) -> dict:
    return {
        "Reanalysis": {
            "pressure-levels": {
                "label": "Pressure Levels (3D: T, U, V, Q, Z)",
                "start_date": datetime(1940, 1, 1),
                "end_date": datetime.today() - timedelta(days=5)
            },
            "single-levels": {
                "label": "Single Levels (2D surface fields)",
                "start_date": datetime(1940, 1, 1),
                "end_date": datetime.today() - timedelta(days=5)
            }
        }
    }

@export
def download_met_dataset(base_dir: Union[str,Path], auth: tuple,
                         dataset_name: str, product_name: str, param_names: List[str],
                         start_date: datetime, end_date: datetime,
                         lat_south: float, lat_north: float, lon_west: float, lon_east: float,
                         interval_hours: int = 3) -> Iterable[Tuple[float,str]]:
    path = get_met_dataset_path(base_dir, dataset_name, product_name, start_date, end_date)

    if path.exists():
        remove_dir(path)
    path.mkdir(parents=True, exist_ok=True)

    cds_key = auth[0]
    if not cds_key:
        raise UserError("Copernicus CDS API key is not configured. Please set it in the plugin options.")

    yield 0.1, "Initializing CDS API Client..."
    
    # Double check streams before creating the client
    if sys.stderr is None:
        sys.stderr = DummyStream()
    if sys.stdout is None:
        sys.stdout = DummyStream()

    client = cdsapi.Client(url="https://cds.climate.copernicus.eu/api", key=cds_key, quiet=True)

    # Compute date parameters
    delta = end_date - start_date
    days_list = [start_date + timedelta(days=i) for i in range(delta.days + 1)]
    
    # Group days by year and month to chunk requests and avoid CDS limits
    from collections import defaultdict
    year_month_days = defaultdict(list)
    for d in days_list:
        year_month_days[(d.strftime('%Y'), d.strftime('%m'))].append(d.strftime('%d'))

    times = [f"{h:02d}:00" for h in range(0, 24, interval_hours)]

    # Bounding box / area (North, West, South, East)
    area = [lat_north, lon_west, lat_south, lon_east]

    num_steps = len(param_names) * len(year_month_days)
    step_count = 0

    for param in param_names:
        for (year, month), days_in_month in year_month_days.items():
            step_progress = step_count / num_steps
            yield step_progress + 0.05, f"Requesting {param} for {year}-{month}..."
            
            days = sorted(list(set(days_in_month)))

            if param == "pressure-levels":
                output_file = path / f"era5_pressure_{year}{month}.grib"
                client.retrieve(
                    'reanalysis-era5-pressure-levels',
                    {
                        'product_type': 'reanalysis',
                        'format': 'grib',
                        'variable': [
                            'geopotential', 'relative_humidity', 'temperature',
                            'u_component_of_wind', 'v_component_of_wind',
                        ],
                        'pressure_level': [
                            '1', '2', '3', '5', '7', '10', '20', '30', '50', '70',
                            '100', '125', '150', '175', '200', '225', '250', '300',
                            '350', '400', '450', '500', '550', '600', '650', '700',
                            '750', '775', '800', '825', '850', '875', '900', '925',
                            '950', '975', '1000',
                        ],
                        'year': year,
                        'month': month,
                        'day': days,
                        'time': times,
                        'area': area,
                    },
                    str(output_file)
                )
            elif param == "single-levels":
                output_file = path / f"era5_surface_{year}{month}.grib"
                client.retrieve(
                    'reanalysis-era5-single-levels',
                    {
                        'product_type': 'reanalysis',
                        'format': 'grib',
                        'variable': [
                            '10m_u_component_of_wind', '10m_v_component_of_wind', '2m_dewpoint_temperature',
                            '2m_temperature', 'land_sea_mask', 'mean_sea_level_pressure',
                            'sea_ice_cover', 'sea_surface_temperature', 'skin_temperature',
                            'snow_depth', 'soil_temperature_level_1', 'soil_temperature_level_2',
                            'soil_temperature_level_3', 'soil_temperature_level_4', 'surface_pressure',
                            'volumetric_soil_water_layer_1', 'volumetric_soil_water_layer_2',
                            'volumetric_soil_water_layer_3', 'volumetric_soil_water_layer_4',
                        ],
                        'year': year,
                        'month': month,
                        'day': days,
                        'time': times,
                        'area': area,
                    },
                    str(output_file)
                )
            step_count += 1

    yield 1.0, "ERA5 download complete"
