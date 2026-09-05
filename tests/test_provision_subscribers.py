import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location('provision', Path(__file__).parents[1] / 'scripts/provision_subscribers.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
UE = dict(imsi='001010000000001', ki='a' * 32, opc='b' * 32, msisdn='1001', amf='9000')


class Response:
    def __init__(self, data, status=200):
        self.status = status
        self.data = json.dumps(data).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self):
        return self.data


def test_existing_records_use_actual_ids_preserve_settings_and_sqn():
    records = {
        'apn': [{'apn_id': 7, 'apn': 'internet', 'apn_ambr_dl': 123}, {'apn_id': 11, 'apn': 'ims'}],
        'auc': [{'auc_id': 42, 'imsi': UE['imsi'], 'sqn': 987}],
        'subscriber': [{'subscriber_id': 51, 'imsi': UE['imsi'], 'enabled': False, 'ue_ambr_dl': 123}],
        'ims_subscriber': [{'ims_subscriber_id': 61, 'imsi': UE['imsi'], 'ifc_path': 'experiment.xml'}],
    }
    writes = []
    def request(req, **kwargs):
        resource = req.full_url.split('/')[3]
        if req.method == 'GET':
            assert '/list?' in req.full_url
            return Response(records[resource])
        assert req.method == 'PATCH'
        data = json.loads(req.data)
        writes.append((resource, data))
        return Response({**records[resource][0], **data})
    with patch.object(p.urllib.request, 'urlopen', side_effect=request):
        p.provision_pyhss({}, [UE])
    writes = dict(writes)
    assert 'apn' not in writes
    assert writes['auc']['amf'] == '9000'
    assert 'sqn' not in writes['auc']
    assert writes['subscriber']['auc_id'] == 42
    assert writes['subscriber']['default_apn'] == 7
    assert writes['subscriber']['apn_list'] == '7,11'
    assert 'ue_ambr_dl' not in writes['subscriber']
    assert 'enabled' not in writes['subscriber']
    assert 'ifc_path' not in writes.get('ims_subscriber', {})


@pytest.mark.parametrize('body,status', [({}, 200), ({'auc_id': 0}, 200), ({'secret': UE['ki']}, 500)])
def test_bad_write_stops_without_secrets(body, status):
    def request(req, **kwargs):
        return Response([]) if req.method == 'GET' else Response(body, status)
    with patch.object(p.urllib.request, 'urlopen', side_effect=request):
        with pytest.raises(RuntimeError) as error:
            p.upsert_pyhss('http://localhost:8080', 'auc', UE, 'imsi', UE['imsi'])
    assert UE['ki'] not in str(error.value)


def test_lookup_failure_never_creates():
    def request(req, **kwargs):
        assert req.method == 'GET'
        return Response({'result': 'Failed'}, 500)
    with patch.object(p.urllib.request, 'urlopen', side_effect=request):
        with pytest.raises(RuntimeError):
            p.upsert_pyhss('http://localhost:8080', 'auc', UE, 'imsi', UE['imsi'])


@pytest.mark.parametrize('records', [
    [{'auc_id': 1, 'imsi': UE['imsi']}, {'auc_id': 2, 'imsi': UE['imsi']}],
    [{'imsi': UE['imsi']}],
])
def test_ambiguous_or_idless_lookup_never_writes(records):
    def request(req, **kwargs):
        assert req.method == 'GET'
        return Response(records)
    with patch.object(p.urllib.request, 'urlopen', side_effect=request):
        with pytest.raises(RuntimeError):
            p.upsert_pyhss('http://localhost:8080', 'auc', UE, 'imsi', UE['imsi'])


def test_malformed_lookup_never_writes():
    def request(req, **kwargs):
        assert req.method == 'GET'
        response = Response([])
        response.data = b'not JSON'
        return response
    with patch.object(p.urllib.request, 'urlopen', side_effect=request):
        with pytest.raises(RuntimeError):
            p.upsert_pyhss('http://localhost:8080', 'auc', UE, 'imsi', UE['imsi'])


