"""Validate uploads and store only a small, freshly encoded avatar without metadata."""
from io import BytesIO
import warnings

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_AVATAR_PIXELS = 16_000_000


def prepare_avatar(upload):
    if upload.size > MAX_AVATAR_BYTES:
        raise ValidationError('Максимальный размер аватарки — 5 МБ.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(upload, formats=['JPEG', 'PNG', 'WEBP']) as source:
                if source.width * source.height > MAX_AVATAR_PIXELS or max(source.size) > 8192:
                    raise ValidationError('Фото слишком большое. Выберите изображение до 16 мегапикселей.')
                source.verify()
            upload.seek(0)
            with Image.open(upload, formats=['JPEG', 'PNG', 'WEBP']) as source:
                square = ImageOps.fit(ImageOps.exif_transpose(source), (512, 512), method=Image.Resampling.LANCZOS)
                rgba = square.convert('RGBA')
                clean = Image.new('RGB', (512, 512), 'white')
                clean.paste(rgba, mask=rgba.getchannel('A'))
                output = BytesIO()
                clean.save(output, format='JPEG', quality=88, optimize=True)
        return ContentFile(output.getvalue(), name='avatar.jpg')
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError('Не удалось прочитать фото. Загрузите исправный JPG, PNG или WebP.') from exc
    finally:
        upload.seek(0)
