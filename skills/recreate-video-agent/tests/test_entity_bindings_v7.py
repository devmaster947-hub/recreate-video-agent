import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import entity_bindings as eb
from scripts import generation_manifest as gm
from scripts import reference_audit, prepare_prompt_handoff, run_generation, server_video_analysis, blueprint_timeline

ROOT = Path(__file__).resolve().parents[1]


def blueprint():
    return {
        'schemaVersion': '7.0', 'reviewRequired': False, 'reviewIssues': [],
        '视频元素': {'人物': [{'characterId': 'c1', 'referenceTime': 1}, {'characterId': 'c2', 'referenceTime': 2.5}], '产品': []},
        '声音结构': {'voiceProfiles': [{'speakerId': 'v1', 'characterId': None}]},
        '逐镜头拆解': [
            {'shotId': 's1', 'start': 0, 'end': 2, 'visibleCharacterIds': ['c1'], 'productIds': [], 'keyframes': [{'time': 1, 'priority': 3}], '声音': {'utterances': []}},
            {'shotId': 's2', 'start': 2, 'end': 9, 'visibleCharacterIds': ['c2'], 'productIds': [], 'keyframes': [{'time': 2.5, 'priority': 3}], '声音': {'utterances': [{'utteranceId': 'u1', 'lineId': 'l1', 'speakerId': 'v1', 'start': 3, 'end': 4, 'text': 'Keep this.', 'emotion': '平静', 'intensity': 1, 'onScreen': False, 'lipSync': False}]}},
        ],
        '节奏结构': {'cutPlan': [{'time': 2, 'fromShotId': 's1', 'toShotId': 's2'}], 'dramaticBeats': []},
    }


def manifest(root, bp=None):
    path = gm.command_init(type('Args', (), {'output_root': str(root), 'task_id': 'task', 'reuse': False})())
    data = gm.load_manifest(path)
    data['userConfig']['duration'] = 9
    source = root / 'blueprint.json'
    source.write_text(json.dumps({'videoBlueprint': bp or blueprint()}), encoding='utf-8')
    data['videoBlueprint'] = {'file': str(source)}
    data['replacementBindings'] = [{'entityType': 'character', 'sourceId': c, 'targetId': c, 'mode': 'preserve'} for c in ('c1', 'c2')]
    data['benchmarkVideo']['analysis'] = {'recommendedSegments': [{'segmentId': 1, 'globalStart': 0, 'globalEnd': 9, 'duration': 9}]}
    image = root / 'board.png'
    image.write_bytes(b'unchanged-board')
    # A brief c1 appearance is absent from the nine selected frames.
    anchors = [{'timestamp': i, 'personPresent': True, 'personCount': 1, 'creatorIds': ['c2'], 'productPresent': False} for i in range(9)]
    board = dict(storyboardId=1, segmentId=1, globalStart=0, globalEnd=9, file=str(image), anchors=anchors)
    data['storyboards']['original'] = [board]
    data['storyboards']['generation'] = [board]
    data['videoPrompts'] = {'segments': [dict(segmentId=1, storyboardIds=[1], creatorIds=['c1','c2'], productPresent=False, globalStart=0, globalEnd=9, duration=9, shotIds=['s1','s2'], beatIds=[], utteranceIds=['u1'], prompt='中文说明，对白：Keep this.') ]}
    gm.save_manifest(path, data)
    return path, data


