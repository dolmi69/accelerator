"""Files and photos in personal messages.

Upload (POST) stores a private file that only the uploader sees; sending the
message over the socket links it to that message. Photos are re-encoded, which
drops EXIF metadata such as GPS coordinates, and get a small preview for the chat.
"""
from datetime import timedelta
from io import BytesIO
import mimetypes
from pathlib import Path
import unicodedata
from uuid import UUID
import warnings

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from founder.models import DirectAttachment

MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_PENDING_PER_CONVERSATION = 20
PENDING_TTL = timedelta(hours=24)
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_SIDE = 12_000
PREVIEW_SIDE = 960

# Photos shown in the chat. Each is decoded and saved again by Pillow.
IMAGE_FORMATS = {
    '.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP',
}
IMAGE_TYPES = {'JPEG': ('image/jpeg', '.jpg'), 'PNG': ('image/png', '.png'), 'WEBP': ('image/webp', '.webp')}
# Other files are always downloaded, never opened in the browser.
FILE_EXTENSIONS = {
    '.pdf', '.txt', '.md', '.csv', '.json', '.rtf',
    '.doc', '.docx', '.odt', '.xls', '.xlsx', '.ods', '.ppt', '.pptx', '.odp', '.key', '.pages', '.numbers',
    '.zip', '.7z', '.rar', '.gif', '.heic', '.heif', '.svg', '.mp3', '.m4a', '.wav', '.mp4', '.mov',
    '.fig', '.sketch', '.psd', '.ai',
}
ALLOWED_EXTENSIONS = set(IMAGE_FORMATS) | FILE_EXTENSIONS


def max_bytes():
    return settings.MAX_UPLOAD_BYTES


def clean_name(name):
    """Display name only: no folders, control characters or overlong names."""
    name = Path(str(name or '').replace('\\', '/')).name
    name = ''.join(char for char in unicodedata.normalize('NFC', name)
                   if unicodedata.category(char)[0] != 'C').strip(' .')
    if not name:
        return 'file'
    stem, suffix = Path(name).stem, Path(name).suffix[:16]
    return f'{stem[:255 - len(suffix)]}{suffix}' or 'file'


def _encode_image(upload, image_format):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(upload, formats=[image_format]) as source:
                if source.width * source.height > MAX_IMAGE_PIXELS or max(source.size) > MAX_IMAGE_SIDE:
                    raise ValidationError('Фото слишком большое: до 50 мегапикселей.')
                source.verify()
            upload.seek(0)
            with Image.open(upload, formats=[image_format]) as source:
                image = ImageOps.exif_transpose(source)
                image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError('Не удалось прочитать изображение. Загрузите исправный JPG, PNG или WebP.') from exc
    finally:
        upload.seek(0)

    has_alpha = image.mode in ('RGBA', 'LA', 'PA') or (image.mode == 'P' and 'transparency' in image.info)
    if image_format == 'JPEG' or not has_alpha:
        image = image.convert('RGB')
    else:
        image = image.convert('RGBA')
    full = BytesIO()
    if image_format == 'JPEG':
        image.save(full, format='JPEG', quality=90, optimize=True, progressive=True)
    elif image_format == 'PNG':
        image.save(full, format='PNG', optimize=True)
    else:
        image.save(full, format='WEBP', quality=90, method=4)
    if full.tell() > max_bytes():
        # PNG photos can grow when saved again; JPEG keeps them within the limit.
        full = BytesIO()
        flat = Image.new('RGB', image.size, 'white')
        flat.paste(image, mask=image.getchannel('A') if image.mode == 'RGBA' else None)
        flat.save(full, format='JPEG', quality=88, optimize=True, progressive=True)
        image_format = 'JPEG'

    preview = image.copy()
    preview.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.Resampling.LANCZOS)
    small = BytesIO()
    preview.save(small, format='WEBP', quality=82, method=4)
    return full.getvalue(), small.getvalue(), image_format, image.size


def store_attachment(user, thread, upload):
    if upload is None:
        raise ValidationError('Выберите файл.')
    if upload.size > max_bytes():
        raise ValidationError(f'Файл больше {max_bytes() // (1024 * 1024)} МБ.')
    if upload.size == 0:
        raise ValidationError('Файл пустой.')
    name = clean_name(upload.name)
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValidationError('Такой тип файла нельзя отправить. Подойдут фото, документы, таблицы, '
                              'презентации, архивы, аудио и видео.')
    attachment = DirectAttachment(conversation=thread, uploader=user, original_name=name)
    if suffix in IMAGE_FORMATS:
        full, small, image_format, (width, height) = _encode_image(upload, IMAGE_FORMATS[suffix])
        content_type, extension = IMAGE_TYPES[image_format]
        if extension != suffix and not (extension == '.jpg' and suffix == '.jpeg'):
            attachment.original_name = clean_name(f'{Path(name).stem}{extension}')
        attachment.kind = DirectAttachment.Kind.IMAGE
        attachment.content_type = content_type
        attachment.size_bytes = len(full)
        attachment.width, attachment.height = width, height
        attachment.file.save(f'image{extension}', ContentFile(full), save=False)
        attachment.preview.save('preview.webp', ContentFile(small), save=False)
    else:
        attachment.kind = DirectAttachment.Kind.FILE
        attachment.content_type = (mimetypes.guess_type(name)[0] or 'application/octet-stream')[:100]
        attachment.size_bytes = upload.size
        attachment.file.save(f'file{suffix}', upload, save=False)
    try:
        attachment.save()
    except Exception:
        delete_files(attachment)
        raise
    return attachment


def delete_files(attachment):
    for field in (attachment.file, attachment.preview):
        if field and field.name:
            try:
                field.storage.delete(field.name)
            except OSError:
                pass


def remove_stale_uploads(user):
    """Files picked but never sent (closed tab, removed from the composer)."""
    stale = DirectAttachment.objects.filter(uploader=user, message__isnull=True,
                                            created_at__lt=timezone.now() - PENDING_TTL)
    for attachment in stale:
        attachment.delete()  # post_delete removes the files.


def parse_ids(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise ValidationError(f'К сообщению можно прикрепить до {MAX_ATTACHMENTS_PER_MESSAGE} файлов.')
    try:
        ids = [UUID(item) for item in value if isinstance(item, str)]
    except ValueError as exc:
        raise ValidationError('Неверный идентификатор файла.') from exc
    if len(ids) != len(value) or len(set(ids)) != len(ids):
        raise ValidationError('Неверный идентификатор файла.')
    return ids


def serialize_attachment(attachment):
    url = reverse('direct_attachment', args=[attachment.pk])
    return {
        'id': str(attachment.pk), 'kind': attachment.kind, 'name': attachment.original_name,
        'size': attachment.size_bytes, 'content_type': attachment.content_type,
        'width': attachment.width, 'height': attachment.height,
        'url': url, 'preview_url': f'{url}?preview=1' if attachment.preview else None,
    }