def test_failed_patch_stops_before_subscriber_write():
    def request(req, **kwargs):
        resource = req.full_url.split('/')[3]
        if req.method == 'GET':
            if resource == 'apn':
                return Response([{'apn_id': 7, 'apn': 'internet'}, {'apn_id': 11, 'apn': 'ims'}])
            assert resource == 'auc'
            return Response([{'auc_id': 42, 'imsi': UE['imsi']}])
        assert req.method == 'PATCH' and resource == 'auc'
        return Response({'Result': 'Failed'}, 500)
    with patch.object(p.urllib.request, 'urlopen', side_effect=request):
        with pytest.raises(RuntimeError):
            p.provision_pyhss({}, [UE])


def test_mongo_failure_aborts_without_done_or_stderr():
    output = io.StringIO()
    with patch.object(p.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr=UE['ki'])):
        with redirect_stdout(output), pytest.raises(RuntimeError) as error:
            p.provision_open5gs({}, [UE])
    assert 'Done' not in output.getvalue()
    assert UE['ki'] not in str(error.value)


@pytest.mark.parametrize('field,value', [('ki', 'bad'), ('opc', ''), ('imsi', '";bad'), ('amf', 'zzzz'), ('msisdn', '')])
def test_invalid_input_rejected_before_external_writes(field, value):
    with patch.object(p.subprocess, 'run') as run, patch.object(p.urllib.request, 'urlopen') as http:
        for provision in (p.provision_open5gs, p.provision_pyhss):
            with pytest.raises(ValueError):
                provision({}, [UE, {**UE, field: value}])
        run.assert_not_called()
        http.assert_not_called()


def test_ifc_checks_mount_without_restarting(tmp_path, capsys):
    template = tmp_path / 'infrastructure/pyhss/default_ifc.xml'
    template.parent.mkdir(parents=True)
    template.write_text('<test/>')
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='<test/>', stderr='')
    with patch.object(p.subprocess, 'run', side_effect=run):
        p.apply_pyhss_ifc_template(tmp_path)
    assert calls == [["docker", "exec", "pyhss", "cat", "/mnt/pyhss/default_ifc.xml"]]
    assert "does not activate" in capsys.readouterr().out
    with patch.object(p.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='wrong', stderr='')):
        with pytest.raises(RuntimeError):
            p.apply_pyhss_ifc_template(tmp_path)


def test_new_records_use_assigned_ids_and_default_amf():
    writes = []
    def request(req, **kwargs):
        if req.method == 'GET':
            return Response([])
        resource = req.full_url.split('/')[3]
        data = json.loads(req.data)
        writes.append((resource, data))
        return Response({**data, f'{resource}_id': 100 + len(writes)})
    ue = {key: value for key, value in UE.items() if key != 'amf'}
    with patch.object(p.urllib.request, 'urlopen', side_effect=request):
        p.provision_pyhss({}, [ue])
    subscriber = dict(writes)['subscriber']
    assert subscriber['auc_id'] == 103
    assert subscriber['default_apn'] == 101
    assert subscriber['apn_list'] == '101,102'
    assert dict(writes)['auc']['amf'] == '8000'
    assert dict(writes)['auc']['sqn'] == 0


def test_main_reads_amf_and_stops_on_failure(tmp_path, capsys):
    (tmp_path / '.env').write_text('\n'.join(f'UE1_{k.upper()}={v}' for k, v in UE.items()))
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[1] == 'ps':
            return SimpleNamespace(returncode=0, stdout='hss\n', stderr='')
        assert '9000' in args[-1]
        return SimpleNamespace(returncode=1, stdout='', stderr=UE['ki'])
    with patch.object(p, '__file__', str(tmp_path / 'scripts/provision_subscribers.py')):
        with patch.object(p.subprocess, 'run', side_effect=run), pytest.raises(RuntimeError):
            p.main()
    output = capsys.readouterr().out
    assert 'Complete' not in output
    assert 'Done' not in output
    assert len(calls) == 2