class EntityBindingTests(unittest.TestCase):
    def test_silent_people_and_narrator_are_independent(self):
        eb.validate_blueprint(blueprint(), 9)

    def test_pure_product_without_people(self):
        bp = blueprint()
        bp['视频元素'] = {'人物': [], '产品': [{'productId': 'p1'}]}
        for s in bp['逐镜头拆解']:
            s['visibleCharacterIds'] = []
            s['productIds'] = ['p1']
        eb.validate_blueprint(bp, 9)

    def test_invalid_schema_reference_and_time_rejected(self):
        for kind in ['schema', 'reference', 'time']:
            bp = blueprint()
            if kind == 'schema': bp['schemaVersion'] = 'unknown'
            if kind == 'reference': bp['逐镜头拆解'][0]['visibleCharacterIds'] = ['missing']
            if kind == 'time': bp['逐镜头拆解'][1]['start'] = 5
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                eb.validate_blueprint(bp, 9)

    def test_review_flag_is_not_blanket_block(self):
        bp = blueprint(); bp['reviewRequired'] = True
        eb.validate_blueprint(bp, 9)

    def test_complete_shots_not_only_nine_frames(self):
        with tempfile.TemporaryDirectory() as td:
            path, data = manifest(Path(td))
            self.assertTrue(reference_audit.audit(data)['passed'])
            result = prepare_prompt_handoff.prepare(path, Path(td) / 'handoff')
            payload = json.loads(Path(result['package']).read_text())
            self.assertEqual(payload['entityContexts'][0]['creatorIds'], ['c1','c2'])
            self.assertEqual((Path(td)/'board.png').read_bytes(), b'unchanged-board')
            gm.validate_prompt_record(data['videoPrompts'], data)
            bad = copy.deepcopy(data['videoPrompts']); bad['segments'][0]['creatorIds'] = ['c2']
            with self.assertRaises(ValueError): gm.validate_prompt_record(bad, data)

    def test_unintended_identity_merge_rejected_and_explicit_merge_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            _, data = manifest(Path(td))
            for x in data['replacementBindings']: x.update(mode='replace', targetId='creator1')
            with self.assertRaises(ValueError): eb.validate_bindings(data, blueprint())
            for x in data['replacementBindings']: x['mergeAuthorized'] = True
            eb.validate_bindings(data, blueprint())

    def test_selective_replacement_and_product_reference(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); bp=blueprint()
            bp['视频元素']['产品']=[{'productId':'p1'},{'productId':'p2'}]
            bp['逐镜头拆解'][0]['productIds']=['p1']
            bp['逐镜头拆解'][1]['productIds']=['p2']
            _, data=manifest(root,bp)
            product=root/'product.png'; product.write_bytes(b'product')
            unrelated=root/'other.png'; unrelated.write_bytes(b'other')
            data['product']={'useBenchmarkProduct':False,'productImages':[str(product),str(unrelated)]}
            data['replacementBindings'] += [
                {'entityType':'product','sourceId':'p1','mode':'preserve','targetId':'p1'},
                {'entityType':'product','sourceId':'p2','mode':'replace','targetId':'new','referenceImages':[str(product)]},
            ]
            segment=data['videoPrompts']['segments'][0]; segment['productPresent']=True
            files=run_generation.prepare_references(data,segment,root,'seedance-2-fast')
            self.assertIn(str(product.resolve()),files); self.assertNotIn(str(unrelated.resolve()),files)
            self.assertEqual(eb.segment_context(data,segment)['sourceProductIds'],['p1','p2'])
            product.unlink()
            with self.assertRaises(ValueError): run_generation.prepare_references(data,segment,root,'seedance-2-fast')

    def test_mirror_instance_does_not_require_new_identity(self):
        with tempfile.TemporaryDirectory() as td:
            _, data=manifest(Path(td))
            data['storyboards']['generation'][0]['anchors'][0]['personCount']=2
            self.assertTrue(reference_audit.audit(data)['passed'])

    def test_new_manifest_requests_v2_legacy_requests_v1(self):
        with tempfile.TemporaryDirectory() as td:
            _, data=manifest(Path(td))
            request=server_video_analysis.build_input(data,{'url':'https://example.invalid/video.mp4'})
            self.assertEqual((request['plannerVersion'],request['userConfig']['blueprintSchemaVersion']),('2','7.0'))
            data['skillVersion']='5.0'; data['userConfig'].pop('plannerVersion')
            self.assertEqual(server_video_analysis.build_input(data,{'url':'https://example.invalid/video.mp4'})['plannerVersion'],'1')


if __name__ == '__main__': unittest.main()
