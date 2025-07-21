# GIS4WRF (https://doi.org/10.5281/zenodo.1288569)
# Copyright (c) 2018 D. Meyer and M. Riechert. Licensed under MIT.

"""This module is an interface to the Research Data Archive (RDA) API"""

from typing import List, Iterable, Tuple, Union
import time
import json
import requests
from pathlib import Path
import glob
import os
import shutil
from datetime import datetime
from rda_apps_clients import rdams_client as rc
from urllib.request import build_opener

from .util import download_file_with_progress, requests_retry_session
from gis4wrf.core.util import export, remove_dir
from gis4wrf.core.errors import UserError
from gis4wrf.plugin.options import get_options

class MetToolsDownloadManager:
    def __init__(self, iface):
        self.options = get_options()
        self.rda_token = self.options.rda_token
        self.products = get_met_products(dataset_name, self.options.rda_token)

DATE_FORMAT = '%Y%m%d%H%M'
COMPLETED_STATUS = 'Completed'
ERROR_STATUS = ['Error']
IGNORE_FILES = ['.csh']

API_BASE_URL = 'https://rda.ucar.edu/json_apps/'
DOWNLOAD_LOGIN_URL = 'https://rda.ucar.edu/cgi-bin/login'

def parse_date(date: int) -> datetime:
    return datetime.strptime(str(date).zfill(len(DATE_FORMAT)), DATE_FORMAT)

def get_result(response: requests.Response) -> dict:
    response.raise_for_status()
    try:
        obj = response.json()
    except:
        raise UserError('RDA error: ' + response.text)
    try:
        if obj['status'] == 'error':
            raise UserError('RDA error: ' + ' '.join(obj['messages']))
    except KeyError:
        raise UserError('RDA error: ' + response.text)
    return obj['result']

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
def download_met_dataset(base_dir: Union[str,Path], auth: tuple,
                         dataset_name: str, product_name: str, param_names: List[str],
                         start_date: datetime, end_date: datetime,
                         lat_south: float, lat_north: float, lon_west: float, lon_east: float
                         ) -> Iterable[Tuple[float,str]]:
    path = get_met_dataset_path(base_dir, dataset_name, product_name, start_date, end_date)

    if path.exists():
        remove_dir(path)

    request_data = {
        'dataset': dataset_name,
        'product': product_name,
        'date': start_date.strftime(DATE_FORMAT) + '/to/' + end_date.strftime(DATE_FORMAT),
        'param': '/'.join(param_names),
        "nlat": lat_north,
        "slat": lat_south,
        "wlon": lon_west,
        "elon": lon_east
    }

    yield 0.05, 'submitting'
    request_id = rda_submit_request(request_data, auth)
    yield 0.1, 'submitted'

    # Check when the dataset is available for download
    # simply by checking the status of the request every 1 minute.
    rda_status = rda_check_status(request_id, auth)
    while rda_status != COMPLETED_STATUS and not rda_is_error_status(rda_status):
        yield 0.1, 'RDA: ' + rda_status
        time.sleep(60)
        rda_status = rda_check_status(request_id, auth)
    
    yield 0.1, 'RDA: ' + rda_status
    if rda_is_error_status(rda_status):
        raise RuntimeError('Unexpected status from RDA: ' + rda_status)

    yield 0.2, 'ready'
    try:
        for dataset_progress, file_progress, url in rda_download_dataset(request_id, auth, path):
            yield 0.2 + (0.95 - 0.2) * dataset_progress, f'downloading {url} ({file_progress*100:.1f}%)'
    finally:
        yield 0.95, 'purging'
        rda_purge_request(request_id, auth)
    
    yield 1.0, 'complete'
    

def rda_submit_request(request_data: dict, auth: tuple) -> str:
    headers = {'Content-type': 'application/json'}
    # Note that requests_retry_session() is not used here since any error may be due
    # to invalid input and the user should be alerted immediately.
    response = requests.post(f'{API_BASE_URL}/request', auth=auth, headers=headers, json=request_data)
    result = get_result(response)
    try:
        request_id = result['request_id']
    except:
        raise UserError('RDA error: ' + json.dumps(result))
    return request_id

def rda_check_status(request_id: str, auth: tuple) -> str:
    with requests_retry_session() as session:
        response = session.get(f'{API_BASE_URL}/request/{request_id}', auth=auth)
        # We don't invoke raise_for_status() here to account for temporary server/proxy issues.
        try:
            obj = response.json()
            if obj['status'] != 'ok':
                return obj['status']
            return obj['result']['status']
        except:
            return response.text

def rda_is_error_status(status: str) -> bool:
    return any(error_status in status for error_status in ERROR_STATUS)

def rda_download_dataset(request_id: str, auth: tuple, path: Path) -> Iterable[Tuple[float,float,str]]:
    path_tmp = path.with_name(path.name + '_tmp')
    if path_tmp.exists():
        remove_dir(path_tmp)
    path_tmp.mkdir(parents=True)
    urls = rda_get_urls_from_request_id(request_id, auth)
    with requests_retry_session() as session:
        login_data = {'email': auth[0], 'passwd': auth[1], 'action': 'login'}
        response = session.post(DOWNLOAD_LOGIN_URL, login_data)
        response.raise_for_status()
        for i, url in enumerate(urls):
            file_name = url.split('/')[-1]
            for file_progress in download_file_with_progress(url, path_tmp / file_name, session=session):
                dataset_progress = (i + file_progress) / len(urls)
                yield dataset_progress, file_progress, url
    
    # Downloaded files may be tar archives, not always though.
    for tar_path in glob.glob(str(path_tmp / '*.tar')):
        shutil.unpack_archive(tar_path, path_tmp)
        os.remove(tar_path)

    path_tmp.rename(path)

