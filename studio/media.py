"""Bounded local media processing. No user supplied commands or remote inputs."""
import base64
import colorsys
import io
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError
from .models import Asset
from .schema import asset

MAX_BYTES = 100 * 1024 * 1024
MAX_PIXELS = 16_000_000

def image_bytes(data):
    try:
        with Image.open(io.BytesIO(data)) as original:
            if original.width * original.height > MAX_PIXELS or original.format not in {'PNG','JPEG','WEBP','GIF'}:
                raise ValueError('Image trop grande ou format non pris en charge.')
            result = ImageOps.exif_transpose(original).convert('RGBA')
            result.load()
            return result
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError('Le fichier ne contient pas une image valide.') from error

def png(image):
    output = io.BytesIO()
    image.save(output,format='PNG')
    return output.getvalue()

def run(command, timeout=180):
    try:
        return subprocess.run(command,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              timeout=timeout).stdout
    except FileNotFoundError as error:
        raise ValueError('Le montage audio et vidéo nécessite FFmpeg sur le serveur.') from error
    except (subprocess.SubprocessError, OSError) as error:
        raise ValueError('Ce média ne peut pas être traité, ou le montage dépasse le délai autorisé.') from error

def inspect_media(data, declared_type=''):
    if not data or len(data)>MAX_BYTES:
        raise ValueError('Fichier vide ou supérieur à 100 Mo.')
    try:
        image = image_bytes(data)
    except ValueError:
        if declared_type.startswith('image/'):
            raise
    else:
        return png(image),'image','image/png',{'width':image.width,'height':image.height},'png'
    with tempfile.TemporaryDirectory(prefix='2ai-probe-') as folder:
        source = Path(folder)/'input'
        source.write_bytes(data)
        info = json.loads(run(['ffprobe','-v','error','-protocol_whitelist','file,pipe',
                              '-format_whitelist','mov,matroska,webm,wav,mp3,ogg,flac','-show_format',
                              '-show_streams','-of','json',str(source)],timeout=20))
    formats = set(info.get('format',{}).get('format_name','').split(','))
    if not formats.intersection({'mov','mp4','matroska','webm','wav','mp3','ogg','flac'}):
        raise ValueError('Utilise une image PNG/JPEG/WebP, une vidéo MP4/WebM ou un son WAV/MP3/M4A/OGG/FLAC.')
    duration = float(info.get('format',{}).get('duration',0))
    if not 0 < duration <= 600:
        raise ValueError('Durée de fichier invalide (dix minutes maximum).')
    videos = [row for row in info.get('streams',[]) if row.get('codec_type')=='video' and not row.get('disposition',{}).get('attached_pic')]
    audio = any(row.get('codec_type')=='audio' for row in info.get('streams',[]))
    metadata = {'duration':round(duration,3),'has_audio':audio}
    if videos:
        width,height = videos[0].get('width',0),videos[0].get('height',0)
        if not 0 < width * height <= MAX_PIXELS:
            raise ValueError('Résolution vidéo trop grande.')
        metadata.update(width=width,height=height)
        kind = 'video'
    elif audio:
        kind = 'audio'
    else:
        raise ValueError('Aucune piste vidéo ou audio lisible.')
    if 'matroska' in formats or 'webm' in formats:
        mime,extension = ('video/webm','webm') if kind=='video' else ('audio/webm','webm')
    elif 'mov' in formats or 'mp4' in formats:
        mime,extension = ('video/mp4','mp4') if kind=='video' else ('audio/mp4','m4a')
    else:
        extension = next(iter(formats.intersection({'wav','mp3','ogg','flac'})))
        mime = 'audio/'+{'mp3':'mpeg','wav':'wav','ogg':'ogg','flac':'flac'}[extension]
    return data,kind,mime,metadata,extension

def store_asset(project, data, name, role='reference', job=None, declared_type=''):
    data,kind,mime,metadata,extension = inspect_media(data,declared_type)
    if role=='mask' and kind!='image':
        raise ValueError('Un masque doit être une image.')
    item = Asset(project=project,job=job,kind=kind,role=role,name=str(name or 'Média')[:120],
                 content_type=mime,metadata=metadata,size_bytes=len(data))
    item.file.save(uuid.uuid4().hex+'.'+extension,ContentFile(data),save=False)
    item.save()
    return item

def read(item):
    with item.file.open('rb') as handle:
        return handle.read(MAX_BYTES+1)

