FROM public.ecr.aws/docker/library/python:3.14-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DJANGO_SETTINGS_MODULE=config.amvera DJANGO_DEBUG=0
WORKDIR /app
COPY requirements.txt requirements-deploy.txt ./
RUN pip install --no-cache-dir -r requirements-deploy.txt
COPY . .
RUN DJANGO_SECRET_KEY=build-only-static-collection python manage.py collectstatic --noinput
EXPOSE 8080
CMD ["python", "deploy/start_amvera.py"]
