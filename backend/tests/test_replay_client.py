"""Replay tests need no CARLA, running stores or network."""
import csv
from dataclasses import replace
from io import BytesIO
import json
import stat
import wave

import httpx
from PIL import Image
import pytest

from app.config import SOUND_CATEGORIES
from app.services.edge_buffer import EdgeBuffer
from edge.carla_client import DeviceCredentials, load_scenarios
from edge import replay_client as replay


class Cloud:
    base_url = 'http://offline.invalid'
    def __init__(self):
        self.commands = {}
        self.acks, self.uploads, self.frames = [], [], []
        self.upload_ok = self.ack_ok = True
        self.events = []
    def heartbeat(self, device, position):
        return self.commands.pop(device.id, [])
    def acknowledge(self, device, number, ok, detail):
        if not self.ack_ok:
            raise httpx.ReadTimeout('simulated lost acknowledgement')
        self.events.append('ack')
        self.acks.append((number, ok, detail))
    def upload(self, device, metadata, audio):
        self.events.append('upload')
        self.uploads.append((dict(metadata), audio))
        return self.upload_ok
    def frame(self, device, data):
        self.frames.append(data)


def command(number, name, scenario=None):
    return {'command_id': number, 'command': name, 'payload': {'scenario': scenario} if scenario else {}}


@pytest.fixture
def setup(tmp_path):
    stream = BytesIO()
    with wave.open(stream, 'wb') as out:
        out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        out.writeframes(b'\0\0' * 1600)
    clip = tmp_path/'clip.wav'
    clip.write_bytes(stream.getvalue())
    for index in range(3):
        Image.new('RGB', (16, 9), (index*50,20,30)).save(tmp_path/f'{index}.jpg')
    recording = replay.Recording('test', 'emergency', 'Town03_Opt', [
        {'t_s':float(i),'file':f'{i}.jpg','lat':37.3+i*.0001,'lon':-121.9,'speed_kmh':10.,'heading':90.}
        for i in range(3)], [{'t_s':1.,'label':'event'}], tmp_path)
    cloud = Cloud()
    mic, camera = DeviceCredentials(1,'private-mic-key'), DeviceCredentials(5,'private-camera-key')
    recordings = {c:replace(recording,category=c) for c in SOUND_CATEGORIES}
    def build():
        return replay.ReplayEdgeClient(cloud,mic,1,EdgeBuffer(tmp_path/'buffer'),
                                       {c:clip for c in SOUND_CATEGORIES},recordings,camera=camera)
    return build(), cloud, build


def test_idle_start_fails_and_ack_requires_durable_event_buffer(setup,monkeypatch):
    client,cloud,_=setup
    cloud.commands[1]=[command(1,'start_route')]
    client.tick(0)
    assert cloud.acks[-1][:2]==(1,False) and 'Choose a sound scenario' in cloud.acks[-1][2]
    original=client.buffer.enqueue
    def enqueue(audio,metadata):
        result=original(audio,metadata)
        cloud.events.append('buffered')
        return result
    monkeypatch.setattr(client.buffer,'enqueue',enqueue)
    cloud.commands[1]=[command(2,'simulate_event','emergency')]
    client.tick(10)
    assert client.playback.elapsed==0 and not cloud.uploads
    assert not any(row[0]==2 for row in cloud.acks)
    client.tick(1)
    assert cloud.acks[-1][:2]==(2,True) and 'buffered' in cloud.acks[-1][2]
    assert cloud.events[-3:]==['buffered','ack','upload']
    client.tick(5)
    cloud.commands[1]=[command(3,'start_route')]
    client.tick(0)
    assert cloud.acks[-1][:2]==(3,False)


def test_pause_resume_freezes_gps_elapsed_and_does_not_refire(setup):
    client,cloud,_=setup
    cloud.commands[1]=[command(1,'simulate_event','emergency')]
    client.tick(0)
    cloud.commands[1]=[command(2,'stop_route')]
    client.tick(.8)
    position=client.telemetry()
    client.tick(20)
    assert client.playback.elapsed==0 and client.telemetry()==position and not cloud.uploads
    cloud.commands[1]=[command(3,'start_route')]
    client.tick(.5)
    assert client.playback.elapsed==.5 and client.telemetry()['speed_kmh']>0
    client.tick(.5)
    assert len(cloud.uploads)==1
    cloud.commands[1]=[command(4,'stop_route')]
    client.tick(0)
    cloud.commands[1]=[command(5,'start_route')]
    client.tick(2)
    assert len(cloud.uploads)==1


def test_stop_capture_cancels_pending_sound_even_after_reenable(setup):
    client,cloud,_=setup
    cloud.commands[1]=[command(1,'simulate_event','emergency')]
    client.tick(0)
    cloud.commands[1]=[command(2,'stop_capture')]
    client.tick(1)
    assert not client.capture_enabled and any(row[:2]==(1,False) for row in cloud.acks)
    cloud.commands[1]=[command(3,'start_capture')]
    client.tick(0)
    client.tick(5)
    assert not cloud.uploads and len(client.buffer)==0
    cloud.commands[1]=[command(4,'simulate_event','emergency')]
    client.tick(0)
    client.tick(1)
    assert len(cloud.uploads)==1