def reference(item):
    # Re-encode and bound reference images before sending them to a provider.
    image = image_bytes(read(item))
    image.thumbnail((2048,2048))
    return 'data:image/png;base64,'+base64.b64encode(png(image)).decode('ascii')

def composite(source, edited, mask):
    original = image_bytes(read(source))
    result = image_bytes(edited).resize(original.size,Image.Resampling.LANCZOS)
    region = image_bytes(read(mask)).convert('L')
    if region.size!=original.size:
        raise ValueError('Le masque et l’image doivent avoir les mêmes dimensions.')
    return png(Image.composite(result,original,region))

def recolor(source, mask, color):
    original = image_bytes(read(source))
    target = tuple(int(color[index:index+2],16)/255 for index in (1,3,5))
    hue,saturation,brightness = colorsys.rgb_to_hsv(*target)
    hsv = original.convert('RGB').convert('HSV')
    _,_,value = hsv.split()
    if brightness<1:
        value = value.point(lambda pixel:round(pixel*brightness))
    changed = Image.merge('HSV',(Image.new('L',original.size,round(hue*255)),
                                Image.new('L',original.size,round(saturation*255)),value)).convert('RGBA')
    changed.putalpha(original.getchannel('A'))
    return composite(source,png(changed),mask)

def render(project, scene, kind):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ValueError('Le montage nécessite FFmpeg sur le serveur.')
    if kind=='video' and not scene['visuals']:
        raise ValueError('Ajoute au moins un plan visuel avant d’exporter une vidéo.')
    if kind=='audio' and not scene['audio']:
        raise ValueError('Ajoute au moins une piste audio avant d’exporter.')
    duration = sum(row['duration'] for row in scene['visuals']) if kind=='video' else scene['settings']['duration']
    with tempfile.TemporaryDirectory(prefix='2ai-render-') as directory:
        folder = Path(directory)
        common = ['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y','-threads','2','-filter_threads','2']
        if kind=='video':
            config = scene['settings']
            clips = []
            for index,track in enumerate(scene['visuals']):
                item = asset(project,track['asset_id'])
                source = folder/f'visual-{index}'
                source.write_bytes(read(item))
                clip = folder/f'clip-{index}.mp4'
                inputs = ['-loop','1'] if item.kind=='image' else ['-stream_loop','-1']
                filters = (f"scale={config['width']}:{config['height']}:force_original_aspect_ratio=decrease,"
                           f"pad={config['width']}:{config['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={config['fps']},format=yuv420p")
                run(common+inputs+['-protocol_whitelist','file,pipe','-i',str(source),'-t',str(track['duration']),
                                   '-vf',filters,'-an','-c:v','libx264','-preset','ultrafast','-threads','2',str(clip)])
                clips.append(clip)
            playlist = folder/'clips.txt'
            playlist.write_text(''.join(f"file '{clip.name}'\n" for clip in clips))
            video = folder/'visual.mp4'
            run(common+['-protocol_whitelist','file,pipe','-f','concat','-safe','1','-i',str(playlist),'-c','copy',str(video)])
        audio_path = None
        if scene['audio']:
            inputs,filters,labels = [],[],[]
            for index,track in enumerate(scene['audio']):
                source = folder/f'audio-{index}'
                source.write_bytes(read(asset(project,track['asset_id'])))
                inputs += ['-protocol_whitelist','file,pipe','-i',str(source)]
                filters.append(f"[{index}:a]volume={track['volume']},adelay={round(track['start']*1000)}:all=1[a{index}]")
                labels.append(f'[a{index}]')
            filters.append(''.join(labels)+f'amix=inputs={len(labels)}:normalize=0,apad,atrim=duration={duration}[mix]')
            audio_path = folder/'mix.wav'
            run(common+inputs+['-filter_complex_threads','2','-filter_complex',';'.join(filters),'-map','[mix]',
                               '-ac','2','-ar','44100','-c:a','pcm_s16le',str(audio_path)])
        if kind=='audio':
            output = audio_path
        else:
            output = folder/'export.mp4'
            inputs = ['-i',str(video)] + (['-i',str(audio_path)] if audio_path else [])
            run(common+inputs+['-map','0:v','-c:v','copy']+
                (['-map','1:a','-c:a','aac','-b:a','192k'] if audio_path else ['-an'])+
                ['-t',str(duration),'-movflags','+faststart',str(output)])
        if output.stat().st_size>MAX_BYTES:
            raise ValueError('Le montage dépasse la limite de 100 Mo. Réduis sa durée.')
        return output.read_bytes(),'audio/wav' if kind=='audio' else 'video/mp4'
