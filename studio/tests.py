import copy
import io
import json
import math
import shutil
import tempfile
import wave
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from PIL import Image
from api.models import AIModel, APIKey, APIProvider
from chat.media_generation import generate_openrouter_media
from chat.services import ProviderError
from . import director, jobs, media
from .models import Asset, Job, Project
from .schema import add_asset, empty_scene, new_version, normalize_operations, validate_scene

def audio_bytes(duration=1):
    output = io.BytesIO()
    with wave.open(output,'wb') as sound:
        sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(8000)
        import struct
        sound.writeframes(b''.join(struct.pack('<h',round(5000*math.sin(index*2*math.pi*440/8000))) for index in range(round(duration*8000))))
    return output.getvalue()

class StudioTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='2ai-studio-tests-')
        self.storage_patch = patch.object(Asset._meta.get_field('file'),'storage',FileSystemStorage(location=self.folder.name))
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop); self.addCleanup(self.folder.cleanup)
        self.user = get_user_model().objects.create_user(username='studio-test',password='TestPass!123')
        self.other = get_user_model().objects.create_user(username='studio-other',password='TestPass!123')
        self.project = Project.objects.create(owner=self.user,title='Projet test')
        new_version(self.project,empty_scene(),'Original'); self.project.refresh_from_db()
        self.original = self.project.current_version
        self.client.force_login(self.user)
        self.image = media.store_asset(self.project,media.png(Image.new('RGBA',(64,48),(20,90,170,255))),'Source')
        mask = Image.new('L',(64,48),0)
        for x in range(32):
            for y in range(48): mask.putpixel((x,y),255)
        self.mask = media.store_asset(self.project,media.png(mask),'Masque','mask')
        self.prefix = f'/studio/{self.project.pk}/'
        self.provider = APIProvider.objects.create(name='Studio OpenRouter',slug='openrouter',base_url='https://openrouter.ai/api')
        key = APIKey(provider=self.provider,name='Test'); key.set_key('test-key-never-call'); key.save()
        self.model = AIModel.objects.create(provider=self.provider,name='Director test',model_id='test/director',input_price_per_million=1,output_price_per_million=1)
        self.user.credit_account.credit(100000,reason='adjustment',note='Isolated test balance')

    def post(self,path,data):
        return self.client.post(self.prefix+path,json.dumps(data),content_type='application/json')
    def balance(self):
        self.user.credit_account.refresh_from_db(); return self.user.credit_account.balance
    def with_image(self):
        scene = empty_scene(); add_asset(scene,self.image); new_version(self.project,scene,'Plan'); self.project.refresh_from_db()
        return scene
    def paid(self):
        return [{'operation':'generate_image','prompt':'Test synthetic image'}]
    def recolor(self):
        return [{'operation':'recolor_image','source_id':self.image.pk,'mask_id':self.mask.pk,'color':'#f00000'}]

    def test_owner_isolation_pages_files_operations(self):
        foreign = Project.objects.create(owner=self.other,title='Other')
        foreign_image = media.store_asset(foreign,media.read(self.image),'Private')
        for path in [f'/studio/{foreign.pk}/',f'/studio/{foreign.pk}/state/',f'/studio/{foreign.pk}/media/{foreign_image.pk}/']:
            self.assertEqual(self.client.get(path).status_code,404)
        response = self.post('quote/',{'operations':[{'operation':'edit_image','source_id':foreign_image.pk,'prompt':'No'}]})
        self.assertEqual(response.status_code,400)
        self.client.logout(); self.assertEqual(self.client.get(self.prefix).status_code,302)

    def test_create_and_template_render(self):
        response = self.client.post('/studio/create/',{'title':'A new test project','persona':self.user.personas.first().pk})
        self.assertEqual(response.status_code,302)
        self.assertContains(self.client.get(response.url),'Directeur IA')
        editor = self.client.get(response.url)
        self.assertContains(editor, 'id="studio-monitor-stage"')
        self.assertContains(editor, 'id="studio-timeline-content"')
        self.assertContains(editor, 'id="studio-clips-panel"')
        self.assertContains(editor, 'studio_editor.js')
        self.assertContains(self.client.get('/studio/'),'A new test project')
        self.assertEqual(self.client.post('/studio/create/',{'persona':'bad'}).status_code,400)

    def test_scene_invalid_shapes_and_finite_numbers(self):
        scene = self.with_image()
        for value in [float('nan'),float('inf'),-1,True,40]:
            broken = copy.deepcopy(scene); broken['visuals'][0]['duration'] = value
            with self.assertRaises(ValueError): validate_scene(self.project,broken)
        broken = copy.deepcopy(scene); broken['settings']['width'] = []
        with self.assertRaises(ValueError): validate_scene(self.project,broken)
        self.assertEqual(self.post('quote/',{'operations':[{'operation':'generate_image','prompt':'a','options':{'image_size':[]}}]}).status_code,400)
        self.assertEqual(self.post('scene/',{'expected_version':self.project.current_version_id,'scene':broken}).status_code,400)

    def test_locked_tracks_reject_director_edits(self):
        scene = self.with_image(); scene['visuals'][0]['locked'] = True
        new_version(self.project,scene,'Locked'); self.project.refresh_from_db()
        with self.assertRaises(ValueError): normalize_operations(self.project,self.recolor())
        with self.assertRaises(ValueError): normalize_operations(self.project,[{'operation':'update_track','track_id':scene['visuals'][0]['id'],'changes':{'duration':3}}])

    def test_save_conflict_and_restore_are_immutable(self):
        original = self.original.scene
        scene = copy.deepcopy(original); scene['brief'] = 'Nouvelle direction'
        result = self.post('scene/',{'expected_version':self.original.pk,'scene':scene})
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.post('scene/',{'expected_version':self.original.pk,'scene':scene}).status_code,409)
        restored = self.post('restore/',{'expected_version':result.json()['version_id'],'version_id':self.original.pk})
        self.assertEqual(restored.status_code,200); self.assertEqual(restored.json()['scene'],original)
        self.original.refresh_from_db(); self.assertEqual(self.original.scene,original)
        self.assertEqual(self.project.versions.count(),3)

    def test_invalid_upload_rejected_valid_upload_private(self):
        bad = SimpleUploadedFile('bad.svg',b'<svg><script>alert(1)</script></svg>',content_type='image/svg+xml')
        self.assertEqual(self.client.post(self.prefix+'upload/',{'file':bad}).status_code,400)
        good = SimpleUploadedFile('image.png',media.read(self.image),content_type='image/png')
        response = self.client.post(self.prefix+'upload/',{'file':good})
        self.assertEqual(response.status_code,200)
        download = self.client.get(self.prefix+f'media/{response.json()["asset_id"]}/')
        self.assertEqual(download['Cache-Control'],'private, no-store'); download.close()

    def test_http_range_delivery_and_invalid_range(self):
        response = self.client.get(self.prefix+f'media/{self.image.pk}/',HTTP_RANGE='bytes=0-9')
        self.assertEqual(response.status_code,206); self.assertEqual(len(b''.join(response.streaming_content)),10)
        suffix = self.client.get(self.prefix+f'media/{self.image.pk}/',HTTP_RANGE='bytes=-4')
        self.assertEqual(len(b''.join(suffix.streaming_content)),4)
        self.assertEqual(self.client.get(self.prefix+f'media/{self.image.pk}/',HTTP_RANGE='bytes=999999-').status_code,416)

    def test_local_recolor_preserves_pixels_outside_mask(self):
        edited = media.image_bytes(media.recolor(self.image,self.mask,'#ff0000'))
        original = media.image_bytes(media.read(self.image))
        self.assertEqual(edited.crop((32,0,64,48)).tobytes(),original.crop((32,0,64,48)).tobytes())
        self.assertNotEqual(edited.getpixel((5,5)),original.getpixel((5,5)))
        small = media.store_asset(self.project,media.png(Image.new('L',(8,8),255)),'Bad','mask')
        with self.assertRaises(ValueError): media.recolor(self.image,small,'#ff0000')

    def test_queue_budget_gate_and_refund_once(self):
        before = self.balance()
        with self.assertRaises(ValueError): jobs.enqueue(self.project,self.paid(),0,self.original.pk)
        self.assertEqual(self.balance(),before)
        quote = jobs.quote(self.project,self.paid())
        job = jobs.enqueue(self.project,self.paid(),quote['estimated_credits'],self.original.pk)
        self.assertEqual(self.balance(),before-quote['estimated_credits'])
        jobs.cancel(job); self.assertEqual(self.balance(),before)
        with self.assertRaises(jobs.Conflict): jobs.cancel(job)
        self.assertEqual(self.balance(),before)

    def test_stale_plan_and_active_job_are_rejected(self):
        self.with_image()
        with self.assertRaises(jobs.Conflict): jobs.enqueue(self.project,self.recolor(),0,self.original.pk)
        job = jobs.enqueue(self.project,self.recolor(),0,self.project.current_version_id)
        with self.assertRaises(jobs.Conflict): jobs.enqueue(self.project,self.recolor(),0,self.project.current_version_id)
        self.assertEqual(jobs.claim_job(job.pk).pk,job.pk); self.assertIsNone(jobs.claim_job(job.pk))

    def test_free_job_creates_version_without_billing(self):
        self.with_image(); before = self.balance()
        task = jobs.enqueue(self.project,self.recolor(),0,self.project.current_version_id)
        jobs.execute_job(jobs.claim_job(task.pk)); task.refresh_from_db(); self.project.refresh_from_db()
        self.assertEqual(task.status,'completed'); self.assertEqual(self.balance(),before)
        self.assertEqual(task.result_version_id,self.project.current_version_id)
        self.assertNotEqual(task.result_version.scene['visuals'][0]['asset_id'],self.image.pk)

    def test_background_result_does_not_overwrite_newer_manual_version(self):
        self.with_image()
        task = jobs.enqueue(self.project,self.recolor(),0,self.project.current_version_id)
        manual = copy.deepcopy(self.project.current_version.scene); manual['brief']='Change while worker is busy'
        version = new_version(self.project,manual,'Manual')
        jobs.execute_job(jobs.claim_job(task.pk)); self.project.refresh_from_db(); task.refresh_from_db()
        self.assertEqual(self.project.current_version_id,version.pk)
        self.assertNotEqual(task.result_version_id,version.pk)

    def test_provider_failure_refunds_and_expired_job_is_not_replayed(self):
        before = self.balance()
        task = jobs.enqueue(self.project,self.paid(),20000,self.original.pk)
        with patch('studio.jobs.generate_openrouter_media',side_effect=ProviderError('Test failure')):
            jobs.execute_job(jobs.claim_job(task.pk))
        task.refresh_from_db(); self.assertEqual(task.status,'failed'); self.assertEqual(self.balance(),before)
        next_task = jobs.enqueue(self.project,self.paid(),20000,self.original.pk)
        jobs.claim_job(next_task.pk)
        Job.objects.filter(pk=next_task.pk).update(lease_until=timezone.now()-timedelta(seconds=1))
        self.assertIsNone(jobs.claim_job(next_task.pk)); next_task.refresh_from_db()
        self.assertEqual(next_task.status,'failed'); self.assertEqual(self.balance(),before)

    def test_generation_references_mask_and_cost_cap(self):
        self.with_image(); before = self.balance()
        operations = [{'operation':'edit_image','source_id':self.image.pk,'mask_id':self.mask.pk,'prompt':'Make selected area green'}]
        reserve = jobs.quote(self.project,operations)['estimated_credits']
        task = jobs.enqueue(self.project,operations,reserve,self.project.current_version_id)
        with patch('studio.jobs.generate_openrouter_media',return_value=(media.png(Image.new('RGB',(64,48),'green')),'image/png',Decimal('100'))) as generate:
            jobs.execute_job(jobs.claim_job(task.pk))
        task.refresh_from_db()
        self.assertEqual(task.status,'completed'); self.assertEqual(before-self.balance(),reserve)
        self.assertTrue(task.warning); self.assertTrue(generate.call_args.kwargs['input_references'][0].startswith('data:image/png;base64,'))
        edited = media.image_bytes(media.read(Asset.objects.get(pk=task.output_ids[0])))
        self.assertEqual(edited.getpixel((50,25)),media.image_bytes(media.read(self.image)).getpixel((50,25)))

    def test_director_valid_plan_is_only_proposed(self):
        reply = json.dumps({'message':'Test plan','operations':self.paid()})
        with patch('studio.director.call_llm_provider',return_value=(reply,100,50)):
            result = director.propose(self.project,'Créer une image',2000)
        self.assertEqual(result['operations'][0]['operation'],'generate_image'); self.assertEqual(Job.objects.count(),0)
        self.assertGreater(result['charged_credits'],0)

    def test_director_invalid_output_refunded_and_mask_request_supported(self):
        before = self.balance()
        for reply in ['invalid json',json.dumps({'message':'Unsafe','operations':[{'operation':'shell','command':'rm -rf /'}]})]:
            with patch('studio.director.call_llm_provider',return_value=(reply,100,50)):
                with self.assertRaises(ValueError): director.propose(self.project,'Test request',2000)
            self.assertEqual(self.balance(),before)
        with patch('studio.director.call_llm_provider',return_value=(json.dumps({'message':'Peins un masque','operations':[]}),100,50)):
            self.assertEqual(director.propose(self.project,'Change only a jacket',2000)['operations'],[])

    def test_openrouter_reference_payloads(self):
        import base64
        with patch('chat.media_generation._json_request',return_value={'data':[{'b64_json':base64.b64encode(b'fake').decode()}]}) as request:
            generate_openrouter_media('fake','image','Edit','test/image',input_references=['data:image/png;base64,AA=='])
        self.assertEqual(request.call_args.args[3]['input_references'][0]['image_url']['url'],'data:image/png;base64,AA==')
        with patch('chat.media_generation._json_request',side_effect=[{'id':'test','polling_url':'https://openrouter.ai/api/v1/videos/test'},
                    {'status':'completed','unsigned_urls':['https://openrouter.ai/api/v1/videos/test/content']}]) as request, patch('chat.media_generation._download_openrouter_video',return_value=(b'fake','video/mp4')):
            generate_openrouter_media('fake','video','Animate','test/video',first_frame='data:image/png;base64,AA==')
        self.assertEqual(request.call_args_list[0].args[3]['frame_images'][0]['frame_type'],'first_frame')

    def test_real_ffmpeg_audio_mix_and_video_assembly(self):
        if not shutil.which('ffmpeg'): self.skipTest('FFmpeg missing')
        sound = media.store_asset(self.project,audio_bytes(),'Synthetic sine','source',declared_type='audio/wav')
        scene = empty_scene(); add_asset(scene,self.image); scene['visuals'][0]['duration']=.5
        add_asset(scene,sound,'voice'); scene['audio'][0].update(start=.1,volume=.5); scene['settings']['duration']=1
        audio,mime = media.render(self.project,scene,'audio')
        with wave.open(io.BytesIO(audio),'rb') as mixed:
            self.assertAlmostEqual(mixed.getnframes()/mixed.getframerate(),1,places=2)
            self.assertEqual(set(mixed.readframes(2200)),{0})
        video,mime = media.render(self.project,scene,'video')
        _,kind,_,info,_ = media.inspect_media(video,mime)
        self.assertEqual(kind,'video'); self.assertTrue(info['has_audio']); self.assertAlmostEqual(info['duration'],.5,delta=.1)
