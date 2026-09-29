import io
from pathlib import Path
import xml.etree.ElementTree as ET
import configparser
import re
import requests

from gis4wrf.core.util import export

QGIS_PLUGINS_REPO_URL = 'https://plugins.qgis.org/plugins/plugins.xml?qgis=3.0.0'
METADATA_PATH = Path(__file__).parents[2] / 'metadata.txt'


def _version_key(version: str) -> tuple:
    ''' Sort key for plugin versions.

        Replaces distutils.version.StrictVersion, which went away together with
        distutils in Python 3.12. Only the leading dotted numeric part is compared,
        so '0.14.7' and '0.14.7-beta' sort as equal, and anything unparseable sorts
        lowest instead of raising.
    '''
    match = re.match(r'\s*(\d+(?:\.\d+)*)', version or '')
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split('.'))

@export
def get_latest_gis4wrf_version() -> str:
    response = requests.get(QGIS_PLUGINS_REPO_URL)
    response.raise_for_status()
    tree = ET.parse(io.BytesIO(response.content))
    version_elements = tree.findall("./pyqgis_plugin[@name='GIS4WRF']/version")
    if not version_elements:
        raise RuntimeError('No version found')
    versions = [i.text for i in version_elements]
    latest_version = sorted(versions, key=_version_key)[-1]
    return latest_version

@export
def get_installed_gis4wrf_version() -> str:
    config = configparser.ConfigParser()
    config.read(str(METADATA_PATH))
    return config['general']['version']

@export
def is_newer_version(a: str, b: str) -> bool:
    return _version_key(a) > _version_key(b)