def rda_get_urls_from_request_id(request_id: str, auth: tuple) -> List[str]:
    with requests_retry_session() as session:
        response = session.get(f'{API_BASE_URL}/request/{request_id}/filelist_json', auth=auth)
        result = get_result(response)
    urls = [f['web_path'] for f in result['web_files']]
    filtered = []
    for url in urls:
        if any(url.endswith(ignore) for ignore in IGNORE_FILES):
            continue
        filtered.append(url)
    return filtered

def rda_purge_request(request_id: str, auth: tuple) -> None:
    with requests_retry_session() as session:
        response = session.delete(f'{API_BASE_URL}/request/{request_id}', auth=auth)
        response.raise_for_status()

def download_met_data(start_date, end_date, nlat, slat, wlon, elon):
    """
    Baixa dados MET usando API RDA com autenticação via token.
    As datas devem estar no formato 'YYYYMMDDHHMM'.
    """
    options = get_options()
    rda_token = options.rda_token
    rda_client = rc.get_authentication(rda_token)

    # Monta o controle da requisição
    control = {
        'dataset': 'ds083.3',
        'date': f'{start_date}/to/{end_date}',
        'param': 'TMP/DPT/SPF H/R H/WEASD/V GRD/U GRD/PRES/PRMSL/HGT/ICEC/LAND/TSOIL/SOILW',
        'nlat': nlat,
        'slat': slat,
        'wlon': wlon,
        'elon': elon,
        'product': 'Analysis'
    }

    with open('debug.log', 'a') as log:
        log.write(f"Submetendo requisição: {control}\n")
    # Submete a requisição
    response = rc.submit_json(control)
    assert response['http_response'] == 200, f"Falha ao submeter requisição: {response['http_response']}"
    rqst_id = response['data']['request_id']

    def check_ready(rqst_id, wait_interval=120):
        for i in range(100):
            res = rc.get_status(rqst_id)
            request_status = res['data']['status']
            if request_status == 'Completed':
                return True
            time.sleep(wait_interval)
        return False

    # Aguarda a requisição ficar pronta
    if check_ready(rqst_id):
        rc.download(rqst_id)
    else:
        print("Requisição não está pronta para download.")
        return

    # Obtém a lista de arquivos (exemplo: pode ser obtida da resposta ou montada conforme datas)
    # Aqui, supondo que os arquivos seguem o padrão 'gdas1.fnl0p25.YYYYMMDDHH.f00.grib2'
    # Você pode ajustar conforme necessário!
    filelist = []
    for dt in [start_date, end_date]:
        hour = dt[-2:]
        filelist.append(f'gdas1.fnl0p25.{dt}.f00.grib2')

    # Faz download manual dos arquivos
    dspath = f'https://request.rda.ucar.edu/dsrqst/{rqst_id}/'
    opener = build_opener()
    for file in filelist:
        filename = dspath + file
        ofile = os.path.basename(filename)
        try:
            with opener.open(filename) as infile, open(ofile, "wb") as outfile:
                outfile.write(infile.read())
            print(f"Baixado: {ofile}")
        except Exception as e:
            print(f"Falha ao baixar {ofile}: {e}")

    # Cria diretório de destino conforme data de início
    date_folder = start_date[:8]  # YYYYMMDD
    drive_path = f'/home/haas/gis4wrf/datasets/met/gdas/{date_folder}'
    os.makedirs(drive_path, exist_ok=True)
    for file in filelist:
        if os.path.exists(file):
            os.rename(file, os.path.join(drive_path, file))
    print(f"Arquivos .grib2 copiados para {drive_path}")

def save_token_to_file(rda_token, token_file='./rdams_token.txt'):
    with open(token_file, 'w') as f:
        f.write(rda_token)

def get_met_products(dataset_name, rda_token):
    from rda_apps_clients import rdams_client as rc
    save_token_to_file(rda_token)  # Salva o token para debug e uso do cliente
    with open('debug.log', 'a') as log:
        log.write(f"get_met_products: dataset={dataset_name}, token={rda_token}\n")
    if not rda_token or rda_token.strip() == "":
        raise UserError("O token RDA não está definido. Configure o token nas opções do plugin.")
    rda_client = rc.get_authentication('./rdams_token.txt')
    products = rc.list_products(dataset_name)
    with open('debug.log', 'a') as log:
        log.write(f"Produtos retornados: {products}\n")
    # Força a inclusão de 'Analysis' para ds083.3 se não estiver presente
    if dataset_name == 'ds083.3':
        if isinstance(products, dict):
            if 'Analysis' not in products:
                products['Analysis'] = {'label': 'Analysis'}
        elif isinstance(products, list):
            if 'Analysis' not in products:
                products.append('Analysis')
    return products

try:
    # Exemplo de código para teste
    print("Teste de bloco try")
except Exception as e:
    with open('debug.log', 'a') as log:
        log.write(f"Erro: {str(e)}\n")
    raise