def test_camera_capture_is_independent_of_microphone(setup):
    client,cloud,_=setup
    cloud.commands[1]=[command(1,'simulate_event','emergency')]
    cloud.commands[5]=[command(2,'stop_capture')]
    client.tick(0)
    client.tick(1)
    assert len(cloud.uploads)==1 and not cloud.frames
    cloud.commands[5]=[command(3,'start_capture')]
    client.tick(0)
    assert cloud.frames


def test_replacement_and_reboot_cancel_only_pending_events(setup):
    client,cloud,_=setup
    cloud.commands[1]=[command(1,'simulate_event','emergency')]
    client.tick(0)
    cloud.commands[1]=[command(2,'simulate_event','human')]
    client.tick(0)
    assert any(row[:2]==(1,False) for row in cloud.acks)
    cloud.commands[1]=[command(3,'reboot')]
    client.tick(1)
    client.tick(5)
    assert any(row[:2]==(2,False) for row in cloud.acks)
    assert client.telemetry()['speed_kmh']==0 and not cloud.uploads


def test_ambiguous_ack_and_upload_outage_survive_restart_without_duplicate(setup):
    client,cloud,build=setup
    cloud.upload_ok=cloud.ack_ok=False
    cloud.commands[1]=[command(10,'simulate_event','emergency')]
    client.tick(0)
    client.tick(1)
    assert len(client.buffer)==1
    clip_id=cloud.uploads[-1][0]['clip_id']
    assert 'private-mic-key' not in client._journal_path.read_text()
    assert stat.S_IMODE(client._journal_path.stat().st_mode)==0o600
    restarted=build()
    cloud.upload_ok=cloud.ack_ok=True
    cloud.commands[1]=[command(10,'simulate_event','emergency')]
    restarted.tick(0)
    assert len(restarted.buffer)==0 and cloud.uploads[-1][0]['clip_id']==clip_id
    assert cloud.acks[-1][:2]==(10,True)
    assert json.loads(restarted._journal_path.read_text())['commands']==[]
    assert restarted.telemetry()['speed_kmh']==0


def test_restart_before_event_explicitly_fails_and_stays_idle(setup):
    client,cloud,build=setup
    cloud.commands[1]=[command(1,'simulate_event','emergency')]
    client.tick(0)
    restarted=build()
    restarted.tick(5)
    assert cloud.acks[-1][:2]==(1,False) and 'interrupted' in cloud.acks[-1][2]
    assert not cloud.uploads and restarted.telemetry()['speed_kmh']==0


def test_full_fifo_fails_event_without_success_ack(setup):
    client,cloud,_=setup
    cloud.upload_ok=False
    client.buffer=EdgeBuffer(client.buffer.directory,capacity=1)
    client.buffer.enqueue(b'existing',{})
    cloud.commands[1]=[command(1,'simulate_event','emergency')]
    client.tick(0)
    client.tick(1)
    assert any(row[:2]==(1,False) for row in cloud.acks) and len(client.buffer)==1


def test_all_five_packaged_recordings_have_valid_frames_events_and_clips():
    recordings=replay.load_recordings(replay.DEFAULT_RECORDINGS_DIR)
    clips=load_scenarios(replay.DEFAULT_CLIPS)
    assert set(recordings)==set(SOUND_CATEGORIES)
    assert sum(len(recording.rows) for recording in recordings.values())==155
    for category,recording in recordings.items():
        assert clips[category].is_file() and recording.duration>0
        assert all(0<=event['t_s']<=recording.duration for event in recording.events)
        for index in range(len(recording.rows)):
            data=recording.frame_bytes(index)
            assert data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9')


@pytest.mark.parametrize('field,value',[('file','../outside.jpg'),('file','/etc/passwd'),('file','missing.jpg'),
    ('t_s','nan'),('t_s','inf'),('t_s','3'),('lat','91'),('lat','nan'),('event','nan'),('event','50')])
def test_bad_recording_metadata_is_rejected(tmp_path,field,value):
    Image.new('RGB',(16,9)).save(tmp_path/'frame.jpg')
    (tmp_path/'scenario.json').write_text(json.dumps({'sound_class':'emergency',
        'events':[{'t_s':value if field=='event' else 2}]}))
    first={'t_s':1,'file':'frame.jpg','lat':37.3,'lon':-121.9,'speed_kmh':10,'heading':90}
    if field!='event': first[field]=value
    with (tmp_path/'telemetry.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(first))
        writer.writeheader();writer.writerow(first);writer.writerow({**first,'t_s':3})
    with pytest.raises((ValueError,OSError)):
        replay.load_recording(tmp_path,'emergency')


def test_missing_category_assets_fail_instead_of_starting_partial_demo(tmp_path):
    with pytest.raises(OSError): replay.load_recordings(tmp_path)
