from django.apps import AppConfig


class SampleMflixConfig(AppConfig):
    default_auto_field = 'django_mongodb_backend.fields.ObjectIdAutoField'
    name = 'sample_mflix'
